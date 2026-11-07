"""
Automated tests for a running server receiving its registry
anchor over the wire (tests/test-cases/sync-mesh.md, case 17),
built in experiment 028.

Cases 13/14/16 move the registry RECORD to a running server;
every one of them states the same remaining limit: the anchor
the delivery is consulted against arrives out of band, placed
by hand, and completing the anchor lifecycle (registration
case 10's same-signer re-anchor) at a server is a manual act
no wire performs. This suite asserts the anchor's own wire
delivery:

- A running server accepts a registry anchor delivery on its
  existing POST surface (POST /deliver-anchor, client helper
  node_server.deliver_anchor) carrying the anchor object's own
  JSON, never a file path - alone ({"anchor": ...}) or paired
  with the record it blesses ({"anchor": ..., "entries": ...}).
- The anchor is judged BEFORE anything changes, in case 17's
  stated order: configuration (no_anchor / no_snapshot), the
  anchor must read as an anchor, byte-identical is no_change,
  succession judged anchor to anchor (registration case 10's
  same-identity rule over the wire: a different identity - or
  an unsigned anchor over a signed one - is refused as
  different_signer, audit-logged in case 10's event shape, the
  current anchor byte-for-byte unchanged), the backward fence
  (anchor_regression, fenced to an earlier state of the
  server's own anchor - a proper-prefix kept history - or of
  its chain - an anchor pinning an earlier prefix), signature
  and kept history under the server's configured identity
  where one is configured (anchor_signature_invalid, before
  any head judgement), and pairing: an anchor alone is
  accepted only for the record the server already stands on
  (anchor_unpaired for a head that pairs with nothing here),
  and a pair is judged together through case 14's discipline
  with the delivered anchor standing in for the server's own -
  neither half applied unless both stand.
- The finding case 17 states, asserted as the evidence shows
  it: a rewritten record paired with the server's GENUINE
  anchor is refused as anchor_mismatch (experiment 012's
  protection), the pair with a differently-signed anchor over
  the rewrite is refused as different_signer, and the pair
  with an anchor over the rewrite signed by the CURRENT
  signer's identity - its history honestly extended by case
  10's own write path, the deliverer legitimately holding
  that identity's enrollment secret in the prototype's
  symmetric model - is ACCEPTED: the server ends serving the
  rewritten record under the delivered anchor. What stops a
  rewrite from being blessed is the custody of the signer's
  secret, and nothing else; the wire adds no weakness, and
  the weakness is experiment 019's recorded symmetric limit.
- The full flow runs over plaintext and over mutual TLS with
  identity binding (experiments 014/017) and a signed anchor
  (experiment 019); servers on which the surface is never
  driven behave exactly as today, proven by the unmodified
  earlier suites in the same full run.

Process cleanup is part of the contract, as in experiments
007-027: every spawned server is registered with addCleanup
the moment it starts. Test credentials are generated inside
the tests at runtime into the test's temporary directory;
the anchor signers' secrets are enrollments issued at
runtime and never leave the test's channel calls and the
server's secret file. No credential or secret is committed.

Run with either:

    python3 -m unittest discover -s packages/synchronization -v -p "test_anchor_wire_delivery.py"
    python3 packages/synchronization/test_anchor_wire_delivery.py

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
import protected_link  # noqa: E402
from registration import (  # noqa: E402
    RegistrationChannel,
    sign_library,
    sign_revocation,
)


def _load_registry_anchor():
    """Load experiment 012's REGISTRY anchor module by file path
    under a private name, the way policy_node.py and experiment
    025's suite do: two packages ship an anchor.py, and in a
    shared discovery process `import anchor` may already have
    resolved to the synchronization one."""
    import importlib.util
    anchor_file = HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_anchor_wire_test_registry_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_anchor_mod = _load_registry_anchor()
write_anchor = _anchor_mod.write_anchor
read_anchor_audit = _anchor_mod.read_anchor_audit

SERVER = HERE / "node_server.py"
ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
BOB_CODE = b"policy: allow if caller.tier == 'guest'\n"
BOB2_CODE = b"policy: allow if caller.tier == 'guest-friend'\n"
MALLORY_CODE = b"policy: allow if caller.tier == 'attacker'\n"
MALLORY2_CODE = b"policy: allow if caller.tier == 'attacker-guest'\n"


class ServerProcess:
    """One node_server.py child process, plaintext or mutual
    TLS, reliably cleaned up. Mirrors
    test_snapshot_wire_transport.py's helper."""

    def __init__(self, test, workdir: Path, name: str, args: list[str],
                 tls=None):
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
        if tls is not None:
            cert, key, ca = tls
            cmd += ["--tls-cert", str(cert), "--tls-key", str(key),
                    "--tls-ca", str(ca)]
        cmd += args
        self.proc = subprocess.Popen(
            cmd, stdout=self._log_fh, stderr=subprocess.STDOUT)
        test.addCleanup(self.stop)
        self.base_url = None
        self.scheme = "https" if tls is not None else "http"

    def wait_ready(self, timeout: float = 20.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"server {self.name} exited early "
                    f"rc={self.proc.returncode}; log:\n{self.log_text()}")
            if self.port_file.exists():
                port = int(self.port_file.read_text(encoding="utf-8").strip())
                self.base_url = f"{self.scheme}://127.0.0.1:{port}"
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
    """The Gate 1 log entries of a snapshot file, as the JSON
    dicts a wire delivery carries."""
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def anchor_dict(path: Path) -> dict:
    """An anchor file's content as the JSON object a wire anchor
    delivery carries."""
    return json.loads(path.read_text(encoding="utf-8"))


class AnchorWireSetup(unittest.TestCase):
    """Shared setup: alice's and bob's genuine registry through
    the real Gate 1 channel. Servers get only snapshot copies +
    payloads + their own anchor file (outside the registry's
    directory, as in experiment 012); each test class writes
    the anchor it consults. Both users' enrollment secrets are
    kept: alice signs the server's anchor, bob signs the
    differently-signed anchors of the succession tests."""

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
        # The server's anchor lives outside the registry's own
        # directory, as in experiment 012.
        self.anchor_path = tmp / "outside" / "registry-anchor.json"
        # The deliverer's staging area, likewise outside.
        self.staging_dir = tmp / "staging"
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    # -- helpers -----------------------------------------------------

    def _snapshot_copy(self, name: str) -> Path:
        dest_dir = self.tmp / f"{name}-dir"
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / "snapshot.jsonl"
        shutil.copyfile(self.registry_log, dest)
        return dest

    def _server_args(self, snapshot: Path, extra=(), payloads=None):
        return [
            "--snapshot", str(snapshot),
            "--payloads", str(payloads or self.payloads_file),
            "--serve-policies",
            "--anchor", str(self.anchor_path),
            *extra,
        ]

    def _signer_args(self):
        return [
            "--anchor-signer", "alice",
            "--anchor-secret-file", str(self.secret_file),
        ]

    def _write_secret_file(self):
        self.secret_file = self.tmp / "outside" / "anchor-secret.txt"
        self.secret_file.parent.mkdir(parents=True, exist_ok=True)
        self.secret_file.write_text(self.alice_secret, encoding="utf-8")

    def _revoke_alice(self):
        revocation, created = self.channel.revoke(
            author="alice",
            policy_hash=self.alice_record.policy_hash,
            signature=sign_revocation(self.alice_secret,
                                      self.alice_record.policy_hash))
        assert created

    def _staged_anchor(self, name: str, log_path: Path,
                       signer: str, secret_hex: str,
                       extend_from: Path | None = None) -> Path:
        """The deliverer's own anchor over a registry log: a
        fresh signed anchor at a staging path, or - with
        extend_from - case 10's write path run over a copy of
        an existing anchor file, honestly extending its kept
        history by one entry over the log's current head."""
        staging = self.staging_dir / name
        if extend_from is not None:
            shutil.copyfile(extend_from, staging)
        write_anchor(log_path, staging, signer=signer,
                     secret_hex=secret_hex)
        return staging

    def _rewritten_log(self):
        """Experiment 012's negative control, at the genuine
        chain's length: mallory's registry, registered from
        genesis with different content (two entries).
        Internally consistent - it passes Gate 2's own snapshot
        verification - and NOT the anchored chain."""
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

    def _assert_revoked_over_wire(self, base_url: str):
        """Case 12's clean refusal: 410 naming the revocation,
        no policy bytes, and the non-revoked policy still
        serving from the same running process."""
        status, body = node_server.fetch_policy(
            base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 410)
        self.assertEqual(body["error"], "policy_revoked")
        self.assertIn("revoked", body["detail"])
        self.assertNotIn("code_base64", body)
        status, body = node_server.fetch_policy(
            base_url, self.bob_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), BOB_CODE)

    def _assert_alice_serves(self, base_url: str):
        """The stale window, asserted honestly: this running
        process does not yet know of the revocation and still
        serves alice's policy with its exact bytes."""
        status, body = node_server.fetch_policy(
            base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)


class SignedPlaintextAnchorTests(AnchorWireSetup):
    """The signer-configured server (case 10's protected
    configuration) on experiment 007's plaintext transport."""

    def setUp(self):
        super().setUp()
        # Signed anchor (experiment 019): alice signs over the
        # genuine 2-entry chain; her enrollment secret lives
        # only in a temp file for the server's
        # --anchor-secret-file.
        write_anchor(self.registry_log, self.anchor_path,
                     signer="alice", secret_hex=self.alice_secret)
        self._write_secret_file()

    def _start(self, name, snapshot, payloads=None):
        server = ServerProcess(
            self, self.tmp, name,
            self._server_args(snapshot, extra=self._signer_args(),
                              payloads=payloads))
        server.wait_ready()
        return server

    # Case 17, the anchor-alone pairings: a byte-identical
    # anchor delivery is no_change and writes nothing; the
    # deliverer re-anchors the standing head at the source (the
    # kept history grows by one entry over the SAME head), and
    # that anchor delivered alone is accepted - the server's
    # anchor file is byte-for-byte the delivered anchor, its
    # standing becomes anchored under it, and the standing
    # record serves exactly as before.
    def test_anchor_alone_no_change_then_refresh_delivered(self):
        snapshot = self._snapshot_copy("anchor-a")
        server = self._start("anchor-a", snapshot)
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["anchor_status"], "anchored")
        self.assertIsNone(health["last_anchor_delivery"])
        self._assert_alice_serves(server.base_url)

        before_bytes = self.anchor_path.read_bytes()
        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(self.anchor_path))
        self.assertEqual(report["status"], "no_change")
        self.assertEqual(report["before_seq"], 0)
        self.assertEqual(report["after_seq"], 0)
        self.assertEqual(self.anchor_path.read_bytes(), before_bytes)

        refreshed = self._staged_anchor(
            "refreshed.json", self.registry_log, "alice",
            self.alice_secret, extend_from=self.anchor_path)
        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(refreshed))
        self.assertEqual(report["status"], "delivered")
        self.assertFalse(report["paired"])
        self.assertEqual(report["before_seq"], 0)
        self.assertEqual(report["after_seq"], 1)
        self.assertEqual(report["signer"], "alice")
        self.assertEqual(report["anchor_status"], "anchored")
        self.assertIsNone(report["loaded_entries"])
        print("ANCHOR WIRE (plaintext): the refreshed anchor "
              "over the standing head delivered alone - history "
              "0 -> 1, the server's anchor file is the delivered "
              "anchor, the standing record undisturbed")
        self.assertEqual(self.anchor_path.read_bytes(),
                         refreshed.read_bytes())
        self._assert_alice_serves(server.base_url)
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["anchor_status"], "anchored")
        self.assertEqual(health["last_anchor_delivery"]["status"],
                         "delivered")

    # Case 17, pairing's refusal: the SAME identity's genuine
    # signed anchor over experiment 012's rewritten chain pairs
    # with nothing this server holds - identity and signature
    # both pass, and the delivery is refused as anchor_unpaired
    # with the anchor file byte-for-byte unchanged.
    def test_anchor_alone_unpaired_refused(self):
        snapshot = self._snapshot_copy("anchor-unpaired")
        server = self._start("anchor-unpaired", snapshot)
        evil_log, _rec1, _rec2 = self._rewritten_log()
        foreign = self._staged_anchor(
            "foreign.json", evil_log, "alice", self.alice_secret)
        before_bytes = self.anchor_path.read_bytes()

        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(foreign))
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_unpaired")
        self.assertEqual(self.anchor_path.read_bytes(), before_bytes)
        self._assert_alice_serves(server.base_url)
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["last_anchor_delivery"]["reason"],
                         "anchor_unpaired")
        print("ANCHOR WIRE anchor_unpaired: a genuine signed "
              "anchor over a foreign chain refused - it pairs "
              "with nothing this server holds")

    # Case 17, the pair judged together: the grown record with
    # the OLD anchor is refused as anchor_stale and NEITHER
    # file changes; the NEW anchor alone is refused as
    # anchor_unpaired (it pairs with the record the server does
    # not hold yet); the pair together delivers - both files
    # byte-for-byte the delivered pair, the revocation live
    # over the wire, no restart, no manual lifecycle step at
    # this server.
    def test_paired_delivery_judged_together(self):
        snapshot = self._snapshot_copy("anchor-pair")
        server = self._start("anchor-pair", snapshot)
        self._assert_alice_serves(server.base_url)
        old_anchor = anchor_dict(self.anchor_path)
        snap_before = snapshot.read_bytes()
        anchor_before = self.anchor_path.read_bytes()

        self._revoke_alice()
        grown = entries_of(self.registry_log)
        report = node_server.deliver_anchor(
            server.base_url, old_anchor, entries=grown)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_stale")
        self.assertEqual(snapshot.read_bytes(), snap_before)
        self.assertEqual(self.anchor_path.read_bytes(), anchor_before)
        self._assert_alice_serves(server.base_url)
        print("ANCHOR WIRE paired refusal: grown record + old "
              "anchor refused as anchor_stale, neither half "
              "applied")

        new_anchor_path = self._staged_anchor(
            "grown.json", self.registry_log, "alice",
            self.alice_secret, extend_from=self.anchor_path)
        new_anchor = anchor_dict(new_anchor_path)
        report = node_server.deliver_anchor(server.base_url, new_anchor)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_unpaired")
        self.assertEqual(self.anchor_path.read_bytes(), anchor_before)
        self._assert_alice_serves(server.base_url)
        print("ANCHOR WIRE anchor_unpaired: the new anchor "
              "alone pairs with the record the server does not "
              "hold yet, not the one it stands on")

        report = node_server.deliver_anchor(
            server.base_url, new_anchor, entries=grown)
        self.assertEqual(report["status"], "delivered")
        self.assertTrue(report["paired"])
        self.assertEqual(report["loaded_entries"], 3)
        self.assertEqual(report["before_count"], 2)
        self.assertEqual(report["after_count"], 3)
        self.assertEqual(report["anchor_status"], "anchored")
        print("ANCHOR WIRE (plaintext): record + anchor "
              "delivered together and judged together - 2 -> 3 "
              "entries under the delivered anchor, no restart, "
              "no manual lifecycle step at this server")
        self.assertEqual(snapshot.read_bytes(),
                         self.registry_log.read_bytes())
        self.assertEqual(self.anchor_path.read_bytes(),
                         new_anchor_path.read_bytes())
        self._assert_revoked_over_wire(server.base_url)
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["snapshot_entries"], 3)
        self.assertEqual(health["anchor_status"], "anchored")
        self.assertEqual(health["last_anchor_delivery"]["status"],
                         "delivered")
        # File and view moved together, as in case 14: a case
        # 13 check right after reports no_change.
        report = node_server.check_snapshot(server.base_url)
        self.assertEqual(report["status"], "no_change")

    # Case 17, signature before head: the genuine next anchor
    # with one history entry's recorded head edited in the
    # delivered object (entry hashes left standing, experiment
    # 021's tamper shape) is refused as
    # anchor_signature_invalid; neither file changes.
    def test_tampered_history_refused_signature_invalid(self):
        snapshot = self._snapshot_copy("anchor-tampered")
        server = self._start("anchor-tampered", snapshot)
        self._revoke_alice()
        genuine = self._staged_anchor(
            "grown-for-tamper.json", self.registry_log, "alice",
            self.alice_secret, extend_from=self.anchor_path)
        tampered = anchor_dict(genuine)
        tampered["history"][0] = dict(
            tampered["history"][0], chain_head="f" * 64)
        anchor_before = self.anchor_path.read_bytes()
        snap_before = snapshot.read_bytes()

        report = node_server.deliver_anchor(server.base_url, tampered)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_signature_invalid")
        self.assertEqual(self.anchor_path.read_bytes(), anchor_before)
        self.assertEqual(snapshot.read_bytes(), snap_before)
        self._assert_alice_serves(server.base_url)
        print("ANCHOR WIRE anchor_signature_invalid: a kept "
              "history edited in the delivered object refused "
              "before any head judgement")

    # Case 17, the backward fence in both of its shapes: (i) an
    # earlier version of the server's own anchor file - its
    # kept history a proper prefix of the current one's - is
    # refused as anchor_regression and audit-logged; (ii) an
    # anchor pinning an earlier prefix of the standing chain,
    # genuinely signed by the same identity over the truncated
    # record, is refused as anchor_regression the same way -
    # for where it points, not for how it is signed.
    def test_backward_anchor_refused_anchor_regression(self):
        record2, created = self.channel.register(
            author="bob", name="guest-access-2", version="1.0.0",
            code=BOB2_CODE,
            signature=sign_library(self.bob_secret, "bob",
                                   "guest-access-2", "1.0.0", BOB2_CODE))
        assert created
        # The server's anchor: alice over the 3-entry chain,
        # written twice more at the source (the class setUp
        # already wrote sequence 0 over the 2-entry chain, so
        # the first of these is sequence 1 over the new head -
        # saved as the earlier version - and the second is
        # sequence 2, the live file).
        write_anchor(self.registry_log, self.anchor_path,
                     signer="alice", secret_hex=self.alice_secret)
        earlier = self.tmp / "earlier-anchor.json"
        shutil.copyfile(self.anchor_path, earlier)
        write_anchor(self.registry_log, self.anchor_path,
                     signer="alice", secret_hex=self.alice_secret)
        snapshot = self._snapshot_copy("anchor-backward")
        server = self._start("anchor-backward", snapshot)
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["snapshot_entries"], 3)
        anchor_before = self.anchor_path.read_bytes()

        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(earlier))
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_regression")
        self.assertEqual(report["before_seq"], 2)
        self.assertEqual(report["after_seq"], 1)
        self.assertEqual(self.anchor_path.read_bytes(), anchor_before)
        events = read_anchor_audit(self.anchor_path)
        self.assertEqual(events[-1]["event"], "reanchor_rejected")
        self.assertEqual(events[-1]["reason"], "anchor_regression")
        print("ANCHOR WIRE anchor_regression (i): an earlier "
              "version of the server's own anchor file refused "
              "and audit-logged")

        trunc_dir = self.tmp / "truncated"
        trunc_dir.mkdir(exist_ok=True)
        trunc_log = trunc_dir / "registry.jsonl"
        lines = self.registry_log.read_text(
            encoding="utf-8").splitlines()[:2]
        trunc_log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        prefix_anchor = self._staged_anchor(
            "prefix.json", trunc_log, "alice", self.alice_secret)
        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(prefix_anchor))
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_regression")
        self.assertEqual(self.anchor_path.read_bytes(), anchor_before)
        events = read_anchor_audit(self.anchor_path)
        self.assertEqual(events[-1]["reason"], "anchor_regression")
        print("ANCHOR WIRE anchor_regression (ii): an anchor "
              "pinning an earlier prefix of the standing chain "
              "refused the same way - its signature verifies; "
              "it is refused for where it points")
        status, body = node_server.fetch_policy(
            server.base_url, self.bob_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), BOB_CODE)

    # Case 17's finding, in its three legs. (i) The rewritten
    # record paired with the server's GENUINE anchor is
    # refused as anchor_mismatch - experiment 012's protection.
    # (ii) The pair with an anchor over the rewrite signed by
    # bob is refused as different_signer at succession,
    # audit-logged. (iii) The pair with an anchor over the
    # rewrite signed by ALICE - the current signer, its
    # history honestly extended from the server's current
    # anchor by case 10's own write path, the deliverer
    # legitimately holding alice's enrollment secret as every
    # verifier in this symmetric model does - is ACCEPTED:
    # the server ends serving the rewritten record under the
    # delivered anchor. The test asserts the acceptance
    # exactly, because the finding is the result.
    def test_rewrite_anchor_finding(self):
        evil_log, mallory1, _mallory2 = self._rewritten_log()
        payloads = self.tmp / "payloads-with-mallory.json"
        payloads.write_text(json.dumps({
            self.alice_record.policy_hash: ALICE_CODE.hex(),
            self.bob_record.policy_hash: BOB_CODE.hex(),
            mallory1.policy_hash: MALLORY_CODE.hex(),
        }), encoding="utf-8")
        snapshot = self._snapshot_copy("anchor-finding")
        server = self._start("anchor-finding", snapshot,
                             payloads=payloads)
        evil_entries = entries_of(evil_log)
        snap_before = snapshot.read_bytes()
        anchor_before = self.anchor_path.read_bytes()

        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(self.anchor_path),
            entries=evil_entries)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_mismatch")
        self.assertEqual(snapshot.read_bytes(), snap_before)
        self.assertEqual(self.anchor_path.read_bytes(), anchor_before)
        self._assert_alice_serves(server.base_url)
        print("FINDING leg (i): rewritten record + the genuine "
              "anchor refused as anchor_mismatch - a rewrite "
              "cannot pass under an anchor that was not "
              "re-signed over it")

        bob_anchor = self._staged_anchor(
            "evil-bob.json", evil_log, "bob", self.bob_secret)
        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(bob_anchor),
            entries=evil_entries)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "different_signer")
        self.assertEqual(snapshot.read_bytes(), snap_before)
        self.assertEqual(self.anchor_path.read_bytes(), anchor_before)
        events = read_anchor_audit(self.anchor_path)
        self.assertEqual(events[-1]["reason"], "different_signer")
        self.assertEqual(events[-1]["attempted_signer"], "bob")
        self.assertEqual(events[-1]["current_signer"], "alice")
        self._assert_alice_serves(server.base_url)
        print("FINDING leg (ii): rewritten record + an anchor "
              "over it signed by a different identity refused "
              "as different_signer - identity custody is a fence")

        alice_anchor = self._staged_anchor(
            "evil-alice.json", evil_log, "alice", self.alice_secret,
            extend_from=self.anchor_path)
        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(alice_anchor),
            entries=evil_entries)
        self.assertEqual(report["status"], "delivered")
        self.assertEqual(report["loaded_entries"], 2)
        self.assertEqual(report["anchor_status"], "anchored")
        print("FINDING leg (iii): rewritten record + an anchor "
              "over it signed by the CURRENT signer's identity "
              "ACCEPTED - succession passes, the backward fence "
              "does not fire, the signature verifies, and the "
              "pair stands together under the same consultation "
              "that refuses legs (i) and (ii). What stops a "
              "rewrite from being blessed is the custody of "
              "the signer's secret, and nothing else")
        self.assertEqual(snapshot.read_bytes(),
                         evil_log.read_bytes())
        self.assertEqual(self.anchor_path.read_bytes(),
                         alice_anchor.read_bytes())
        status, body = node_server.fetch_policy(
            server.base_url, mallory1.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]),
                         MALLORY_CODE)
        status, body = node_server.fetch_policy(
            server.base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "policy_not_found")
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["anchor_status"], "anchored")
        self.assertEqual(health["snapshot_entries"], 2)


class HeadOnlySuccessionTests(AnchorWireSetup):
    """Succession over the wire on a server whose anchor
    consultation is head-only: no signing identity configured,
    the anchor it holds signed by alice (the configuration
    experiments 012-018 built and case 10 kept working)."""

    def setUp(self):
        super().setUp()
        write_anchor(self.registry_log, self.anchor_path,
                     signer="alice", secret_hex=self.alice_secret)

    # Case 17, succession judged anchor to anchor: bob's
    # genuine signed anchor over the very same chain, delivered
    # alone, is refused as different_signer; the anchor file is
    # byte-for-byte unchanged, and the audit log beside it
    # gains case 10's reanchor_rejected / different_signer
    # event naming bob and alice.
    def test_different_signer_refused_and_logged(self):
        snapshot = self._snapshot_copy("anchor-succession")
        server = ServerProcess(
            self, self.tmp, "anchor-succession",
            self._server_args(snapshot))
        server.wait_ready()
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["anchor_status"], "anchored")
        self.assertTrue(health["anchor_configured"])
        self._assert_alice_serves(server.base_url)

        bob_anchor = self._staged_anchor(
            "bob-same-chain.json", self.registry_log, "bob",
            self.bob_secret)
        anchor_before = self.anchor_path.read_bytes()
        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(bob_anchor))
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "different_signer")
        self.assertEqual(report["signer"], "bob")
        self.assertEqual(self.anchor_path.read_bytes(), anchor_before)
        events = read_anchor_audit(self.anchor_path)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "reanchor_rejected")
        self.assertEqual(events[0]["reason"], "different_signer")
        self.assertEqual(events[0]["attempted_signer"], "bob")
        self.assertEqual(events[0]["current_signer"], "alice")
        self._assert_alice_serves(server.base_url)
        print("ANCHOR WIRE different_signer: a differently-"
              "signed anchor over the same chain refused at "
              "succession and logged in case 10's audit shape; "
              "the current anchor is byte-for-byte unchanged")


class ConfigEdgeTests(AnchorWireSetup):

    def setUp(self):
        super().setUp()
        write_anchor(self.registry_log, self.anchor_path,
                     signer="alice", secret_hex=self.alice_secret)

    # Case 17, configuration first: a server started without
    # --anchor consults no registry anchor, and an anchor
    # delivery to it reports no_anchor; the server is
    # otherwise undisturbed.
    def test_server_without_anchor_reports_no_anchor(self):
        snapshot = self._snapshot_copy("anchor-none")
        server = ServerProcess(self, self.tmp, "anchor-none", [
            "--snapshot", str(snapshot),
            "--payloads", str(self.payloads_file),
            "--serve-policies",
        ])
        server.wait_ready()
        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(self.anchor_path))
        self.assertEqual(report["status"], "no_anchor")
        health = node_server.fetch_health(server.base_url)
        self.assertFalse(health["anchor_configured"])
        self.assertEqual(health["last_anchor_delivery"]["status"],
                         "no_anchor")
        self._assert_alice_serves(server.base_url)

    # Case 17, configuration first: a node constructed with an
    # anchor path but no snapshot has no record to pair an
    # anchor against and reports no_snapshot (in-process: the
    # server CLI requires --snapshot with --anchor, so this
    # state is a construction state, asserted where it can
    # exist).
    def test_node_with_anchor_but_no_snapshot_reports_no_snapshot(self):
        node = node_server.NetworkNode(
            node_id="anchor-edge", anchor_path=self.anchor_path)
        report = node.deliver_anchor(anchor_dict(self.anchor_path))
        self.assertEqual(report["status"], "no_snapshot")
        self.assertIsNone(report["loaded_entries"])


class SignedTlsAnchorTests(AnchorWireSetup):
    """Case 17 over the protected link: mutual TLS, identity
    binding, and a signed anchor with its signing identity
    configured (experiments 014/017/019)."""

    def setUp(self):
        super().setUp()
        self.addCleanup(node_server.clear_client_tls)
        write_anchor(self.registry_log, self.anchor_path,
                     signer="alice", secret_hex=self.alice_secret)
        self._write_secret_file()
        self.creds = protected_link.generate_test_credentials(
            self.tmp / "creds", ["anchor-tls", "test-client"])
        client = self.creds.nodes["test-client"]
        # The delivering peer claims the node id its
        # certificate was issued for; the receiving server runs
        # with identity binding on and checks the claim
        # (case 11).
        node_server.set_client_tls(client.cert, client.key,
                                   self.creds.ca_cert,
                                   node_id="test-client")

    def _tls(self):
        cert = self.creds.nodes["anchor-tls"]
        return (cert.cert, cert.key, self.creds.ca_cert)

    # Case 17 over HTTPS: the paired delivery (grown record +
    # its same-signer anchor) reports delivered, the revoked
    # policy refuses 410 over the protected link, and an
    # anchor-alone delivery of the anchor the server then
    # holds reports no_change over the same link.
    def test_paired_delivery_over_tls(self):
        snapshot = self._snapshot_copy("anchor-tls")
        args = self._server_args(snapshot, extra=(
            *self._signer_args(),
            "--tls-bind-identity",
        ))
        server = ServerProcess(self, self.tmp, "anchor-tls", args,
                               tls=self._tls())
        server.wait_ready()
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["anchor_status"], "anchored")
        self.assertEqual(health["snapshot_entries"], 2)
        self._assert_alice_serves(server.base_url)

        self._revoke_alice()
        new_anchor_path = self._staged_anchor(
            "grown-tls.json", self.registry_log, "alice",
            self.alice_secret, extend_from=self.anchor_path)
        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(new_anchor_path),
            entries=entries_of(self.registry_log))
        self.assertEqual(report["status"], "delivered")
        self.assertEqual(report["loaded_entries"], 3)
        print("ANCHOR WIRE (mutual TLS, identity binding, "
              "signed anchor): record + anchor delivered "
              "together over HTTPS; 410 over the protected link")
        self.assertEqual(snapshot.read_bytes(),
                         self.registry_log.read_bytes())
        self.assertEqual(self.anchor_path.read_bytes(),
                         new_anchor_path.read_bytes())
        self._assert_revoked_over_wire(server.base_url)
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["snapshot_entries"], 3)
        self.assertEqual(health["last_anchor_delivery"]["status"],
                         "delivered")

        report = node_server.deliver_anchor(
            server.base_url, anchor_dict(self.anchor_path))
        self.assertEqual(report["status"], "no_change")
        self.assertEqual(self.anchor_path.read_bytes(),
                         new_anchor_path.read_bytes())


if __name__ == "__main__":
    unittest.main(verbosity=2)
