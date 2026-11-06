"""
Automated tests for a running server learning of a revocation
without a restart (tests/test-cases/sync-mesh.md, case 13),
built in experiment 024.

Since experiment 007, a node server process built its PolicyNode
once at startup and never reloaded, so a revocation reached a
network client only after a restart over an updated snapshot -
the shape cases 10-12 all used, and the limit experiments
018/021/022/023 each restated. Experiments 022/023 closed the
trigger half of that gap for in-process placed policies; the
server processes were explicitly left out. This suite asserts
the server-side close:

- A running server exposes POST /check-snapshot on its existing
  POST surface (client helper: node_server.check_snapshot). One
  check compares the server's own snapshot file's presented
  facts (count, chain head, file fingerprint - experiment 022's
  change fact, reused) with the facts its PolicyNode last
  loaded.
- No change: the check reports "no_change", reloads nothing
  (loaded_entries null), and serving is undisturbed.
- Clean change (updated record copied over the server's
  snapshot, grown head re-anchored): ONE check reloads the
  running PolicyNode in that same check; the revoked policy
  then returns case 12's clean 410 policy_revoked refusal over
  the wire, a non-revoked policy keeps serving 200 with its
  exact bytes, and /health reports the new standing (anchor
  status, loaded snapshot entry count, the last check's
  report). Proven over plaintext, and over mutual TLS with a
  signed anchor (experiments 014/019).
- Untrusted changes are refused by the reload under its own
  names - anchor_stale (growth delivered without the
  re-anchor), tampered_record (an entry edited in place),
  anchor_mismatch (the file replaced with experiment 012's
  rewritten-from-genesis chain) - the previous view keeps
  serving, /health carries the refusal's name in the last
  check's report, and every later check re-reports the same
  refusal until the cause is fixed, when the next check
  delivers (experiment 022's discipline, at the process
  surface).
- Restart behavior is unchanged: a server STARTED over the
  rewritten snapshot with an anchor still refuses to start as
  anchor_mismatch (also asserted unmodified by experiments
  018/019's suites in the same full run).
- A mesh-only server (no snapshot) reports "no_snapshot" and
  is otherwise undisturbed.

Process cleanup is part of the contract, as in experiments
007/014/017/018/019/020/021: every spawned server is
registered with addCleanup the moment it starts. Test
credentials are generated inside the tests at runtime into
the test's temporary directory; the anchor signer's secret is
an enrollment issued at runtime and held only in a temp file
for the server's --anchor-secret-file argument. No credential
or secret is committed.

Run with either:

    python3 -m unittest discover -s packages/synchronization -v -p "test_server_live_reload.py"
    python3 packages/synchronization/test_server_live_reload.py

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
    018's suite do: two packages ship an anchor.py, and in a
    shared discovery process `import anchor` may already have
    resolved to the synchronization one."""
    import importlib.util
    anchor_file = HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_live_reload_test_registry_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


write_anchor = _load_registry_anchor().write_anchor

SERVER = HERE / "node_server.py"
ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
BOB_CODE = b"policy: allow if caller.tier == 'guest'\n"
MALLORY_CODE = b"policy: allow if caller.tier == 'attacker'\n"


class ServerProcess:
    """One node_server.py child process, plaintext or mutual
    TLS, reliably cleaned up. Mirrors
    test_anchor_in_servers.py's helper (with wait_refused for
    the startup-gate test)."""

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


class LiveReloadSetup(unittest.TestCase):
    """Shared setup: alice's and bob's genuine registry through
    the real Gate 1 channel. Servers get only snapshot copies +
    payloads; each test class writes its own anchor."""

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
        bob_secret = self.channel.enroll_user("bob")
        self.alice_record, created = self.channel.register(
            author="alice", name="member-access", version="1.0.0",
            code=ALICE_CODE,
            signature=sign_library(self.alice_secret, "alice",
                                   "member-access", "1.0.0", ALICE_CODE))
        assert created
        self.bob_record, created = self.channel.register(
            author="bob", name="guest-access", version="1.0.0",
            code=BOB_CODE,
            signature=sign_library(bob_secret, "bob",
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

    def _deliver(self, snapshot: Path):
        """Delivery is an explicit copy of the updated record
        over the server's own snapshot file - the shape of every
        earlier experiment; no push is added by this case."""
        shutil.copyfile(self.registry_log, snapshot)

    def _rewritten_log(self) -> Path:
        """Experiment 012's negative control: mallory's registry,
        registered from genesis with different content.
        Internally consistent - it passes Gate 2's own snapshot
        verification - and NOT the anchored chain."""
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
        """Case 12's clean refusal, asserted after a live
        reload: 410 naming the revocation, no policy bytes, and
        the non-revoked policy still serving from the same
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


class PlaintextLiveReloadTests(LiveReloadSetup):

    def setUp(self):
        super().setUp()
        # Unsigned anchor over the genuine 2-entry chain
        # (experiment 012/018's head-only consultation).
        write_anchor(self.registry_log, self.anchor_path)

    # Case 13, main outcomes over plaintext: a no-change check
    # reloads nothing and serving is undisturbed; after the
    # revocation is delivered AND the grown head re-anchored,
    # ONE check reloads the running process - no restart - and
    # the wire behavior flips to case 12's refusal.
    def test_no_change_then_clean_delivery_reloads_plaintext(self):
        snapshot = self._snapshot_copy("live-a")
        server = ServerProcess(self, self.tmp, "live-a",
                               self._server_args(snapshot))
        server.wait_ready()
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["anchor_status"], "anchored")
        self.assertEqual(health["snapshot_entries"], 2)
        self.assertIsNone(health["last_snapshot_check"])
        status, body = node_server.fetch_policy(
            server.base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)

        report = node_server.check_snapshot(server.base_url)
        self.assertEqual(report["status"], "no_change")
        self.assertIsNone(report["loaded_entries"])
        self.assertEqual(report["before_count"], 2)
        self.assertEqual(report["after_count"], 2)
        self.assertEqual(report["before_head"], report["after_head"])
        # Undisturbed: the same policy serves the same bytes.
        status, body = node_server.fetch_policy(
            server.base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)

        self._revoke_alice()
        self._deliver(snapshot)
        write_anchor(snapshot, self.anchor_path)
        report = node_server.check_snapshot(server.base_url)
        self.assertEqual(report["status"], "reloaded")
        self.assertEqual(report["loaded_entries"], 3)
        self.assertEqual(report["before_count"], 2)
        self.assertEqual(report["after_count"], 3)
        self.assertNotEqual(report["before_head"], report["after_head"])
        self.assertEqual(report["anchor_status"], "anchored")
        print("LIVE RELOAD (plaintext): one check on the running "
              "process reloaded 2 -> 3 entries; the revoked "
              "policy now refuses over the wire, no restart")

        self._assert_revoked_over_wire(server.base_url)
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["anchor_status"], "anchored")
        self.assertEqual(health["snapshot_entries"], 3)
        self.assertEqual(health["last_snapshot_check"]["status"],
                         "reloaded")
        # Settled: a further check over the now-current snapshot
        # is quiet again.
        report = node_server.check_snapshot(server.base_url)
        self.assertEqual(report["status"], "no_change")

    # Case 13, negative control 1: honest growth delivered
    # WITHOUT the re-anchor is refused as anchor_stale by the
    # running server's reload; the previous view keeps serving
    # (the stale window, asserted, not hidden); the refusal is
    # re-reported on every later check and carried on /health;
    # after the re-anchor the next check delivers.
    def test_anchor_stale_refused_re_reported_then_delivered(self):
        snapshot = self._snapshot_copy("live-stale")
        server = ServerProcess(self, self.tmp, "live-stale",
                               self._server_args(snapshot))
        server.wait_ready()
        self._revoke_alice()
        self._deliver(snapshot)  # no re-anchor: lifecycle skipped

        for _ in range(2):
            report = node_server.check_snapshot(server.base_url)
            self.assertEqual(report["status"], "reload_refused")
            self.assertEqual(report["reason"], "anchor_stale")
            self.assertEqual(report["after_count"], 3)
            self.assertIsNone(report["loaded_entries"])
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["last_snapshot_check"]["reason"],
                         "anchor_stale")
        self.assertEqual(health["snapshot_entries"], 2)
        # Previous view still serves: the revocation is not yet
        # known to this running process.
        status, body = node_server.fetch_policy(
            server.base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)
        print("LIVE RELOAD anchor_stale: refused twice, previous "
              "view still serving; delivery lands after re-anchor")

        write_anchor(snapshot, self.anchor_path)
        report = node_server.check_snapshot(server.base_url)
        self.assertEqual(report["status"], "reloaded")
        self._assert_revoked_over_wire(server.base_url)

    # Case 13, negative control 2: an entry edited in place
    # (experiment 021's tamper - head and count unchanged, the
    # fingerprint is what sees it) is refused as
    # tampered_record; the previous view serves; once the
    # genuine updated record is delivered and anchored, the
    # next check delivers.
    def test_tampered_delivery_refused_then_genuine_lands(self):
        snapshot = self._snapshot_copy("live-tamper")
        server = ServerProcess(self, self.tmp, "live-tamper",
                               self._server_args(snapshot))
        server.wait_ready()
        lines = snapshot.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[0])
        entry["name"] = "member-access-tampered"  # entry_hash left standing
        lines[0] = json.dumps(entry)
        snapshot.write_text("\n".join(lines) + "\n", encoding="utf-8")

        report = node_server.check_snapshot(server.base_url)
        self.assertEqual(report["status"], "reload_refused")
        self.assertEqual(report["reason"], "tampered_record")
        report = node_server.check_snapshot(server.base_url)
        self.assertEqual(report["status"], "reload_refused")
        self.assertEqual(report["reason"], "tampered_record")
        status, body = node_server.fetch_policy(
            server.base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)
        print("LIVE RELOAD tampered_record: refused twice, "
              "previous view serving; genuine delivery lands")

        self._revoke_alice()
        self._deliver(snapshot)  # overwrites the tamper, genuine chain
        write_anchor(snapshot, self.anchor_path)
        report = node_server.check_snapshot(server.base_url)
        self.assertEqual(report["status"], "reloaded")
        self._assert_revoked_over_wire(server.base_url)

    # Case 13, negative control 3: the snapshot file replaced
    # with a full rewrite from genesis is refused as
    # anchor_mismatch; the previous view keeps serving; and
    # restoring the genuine file is, honestly, no change at
    # all - the running view never moved.
    def test_rewritten_delivery_refused_anchor_mismatch(self):
        snapshot = self._snapshot_copy("live-rewrite")
        server = ServerProcess(self, self.tmp, "live-rewrite",
                               self._server_args(snapshot))
        server.wait_ready()
        shutil.copyfile(self._rewritten_log(), snapshot)

        report = node_server.check_snapshot(server.base_url)
        self.assertEqual(report["status"], "reload_refused")
        self.assertEqual(report["reason"], "anchor_mismatch")
        self.assertEqual(report["after_count"], 1)
        status, body = node_server.fetch_policy(
            server.base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)
        print("LIVE RELOAD anchor_mismatch: rewritten file "
              "refused, previous view serving")

        shutil.copyfile(self.registry_log, snapshot)  # restore genuine
        report = node_server.check_snapshot(server.base_url)
        self.assertEqual(report["status"], "no_change")
        status, body = node_server.fetch_policy(
            server.base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 200)

    # Case 13, startup gate unchanged: the live check is an
    # addition to a RUNNING server; a server STARTED over the
    # rewritten snapshot with an anchor still refuses to start,
    # exactly as in cases 10/11.
    def test_restart_over_rewritten_snapshot_still_refused(self):
        rewritten_dir = self.tmp / "rewritten-start-dir"
        rewritten_dir.mkdir(exist_ok=True)
        rewritten = rewritten_dir / "snapshot.jsonl"
        shutil.copyfile(self._rewritten_log(), rewritten)
        refused = ServerProcess(self, self.tmp, "live-restart-refused",
                                self._server_args(rewritten))
        log = refused.wait_refused()
        self.assertIn("AnchorMismatch", log)
        self.assertIn("anchor_mismatch", log)

    # Case 13, no snapshot configured: the check reports
    # no_snapshot and the mesh behavior is undisturbed.
    def test_mesh_only_server_reports_no_snapshot(self):
        server = ServerProcess(self, self.tmp, "live-mesh-only", [])
        server.wait_ready()
        report = node_server.check_snapshot(server.base_url)
        self.assertEqual(report["status"], "no_snapshot")
        health = node_server.fetch_health(server.base_url)
        self.assertFalse(health["policy_configured"])
        self.assertIsNone(health["snapshot_entries"])
        node_server.mutate(server.base_url, "mode", "normal")
        state = node_server.fetch_state(server.base_url)
        self.assertEqual(state["state"]["mode"], "normal")


class SignedTlsLiveReloadTests(LiveReloadSetup):

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
            self.tmp / "creds", ["live-tls", "test-client"])
        client = self.creds.nodes["test-client"]
        node_server.set_client_tls(client.cert, client.key,
                                   self.creds.ca_cert)

    def _tls(self):
        cert = self.creds.nodes["live-tls"]
        return (cert.cert, cert.key, self.creds.ca_cert)

    # Case 13 over the protected link, with a signed anchor:
    # no-change check; revocation delivered without the
    # re-anchor refused as anchor_stale (the signature still
    # verifies - the anchor itself is untouched - so the
    # refusal is the head judgement, under its own name); the
    # same-signer re-anchor; ONE check reloads the running TLS
    # server; the revoked policy refuses 410 over HTTPS and
    # the non-revoked policy keeps serving.
    def test_signed_anchor_tls_live_reload(self):
        snapshot = self._snapshot_copy("live-tls")
        args = self._server_args(snapshot, extra=(
            "--anchor-signer", "alice",
            "--anchor-secret-file", str(self.secret_file),
        ))
        server = ServerProcess(self, self.tmp, "live-tls", args,
                               tls=self._tls())
        server.wait_ready()
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["anchor_status"], "anchored")
        self.assertEqual(health["snapshot_entries"], 2)
        status, body = node_server.fetch_policy(
            server.base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)

        report = node_server.check_snapshot(server.base_url)
        self.assertEqual(report["status"], "no_change")

        self._revoke_alice()
        self._deliver(snapshot)  # signed lifecycle not yet followed
        report = node_server.check_snapshot(server.base_url)
        self.assertEqual(report["status"], "reload_refused")
        self.assertEqual(report["reason"], "anchor_stale")
        status, body = node_server.fetch_policy(
            server.base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 200)

        write_anchor(snapshot, self.anchor_path,
                     signer="alice", secret_hex=self.alice_secret)
        report = node_server.check_snapshot(server.base_url)
        self.assertEqual(report["status"], "reloaded")
        self.assertEqual(report["loaded_entries"], 3)
        print("LIVE RELOAD (mutual TLS, signed anchor): stale "
              "refused, same-signer re-anchor, one check "
              "reloaded the running server; 410 over HTTPS")
        self._assert_revoked_over_wire(server.base_url)
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["snapshot_entries"], 3)
        self.assertEqual(health["last_snapshot_check"]["status"],
                         "reloaded")


if __name__ == "__main__":
    unittest.main(verbosity=2)
