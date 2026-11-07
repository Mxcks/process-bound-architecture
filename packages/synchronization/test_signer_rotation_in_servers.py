"""
Automated tests for anchor signer rotation at the networked
server processes (tests/test-cases/sync-mesh.md case 26 and
registration.md case 12, built in experiment 038).

Experiment 028's finding, re-run by experiment 029 at a
witness-configured server, is re-run here once more under
ROTATION: the server's anchor authority has legitimately
moved from alice to bob by case 12's rotation record, and the
question is what the move does to the rewrite. The server is
configured with the anchor's LINEAGE SECRETS
(--anchor-signer-secrets-file, a JSON identity-to-secret map,
beside case 10's signer flags and case 11's witness flags);
the anchor signer flag names the lineage's root, alice.

- A paired delivery carrying the successor's anchor and its
  rotation is accepted at the lineage-configured server; the
  rotation is recorded in the server's anchor file, the
  successor's later deliveries land as ordinary same-signer
  deliveries, and /health reports anchored throughout.
- The identical delivery at a server with NO lineage
  configuration is refused as different_signer, exactly as
  case 17 refuses any different signer: rotation is opt-in
  at the server, as signing and witnessing are.
- The finding re-run under rotation, in its three legs:
  (i) experiment 028's rewrite pair signed by the OLD signer
  is refused as anchor_signer_retired; (ii) the same rewrite
  signed by the NEW signer without the configured witness's
  countersignature is refused as anchor_witness_invalid,
  exactly as case 18 judged the old signer's pair; (iii) the
  same rewrite signed by the new signer WITH the witness's
  countersignature is ACCEPTED under the standing rules -
  the fence after rotation is the new signer's secret plus
  the witness's, two custodies as before.
- A paired push (case 19) from a rotated server carries the
  rotation to a lineage-configured peer in the same round:
  the peer's anchor ends byte-for-byte the pushed anchor,
  rotations included, and the peer then refuses the old
  signer's deliveries as anchor_signer_retired.

Servers on which no lineage is configured behave exactly as
experiments 019/028/029 left them, proven by those
unmodified suites in the same full run. Process cleanup is
part of the contract, as in experiments 007-037: every
spawned server is registered with addCleanup the moment it
starts. Test credentials are generated inside the tests at
runtime; the signers' and witness's secrets are enrollments
issued at runtime and never leave the test's channel calls
and the servers' secret files. No credential or secret is
committed.

Run with either:

    python3 -m unittest discover -s packages/synchronization -v -p "test_signer_rotation_in_servers.py"
    python3 packages/synchronization/test_signer_rotation_in_servers.py

Standard library only. Localhost only; nothing leaves this
machine.
"""

import base64
import json
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "policy-library"))
sys.path.insert(0, str(HERE.parent / "registration"))

import node_server  # noqa: E402
from registration import (  # noqa: E402
    RegistrationChannel,
    sign_library,
)


def _load_registry_anchor():
    """Experiment 012's REGISTRY anchor module by file path
    under a private name, as in experiment 028's suite."""
    import importlib.util
    anchor_file = HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_rotation_test_registry_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_anchor_mod = _load_registry_anchor()
write_anchor = _anchor_mod.write_anchor
make_anchor_rotation = _anchor_mod.make_anchor_rotation
read_anchor_audit = _anchor_mod.read_anchor_audit

SERVER = HERE / "node_server.py"
ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
BOB_CODE = b"policy: allow if caller.tier == 'guest'\n"
MALLORY_CODE = b"policy: allow if caller.tier == 'attacker'\n"
MALLORY2_CODE = b"policy: allow if caller.tier == 'attacker-guest'\n"


class ServerProcess:
    """One node_server.py child process, reliably cleaned up.
    Mirrors test_witnessed_anchor_in_servers.py's helper."""

    def __init__(self, test, workdir: Path, name: str, args: list[str]):
        self.name = name
        self.port_file = workdir / f"{name}.port"
        self.log_path = workdir / f"{name}.log"
        self._log_fh = self.log_path.open("w", encoding="utf-8")
        cmd = [
            sys.executable, str(SERVER),
            "--node-id", name,
            "--port", "0",
            "--port-file", str(self.port_file),
        ]
        cmd += args
        self.proc = subprocess.Popen(
            cmd, stdout=self._log_fh, stderr=subprocess.STDOUT)
        test.addCleanup(self.stop)
        self.base_url = None

    def wait_ready(self, timeout: float = 20.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"server {self.name} exited early "
                    f"rc={self.proc.returncode}; log:\n{self.log_text()}")
            if self.port_file.exists():
                port = int(self.port_file.read_text(encoding="utf-8").strip())
                self.base_url = f"http://127.0.0.1:{port}"
                try:
                    node_server.fetch_health(self.base_url)
                    return self.base_url
                except (AssertionError, urllib.error.URLError, OSError,
                        ssl.SSLError):
                    pass
            time.sleep(0.05)
        raise AssertionError(f"server {self.name} not ready in {timeout}s")

    def log_text(self) -> str:
        self._log_fh.flush()
        return self.log_path.read_text(encoding="utf-8")

    def stop(self) -> None:
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=5)
        finally:
            self._log_fh.close()


def entries_of(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def anchor_dict(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class RotationServerSetup(unittest.TestCase):
    """Shared setup: alice's and bob's genuine registry
    through the real Gate 1 channel; each server's anchor is
    signed by alice and countersigned by carol, the witness.
    Enrollment secrets for alice (the lineage root), bob (the
    successor), and carol (the witness) are all kept."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.tmp = tmp

        central = tmp / "central"
        self.registry_log = central / "registry.jsonl"
        self.channel = RegistrationChannel(
            self.registry_log, central / "audit.jsonl")
        self.alice_secret = self.channel.enroll_user("alice")
        self.bob_secret = self.channel.enroll_user("bob")
        self.carol_secret = self.channel.enroll_user("carol")
        self.alice_record, created = self.channel.register(
            author="alice", name="member-access", version="1.0.0",
            code=ALICE_CODE,
            signature=sign_library(self.alice_secret, "alice",
                                   "member-access", "1.0.0", ALICE_CODE))
        assert created
        self.bob_record, created = self.channel.register(
            author="bob", name="guest-access", version="1.0.0",
            code=BOB_CODE,
            signature=sign_library(self.bob_secret, "bob",
                                   "guest-access", "1.0.0", BOB_CODE))
        assert created
        self.payloads_file = tmp / "payloads.json"
        self.payloads_file.write_text(json.dumps({
            self.alice_record.policy_hash: ALICE_CODE.hex(),
            self.bob_record.policy_hash: BOB_CODE.hex(),
        }), encoding="utf-8")
        self.staging_dir = tmp / "staging"
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    # -- helpers -----------------------------------------------------

    def _anchor_path(self, name: str) -> Path:
        return self.tmp / f"{name}-outside" / "registry-anchor.json"

    def _secret_file(self, name: str, filename: str, content: str) -> Path:
        path = self.tmp / f"{name}-outside" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _lineage_file(self, name: str) -> Path:
        return self._secret_file(
            name, "anchor-signer-secrets.json",
            json.dumps({"alice": self.alice_secret,
                        "bob": self.bob_secret}))

    def _initial_anchor(self, name: str):
        write_anchor(
            self.registry_log, self._anchor_path(name),
            signer="alice", secret_hex=self.alice_secret,
            witness="carol", witness_secret_hex=self.carol_secret)

    def _snapshot_copy(self, name: str) -> Path:
        dest_dir = self.tmp / f"{name}-dir"
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / "snapshot.jsonl"
        shutil.copyfile(self.registry_log, dest)
        return dest

    def _server_args(self, name: str, snapshot: Path,
                     lineage: bool = True, payloads=None):
        args = [
            "--snapshot", str(snapshot),
            "--payloads", str(payloads or self.payloads_file),
            "--serve-policies",
            "--anchor", str(self._anchor_path(name)),
            "--anchor-signer", "alice",
            "--anchor-secret-file", str(self._secret_file(
                name, "anchor-secret.txt", self.alice_secret)),
            "--anchor-witness", "carol",
            "--anchor-witness-secret-file", str(self._secret_file(
                name, "anchor-witness-secret.txt", self.carol_secret)),
        ]
        if lineage:
            args += ["--anchor-signer-secrets-file",
                     str(self._lineage_file(name))]
        return args

    def _start(self, name, lineage=True, payloads=None):
        server = ServerProcess(
            self, self.tmp, name,
            self._server_args(name, self._snapshot_copy(name),
                              lineage=lineage, payloads=payloads))
        server.wait_ready()
        return server

    def _grow(self, author="dave", name="late-addition"):
        secret = self.channel.enroll_user(author)
        code = f"policy: {name}\n".encode("utf-8")
        record, created = self.channel.register(
            author=author, name=name, version="1.0.0", code=code,
            signature=sign_library(secret, author, name, "1.0.0",
                                   code))
        assert created
        return record

    def _lineage(self):
        return {"alice": self.alice_secret, "bob": self.bob_secret}

    def _staged_rotation_anchor(self, staging_name: str,
                                extend_from: Path) -> Path:
        """The successor's anchor over the channel's current
        log, extending the given anchor file: the rotation
        record (alice -> bob at the next sequence, over the
        current head) applied by bob's first write, carol
        countersigning as the configured witness."""
        staging = self.staging_dir / staging_name
        shutil.copyfile(extend_from, staging)
        entries = entries_of(self.registry_log)
        rotation = make_anchor_rotation(
            from_signer="alice", from_secret_hex=self.alice_secret,
            to_signer="bob", to_secret_hex=self.bob_secret,
            rotation_seq=0, effective_anchor_seq=1,
            effective_chain_head=entries[-1]["entry_hash"],
            effective_entry_count=len(entries),
        )
        write_anchor(
            self.registry_log, staging,
            signer="bob", secret_hex=self.bob_secret,
            rotation=rotation,
            predecessor_secret_hex=self.alice_secret,
            witness="carol", witness_secret_hex=self.carol_secret)
        return staging

    def _rotate_server(self, name, server):
        """The legitimate rotation at one running server: the
        registry grows, and the successor's anchor - with its
        rotation - is delivered paired. Returns the report."""
        self._grow()
        staging = self._staged_rotation_anchor(
            f"{name}-rotated.json", self._anchor_path(name))
        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(staging),
            entries=entries_of(self.registry_log))
        return report

    def _assert_alice_serves(self, base_url: str):
        status, body = node_server.fetch_policy(
            base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]),
                         ALICE_CODE)


class RotationServerTests(RotationServerSetup):

    # Case 26: the rotation reaches a running server over the
    # paired delivery surface and is accepted: the server's
    # anchor file records the rotation and names bob as its
    # signer, /health reports anchored and the lineage as
    # configured, and the successor's later ordinary delivery
    # (bob re-anchoring his own rotated anchor) lands too.
    def test_rotation_delivered_paired_and_accepted(self):
        self._initial_anchor("rot-a")
        server = self._start("rot-a")
        health = node_server.fetch_health(server.base_url)
        self.assertTrue(health["anchor_signer_secrets_configured"])
        self.assertEqual(health["anchor_status"], "anchored")

        report = self._rotate_server("rot-a", server)
        self.assertEqual(report["status"], "delivered")
        self.assertEqual(report["signer"], "bob")
        carried = anchor_dict(self._anchor_path("rot-a"))
        self.assertEqual(carried["signer"], "bob")
        self.assertEqual(len(carried["rotations"]), 1)
        self.assertEqual(carried["rotations"][0]["from_signer"],
                         "alice")
        self.assertEqual(carried["rotations"][0]["to_signer"],
                         "bob")
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["anchor_status"], "anchored")
        self.assertEqual(health["snapshot_entries"], 3)
        self._assert_alice_serves(server.base_url)
        print("ROTATION AT A SERVER: the successor's anchor "
              "and its rotation landed paired; the anchor "
              "file records the rotation and names bob")

        # The successor's later writes are ordinary: bob
        # re-anchors the grown record under his own rotated
        # anchor (the write presents the lineage secrets, as
        # case 12's write path requires over a rotated
        # anchor), and the pair lands.
        self._grow(author="erin", name="second-addition")
        staging = self.staging_dir / "rot-a-grown.json"
        shutil.copyfile(self._anchor_path("rot-a"), staging)
        write_anchor(
            self.registry_log, staging,
            signer="bob", secret_hex=self.bob_secret,
            signer_secrets=self._lineage(),
            witness="carol", witness_secret_hex=self.carol_secret)
        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(staging),
            entries=entries_of(self.registry_log))
        self.assertEqual(report["status"], "delivered")
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["snapshot_entries"], 4)
        self.assertEqual(health["anchor_status"], "anchored")
        print("ROTATION AT A SERVER: the successor's later "
              "ordinary delivery lands as a same-signer "
              "delivery")

    # Case 26: the identical rotated delivery at a server
    # with NO lineage configuration is refused as
    # different_signer - case 17's succession, unchanged -
    # and neither file moves.
    def test_rotation_refused_without_lineage_configuration(self):
        self._initial_anchor("rot-b")
        snapshot = self._snapshot_copy("rot-b")
        server = ServerProcess(
            self, self.tmp, "rot-b",
            self._server_args("rot-b", snapshot, lineage=False))
        server.wait_ready()
        health = node_server.fetch_health(server.base_url)
        self.assertFalse(health["anchor_signer_secrets_configured"])

        self._grow()
        staging = self._staged_rotation_anchor(
            "rot-b-rotated.json", self._anchor_path("rot-b"))
        snap_before = snapshot.read_bytes()
        anchor_before = self._anchor_path("rot-b").read_bytes()
        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(staging),
            entries=entries_of(self.registry_log))
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "different_signer")
        self.assertEqual(snapshot.read_bytes(), snap_before)
        self.assertEqual(self._anchor_path("rot-b").read_bytes(),
                         anchor_before)
        self._assert_alice_serves(server.base_url)
        print("ROTATION AT A SERVER: with no lineage "
              "configuration the successor's delivery is "
              "refused as different_signer, as case 17 "
              "refuses any different signer")

    # Case 26's central assertion: experiment 028's finding
    # re-run under rotation. The server's anchor first
    # rotates legitimately to bob; then (i) the rewrite pair
    # signed by the OLD signer is refused as
    # anchor_signer_retired; (ii) the same rewrite signed by
    # the NEW signer without the witness's countersignature
    # is refused as anchor_witness_invalid; (iii) the same
    # rewrite signed by the new signer WITH the configured
    # witness's countersignature is ACCEPTED under the
    # standing rules - the fence after rotation is the new
    # signer's secret plus the witness's.
    def test_finding_rerun_under_rotation(self):
        evil_log, mallory1, _mallory2 = self._rewritten_log()
        payloads = self.tmp / "payloads-with-mallory.json"
        payloads.write_text(json.dumps({
            self.alice_record.policy_hash: ALICE_CODE.hex(),
            self.bob_record.policy_hash: BOB_CODE.hex(),
            mallory1.policy_hash: MALLORY_CODE.hex(),
        }), encoding="utf-8")
        self._initial_anchor("rot-c")
        pre_rotation_anchor = self.tmp / "rot-c-pre-rotation.json"
        shutil.copyfile(self._anchor_path("rot-c"),
                        pre_rotation_anchor)
        snapshot = self._snapshot_copy("rot-c")
        server = ServerProcess(
            self, self.tmp, "rot-c",
            self._server_args("rot-c", snapshot, payloads=payloads))
        server.wait_ready()

        report = self._rotate_server("rot-c", server)
        self.assertEqual(report["status"], "delivered")
        snap_before = snapshot.read_bytes()
        anchor_before = self._anchor_path("rot-c").read_bytes()
        evil_entries = entries_of(evil_log)

        # Leg (i): the rewrite, anchored by alice extending
        # her pre-rotation anchor over the rewritten record,
        # countersigned by carol as usual - every signature
        # genuine, the signer retired.
        staging = self.staging_dir / "evil-alice.json"
        shutil.copyfile(pre_rotation_anchor, staging)
        write_anchor(
            evil_log, staging,
            signer="alice", secret_hex=self.alice_secret,
            witness="carol", witness_secret_hex=self.carol_secret)
        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(staging),
            entries=evil_entries)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"],
                         "anchor_signer_retired")
        self.assertEqual(snapshot.read_bytes(), snap_before)
        self.assertEqual(self._anchor_path("rot-c").read_bytes(),
                         anchor_before)
        events = read_anchor_audit(self._anchor_path("rot-c"))
        retired = [e for e in events
                   if e.get("reason") == "anchor_signer_retired"]
        self.assertEqual(len(retired), 1)
        self._assert_alice_serves(server.base_url)
        print("FINDING UNDER ROTATION leg (i): the rewrite "
              "signed by the OLD signer is REFUSED as "
              "anchor_signer_retired")

        # Leg (ii): the rewrite anchored by bob, the current
        # signer, extending the server's rotated anchor -
        # without carol's countersignature.
        staging = self.staging_dir / "evil-bob-nowitness.json"
        shutil.copyfile(self._anchor_path("rot-c"), staging)
        write_anchor(
            evil_log, staging,
            signer="bob", secret_hex=self.bob_secret,
            signer_secrets=self._lineage())
        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(staging),
            entries=evil_entries)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"],
                         "anchor_witness_invalid")
        self.assertEqual(snapshot.read_bytes(), snap_before)
        self.assertEqual(self._anchor_path("rot-c").read_bytes(),
                         anchor_before)
        self._assert_alice_serves(server.base_url)
        print("FINDING UNDER ROTATION leg (ii): the rewrite "
              "signed by the NEW signer without the witness "
              "is REFUSED as anchor_witness_invalid - rotation "
              "changed who signs, not the witness rule")

        # Leg (iii): the same pair, countersigned by carol,
        # the configured witness - judged by the standing
        # rules and accepted, as case 18's third leg was.
        staging = self.staging_dir / "evil-bob-witnessed.json"
        shutil.copyfile(self._anchor_path("rot-c"), staging)
        write_anchor(
            evil_log, staging,
            signer="bob", secret_hex=self.bob_secret,
            signer_secrets=self._lineage(),
            witness="carol", witness_secret_hex=self.carol_secret)
        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(staging),
            entries=evil_entries)
        self.assertEqual(report["status"], "delivered")
        self.assertEqual(report["anchor_status"], "anchored")
        self.assertEqual(snapshot.read_bytes(),
                         evil_log.read_bytes())
        status, body = node_server.fetch_policy(
            server.base_url, mallory1.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]),
                         MALLORY_CODE)
        print("FINDING UNDER ROTATION leg (iii): the rewrite "
              "signed by the NEW signer and countersigned by "
              "the configured witness is ACCEPTED under the "
              "standing rules - the fence is the new signer's "
              "secret plus the witness's, as before")

    # Case 26: a paired push from a rotated server carries
    # the rotation to a lineage-configured peer in the same
    # round - the peer's anchor ends byte-for-byte the pushed
    # anchor, rotations included - and the peer then refuses
    # the old signer's anchor as anchor_signer_retired.
    def test_paired_push_carries_rotation(self):
        self._initial_anchor("rot-d")
        self._initial_anchor("rot-e")
        a = self._start("rot-d")
        b = self._start("rot-e")

        report = self._rotate_server("rot-d", a)
        self.assertEqual(report["status"], "delivered")

        status, push = node_server.push_anchor(
            a.base_url, b.base_url)
        self.assertEqual(status, 200)
        self.assertEqual(push["status"], "delivered")
        self.assertEqual(
            self._anchor_path("rot-e").read_bytes(),
            self._anchor_path("rot-d").read_bytes())
        carried = anchor_dict(self._anchor_path("rot-e"))
        self.assertEqual(carried["signer"], "bob")
        self.assertEqual(len(carried["rotations"]), 1)
        health_b = node_server.fetch_health(b.base_url)
        self.assertEqual(health_b["anchor_status"], "anchored")
        self.assertEqual(health_b["snapshot_entries"], 3)
        print("ROTATION PUSH: one paired push carried the "
              "rotated anchor to the peer byte-for-byte, "
              "rotations included")

        # The peer now refuses the old signer: alice's anchor
        # over the very record the peer stands on (an
        # anchor-alone delivery; the record half is the
        # standing chain) is refused as retired, not as a
        # different signer and not as a regression.
        staging = self.staging_dir / "rot-e-alice-again.json"
        pre = self.tmp / "rot-e-pre-push.json"
        # Rebuild alice's pre-rotation anchor over the grown
        # record from the initial write: write a fresh staged
        # anchor file as alice alone over the grown log in a
        # scratch anchor lineage (seq 0), which succession
        # judges against the peer's rotated anchor.
        write_anchor(
            self.registry_log, staging,
            signer="alice", secret_hex=self.alice_secret,
            witness="carol", witness_secret_hex=self.carol_secret)
        report = node_server.deliver_anchor(
            b.base_url, anchor_dict(staging),
            entries=entries_of(self.registry_log))
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"],
                         "anchor_signer_retired")
        print("ROTATION PUSH: the peer refuses the old "
              "signer's anchor as anchor_signer_retired")

    # -- the rewrite fixture --------------------------------------

    def _rewritten_log(self):
        """Experiment 012's negative control, as in experiment
        028's suite: mallory's registry from genesis, two
        entries, internally consistent."""
        evil_dir = self.tmp / "evil-central"
        evil_log = evil_dir / "registry.jsonl"
        channel = RegistrationChannel(evil_log, evil_dir / "audit.jsonl")
        secret = channel.enroll_user("mallory")
        record1, created = channel.register(
            author="mallory", name="member-access", version="1.0.0",
            code=MALLORY_CODE,
            signature=sign_library(secret, "mallory", "member-access",
                                   "1.0.0", MALLORY_CODE))
        assert created
        record2, created = channel.register(
            author="mallory", name="guest-access", version="1.0.0",
            code=MALLORY2_CODE,
            signature=sign_library(secret, "mallory", "guest-access",
                                   "1.0.0", MALLORY2_CODE))
        assert created
        return evil_log, record1, record2


if __name__ == "__main__":
    unittest.main(verbosity=2)
