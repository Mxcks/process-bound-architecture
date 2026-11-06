"""
Runnable walkthrough of certificate-to-node-id binding
(experiment 017).

Starts real node-server processes on ephemeral localhost ports
speaking mutual TLS with identity binding enabled, and shows:
two correctly identified processes sync, converge, and serve a
registered policy exactly as in experiment 014; a client
presenting net-b's valid certificate while claiming net-a is
refused with a plainly stated identity_mismatch, while the SAME
certificate claiming net-b is served; a process claiming an id
its certificate was not issued for refuses to start under
binding, and started without binding it is refused by a
binding-enabled initiator before any entries move. Prints four
outcomes as True/False at the end. All server processes are
cleaned up, and all credentials are generated at runtime into a
temporary directory that is deleted at the end - no key or
certificate file is kept.

    python3 packages/synchronization/demo_cert_node_binding.py

Standard library only (openssl subprocess for test credentials).
Localhost only; nothing leaves this machine.
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
import protected_link  # noqa: E402
from registration import RegistrationChannel, sign_library  # noqa: E402

CODE = b"policy: allow if caller.tier == 'member'\n"


class DemoServer:
    """One node_server.py process. label names its files, node_id
    is what it claims, cert_name is the credential it presents."""

    def __init__(self, workdir: Path, label: str, node_id: str,
                 cert_name: str, args: list[str], creds, bind: bool = True):
        self.label = label
        self.node_id = node_id
        self.port_file = workdir / f"{label}.port"
        self.log_path = workdir / f"{label}.log"
        self._log_fh = self.log_path.open("w", encoding="utf-8")
        cert = creds.nodes[cert_name]
        cmd = [
            sys.executable, str(HERE / "node_server.py"),
            "--node-id", node_id, "--port", "0",
            "--port-file", str(self.port_file),
            "--tls-cert", str(cert.cert),
            "--tls-key", str(cert.key),
            "--tls-ca", str(creds.ca_cert),
            *args,
        ]
        if bind:
            cmd.append("--tls-bind-identity")
        self.proc = subprocess.Popen(
            cmd, stdout=self._log_fh, stderr=subprocess.STDOUT
        )
        self.base_url = None
        self.port = None

    def wait_ready(self, timeout: float = 20.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"{self.label} exited early rc={self.proc.returncode}; "
                    f"log:\n{self.log_path.read_text(encoding='utf-8')}"
                )
            if self.port_file.exists():
                port = int(self.port_file.read_text(encoding="utf-8").strip())
                self.port = port
                self.base_url = f"https://127.0.0.1:{port}"
                try:
                    node_server.fetch_health(self.base_url)
                    return self.base_url
                except (AssertionError, urllib.error.URLError, OSError):
                    pass
            time.sleep(0.05)
        raise RuntimeError(f"{self.label} did not start")

    def log_text(self) -> str:
        self._log_fh.flush()
        return self.log_path.read_text(encoding="utf-8")

    def stop(self):
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


def main() -> int:
    outcomes = {}
    servers: list[DemoServer] = []
    with tempfile.TemporaryDirectory(prefix="pba-bind-demo-") as tmp:
        workdir = Path(tmp)
        try:
            creds = protected_link.generate_test_credentials(
                workdir / "creds", ["net-a", "net-b", "net-c", "demo-client"]
            )
            client = creds.nodes["demo-client"]
            node_server.set_client_tls(
                client.cert, client.key, creds.ca_cert, node_id="demo-client"
            )

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

            def start(label, node_id=None, cert_name=None, serve=True,
                      with_policy=True, bind=True):
                node_id = node_id or label
                cert_name = cert_name or node_id
                args: list[str] = []
                if with_policy:
                    snapshot = workdir / f"{label}-snapshot.jsonl"
                    shutil.copyfile(registry_log, snapshot)
                    args += ["--snapshot", str(snapshot),
                             "--payloads", str(payloads_file)]
                    if serve:
                        args.append("--serve-policies")
                server = DemoServer(workdir, label, node_id, cert_name,
                                    args, creds, bind=bind)
                servers.append(server)
                server.wait_ready()
                print(f"started process {label} (claims {node_id}, "
                      f"certificate for "
                      f"{protected_link.cert_file_node_id(creds.nodes[cert_name].cert)}) "
                      f"at {server.base_url} (pid {server.proc.pid})")
                return server

            a = start("net-a")
            b = start("net-b", with_policy=False)

            print()
            print("=== Outcome 1: correctly identified peers converge, policy crosses ===")
            print("certificate ids:",
                  {n: protected_link.cert_file_node_id(c.cert)
                   for n, c in creds.nodes.items()})
            node_server.mutate(a.base_url, "region", "us-central")
            node_server.mutate(b.base_url, "tier", "member")
            outcome = node_server.request_sync(a.base_url, b.base_url)
            print("sync diverged:", outcome["self"]["divergence_detected"])
            state_a = node_server.fetch_state(a.base_url)
            state_b = node_server.fetch_state(b.base_url)
            status, body = node_server.fetch_policy(a.base_url, record.policy_hash)
            print("states:", state_a["state"])
            print("hashes equal:", state_a["state_hash"] == state_b["state_hash"])
            print("policy served by:", body.get("served_by"))
            print("peer identity verified as:",
                  node_server.verify_peer_identity(a.base_url))
            outcomes[1] = (
                state_a["state_hash"] == state_b["state_hash"]
                and len(state_a["entry_ids"]) == 2
                and status == 200
                and base64.b64decode(body["code_base64"]) == CODE
            )

            print()
            print("=== Outcome 2: a valid certificate claiming the wrong id is refused ===")

            def attempt(path, cred_name, claimed):
                cred = creds.nodes[cred_name]
                context = protected_link.client_context(
                    cred.cert, cred.key, creds.ca_cert
                )
                req = urllib.request.Request(a.base_url + path, method="GET")
                req.add_header(node_server.CLAIM_HEADER, claimed)
                try:
                    with urllib.request.urlopen(req, timeout=10, context=context) as resp:
                        return resp.status, json.loads(resp.read().decode("utf-8"))
                except urllib.error.HTTPError as exc:
                    return exc.code, json.loads(exc.read().decode("utf-8"))

            status_bad, body_bad = attempt("/entries", "net-b", "net-a")
            print("net-b's certificate claiming net-a:",
                  status_bad, body_bad["error"],
                  "claimed", body_bad["claimed"],
                  "certified", body_bad["certified"])
            status_good, _ = attempt("/health", "net-b", "net-b")
            print("the SAME certificate claiming net-b:", status_good)
            health = node_server.fetch_health(a.base_url)
            print("server still serves a correctly identified peer:",
                  health["ok"])
            outcomes[2] = (
                status_bad == 403
                and body_bad["error"] == "identity_mismatch"
                and status_good == 200
                and health["ok"]
            )

            print()
            print("=== Outcome 3: a false-claiming process refuses to start under binding ===")
            snapshot = workdir / "false-start-snapshot.jsonl"
            shutil.copyfile(registry_log, snapshot)
            false_start = DemoServer(
                workdir, "false-start", "net-a", "net-b",
                ["--snapshot", str(snapshot), "--payloads", str(payloads_file),
                 "--serve-policies"],
                creds, bind=True,
            )
            servers.append(false_start)
            deadline = time.monotonic() + 20
            while false_start.proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            exited = false_start.proc.poll() is not None
            print("process exited on its own:", exited,
                  "rc:", false_start.proc.returncode)
            print("its log says:", false_start.log_text().strip().splitlines()[-1]
                  if false_start.log_text().strip() else "(nothing)")
            outcomes[3] = (
                exited
                and false_start.proc.returncode != 0
                and "identity_mismatch" in false_start.log_text()
            )

            print()
            print("=== Outcome 4: a binding initiator refuses the false claimant, no state moves ===")
            impostor = start("impostor", node_id="net-a", cert_name="net-b",
                             bind=False)
            node_server.mutate(impostor.base_url, "impostor-key", "must-not-move")
            c = start("net-c", with_policy=False)
            status, body = node_server.post_json(
                c.base_url, "/sync", {"peer": impostor.base_url}
            )
            print("net-c initiating sync toward the impostor:",
                  status, body.get("error"),
                  "claimed", body.get("claimed"),
                  "certified", body.get("certified"))
            state_c = node_server.fetch_state(c.base_url)
            print("net-c holds the impostor's entry:",
                  "impostor-key" in state_c["state"])
            outcomes[4] = (
                status == 502
                and body.get("error") == "identity_mismatch"
                and state_c["entry_ids"] == []
            )
        finally:
            for server in servers:
                server.stop()
            node_server.clear_client_tls()

    print()
    print("Certificate binding outcomes:", outcomes)
    return 0 if all(outcomes.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
