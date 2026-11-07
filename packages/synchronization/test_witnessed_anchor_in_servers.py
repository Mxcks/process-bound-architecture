"""
Automated tests for witnessed anchors at the networked
server processes (tests/test-cases/sync-mesh.md case 18 and
registration.md case 11, built in experiment 029).

Experiment 028's finding, recorded with evidence there: under
experiment 019's symmetric signing, a running server ACCEPTED
a rewritten record paired with an anchor over the rewrite
signed by the current signer - what stopped a rewrite from
being blessed was the custody of the signer's secret, and
nothing else. This suite re-runs that finding at a server
configured with a WITNESS (a second enrolled identity whose
countersignature the server's anchor consultations require,
startup flags --anchor-witness / --anchor-witness-secret-file
beside case 10's signer flags):

- Startup: a server started with a witness configured over a
  signer-alone anchor refuses to start, its log naming
  anchor_witness_invalid; over a countersigned anchor it
  starts and serves, health reporting the witness configured.
- The finding re-run, in its three new legs: the pair 028
  accepted is REFUSED as anchor_witness_invalid with no
  countersignature, refused again under the same name when
  countersigned by an identity who is not the configured
  witness, and ACCEPTED when countersigned by the configured
  witness - the fence is now two secrets' custody, stated
  as the result, not softened.
- Honest flows under a witness configuration: the paired
  delivery of case 17 (grown record + its countersigned
  anchor) lands and the revocation is live; succession over
  the wire is unchanged (a pair signed by a different signer
  and genuinely countersigned by the configured witness is
  still refused as different_signer, first in the order);
  the case 13 live reload is refused against a signer-alone
  re-anchor and lands against the countersigned one.
- The witness is configuration only: it arrives by startup
  flags (or the NetworkNode constructor), never inside a
  delivery - a delivered anchor countersigned by a new
  identity is refused, and the node's configured witness is
  the same identity afterward.
- The mesh side takes the same configuration independently
  (--mesh-anchor-witness / --mesh-anchor-witness-secret-file):
  health reports the mesh anchor's standing under the witness
  judgement.

Servers on which no witness is configured behave exactly as
experiments 019/028 left them, proven by those unmodified
suites in the same full run. Process cleanup is part of the
contract, as in experiments 007-028: every spawned server is
registered with addCleanup the moment it starts. Test
credentials are generated inside the tests at runtime; the
signers' and witness's secrets are enrollments issued at
runtime and never leave the test's channel calls and the
servers' secret files. No credential or secret is committed.

Run with either:

    python3 -m unittest discover -s packages/synchronization -v -p "test_witnessed_anchor_in_servers.py"
    python3 packages/synchronization/test_witnessed_anchor_in_servers.py

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
    sign_revocation,
)
from sync_mesh import SyncNode  # noqa: E402


def _load_registry_anchor():
    """Experiment 012's REGISTRY anchor module by file path
    under a private name, as in experiment 028's suite: two
    packages ship an anchor.py, and in a shared discovery
    process `import anchor` may already have resolved to the
    synchronization one."""
    import importlib.util
    anchor_file = HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_witnessed_test_registry_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_mesh_anchor():
    import importlib.util
    anchor_file = HERE / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_witnessed_test_mesh_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_anchor_mod = _load_registry_anchor()
write_anchor = _anchor_mod.write_anchor
read_anchor_audit = _anchor_mod.read_anchor_audit
_mesh_anchor_mod = _load_mesh_anchor()
write_mesh_anchor = _mesh_anchor_mod.write_mesh_anchor

SERVER = HERE / "node_server.py"
ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
BOB_CODE = b"policy: allow if caller.tier == 'guest'\n"
MALLORY_CODE = b"policy: allow if caller.tier == 'attacker'\n"
MALLORY2_CODE = b"policy: allow if caller.tier == 'attacker-guest'\n"


class ServerProcess:
    """One node_server.py child process, reliably cleaned up.
    Mirrors test_anchor_wire_delivery.py's helper."""

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


class WitnessedServerSetup(unittest.TestCase):
    """Shared setup: alice's and bob's genuine registry
    through the real Gate 1 channel; the server's anchor is
    signed by alice and countersigned by carol, the witness.
    Enrollment secrets for alice (signer), bob (a non-witness
    identity), and carol (the witness) are all kept."""

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
        self.anchor_path = tmp / "outside" / "registry-anchor.json"
        self.staging_dir = tmp / "staging"
        self.staging_dir.mkdir(parents=True, exist_ok=True)

        write_anchor(self.registry_log, self.anchor_path,
                     signer="alice", secret_hex=self.alice_secret,
                     witness="carol",
                     witness_secret_hex=self.carol_secret)
        outside = tmp / "outside"
        self.signer_secret_file = outside / "anchor-secret.txt"
        self.signer_secret_file.write_text(self.alice_secret,
                                           encoding="utf-8")
        self.witness_secret_file = outside / "anchor-witness-secret.txt"
        self.witness_secret_file.write_text(self.carol_secret,
                                            encoding="utf-8")

    # -- helpers -----------------------------------------------------

    def _snapshot_copy(self, name: str) -> Path:
        dest_dir = self.tmp / f"{name}-dir"
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / "snapshot.jsonl"
        shutil.copyfile(self.registry_log, dest)
        return dest

    def _server_args(self, snapshot: Path, payloads=None):
        return [
            "--snapshot", str(snapshot),
            "--payloads", str(payloads or self.payloads_file),
            "--serve-policies",
            "--anchor", str(self.anchor_path),
            "--anchor-signer", "alice",
            "--anchor-secret-file", str(self.signer_secret_file),
            "--anchor-witness", "carol",
            "--anchor-witness-secret-file",
            str(self.witness_secret_file),
        ]

    def _start(self, name, snapshot, payloads=None, args=None):
        server = ServerProcess(
            self, self.tmp, name,
            args if args is not None
            else self._server_args(snapshot, payloads=payloads))
        server.wait_ready()
        return server

    def _staged_anchor(self, name: str, log_path: Path,
                       signer: str, secret_hex: str,
                       extend_from: Path | None = None,
                       witness: str | None = None,
                       witness_secret_hex: str | None = None) -> Path:
        """The deliverer's own anchor over a registry log, in
        experiment 028's staging style, with experiment 029's
        optional countersignature."""
        staging = self.staging_dir / name
        if extend_from is not None:
            shutil.copyfile(extend_from, staging)
        kwargs = {}
        if witness is not None:
            kwargs = {"witness": witness,
                      "witness_secret_hex": witness_secret_hex}
        write_anchor(log_path, staging, signer=signer,
                     secret_hex=secret_hex, **kwargs)
        return staging

    def _revoke_alice(self):
        revocation, created = self.channel.revoke(
            author="alice",
            policy_hash=self.alice_record.policy_hash,
            signature=sign_revocation(self.alice_secret,
                                      self.alice_record.policy_hash))
        assert created

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

    def _payloads_with_mallory(self, mallory1) -> Path:
        payloads = self.tmp / "payloads-with-mallory.json"
        payloads.write_text(json.dumps({
            self.alice_record.policy_hash: ALICE_CODE.hex(),
            self.bob_record.policy_hash: BOB_CODE.hex(),
            mallory1.policy_hash: MALLORY_CODE.hex(),
        }), encoding="utf-8")
        return payloads

    def _assert_alice_serves(self, base_url: str):
        status, body = node_server.fetch_policy(
            base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)

    def _assert_revoked_over_wire(self, base_url: str):
        status, body = node_server.fetch_policy(
            base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 410)
        self.assertEqual(body["error"], "policy_revoked")
        status, body = node_server.fetch_policy(
            base_url, self.bob_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), BOB_CODE)


class WitnessedServerTests(WitnessedServerSetup):

    # Case 18, startup: the witness-configured server over a
    # countersigned anchor starts and serves, health
    # reporting the witness as configured; over a signer-alone
    # anchor the same configuration refuses to start, its log
    # naming anchor_witness_invalid - and the signer-only
    # configuration of experiments 019/028 starts over that
    # same anchor, the stated control.
    def test_startup_witness_required_and_countersigned_serves(self):
        snapshot = self._snapshot_copy("witness-start")
        server = self._start("witness-start", snapshot)
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["anchor_status"], "anchored")
        self.assertTrue(health["anchor_witness_configured"])
        self._assert_alice_serves(server.base_url)
        server.stop()

        # The server's anchor rewritten signer-alone (the
        # same signer's successor write, no witness).
        write_anchor(self.registry_log, self.anchor_path,
                     signer="alice", secret_hex=self.alice_secret)
        refused = ServerProcess(
            self, self.tmp, "witness-start-refused",
            self._server_args(self._snapshot_copy("witness-refused")))
        with self.assertRaises(AssertionError) as ctx:
            refused.wait_ready()
        self.assertIn("anchor_witness_invalid", str(ctx.exception))
        self.assertIn("AnchorWitnessInvalid", str(ctx.exception))
        print("WITNESSED SERVER startup: a signer-alone anchor "
              "refuses the start as anchor_witness_invalid "
              "when a witness is configured")

        control = ServerProcess(self, self.tmp, "witness-control", [
            "--snapshot", str(self._snapshot_copy("witness-control")),
            "--payloads", str(self.payloads_file),
            "--serve-policies",
            "--anchor", str(self.anchor_path),
            "--anchor-signer", "alice",
            "--anchor-secret-file", str(self.signer_secret_file),
        ])
        control.wait_ready()
        health = node_server.fetch_health(control.base_url)
        self.assertEqual(health["anchor_status"], "anchored")
        self.assertFalse(health["anchor_witness_configured"])
        self._assert_alice_serves(control.base_url)

    # Case 18's central assertion: experiment 028's finding
    # re-run at a witness-configured server. (i) The pair 028
    # accepted - rewritten record, anchor over it signed by
    # alice, history honestly extended - with NO
    # countersignature is refused as anchor_witness_invalid.
    # (ii) Countersigned by bob, who is not the configured
    # witness, it is refused under the same name. (iii)
    # Countersigned by carol, the configured witness, it is
    # ACCEPTED and the server serves the rewritten record.
    # The fence moved from one secret's custody to two; the
    # symmetric model did not change, and leg (iii) is
    # asserted exactly, as 028 asserted its acceptance.
    def test_finding_rerun_at_witness_configured_server(self):
        evil_log, mallory1, _mallory2 = self._rewritten_log()
        payloads = self._payloads_with_mallory(mallory1)
        snapshot = self._snapshot_copy("witness-finding")
        server = self._start("witness-finding", snapshot, payloads=payloads)
        evil_entries = entries_of(evil_log)
        snap_before = snapshot.read_bytes()
        anchor_before = self.anchor_path.read_bytes()

        no_witness = self._staged_anchor(
            "evil-alice-nowitness.json", evil_log, "alice",
            self.alice_secret, extend_from=self.anchor_path)
        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(no_witness),
            entries=evil_entries)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_witness_invalid")
        self.assertEqual(snapshot.read_bytes(), snap_before)
        self.assertEqual(self.anchor_path.read_bytes(), anchor_before)
        self._assert_alice_serves(server.base_url)
        print("FINDING RE-RUN leg (i): the pair experiment "
              "028 accepted, with no countersignature, is "
              "REFUSED as anchor_witness_invalid")

        bob_witness = self._staged_anchor(
            "evil-alice-bobwitness.json", evil_log, "alice",
            self.alice_secret, extend_from=self.anchor_path,
            witness="bob", witness_secret_hex=self.bob_secret)
        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(bob_witness),
            entries=evil_entries)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_witness_invalid")
        self.assertEqual(snapshot.read_bytes(), snap_before)
        self.assertEqual(self.anchor_path.read_bytes(), anchor_before)
        self._assert_alice_serves(server.base_url)
        print("FINDING RE-RUN leg (ii): the same pair "
              "countersigned by a non-witness identity is "
              "REFUSED under the same name")

        carol_witness = self._staged_anchor(
            "evil-alice-carolwitness.json", evil_log, "alice",
            self.alice_secret, extend_from=self.anchor_path,
            witness="carol", witness_secret_hex=self.carol_secret)
        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(carol_witness),
            entries=evil_entries)
        self.assertEqual(report["status"], "delivered")
        self.assertEqual(report["loaded_entries"], 2)
        self.assertEqual(report["anchor_status"], "anchored")
        print("FINDING RE-RUN leg (iii): the same pair "
              "countersigned by the configured witness is "
              "ACCEPTED - two secrets' custody is the fence "
              "now, and a party holding both still blesses a "
              "rewrite; the model is unchanged")
        self.assertEqual(snapshot.read_bytes(), evil_log.read_bytes())
        self.assertEqual(self.anchor_path.read_bytes(),
                         carol_witness.read_bytes())
        status, body = node_server.fetch_policy(
            server.base_url, mallory1.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]),
                         MALLORY_CODE)
        status, body = node_server.fetch_policy(
            server.base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 404)

    # Case 18, honest flows under a witness configuration:
    # case 17's paired delivery lands when the anchor
    # lifecycle is countersigned (the revocation live in the
    # same call), and succession over the wire is unchanged -
    # an anchor signed by bob and genuinely countersigned by
    # carol, the configured witness, is still refused as
    # different_signer, first in the judgement order, and
    # audit-logged.
    def test_honest_pair_lands_and_succession_still_first(self):
        snapshot = self._snapshot_copy("witness-honest")
        server = self._start("witness-honest", snapshot)
        self._revoke_alice()
        grown_anchor = self._staged_anchor(
            "grown-witnessed.json", self.registry_log, "alice",
            self.alice_secret, extend_from=self.anchor_path,
            witness="carol", witness_secret_hex=self.carol_secret)
        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(grown_anchor),
            entries=entries_of(self.registry_log))
        self.assertEqual(report["status"], "delivered")
        self.assertEqual(report["loaded_entries"], 3)
        self.assertEqual(report["anchor_status"], "anchored")
        self._assert_revoked_over_wire(server.base_url)
        report = node_server.check_snapshot(server.base_url)
        self.assertEqual(report["status"], "no_change")
        print("WITNESSED SERVER honest flow: grown record + "
              "countersigned anchor delivered together, the "
              "revocation live, no restart")

        bob_anchor = self._staged_anchor(
            "bob-witnessed.json", self.registry_log, "bob",
            self.bob_secret, witness="carol",
            witness_secret_hex=self.carol_secret)
        anchor_before = self.anchor_path.read_bytes()
        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(bob_anchor))
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "different_signer")
        self.assertEqual(self.anchor_path.read_bytes(), anchor_before)
        events = read_anchor_audit(self.anchor_path)
        self.assertEqual(events[-1]["reason"], "different_signer")
        self.assertEqual(events[-1]["attempted_signer"], "bob")
        self._assert_revoked_over_wire(server.base_url)
        print("WITNESSED SERVER succession: bob's anchor, "
              "genuinely countersigned by the configured "
              "witness, still refused as different_signer")

    # Case 18, the live reload (case 13) under a witness
    # configuration: the updated record copied over the
    # running server's snapshot with a signer-alone re-anchor
    # is refused as anchor_witness_invalid, the previous view
    # serving; with the countersigned anchor in place the
    # same check reloads and the revocation is live.
    def test_live_reload_refused_signer_alone_then_lands_witnessed(self):
        snapshot = self._snapshot_copy("witness-reload")
        server = self._start("witness-reload", snapshot)
        self._revoke_alice()
        shutil.copyfile(self.registry_log, snapshot)

        write_anchor(self.registry_log, self.anchor_path,
                     signer="alice", secret_hex=self.alice_secret)
        report = node_server.check_snapshot(server.base_url)
        self.assertEqual(report["status"], "reload_refused")
        self.assertEqual(report["reason"], "anchor_witness_invalid")
        self._assert_alice_serves(server.base_url)
        print("WITNESSED SERVER live reload: a signer-alone "
              "re-anchor refuses the reload as "
              "anchor_witness_invalid, the stale view serving")

        write_anchor(self.registry_log, self.anchor_path,
                     signer="alice", secret_hex=self.alice_secret,
                     witness="carol",
                     witness_secret_hex=self.carol_secret)
        report = node_server.check_snapshot(server.base_url)
        self.assertEqual(report["status"], "reloaded")
        self.assertEqual(report["loaded_entries"], 3)
        self._assert_revoked_over_wire(server.base_url)

    # Case 18, the witness is configuration only (in-process,
    # where the configuration lives): a NetworkNode built
    # with witness carol refuses a pair countersigned by a
    # NEW identity (dave, genuinely enrolled) as
    # anchor_witness_invalid, and its configured witness is
    # still carol afterward - no delivery installs or
    # replaces the witness - proven by the carol-countersigned
    # pair landing on the same node right after.
    def test_witness_is_configuration_only(self):
        dave_secret = self.channel.enroll_user("dave")
        node = node_server.NetworkNode(
            node_id="witness-config-node",
            registry_snapshot_path=self._snapshot_copy("witness-config"),
            payloads={
                self.alice_record.policy_hash: ALICE_CODE,
                self.bob_record.policy_hash: BOB_CODE,
            },
            serve_policies=True,
            anchor_path=self.anchor_path,
            anchor_signer="alice", anchor_secret_hex=self.alice_secret,
            anchor_witness="carol",
            anchor_witness_secret_hex=self.carol_secret,
        )
        self.assertEqual(node.policy_node.anchor_witness, "carol")

        self._revoke_alice()
        dave_anchor = self._staged_anchor(
            "grown-dave-witness.json", self.registry_log, "alice",
            self.alice_secret, extend_from=self.anchor_path,
            witness="dave", witness_secret_hex=dave_secret)
        report = node.deliver_anchor(
            anchor_dict(dave_anchor),
            entries=entries_of(self.registry_log))
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_witness_invalid")
        self.assertEqual(node.policy_node.anchor_witness, "carol")
        self.assertEqual(
            node.policy_node.anchor_status, "anchored")

        carol_anchor = self._staged_anchor(
            "grown-carol-witness.json", self.registry_log, "alice",
            self.alice_secret, extend_from=self.anchor_path,
            witness="carol", witness_secret_hex=self.carol_secret)
        report = node.deliver_anchor(
            anchor_dict(carol_anchor),
            entries=entries_of(self.registry_log))
        self.assertEqual(report["status"], "delivered")
        self.assertEqual(node.policy_node.anchor_witness, "carol")
        print("WITNESSED SERVER configuration: a delivery "
              "countersigned by a new identity is refused and "
              "installs nothing; the configured witness is "
              "unchanged and its pair still lands")


class MeshWitnessServerTests(WitnessedServerSetup):
    """Case 18, the mesh side's witness configuration,
    independent of the registry side's: a server started with
    a mesh anchor, its signer, and a witness reports the mesh
    anchor's standing under the witness judgement on /health.
    The mesh anchor here pins the empty mesh state a fresh
    server holds."""

    def _mesh_anchor_args(self, mesh_anchor_path: Path):
        return [
            "--mesh-anchor", str(mesh_anchor_path),
            "--mesh-anchor-signer", "alice",
            "--mesh-anchor-secret-file", str(self.signer_secret_file),
            "--mesh-anchor-witness", "carol",
            "--mesh-anchor-witness-secret-file",
            str(self.witness_secret_file),
        ]

    def test_mesh_witness_standing_on_health(self):
        mesh_anchor_path = self.tmp / "outside" / "mesh-anchor.json"
        write_mesh_anchor(
            SyncNode("mesh-witness"), mesh_anchor_path,
            signer="alice", secret_hex=self.alice_secret)
        server = ServerProcess(self, self.tmp, "mesh-witness", [
            *self._mesh_anchor_args(mesh_anchor_path),
        ])
        server.wait_ready()
        health = node_server.fetch_health(server.base_url)
        self.assertTrue(health["mesh_anchor_configured"])
        self.assertEqual(health["mesh_anchor_status"],
                         "anchor_witness_invalid")
        print("WITNESSED SERVER mesh side: a signer-alone "
              "mesh anchor reports anchor_witness_invalid on "
              "/health when a mesh witness is configured")
        server.stop()

        write_mesh_anchor(
            SyncNode("mesh-witness"), mesh_anchor_path,
            signer="alice", secret_hex=self.alice_secret,
            witness="carol", witness_secret_hex=self.carol_secret)
        server = ServerProcess(self, self.tmp, "mesh-witness-2", [
            *self._mesh_anchor_args(mesh_anchor_path),
        ])
        server.wait_ready()
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")


if __name__ == "__main__":
    unittest.main(verbosity=2)
