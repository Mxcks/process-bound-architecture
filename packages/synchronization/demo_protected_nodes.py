"""
Runnable walkthrough of the protected node link prototype
(experiment 014).

Starts two real node-server processes on ephemeral localhost ports
speaking mutual TLS, drives them only over HTTPS with a client
certificate: each mutates its own state, they sync server-to-server
and converge, a registered policy is served by the second process,
traffic through a byte-capturing proxy carries no plaintext, an
unauthenticated client is refused, then the second process is
killed and the first still serves both the state and the policy.
Prints four outcomes as True/False at the end. All server processes
are cleaned up, and all credentials are generated at runtime into a
temporary directory that is deleted at the end - no key or
certificate file is kept.

    python3 packages/synchronization/demo_protected_nodes.py

Standard library only (openssl subprocess for test credentials).
Localhost only; nothing leaves this machine.
"""

import base64
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "registration"))

import node_server  # noqa: E402
import protected_link  # noqa: E402
from registration import RegistrationChannel, sign_library  # noqa: E402

CODE = b"policy: allow if caller.tier == 'member'\n"
SECRET_VALUE = "demo-secret-value-014"


class DemoServer:
    def __init__(self, workdir: Path, name: str, args: list[str], creds):
        self.name = name
        self.port_file = workdir / f"{name}.port"
        cert = creds.nodes[name]
        cmd = [
            sys.executable, str(HERE / "node_server.py"),
            "--node-id", name, "--port", "0",
            "--port-file", str(self.port_file),
            "--tls-cert", str(cert.cert),
            "--tls-key", str(cert.key),
            "--tls-ca", str(creds.ca_cert),
            *args,
        ]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.port_file.exists():
                port = int(self.port_file.read_text(encoding="utf-8").strip())
                self.port = port
                self.base_url = f"https://127.0.0.1:{port}"
                try:
                    node_server.fetch_health(self.base_url)
                    return
                except (AssertionError, urllib.error.URLError, OSError):
                    pass
            time.sleep(0.05)
        raise RuntimeError(f"{name} did not start")

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)


class CapturingProxy:
    """Raw TCP forwarding proxy that records every forwarded byte;
    see test_protected_nodes.py for the full version."""

    def __init__(self, target_port: int):
        self.captured = bytearray()
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(16)
        self.port = self._listener.getsockname()[1]
        self.base_url = f"https://127.0.0.1:{self.port}"
        self._target_port = target_port
        self._closed = False
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while not self._closed:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, client_conn):
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


def main() -> int:
    outcomes = {}
    servers: list[DemoServer] = []
    with tempfile.TemporaryDirectory(prefix="pba-tls-demo-") as tmp:
        workdir = Path(tmp)
        try:
            # Test credentials, generated here and deleted with the
            # temporary directory: one CA, one cert per process,
            # one for this demo process as a client.
            creds = protected_link.generate_test_credentials(
                workdir / "creds", ["demo-a", "demo-b", "demo-client"]
            )
            client = creds.nodes["demo-client"]
            node_server.set_client_tls(client.cert, client.key, creds.ca_cert)

            # Setup: Gate 1 registers the policy. The server
            # processes below never see the channel, only their own
            # snapshot copies.
            central = workdir / "central"
            registry_log = central / "registry.jsonl"
            channel = RegistrationChannel(registry_log, central / "audit.jsonl")
            secret = channel.enroll_user("alice")
            record, _ = channel.register(
                author="alice", name="member-access", version="1.0.0", code=CODE,
                signature=sign_library(secret, "alice", "member-access", "1.0.0", CODE),
            )
            payloads_file = workdir / "payloads.json"
            payloads_file.write_text(
                json.dumps({record.policy_hash: CODE.hex()}), encoding="utf-8"
            )

            def start(name: str) -> DemoServer:
                snapshot = workdir / f"{name}-snapshot.jsonl"
                shutil.copyfile(registry_log, snapshot)
                server = DemoServer(workdir, name, [
                    "--snapshot", str(snapshot),
                    "--payloads", str(payloads_file),
                    "--serve-policies",
                ], creds)
                servers.append(server)
                print(f"started protected process {name} at {server.base_url} "
                      f"(pid {server.proc.pid})")
                return server

            a = start("demo-a")
            b = start("demo-b")

            print()
            print("=== Outcome 1: two protected processes sync and converge ===")
            node_server.mutate(a.base_url, "region", "us-central")
            node_server.mutate(b.base_url, "tier", "member")
            print("before sync, hashes differ:",
                  node_server.fetch_state(a.base_url)["state_hash"]
                  != node_server.fetch_state(b.base_url)["state_hash"])
            outcome = node_server.request_sync(a.base_url, b.base_url)
            print("sync result (A's merge):", outcome["self"])
            print("sync result (B's merge):", outcome["peer"])
            state_a = node_server.fetch_state(a.base_url)
            state_b = node_server.fetch_state(b.base_url)
            print("states:", state_a["state"])
            print("hashes equal:", state_a["state_hash"] == state_b["state_hash"])
            outcomes[1] = (
                state_a["state"] == state_b["state"]
                and state_a["state_hash"] == state_b["state_hash"]
                and len(state_a["entry_ids"]) == 2
            )

            print()
            print("=== Outcome 2: the wire is encrypted (capture through a proxy) ===")
            proxy = CapturingProxy(a.port)
            node_server.mutate(proxy.base_url, "capture-key", SECRET_VALUE)
            status, body = node_server.fetch_policy(proxy.base_url, record.policy_hash)
            proxy.close()
            time.sleep(0.2)
            captured = bytes(proxy.captured)
            print("exchange over the captured path succeeded:", status == 200)
            print("bytes captured:", len(captured))
            print("secret value in the capture:", SECRET_VALUE.encode() in captured)
            print("policy code in the capture:", CODE in captured)
            outcomes[2] = (
                status == 200
                and len(captured) > 500
                and SECRET_VALUE.encode() not in captured
                and CODE not in captured
            )

            print()
            print("=== Outcome 3: an unauthenticated peer is refused ===")
            anon = protected_link.anonymous_client_context(creds.ca_cert)
            refused = False
            try:
                req = urllib.request.Request(b.base_url + "/entries", method="GET")
                urllib.request.urlopen(req, timeout=10, context=anon).read()
            except (urllib.error.URLError, OSError):
                refused = True
            print("client with no credential refused at the handshake:", refused)
            print("policy served to the credentialed client by:",
                  body.get("served_by"))
            outcomes[3] = refused and status == 200

            print()
            print("=== Outcome 4: kill the second process; the first still serves ===")
            b.proc.kill()
            b.proc.wait(timeout=5)
            print(f"process demo-b (pid {b.proc.pid}) killed")
            state_a = node_server.fetch_state(a.base_url)
            status, body = node_server.fetch_policy(a.base_url, record.policy_hash)
            print("A's state still holds both nodes' entries:",
                  state_a["state"].get("tier") == "member"
                  and state_a["state"].get("capture-key") == SECRET_VALUE)
            print("policy still served, now by:", body.get("served_by"))
            outcomes[4] = (
                state_a["state"].get("tier") == "member"
                and status == 200
                and base64.b64decode(body["code_base64"]) == CODE
            )
        finally:
            for server in servers:
                server.stop()
            node_server.clear_client_tls()

    print()
    print("Protected node outcomes:", outcomes)
    return 0 if all(outcomes.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
