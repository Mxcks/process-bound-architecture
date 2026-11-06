"""
Automated tests for a running server receiving the updated
registry record over the wire (tests/test-cases/sync-mesh.md,
case 14), built in experiment 025.

Case 13 (experiment 024) closed the reload half of server
delivery: a running server reloads when POST /check-snapshot
is driven. It stated its remaining limit in the same breath:
the updated record still reaches the server's snapshot file
by an explicit copy made outside the process - no push, no
broadcast, no network transport of the record exists. This
suite asserts the transport half:

- A running server accepts a registry snapshot delivery on
  its existing POST surface (POST /deliver-snapshot, client
  helper node_server.deliver_snapshot) carrying the snapshot
  ENTRIES themselves, never a file path.
- The delivery is judged exactly as strictly as a file
  delivery: the entries are verified as a chain and consulted
  against the server's configured anchor (and its signature,
  where a signer is configured) BEFORE anything changes.
  Only a delivery that stands is applied, through case 13's
  reload path: the server's snapshot file is rewritten from
  the verified delivery in Gate 1's log format and the
  running PolicyNode reloads from it, so file and view move
  together - a case 13 check right after a stood delivery
  reports no_change, and /health carries the last delivery's
  report beside the last check's.
- The clean flow, over plaintext and over mutual TLS with
  identity binding (experiments 014/017) and a signed anchor
  (experiment 019): the author revokes, the deliverer follows
  the anchor lifecycle, the grown entries are pushed to the
  RUNNING server, and the revoked policy returns case 12's
  clean 410 policy_revoked over the wire while a non-revoked
  policy keeps serving - no restart, no manual file copy.
- Refusals, each named, each leaving the serving view AND
  the snapshot file byte-for-byte unchanged, none wedging
  the surface: anchor_stale (growth pushed without the
  re-anchor), tampered_record (an entry edited in transit),
  anchor_mismatch (a chain rewritten from genesis - shorter
  than the standing chain, but NOT its prefix, so it is
  judged a rewrite, not a regression), anchor_signature_
  invalid (the grown head anchored by an enrolled identity
  the server is not configured to trust), and
  snapshot_regression (the name case 14 sets: the delivered
  entries are exactly an earlier prefix of the very chain
  the server already stands on). After every refusal a later
  clean delivery still lands.
- Existing behavior is unchanged: case 13's check, the mesh
  surface, and the startup gate (a server STARTED over a
  rewritten snapshot still refuses to start), the last also
  asserted unmodified by experiments 018/019/024's suites.

Process cleanup is part of the contract, as in experiments
007-024: every spawned server is registered with addCleanup
the moment it starts. Test credentials are generated inside
the tests at runtime into the test's temporary directory;
the anchor signers' secrets are enrollments issued at
runtime and never leave the test's channel calls. No
credential or secret is committed.

Run with either:

    python3 -m unittest discover -s packages/synchronization -v -p "test_snapshot_wire_transport.py"
    python3 packages/synchronization/test_snapshot_wire_transport.py

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
    024's suite do: two packages ship an anchor.py, and in a
    shared discovery process `import anchor` may already have
    resolved to the synchronization one."""
    import importlib.util
    anchor_file = HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_wire_transport_test_registry_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


write_anchor = _load_registry_anchor().write_anchor

SERVER = HERE / "node_server.py"
ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
BOB_CODE = b"policy: allow if caller.tier == 'guest'\n"
BOB2_CODE = b"policy: allow if caller.tier == 'guest-friend'\n"
MALLORY_CODE = b"policy: allow if caller.tier == 'attacker'\n"


class ServerProcess:
    """One node_server.py child process, plaintext or mutual
    TLS, reliably cleaned up. Mirrors
    test_server_live_reload.py's helper."""

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

    def wait_refused(self, timeout: float = 15.0) -> str:
        """Wait for a server that must refuse to start; returns its log."""
        rc = self.proc.wait(timeout=timeout)
        assert rc != 0, f"server {self.name} should have refused to start"
        self._log_fh.flush()
        return self.log_path.read_text(encoding="utf-8")

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


class WireTransportSetup(unittest.TestCase):
    """Shared setup: alice's and bob's genuine registry through
    the real Gate 1 channel. Servers get only snapshot copies +
    payloads; each test class writes its own anchor. Both
    users' enrollment secrets are kept: bob's second
    registration grows the chain in the regression test, and
    bob signs an anchor in the signature test."""

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
        # The anchor lives outside the registry's own directory,
        # as in experiment 012.
        self.anchor_path = tmp / "outside" / "registry-anchor.json"

    # -- helpers -----------------------------------------------------

    def _snapshot_copy(self, name: str) -> Path:
        dest_dir = self.tmp / f"{name}-dir"
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / "snapshot.jsonl"
        shutil.copyfile(self.registry_log, dest)
        return dest

    def _server_args(self, snapshot: Path, extra=()):
        return [
            "--snapshot", str(snapshot),
            "--payloads", str(self.payloads_file),
            "--serve-policies",
            "--anchor", str(self.anchor_path),
            *extra,
        ]

    def _revoke_alice(self):
        revocation, created = self.channel.revoke(
            author="alice",
            policy_hash=self.alice_record.policy_hash,
            signature=sign_revocation(self.alice_secret,
                                      self.alice_record.policy_hash))
        assert created

    def _rewritten_log(self) -> Path:
        """Experiment 012's negative control: mallory's registry,
        registered from genesis with different content.
        Internally consistent - it passes Gate 2's own snapshot
        verification - and NOT the anchored chain, and not a
        prefix of it either."""
        evil_dir = self.tmp / "evil-central"
        evil_log = evil_dir / "registry.jsonl"
        channel = RegistrationChannel(evil_log, evil_dir / "audit.jsonl")
        secret = channel.enroll_user("mallory")
        record, created = channel.register(
            author="mallory", name="member-access", version="1.0.0",
            code=MALLORY_CODE,
            signature=sign_library(secret, "mallory", "member-access",
                                   "1.0.0", MALLORY_CODE))
        assert created
        return evil_log

    def _assert_revoked_over_wire(self, base_url: str):
        """Case 12's clean refusal, asserted after a wire
        delivery: 410 naming the revocation, no policy bytes,
        and the non-revoked policy still serving from the same
        running process."""
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


class PlaintextWireTransportTests(WireTransportSetup):

    def setUp(self):
        super().setUp()
        # Unsigned anchor over the genuine 2-entry chain
        # (experiment 012/018's head-only consultation).
        write_anchor(self.registry_log, self.anchor_path)

    # Case 14, main outcomes over plaintext: a delivery of the
    # standing entries is no_change and rewrites nothing; the
    # grown record pushed WITHOUT the re-anchor is refused as
    # anchor_stale with file and view untouched; after the
    # lifecycle is followed, ONE push delivers - the server's
    # file is rewritten from the verified delivery, the
    # running process reloads (no restart, no file copy), a
    # case 13 check right after reports no_change, and the
    # wire flips to case 12's refusal.
    def test_no_change_stale_then_clean_wire_delivery(self):
        snapshot = self._snapshot_copy("wire-a")
        server = ServerProcess(self, self.tmp, "wire-a",
                               self._server_args(snapshot))
        server.wait_ready()
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["anchor_status"], "anchored")
        self.assertEqual(health["snapshot_entries"], 2)
        self.assertIsNone(health["last_snapshot_delivery"])
        self._assert_alice_serves(server.base_url)

        before_bytes = snapshot.read_bytes()
        report = node_server.deliver_snapshot(
            server.base_url, entries_of(self.registry_log))
        self.assertEqual(report["status"], "no_change")
        self.assertIsNone(report["loaded_entries"])
        self.assertEqual(report["before_count"], 2)
        self.assertEqual(report["after_count"], 2)
        self.assertEqual(snapshot.read_bytes(), before_bytes)

        self._revoke_alice()
        grown = entries_of(self.registry_log)
        report = node_server.deliver_snapshot(server.base_url, grown)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_stale")
        self.assertEqual(report["after_count"], 3)
        self.assertIsNone(report["loaded_entries"])
        self.assertEqual(snapshot.read_bytes(), before_bytes)
        self._assert_alice_serves(server.base_url)
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["snapshot_entries"], 2)
        self.assertEqual(health["last_snapshot_delivery"]["reason"],
                         "anchor_stale")

        write_anchor(self.registry_log, self.anchor_path)
        report = node_server.deliver_snapshot(server.base_url, grown)
        self.assertEqual(report["status"], "delivered")
        self.assertEqual(report["loaded_entries"], 3)
        self.assertEqual(report["before_count"], 2)
        self.assertEqual(report["after_count"], 3)
        self.assertNotEqual(report["before_head"], report["after_head"])
        self.assertEqual(report["anchor_status"], "anchored")
        print("WIRE TRANSPORT (plaintext): one push delivered "
              "2 -> 3 entries to the running process; its file "
              "was rewritten from the verified delivery, no "
              "restart, no manual copy")
        # File and view moved together: the server's file IS
        # the delivered record now, byte-for-byte the channel's
        # own log serialization.
        self.assertEqual(snapshot.read_bytes(),
                         self.registry_log.read_bytes())
        self._assert_revoked_over_wire(server.base_url)
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["snapshot_entries"], 3)
        self.assertEqual(health["last_snapshot_delivery"]["status"],
                         "delivered")
        # ...and case 13's check sees the same record: quiet.
        report = node_server.check_snapshot(server.base_url)
        self.assertEqual(report["status"], "no_change")

    # Case 14, negative control: an entry edited in transit
    # (experiment 021's tamper - body changed, entry_hash left
    # standing) is refused as tampered_record; file and view
    # are unchanged; the genuine delivery then lands.
    def test_tampered_entry_in_transit_refused_then_clean_lands(self):
        snapshot = self._snapshot_copy("wire-tamper")
        server = ServerProcess(self, self.tmp, "wire-tamper",
                               self._server_args(snapshot))
        server.wait_ready()
        self._revoke_alice()
        write_anchor(self.registry_log, self.anchor_path)
        tampered = entries_of(self.registry_log)
        tampered[0] = dict(tampered[0], name="member-access-tampered")
        before_bytes = snapshot.read_bytes()

        report = node_server.deliver_snapshot(server.base_url, tampered)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "tampered_record")
        self.assertEqual(snapshot.read_bytes(), before_bytes)
        self._assert_alice_serves(server.base_url)
        print("WIRE TRANSPORT tampered_record: an entry edited "
              "in transit refused, file and view unchanged")

        report = node_server.deliver_snapshot(
            server.base_url, entries_of(self.registry_log))
        self.assertEqual(report["status"], "delivered")
        self._assert_revoked_over_wire(server.base_url)

    # Case 14, negative control: a chain rewritten from
    # genesis is refused as anchor_mismatch - and NOT as
    # snapshot_regression, although it is shorter than the
    # standing chain: it is not that chain's prefix, so the
    # backward name cannot launder a rewrite.
    def test_rewritten_chain_refused_anchor_mismatch(self):
        snapshot = self._snapshot_copy("wire-rewrite")
        server = ServerProcess(self, self.tmp, "wire-rewrite",
                               self._server_args(snapshot))
        server.wait_ready()
        before_bytes = snapshot.read_bytes()

        report = node_server.deliver_snapshot(
            server.base_url, entries_of(self._rewritten_log()))
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_mismatch")
        self.assertEqual(report["after_count"], 1)
        self.assertEqual(snapshot.read_bytes(), before_bytes)
        self._assert_alice_serves(server.base_url)
        print("WIRE TRANSPORT anchor_mismatch: a rewritten "
              "chain refused under the rewrite's own name, not "
              "the backward name")

        self._revoke_alice()
        write_anchor(self.registry_log, self.anchor_path)
        report = node_server.deliver_snapshot(
            server.base_url, entries_of(self.registry_log))
        self.assertEqual(report["status"], "delivered")
        self._assert_revoked_over_wire(server.base_url)

    # Case 14, the backward delivery: once the server stands
    # on the 3-entry record, pushing the old 2-entry prefix of
    # that very chain is refused as snapshot_regression; the
    # standing record, the file, and the revocation all stay
    # exactly as they were; and a further clean growth still
    # lands afterward.
    def test_backward_delivery_refused_snapshot_regression(self):
        snapshot = self._snapshot_copy("wire-backward")
        server = ServerProcess(self, self.tmp, "wire-backward",
                               self._server_args(snapshot))
        server.wait_ready()
        old_entries = entries_of(self.registry_log)  # the 2-entry chain

        self._revoke_alice()
        write_anchor(self.registry_log, self.anchor_path)
        report = node_server.deliver_snapshot(
            server.base_url, entries_of(self.registry_log))
        self.assertEqual(report["status"], "delivered")
        self._assert_revoked_over_wire(server.base_url)
        standing_bytes = snapshot.read_bytes()

        report = node_server.deliver_snapshot(server.base_url, old_entries)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "snapshot_regression")
        self.assertEqual(report["before_count"], 3)
        self.assertEqual(report["after_count"], 2)
        self.assertEqual(snapshot.read_bytes(), standing_bytes)
        # The revocation stands: alice still refuses, bob still
        # serves, and /health names the refusal.
        self._assert_revoked_over_wire(server.base_url)
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["snapshot_entries"], 3)
        self.assertEqual(health["last_snapshot_delivery"]["reason"],
                         "snapshot_regression")
        print("WIRE TRANSPORT snapshot_regression: our own "
              "chain, only older, refused under the backward "
              "name; the standing record never moved")

        # A later clean delivery still lands: bob registers a
        # second policy, the head is re-anchored, the 4-entry
        # record is pushed.
        record2, created = self.channel.register(
            author="bob", name="guest-access-2", version="1.0.0",
            code=BOB2_CODE,
            signature=sign_library(self.bob_secret, "bob",
                                   "guest-access-2", "1.0.0", BOB2_CODE))
        assert created
        write_anchor(self.registry_log, self.anchor_path)
        report = node_server.deliver_snapshot(
            server.base_url, entries_of(self.registry_log))
        self.assertEqual(report["status"], "delivered")
        self.assertEqual(report["loaded_entries"], 4)
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["snapshot_entries"], 4)
        self._assert_revoked_over_wire(server.base_url)

    # Case 14, no snapshot configured: the delivery reports
    # no_snapshot and the mesh behavior is undisturbed.
    def test_mesh_only_server_reports_no_snapshot(self):
        server = ServerProcess(self, self.tmp, "wire-mesh-only", [])
        server.wait_ready()
        report = node_server.deliver_snapshot(
            server.base_url, entries_of(self.registry_log))
        self.assertEqual(report["status"], "no_snapshot")
        health = node_server.fetch_health(server.base_url)
        self.assertFalse(health["policy_configured"])
        self.assertIsNone(health["snapshot_entries"])
        self.assertEqual(health["last_snapshot_delivery"]["status"],
                         "no_snapshot")
        node_server.mutate(server.base_url, "mode", "normal")
        state = node_server.fetch_state(server.base_url)
        self.assertEqual(state["state"]["mode"], "normal")

    # Case 14, startup gate unchanged (a pre-existing property
    # of cases 10/11, re-asserted here as case 13 re-asserted
    # it): a server STARTED over the rewritten snapshot with
    # an anchor still refuses to start.
    def test_restart_over_rewritten_snapshot_still_refused(self):
        rewritten_dir = self.tmp / "rewritten-start-dir"
        rewritten_dir.mkdir(exist_ok=True)
        rewritten = rewritten_dir / "snapshot.jsonl"
        shutil.copyfile(self._rewritten_log(), rewritten)
        refused = ServerProcess(self, self.tmp, "wire-restart-refused",
                                self._server_args(rewritten))
        log = refused.wait_refused()
        self.assertIn("AnchorMismatch", log)
        self.assertIn("anchor_mismatch", log)


class SignedTlsWireTransportTests(WireTransportSetup):

    def setUp(self):
        super().setUp()
        self.addCleanup(node_server.clear_client_tls)
        # Signed anchor (experiment 019): alice signs over the
        # genuine chain; her enrollment secret lives only in a
        # temp file for the server's --anchor-secret-file.
        write_anchor(self.registry_log, self.anchor_path,
                     signer="alice", secret_hex=self.alice_secret)
        self.secret_file = self.tmp / "outside" / "anchor-secret.txt"
        self.secret_file.parent.mkdir(parents=True, exist_ok=True)
        self.secret_file.write_text(self.alice_secret, encoding="utf-8")
        self.creds = protected_link.generate_test_credentials(
            self.tmp / "creds", ["wire-tls", "test-client"])
        client = self.creds.nodes["test-client"]
        # The pushing peer claims the node id its certificate
        # was issued for; the receiving server runs with
        # identity binding on and checks the claim (case 11).
        node_server.set_client_tls(client.cert, client.key,
                                   self.creds.ca_cert,
                                   node_id="test-client")

    def _tls(self):
        cert = self.creds.nodes["wire-tls"]
        return (cert.cert, cert.key, self.creds.ca_cert)

    # Case 14 over the protected link, with identity binding
    # and a signed anchor: no-change push; the revocation
    # record pushed WITHOUT the re-anchor refused as
    # anchor_stale; the grown head anchored by a DIFFERENT
    # enrolled identity (bob) refused as
    # anchor_signature_invalid - the anchor the server
    # consults arrived signed by an identity it is not
    # configured to trust (built at a staging path because
    # experiment 019's same-identity rule, correctly, refuses
    # to let bob re-anchor over alice's anchor in place);
    # the same-signer anchor; ONE push delivers to the running
    # TLS server; the revoked policy refuses 410 over HTTPS.
    def test_signed_tls_wire_delivery_full_flow(self):
        snapshot = self._snapshot_copy("wire-tls")
        args = self._server_args(snapshot, extra=(
            "--anchor-signer", "alice",
            "--anchor-secret-file", str(self.secret_file),
            "--tls-bind-identity",
        ))
        server = ServerProcess(self, self.tmp, "wire-tls", args,
                               tls=self._tls())
        server.wait_ready()
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["anchor_status"], "anchored")
        self.assertEqual(health["snapshot_entries"], 2)
        self._assert_alice_serves(server.base_url)

        report = node_server.deliver_snapshot(
            server.base_url, entries_of(self.registry_log))
        self.assertEqual(report["status"], "no_change")

        self._revoke_alice()
        grown = entries_of(self.registry_log)
        before_bytes = snapshot.read_bytes()
        report = node_server.deliver_snapshot(server.base_url, grown)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_stale")
        self.assertEqual(snapshot.read_bytes(), before_bytes)
        self._assert_alice_serves(server.base_url)

        bob_anchor = self.tmp / "bob-outside" / "registry-anchor.json"
        write_anchor(self.registry_log, bob_anchor,
                     signer="bob", secret_hex=self.bob_secret)
        shutil.copyfile(bob_anchor, self.anchor_path)
        report = node_server.deliver_snapshot(server.base_url, grown)
        self.assertEqual(report["status"], "delivery_refused")
        self.assertEqual(report["reason"], "anchor_signature_invalid")
        self.assertEqual(snapshot.read_bytes(), before_bytes)
        self._assert_alice_serves(server.base_url)
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["last_snapshot_delivery"]["reason"],
                         "anchor_signature_invalid")
        print("WIRE TRANSPORT anchor_signature_invalid (mutual "
              "TLS, identity binding): the grown head anchored "
              "by an untrusted identity refused, file and view "
              "unchanged")

        alice_anchor = self.tmp / "alice-outside" / "registry-anchor.json"
        write_anchor(self.registry_log, alice_anchor,
                     signer="alice", secret_hex=self.alice_secret)
        shutil.copyfile(alice_anchor, self.anchor_path)
        report = node_server.deliver_snapshot(server.base_url, grown)
        self.assertEqual(report["status"], "delivered")
        self.assertEqual(report["loaded_entries"], 3)
        print("WIRE TRANSPORT (mutual TLS, signed anchor): "
              "same-signer anchor, one push delivered to the "
              "running server; 410 over HTTPS")
        self.assertEqual(snapshot.read_bytes(),
                         self.registry_log.read_bytes())
        self._assert_revoked_over_wire(server.base_url)
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["snapshot_entries"], 3)
        self.assertEqual(health["last_snapshot_delivery"]["status"],
                         "delivered")


if __name__ == "__main__":
    unittest.main(verbosity=2)
