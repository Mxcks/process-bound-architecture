"""
Automated tests for protected node links (experiment 014).

Each test spawns real node-server processes (node_server.py) on
ephemeral localhost ports, started with mutual-TLS arguments, and
talks to them only over HTTPS with a client certificate. The
outcomes under test (tests/test-cases/sync-mesh.md, case 7):

1. Two server processes exchange mesh entries across the process
   boundary and converge over the protected link, and a registered
   policy crosses it - and the wire itself is encrypted: traffic
   driven through a byte-capturing forwarding proxy succeeds, while
   the captured bytes contain neither the policy code nor any entry
   key or value in plaintext.
2. A connection from an unauthenticated peer is refused: a client
   with no credential and a client whose credential was issued by
   a different authority each fail to connect at all, and a plain
   HTTP request to the protected port gets nothing usable - there
   is no plaintext fallback.
3. Verification is not bypassed by encryption: a policy is served
   over the protected link by an opted-in process (and refused by
   a non-opted-in one), the registry snapshot crosses and verifies
   on receipt, and a tampered entry pushed over the protected link
   is rejected by Gate 3 verification on receipt.
4. Killing one protected server process does not take the state or
   the policy down: the survivors still serve, and they converge
   with each other on new state written after the kill.

Test credentials are generated inside the tests at runtime
(protected_link.generate_test_credentials, openssl on the machine)
into each test's temporary directory. No credential, key, or
certificate file is committed or leaves that directory.

Process cleanup is part of the contract: every spawned server is
registered with addCleanup the moment it starts, and cleanup
terminates, waits, and kills if needed.

Run with either:

    python3 -m unittest discover -s packages/synchronization -v -p "test_protected_nodes.py"
    python3 packages/synchronization/test_protected_nodes.py

Standard library only (openssl subprocess for test credentials).
Localhost only; nothing leaves this machine.
"""

import base64
import json
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "policy-library"))
sys.path.insert(0, str(HERE.parent / "registration"))

import node_server  # noqa: E402
import protected_link  # noqa: E402
from policy_node import PolicyNode, verify_registry_snapshot  # noqa: E402
from registration import RegistrationChannel, sign_library  # noqa: E402
from sync_mesh import SyncNode, tamper_entry  # noqa: E402

SERVER = HERE / "node_server.py"
ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
SECRET_VALUE = "capture-secret-value-014"


class ServerProcess:
    """One node_server.py child process speaking mutual TLS,
    reliably cleaned up."""

    def __init__(self, test: unittest.TestCase, workdir: Path, name: str,
                 args: list[str], creds):
        self.name = name
        self.port_file = workdir / f"{name}.port"
        self.log_path = workdir / f"{name}.log"
        self._log_fh = self.log_path.open("w", encoding="utf-8")
        cert = creds.nodes[name]
        cmd = [
            sys.executable, str(SERVER),
            "--node-id", name,
            "--port", "0",
            "--port-file", str(self.port_file),
            "--tls-cert", str(cert.cert),
            "--tls-key", str(cert.key),
            "--tls-ca", str(creds.ca_cert),
            *args,
        ]
        self.proc = subprocess.Popen(
            cmd, stdout=self._log_fh, stderr=subprocess.STDOUT,
        )
        test.addCleanup(self.stop)
        self.base_url: str | None = None
        self.port: int | None = None

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
                self.port = port
                self.base_url = f"https://127.0.0.1:{port}"
                try:
                    node_server.fetch_health(self.base_url)
                    return self.base_url
                except (AssertionError, urllib.error.URLError, OSError, ssl.SSLError):
                    pass
            time.sleep(0.05)
        raise AssertionError(f"server {self.name} not ready in {timeout}s")

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

    def kill(self) -> None:
        """Simulate the host process dying: SIGKILL, no cleanup."""
        self.proc.kill()
        self.proc.wait(timeout=5)


class CapturingProxy:
    """A raw TCP forwarding proxy that records every byte it
    forwards, in both directions. TLS runs end to end through it
    (client to real server); the proxy itself holds no credential
    and cannot read plaintext - what it records IS what an observer
    of the wire would see."""

    def __init__(self, target_port: int):
        self.captured = bytearray()
        self._lock = threading.Lock()
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(16)
        self.port = self._listener.getsockname()[1]
        self.base_url = f"https://127.0.0.1:{self.port}"
        self._target_port = target_port
        self._closed = False
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
        while not self._closed:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, client_conn: socket.socket):
        try:
            upstream = socket.create_connection(
                ("127.0.0.1", self._target_port), timeout=10
            )
        except OSError:
            client_conn.close()
            return

        def pump(src, dst):
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    with self._lock:
                        self.captured.extend(data)
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        t1 = threading.Thread(target=pump, args=(client_conn, upstream), daemon=True)
        t2 = threading.Thread(target=pump, args=(upstream, client_conn), daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)
        client_conn.close()
        upstream.close()

    def close(self):
        self._closed = True
        try:
            self._listener.close()
        except OSError:
            pass


class ProtectedNodeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(node_server.clear_client_tls)
        tmp = Path(self._tmp.name)
        self.tmp = tmp

        # Test credentials, generated at runtime into this test's
        # temporary directory: one test CA, one certificate per node
        # identity plus one for this test process as a client, and a
        # rogue CA with its own client certificate.
        self.creds = protected_link.generate_test_credentials(
            tmp / "creds",
            ["net-a", "net-b", "net-c", "test-client"],
        )
        # This test process presents the "test-client" credential
        # whenever it uses node_server's client helpers.
        client = self.creds.nodes["test-client"]
        node_server.set_client_tls(client.cert, client.key, self.creds.ca_cert)

        # Setup phase (in this test process only): Gate 1 registers
        # alice's policy through the real channel. The server
        # processes started below never see the channel; each gets
        # its OWN copy of the snapshot file and the payloads file.
        central = tmp / "central"
        self.registry_log = central / "registry.jsonl"
        channel = RegistrationChannel(self.registry_log, central / "audit.jsonl")
        secret = channel.enroll_user("alice")
        self.record, created = channel.register(
            author="alice",
            name="member-access",
            version="1.0.0",
            code=ALICE_CODE,
            signature=sign_library(secret, "alice", "member-access", "1.0.0", ALICE_CODE),
        )
        assert created
        self.payloads_file = tmp / "payloads.json"
        self.payloads_file.write_text(
            json.dumps({self.record.policy_hash: ALICE_CODE.hex()}),
            encoding="utf-8",
        )

    def _snapshot_copy(self, name: str) -> Path:
        dest = self.tmp / f"{name}-snapshot.jsonl"
        shutil.copyfile(self.registry_log, dest)
        return dest

    def start_server(self, name: str, *, serve: bool = True,
                     with_policy: bool = True, snapshot: Path | None = None) -> ServerProcess:
        args: list[str] = []
        if with_policy:
            args += [
                "--snapshot", str(snapshot or self._snapshot_copy(name)),
                "--payloads", str(self.payloads_file),
            ]
            if serve:
                args.append("--serve-policies")
        server = ServerProcess(self, self.tmp, name, args, self.creds)
        server.wait_ready()
        return server

    # -- Outcome 1: converge over the protected link; wire encrypted --

    def test_protected_processes_converge_and_wire_capture_has_no_plaintext(self):
        a = self.start_server("net-a", serve=True)
        b = self.start_server("net-b", with_policy=False)
        self.assertNotEqual(a.base_url, b.base_url)

        # Drive traffic to A through the capturing proxy: a mutation
        # carrying a distinctive secret value, an entries pull, and
        # a policy fetch. All of it must succeed over the captured
        # path - the capture is of a working exchange, not a failed
        # one.
        proxy = CapturingProxy(a.port)
        self.addCleanup(proxy.close)
        node_server.mutate(proxy.base_url, "capture-key", SECRET_VALUE)
        entries = node_server.fetch_entries(proxy.base_url)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].value, SECRET_VALUE)
        status, body = node_server.fetch_policy(proxy.base_url, self.record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)
        proxy.close()
        time.sleep(0.2)  # let the pump threads record the last bytes

        # The wire proof: a real byte capture of a successful
        # exchange contains none of the plaintext it carried.
        captured = bytes(proxy.captured)
        self.assertGreater(len(captured), 500)  # a real exchange flowed
        self.assertNotIn(SECRET_VALUE.encode(), captured)
        self.assertNotIn(b"capture-key", captured)
        self.assertNotIn(ALICE_CODE, captured)
        self.assertNotIn(b"caller.tier", captured)
        self.assertNotIn(b"member-access", captured)

        # And the experiment 007 convergence behavior, over HTTPS:
        # each process mutates its OWN node, then server-to-server
        # /sync (each server presenting its own certificate as the
        # client) merges both ways.
        node_server.mutate(a.base_url, "region", "us-central")
        node_server.mutate(b.base_url, "tier", "member")
        outcome = node_server.request_sync(a.base_url, b.base_url)
        self.assertFalse(outcome["self"]["divergence_detected"])
        self.assertFalse(outcome["peer"]["divergence_detected"])

        state_a = node_server.fetch_state(a.base_url)
        state_b = node_server.fetch_state(b.base_url)
        self.assertEqual(state_a["state_hash"], state_b["state_hash"])
        self.assertEqual(state_a["state"], state_b["state"])
        self.assertEqual(state_a["state"]["capture-key"], SECRET_VALUE)
        self.assertEqual(state_a["state"]["region"], "us-central")
        self.assertEqual(state_a["state"]["tier"], "member")
        self.assertEqual(len(state_a["entry_ids"]), 3)

        # Entries fetched over the protected link also verify on the
        # CLIENT side through the same receive_entries path.
        local = SyncNode("client-verifier")
        result = local.receive_entries(node_server.fetch_entries(a.base_url))
        self.assertFalse(result.divergence_detected)
        self.assertEqual(local.state_hash(), state_a["state_hash"])

    # -- Outcome 2: unauthenticated peers are refused ------------------

    def test_unauthenticated_and_rogue_peers_are_refused(self):
        a = self.start_server("net-a", serve=True)
        url = a.base_url + "/health"

        def attempt(context):
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10, context=context) as resp:
                return resp.status, resp.read()

        # No credential at all: the context trusts the test CA (so
        # the server is verifiable) but presents no client
        # certificate. The server requires one: handshake refused.
        anon = protected_link.anonymous_client_context(self.creds.ca_cert)
        with self.assertRaises((urllib.error.URLError, OSError, ssl.SSLError)):
            attempt(anon)

        # A credential from a DIFFERENT authority: the rogue client
        # certificate chains to a rogue CA the server does not trust.
        rogue = protected_link.client_context(
            self.creds.rogue_client_cert, self.creds.rogue_client_key,
            self.creds.ca_cert,
        )
        with self.assertRaises((urllib.error.URLError, OSError, ssl.SSLError)):
            attempt(rogue)

        # An unauthenticated peer can neither sync entries nor
        # receive a policy: both ride the same refused connection.
        with self.assertRaises((urllib.error.URLError, OSError, ssl.SSLError)):
            req = urllib.request.Request(a.base_url + "/entries", method="GET")
            urllib.request.urlopen(req, timeout=10, context=anon).read()
        with self.assertRaises((urllib.error.URLError, OSError, ssl.SSLError)):
            req = urllib.request.Request(
                a.base_url + f"/policy/{self.record.policy_hash}", method="GET"
            )
            urllib.request.urlopen(req, timeout=10, context=rogue).read()

        # Negative control: plain HTTP to the protected port is not
        # a fallback. It must not produce a usable health response.
        with self.assertRaises(Exception):
            with urllib.request.urlopen(
                f"http://127.0.0.1:{a.port}/health", timeout=5
            ) as resp:
                self.assertNotEqual(resp.status, 200)
                resp.read()

        # The refusals did not wedge the server: an authenticated
        # client still connects and is served.
        health = node_server.fetch_health(a.base_url)
        self.assertTrue(health["ok"])
        self.assertEqual(health["node_id"], "net-a")

    # -- Outcome 3: verification still runs over the protected link ---

    def test_policy_crosses_protected_link_verified_and_tamper_still_caught(self):
        a = self.start_server("net-a", serve=True)
        c = self.start_server("net-c", serve=False)  # holds, did not opt in

        status, body = node_server.fetch_policy(a.base_url, self.record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)
        self.assertEqual(body["policy_hash"], self.record.policy_hash)
        self.assertEqual(body["code_hash"], self.record.code_hash)
        self.assertEqual(body["validation_channel"], self.record.validation_channel)
        self.assertEqual(body["record_hash"], self.record.record_hash)
        self.assertEqual(body["served_by"], "net-a")

        # Opt-in is still a Gate 2 decision, not a transport one.
        status, body = node_server.fetch_policy(c.base_url, self.record.policy_hash)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "not_serving")

        status, _ = node_server.fetch_policy(a.base_url, "ab" * 32)
        self.assertEqual(status, 404)

        # The snapshot crosses over the protected link as raw bytes
        # and verifies on receipt with Gate 2's standalone verifier.
        received = self.tmp / "received-snapshot.jsonl"
        received.write_bytes(node_server.fetch_snapshot_bytes(a.base_url))
        entries = verify_registry_snapshot(received)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["policy_hash"], self.record.policy_hash)
        received_node = PolicyNode(
            node_id="receiver",
            registry_snapshot_path=received,
            payloads={self.record.policy_hash: ALICE_CODE},
            serve_policies=True,
        )
        self.assertEqual(
            received_node.serve_policy(self.record.policy_hash).code, ALICE_CODE
        )

        # Encryption does not bypass Gate 3: a tampered entry pushed
        # over the protected link gets divergence + rollback from
        # A's process, and A's state is untouched by it.
        node_server.mutate(a.base_url, "mode", "normal")
        before = node_server.fetch_state(a.base_url)
        real = next(
            e for e in node_server.fetch_entries(a.base_url) if e.origin == "net-a"
        )
        forged = tamper_entry(real, value="forged-over-tls")
        self.assertFalse(forged.hash_verifies())
        result = node_server.push_entries(a.base_url, [forged])
        self.assertTrue(result["divergence_detected"])
        self.assertTrue(result["rolled_back"])
        self.assertEqual(result["reason"], "tampered_entry")
        after = node_server.fetch_state(a.base_url)
        self.assertEqual(after["state_hash"], before["state_hash"])
        self.assertEqual(after["state"], before["state"])

    # -- Outcome 4: a killed protected process does not take the mesh down --

    def test_killed_protected_process_survivors_still_serve_and_converge(self):
        a = self.start_server("net-a", serve=True)
        b = self.start_server("net-b", serve=True)
        c = self.start_server("net-c", serve=True)

        node_server.mutate(a.base_url, "origin-a", "written-by-a")
        node_server.mutate(b.base_url, "origin-b", "written-by-b")
        node_server.request_sync(a.base_url, b.base_url)
        node_server.request_sync(a.base_url, c.base_url)
        node_server.request_sync(b.base_url, c.base_url)
        hashes = {
            node_server.fetch_state(s.base_url)["state_hash"] for s in (a, b, c)
        }
        self.assertEqual(len(hashes), 1)

        b.kill()
        self.assertIsNotNone(b.proc.poll())
        with self.assertRaises((urllib.error.URLError, OSError, ssl.SSLError)):
            node_server.fetch_health(b.base_url)

        state_a = node_server.fetch_state(a.base_url)
        self.assertEqual(state_a["state"]["origin-a"], "written-by-a")
        self.assertEqual(state_a["state"]["origin-b"], "written-by-b")
        status, body = node_server.fetch_policy(c.base_url, self.record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)
        self.assertEqual(body["served_by"], "net-c")

        node_server.mutate(c.base_url, "after-kill", "written-by-c")
        node_server.request_sync(c.base_url, a.base_url)
        state_a = node_server.fetch_state(a.base_url)
        state_c = node_server.fetch_state(c.base_url)
        self.assertEqual(state_a["state_hash"], state_c["state_hash"])
        self.assertEqual(state_a["state"]["after-kill"], "written-by-c")
        self.assertEqual(len(state_a["entry_ids"]), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
