"""
Automated tests for signed-anchor consultation in the
networked node server processes (experiment 019).

Each test maps to tests/test-cases/registration.md case 10
(signed anchors with a kept history, written before this
code), at the server surface experiment 018 wired: a server
started with --anchor also given a signing identity
(--anchor-signer plus --anchor-secret-file, a file in the
test's temp dir holding the enrollment secret issued at
runtime inside the test; no secret is committed) passes both
to the PolicyNode it builds, whose experiment 019 consultation
verifies the anchor's signature at startup. Run with either:

    python3 -m unittest discover -s packages/synchronization -v -p "test_signed_anchor_in_servers.py"
    python3 packages/synchronization/test_signed_anchor_in_servers.py

Expected: a genuine snapshot with a correctly signed anchor
starts and serves, health reporting anchored with the
signature verified; the same server over an anchor whose
signature was tampered with after signing REFUSES TO START,
its log naming AnchorSignatureInvalid / anchor_signature_invalid.
A server given --anchor but no signing identity behaves
exactly as experiment 018 built. Process cleanup is part of
the contract, as in experiments 007/014/018: every spawned
server is registered with addCleanup the moment it starts.
Standard library only. Localhost only.
"""

import base64
import json
import shutil
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
from registration import RegistrationChannel, sign_library  # noqa: E402


def _load_registry_anchor():
    """Registry anchor module by file path under a private
    name, as in test_anchor_in_servers.py (two packages ship
    an anchor.py, and a plain import can resolve wrongly)."""
    import importlib.util
    anchor_file = HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_signed_server_test_registry_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


write_anchor = _load_registry_anchor().write_anchor

SERVER = HERE / "node_server.py"
ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"


class ServerProcess:
    """One node_server.py child process, reliably cleaned up.
    Mirrors test_anchor_in_servers.py."""

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
        ] + args
        self.proc = subprocess.Popen(
            cmd, stdout=self._log_fh, stderr=subprocess.STDOUT,
        )
        test.addCleanup(self.stop)

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
                self.base_url = f"http://127.0.0.1:{port}"
                try:
                    node_server.fetch_health(self.base_url)
                    return self.base_url
                except (AssertionError, urllib.error.URLError, OSError):
                    pass
            time.sleep(0.05)
        raise AssertionError(f"server {self.name} not ready in {timeout}s")

    def wait_refused(self, timeout: float = 15.0) -> str:
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


class SignedAnchorServerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.tmp = tmp

        central = tmp / "central"
        self.registry_log = central / "registry.jsonl"
        channel = RegistrationChannel(self.registry_log, central / "audit.jsonl")
        self.alice_secret = channel.enroll_user("alice")
        self.record, created = channel.register(
            author="alice", name="member-access", version="1.0.0",
            code=ALICE_CODE,
            signature=sign_library(self.alice_secret, "alice",
                                   "member-access", "1.0.0", ALICE_CODE),
        )
        assert created
        self.payloads_file = tmp / "payloads.json"
        self.payloads_file.write_text(
            json.dumps({self.record.policy_hash: ALICE_CODE.hex()}),
            encoding="utf-8",
        )
        # The signing identity for the server: the signer's id,
        # and a temp file holding the secret enrollment issued
        # above. Both live only in this test's temp dir.
        self.secret_file = tmp / "outside" / "anchor-secret.txt"
        self.secret_file.parent.mkdir(parents=True, exist_ok=True)
        self.secret_file.write_text(self.alice_secret, encoding="utf-8")

        self.anchor_path = tmp / "outside" / "registry-anchor.json"
        write_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )

    def _snapshot_copy(self, name: str) -> Path:
        dest_dir = self.tmp / f"{name}-dir"
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / "snapshot.jsonl"
        shutil.copyfile(self.registry_log, dest)
        return dest

    def _server_args(self, name: str, *, with_identity: bool = True):
        args = [
            "--snapshot", str(self._snapshot_copy(name)),
            "--payloads", str(self.payloads_file),
            "--serve-policies",
            "--anchor", str(self.anchor_path),
        ]
        if with_identity:
            args += [
                "--anchor-signer", "alice",
                "--anchor-secret-file", str(self.secret_file),
            ]
        return args

    # Case 10, server surface: genuine snapshot + correctly
    # signed anchor + signing identity starts and serves,
    # health reporting the anchor as configured and anchored.
    def test_signed_anchor_server_starts_and_serves(self):
        server = ServerProcess(
            self, self.tmp, "signed-genuine", self._server_args("signed-genuine")
        )
        server.wait_ready()
        health = node_server.fetch_health(server.base_url)
        self.assertTrue(health["anchor_configured"])
        self.assertEqual(health["anchor_status"], "anchored")
        status, body = node_server.fetch_policy(
            server.base_url, self.record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)

    # Case 10, server surface: the anchor's signature tampered
    # with after signing refuses the start, named
    # anchor_signature_invalid - the file-level re-anchor
    # attack of experiments 012-018 no longer passes startup
    # when a signing identity is configured.
    def test_tampered_signature_server_refuses_to_start(self):
        data = json.loads(self.anchor_path.read_text(encoding="utf-8"))
        sig = data["signature"]
        data["signature"] = ("0" if sig[0] != "0" else "1") + sig[1:]
        self.anchor_path.write_text(
            json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        server = ServerProcess(
            self, self.tmp, "signed-tampered",
            self._server_args("signed-tampered"),
        )
        log = server.wait_refused()
        self.assertIn("AnchorSignatureInvalid", log)
        self.assertIn("anchor_signature_invalid", log)
        self.assertNotIn("AnchorMismatch", log)

    # Case 10, server backward compatibility: the same signed
    # anchor with NO signing identity on the server command
    # behaves exactly as experiment 018 built - starts, serves,
    # anchored by head alone.
    def test_signed_anchor_without_identity_behaves_as_018(self):
        server = ServerProcess(
            self, self.tmp, "signed-noidentity",
            self._server_args("signed-noidentity", with_identity=False),
        )
        server.wait_ready()
        health = node_server.fetch_health(server.base_url)
        self.assertTrue(health["anchor_configured"])
        self.assertEqual(health["anchor_status"], "anchored")
        status, body = node_server.fetch_policy(
            server.base_url, self.record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
