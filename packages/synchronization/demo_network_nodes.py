"""
Runnable walkthrough of the networked node prototype (experiment 007).

Starts two real node-server processes on ephemeral localhost ports,
drives them only over HTTP: each mutates its own state, they sync
server-to-server and converge, a registered policy is served by the
second process, then the second process is killed and the first
still serves both the state and the policy. Prints three outcomes
as True/False at the end. All server processes are cleaned up.

    python3 packages/synchronization/demo_network_nodes.py

Standard library only. Localhost only; nothing leaves this machine.
"""

import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "registration"))

import node_server  # noqa: E402
from registration import RegistrationChannel, sign_library  # noqa: E402

CODE = b"policy: allow if caller.tier == 'member'\n"


class DemoServer:
    def __init__(self, workdir: Path, name: str, args: list[str]):
        self.name = name
        self.port_file = workdir / f"{name}.port"
        cmd = [
            sys.executable, str(HERE / "node_server.py"),
            "--node-id", name, "--port", "0",
            "--port-file", str(self.port_file), *args,
        ]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.port_file.exists():
                port = int(self.port_file.read_text(encoding="utf-8").strip())
                self.base_url = f"http://127.0.0.1:{port}"
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


def main() -> int:
    outcomes = {}
    servers: list[DemoServer] = []
    with tempfile.TemporaryDirectory(prefix="pba-net-demo-") as tmp:
        workdir = Path(tmp)

        # Setup: Gate 1 registers the policy. The server processes
        # below never see the channel, only their own snapshot copies.
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
            ])
            servers.append(server)
            print(f"started process {name} at {server.base_url} "
                  f"(pid {server.proc.pid})")
            return server

        try:
            a = start("demo-a")
            b = start("demo-b")

            print()
            print("=== Outcome 1: two processes sync over HTTP and converge ===")
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
            print("=== Outcome 2: policy served by the second process, over HTTP ===")
            status, body = node_server.fetch_policy(b.base_url, record.policy_hash)
            print("status:", status, "| served_by:", body.get("served_by"))
            print("code matches the registered policy:",
                  base64.b64decode(body["code_base64"]) == CODE)
            outcomes[2] = (
                status == 200
                and base64.b64decode(body["code_base64"]) == CODE
                and body["record_hash"] == record.record_hash
            )

            print()
            print("=== Outcome 3: kill the second process; the first still serves ===")
            b.proc.kill()
            b.proc.wait(timeout=5)
            print(f"process demo-b (pid {b.proc.pid}) killed")
            state_a = node_server.fetch_state(a.base_url)
            status, body = node_server.fetch_policy(a.base_url, record.policy_hash)
            print("A's state still holds both entries:",
                  state_a["state"] == {"region": "us-central", "tier": "member"})
            print("policy still served, now by:", body.get("served_by"))
            outcomes[3] = (
                state_a["state"].get("tier") == "member"
                and status == 200
                and base64.b64decode(body["code_base64"]) == CODE
            )
        finally:
            for server in servers:
                server.stop()

    print()
    print("Networked node outcomes:", outcomes)
    return 0 if all(outcomes.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
