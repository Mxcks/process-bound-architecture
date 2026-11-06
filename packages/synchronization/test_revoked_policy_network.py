"""
Automated tests for a revoked policy requested over the network
transport (tests/test-cases/sync-mesh.md, case 12), built in
experiment 021 over finding F2 of experiment 020.

Experiment 020 asserted the finding exactly: a node server
restarted over a snapshot containing a revocation started
cleanly, but a policy request produced NO HTTP response at all
- node_server's _serve_policy caught NotServing,
PolicyNotFound, and TamperedRecord, and PolicyRevoked (a
sibling PolicyLibraryError, not a subclass of any of those)
propagated out of the handler and dropped the connection. The
policy bytes were not served, but no client could distinguish
"revoked" from "server died".

This suite asserts the fixed behavior, over both transports:

- Plaintext (experiment 007): the request returns a clean HTTP
  refusal in the endpoint's existing shape - status 410, a JSON
  body with error "policy_revoked" and a detail naming the
  revocation - the connection does not drop, no policy bytes
  are served, and the same process afterward still answers
  health and still serves a policy that was NOT revoked.
- Protected (experiment 014, mutual TLS): the same clean
  refusal over HTTPS.

Delivery of the revocation to the server is experiment 008's
shape: the updated record is copied over the server's own
snapshot copy and the process is restarted over it (servers
build their PolicyNode at startup; a running server gains no
reload path in this experiment, and none is claimed).

Process cleanup is part of the contract, as in experiments
007/014/017/018/019/020: every spawned server is registered
with addCleanup the moment it starts. Test credentials are
generated inside the tests at runtime into the test's
temporary directory; no credential is committed.

Run with either:

    python3 -m unittest discover -s packages/synchronization -v -p "test_revoked_policy_network.py"
    python3 packages/synchronization/test_revoked_policy_network.py

Standard library only (openssl subprocess for test credentials).
Localhost only; nothing leaves this machine.
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

SERVER = HERE / "node_server.py"
ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
BOB_CODE = b"policy: allow if caller.tier == 'guest'\n"


class ServerProcess:
    """One node_server.py child process, plaintext or mutual
    TLS, reliably cleaned up. Mirrors test_network_nodes.py /
    test_protected_nodes.py."""

    def __init__(self, test, workdir: Path, name: str, args: list[str],
                 creds=None):
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
        self.scheme = "http"
        if creds is not None:
            cert = creds.nodes[name]
            cmd += [
                "--tls-cert", str(cert.cert),
                "--tls-key", str(cert.key),
                "--tls-ca", str(creds.ca_cert),
            ]
            self.scheme = "https"
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


class RevokedPolicyNetworkTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(node_server.clear_client_tls)
        tmp = Path(self._tmp.name)
        self.tmp = tmp

        # Setup phase (in this test process only): Gate 1
        # registers alice's and bob's policies through the real
        # channel. Servers get only snapshot copies + payloads.
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

    # -- helpers -----------------------------------------------------

    def _snapshot_copy(self, name: str) -> Path:
        dest = self.tmp / f"{name}-snapshot.jsonl"
        shutil.copyfile(self.registry_log, dest)
        return dest

    def _server_args(self, snapshot: Path):
        return [
            "--snapshot", str(snapshot),
            "--payloads", str(self.payloads_file),
            "--serve-policies",
        ]

    def _revoke_alice_and_deliver(self, snapshot: Path):
        revocation, created = self.channel.revoke(
            author="alice",
            policy_hash=self.alice_record.policy_hash,
            signature=sign_revocation(self.alice_secret,
                                      self.alice_record.policy_hash))
        assert created
        shutil.copyfile(self.registry_log, snapshot)

    def _assert_clean_revoked_refusal(self, base_url: str):
        """The fixed F2 shape, asserted the same way on both
        transports: a clean HTTP refusal naming the revocation,
        no dropped connection, no policy bytes, and the server
        still serving afterward."""
        status, body = node_server.fetch_policy(
            base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 410)
        self.assertEqual(body["error"], "policy_revoked")
        self.assertIn("revoked", body["detail"])
        self.assertNotIn("code_base64", body)
        # The refusal did not wedge the server: health answers,
        # and a policy that was NOT revoked still serves from
        # the same process.
        health = node_server.fetch_health(base_url)
        self.assertTrue(health["ok"])
        status, body = node_server.fetch_policy(
            base_url, self.bob_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), BOB_CODE)
        print("FIXED F2 revoked-over-network: clean HTTP 410 "
              "refusal naming policy_revoked; server keeps "
              "serving other policies")

    # -- plaintext (experiment 007 transport) -------------------------

    def test_revoked_policy_clean_refusal_over_plaintext(self):
        snapshot = self._snapshot_copy("net-a")
        args = self._server_args(snapshot)
        server = ServerProcess(self, self.tmp, "net-a", args)
        server.wait_ready()
        status, body = node_server.fetch_policy(
            server.base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)
        server.stop()

        self._revoke_alice_and_deliver(snapshot)
        restarted = ServerProcess(self, self.tmp, "net-a", args)
        restarted.wait_ready()
        self._assert_clean_revoked_refusal(restarted.base_url)

    # -- protected link (experiment 014 transport) ---------------------

    def test_revoked_policy_clean_refusal_over_tls(self):
        creds = protected_link.generate_test_credentials(
            self.tmp / "creds", ["net-tls", "test-client"])
        client = creds.nodes["test-client"]
        node_server.set_client_tls(client.cert, client.key, creds.ca_cert)

        snapshot = self._snapshot_copy("net-tls")
        args = self._server_args(snapshot)
        server = ServerProcess(self, self.tmp, "net-tls", args, creds)
        server.wait_ready()
        status, body = node_server.fetch_policy(
            server.base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)
        server.stop()

        self._revoke_alice_and_deliver(snapshot)
        restarted = ServerProcess(self, self.tmp, "net-tls", args, creds)
        restarted.wait_ready()
        self._assert_clean_revoked_refusal(restarted.base_url)


if __name__ == "__main__":
    unittest.main(verbosity=2)
