"""
Automated tests for anchor consultation in the networked node
server processes (experiment 018).

Each test maps to tests/test-cases/registration.md case 9 (detailed
form in the style of policy-availability case 4; cross-reference
tests/test-cases/sync-mesh.md case 10), written before this code.
Run with either:

    python3 -m unittest discover -s packages/synchronization -v -p "test_anchor_in_servers.py"
    python3 packages/synchronization/test_anchor_in_servers.py

A server process started with --anchor (experiment 012's registry
anchor for its snapshot's chain, passed to the PolicyNode it
builds, which consults it exactly as experiment 015 built) refuses
to start over a snapshot that fails the anchor check - in
particular experiment 012's negative control: a registry rewritten
from genesis, internally consistent, which passes the snapshot's
own chain verification and which a server WITHOUT an anchor serves
exactly as before (asserted here plainly, because that acceptance
is the hole 018 closes only where an anchor is configured).
Honest growth is refused at startup as anchor_stale, under its
own name, until the new head is re-anchored. Both transports are
covered: experiment 007's plaintext mode and experiment 014's
mutual-TLS mode. Test credentials for the TLS case are generated
inside the test at runtime, as in experiment 014; no credential
material is committed.

Process cleanup is part of the contract, as in experiments
007/014: every spawned server is registered with addCleanup the
moment it starts. Standard library only. Localhost only.
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
from policy_node import verify_registry_snapshot  # noqa: E402
from registration import RegistrationChannel, sign_library  # noqa: E402


def _load_registry_anchor():
    """Load experiment 012's REGISTRY anchor module by file path
    under a private name, the way policy_node.py does: two
    packages ship an anchor.py (registration, synchronization),
    and in a shared discovery process `import anchor` may already
    have resolved to the synchronization one."""
    import importlib.util
    anchor_file = HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_server_test_registry_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


write_anchor = _load_registry_anchor().write_anchor

SERVER = HERE / "node_server.py"
ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
BOB_CODE = b"policy: billing export for members\n"
MALLORY_CODE = b"policy: allow if caller.tier == 'attacker'\n"


class ServerProcess:
    """One node_server.py child process, reliably cleaned up.
    Mirrors experiments 007/014: `tls` (a credential triple) turns
    the same command into a protected-link server."""

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
            cmd, stdout=self._log_fh, stderr=subprocess.STDOUT,
        )
        test.addCleanup(self.stop)
        self.base_url: str | None = None
        self.scheme = "https" if tls is not None else "http"

    def wait_ready(self, timeout: float = 20.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"server {self.name} exited early rc={self.proc.returncode}; "
                    f"log:\n{self.log_path.read_text(encoding='utf-8')}"
                )
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


class AnchorServerSetup(unittest.TestCase):
    """Shared setup: alice's genuine registry, its anchor held
    outside, and experiment 012's rewritten-from-genesis snapshot."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.tmp = tmp

        central = tmp / "central"
        self.registry_log = central / "registry.jsonl"
        channel = RegistrationChannel(self.registry_log, central / "audit.jsonl")
        secret = channel.enroll_user("alice")
        self.record, created = channel.register(
            author="alice", name="member-access", version="1.0.0",
            code=ALICE_CODE,
            signature=sign_library(secret, "alice", "member-access",
                                   "1.0.0", ALICE_CODE),
        )
        assert created
        self.payloads_file = tmp / "payloads.json"
        self.payloads_file.write_text(
            json.dumps({self.record.policy_hash: ALICE_CODE.hex()}),
            encoding="utf-8",
        )
        # Experiment 012: anchor the genuine head, outside the
        # registry's own directory.
        self.anchor_path = tmp / "outside" / "registry-anchor.json"
        write_anchor(self.registry_log, self.anchor_path)

    def _snapshot_copy(self, name: str, source: Path | None = None) -> Path:
        dest_dir = self.tmp / f"{name}-dir"
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / "snapshot.jsonl"
        shutil.copyfile(source or self.registry_log, dest)
        return dest

    def _rewritten_snapshot(self, name: str = "rewritten") -> tuple[Path, Path]:
        """Experiment 012's negative control as a snapshot file: a
        second channel's registry, mallory's policy registered from
        genesis with different content. Internally consistent - it
        passes Gate 2's own snapshot verification - and NOT the
        anchored chain."""
        evil_dir = self.tmp / "evil-central"
        evil_log = evil_dir / "registry.jsonl"
        channel = RegistrationChannel(evil_log, evil_dir / "audit.jsonl")
        secret = channel.enroll_user("mallory")
        record, created = channel.register(
            author="mallory", name="member-access", version="1.0.0",
            code=MALLORY_CODE,
            signature=sign_library(secret, "mallory", "member-access",
                                   "1.0.0", MALLORY_CODE),
        )
        assert created
        self.mallory_record = record
        snapshot = self._snapshot_copy(name, source=evil_log)
        # Stated in the test, not assumed: the rewrite verifies on
        # its own, so only the anchor can refuse it.
        entries = verify_registry_snapshot(snapshot)
        assert entries[0]["policy_hash"] == record.policy_hash
        payloads = self.tmp / f"{name}-payloads.json"
        payloads.write_text(
            json.dumps({record.policy_hash: MALLORY_CODE.hex()}),
            encoding="utf-8",
        )
        return snapshot, payloads


class PlaintextAnchorServerTests(AnchorServerSetup):

    # Case 9, main outcomes over plaintext (experiment 007 mode):
    # the rewritten snapshot is refused at startup, named
    # anchor_mismatch; the genuine snapshot with its anchor starts
    # and serves exactly as before, health reporting the anchor.
    def test_rewritten_refused_and_genuine_serves_plaintext(self):
        rewritten, payloads = self._rewritten_snapshot()
        refused = ServerProcess(self, self.tmp, "net-rewritten", [
            "--snapshot", str(rewritten),
            "--payloads", str(payloads),
            "--serve-policies",
            "--anchor", str(self.anchor_path),
        ])
        log = refused.wait_refused()
        self.assertIn("AnchorMismatch", log)
        self.assertIn("anchor_mismatch", log)

        genuine = ServerProcess(self, self.tmp, "net-genuine", [
            "--snapshot", str(self._snapshot_copy("net-genuine")),
            "--payloads", str(self.payloads_file),
            "--serve-policies",
            "--anchor", str(self.anchor_path),
        ])
        genuine.wait_ready()
        health = node_server.fetch_health(genuine.base_url)
        self.assertTrue(health["anchor_configured"])
        self.assertEqual(health["anchor_status"], "anchored")
        status, body = node_server.fetch_policy(
            genuine.base_url, self.record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)
        self.assertEqual(body["served_by"], "net-genuine")
        # The transport itself is unchanged: mesh state still moves.
        node_server.mutate(genuine.base_url, "mode", "normal")
        state = node_server.fetch_state(genuine.base_url)
        self.assertEqual(state["state"]["mode"], "normal")

    # Case 9, negative control: NO anchor configured - the same
    # rewritten snapshot starts and serves exactly as before this
    # case existed. Stated plainly: an anchorless server still
    # serves a fully rewritten chain; 018 closes that only where
    # an anchor is configured.
    def test_no_anchor_server_serves_rewritten_snapshot_pre_018(self):
        rewritten, payloads = self._rewritten_snapshot()
        server = ServerProcess(self, self.tmp, "net-anchorless", [
            "--snapshot", str(rewritten),
            "--payloads", str(payloads),
            "--serve-policies",
        ])
        server.wait_ready()
        health = node_server.fetch_health(server.base_url)
        self.assertFalse(health["anchor_configured"])
        self.assertEqual(health["anchor_status"], "no_anchor")
        status, body = node_server.fetch_policy(
            server.base_url, self.mallory_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), MALLORY_CODE)

    # Case 9, honest growth: one more honest registration after
    # anchoring makes the snapshot an extension of the anchored
    # chain. The anchored server refuses it at startup as
    # anchor_stale (not a mismatch, never a silent start); after
    # the grown head is re-anchored, the same snapshot starts and
    # serves.
    def test_honest_growth_refused_as_stale_until_reanchored(self):
        snapshot = self._snapshot_copy("grown")
        # Honest growth OF THE SNAPSHOT chain itself: a channel over
        # the snapshot file appends bob's registration, chained to
        # the anchored head, entries only appended.
        channel = RegistrationChannel(snapshot, self.tmp / "grow-audit.jsonl")
        secret = channel.enroll_user("bob")
        _bob, created = channel.register(
            author="bob", name="billing-export", version="1.0.0",
            code=BOB_CODE,
            signature=sign_library(secret, "bob", "billing-export",
                                   "1.0.0", BOB_CODE),
        )
        assert created

        refused = ServerProcess(self, self.tmp, "net-stale", [
            "--snapshot", str(snapshot),
            "--payloads", str(self.payloads_file),
            "--serve-policies",
            "--anchor", str(self.anchor_path),
        ])
        log = refused.wait_refused()
        self.assertIn("AnchorStale", log)
        self.assertIn("anchor_stale", log)
        self.assertNotIn("AnchorMismatch", log)

        # Re-anchor the grown head (experiment 012's lifecycle),
        # then the same server starts and serves alice's policy.
        write_anchor(snapshot, self.anchor_path)
        server = ServerProcess(self, self.tmp, "net-reanchored", [
            "--snapshot", str(snapshot),
            "--payloads", str(self.payloads_file),
            "--serve-policies",
            "--anchor", str(self.anchor_path),
        ])
        server.wait_ready()
        health = node_server.fetch_health(server.base_url)
        self.assertEqual(health["anchor_status"], "anchored")
        status, body = node_server.fetch_policy(
            server.base_url, self.record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)


class ProtectedAnchorServerTests(AnchorServerSetup):

    def setUp(self):
        super().setUp()
        self.addCleanup(node_server.clear_client_tls)
        self.creds = protected_link.generate_test_credentials(
            self.tmp / "creds", ["net-tls", "test-client"],
        )
        client = self.creds.nodes["test-client"]
        node_server.set_client_tls(client.cert, client.key, self.creds.ca_cert)

    def _tls(self):
        cert = self.creds.nodes["net-tls"]
        return (cert.cert, cert.key, self.creds.ca_cert)

    # Case 9, main outcomes over the protected link (experiment
    # 014 mode): the same anchor consultation at startup - the
    # genuine snapshot starts and serves over mutual TLS, the
    # rewritten snapshot refuses to start, named anchor_mismatch.
    def test_genuine_serves_and_rewritten_refused_over_tls(self):
        genuine = ServerProcess(self, self.tmp, "net-tls", [
            "--snapshot", str(self._snapshot_copy("net-tls")),
            "--payloads", str(self.payloads_file),
            "--serve-policies",
            "--anchor", str(self.anchor_path),
        ], tls=self._tls())
        genuine.wait_ready()
        health = node_server.fetch_health(genuine.base_url)
        self.assertTrue(health["anchor_configured"])
        self.assertEqual(health["anchor_status"], "anchored")
        status, body = node_server.fetch_policy(
            genuine.base_url, self.record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)

        rewritten, payloads = self._rewritten_snapshot("tls-rewritten")
        refused = ServerProcess(self, self.tmp, "net-tls-rewritten", [
            "--snapshot", str(rewritten),
            "--payloads", str(payloads),
            "--serve-policies",
            "--anchor", str(self.anchor_path),
        ], tls=self._tls())
        log = refused.wait_refused()
        self.assertIn("AnchorMismatch", log)
        self.assertIn("anchor_mismatch", log)


if __name__ == "__main__":
    unittest.main(verbosity=2)
