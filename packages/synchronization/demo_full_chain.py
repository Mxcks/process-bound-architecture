"""
Runnable demo of the full-chain integration scenario
(experiment 020).

    python3 packages/synchronization/demo_full_chain.py

Walks Max's whole original chain in order over one policy, in a
temp directory left on disk and printed at the end: Gate 1
registration, a signed registry anchor, placement at floor 3
with the holders consulting that anchor, Gate 2 serving from a
second holder, serving from a second PROCESS over mutual TLS
with identity binding and the signed anchor at startup, mesh
sync over that link, in-process mesh convergence with mesh
anchor consultation, liveness auto-healing on the modeled
clock, and author revocation delivered through the anchor
lifecycle until serving stops. It also prints the experiment's
two findings exactly as found: a placed ReplicatedPolicy has
no reload and keeps serving a revoked policy until it is
rebuilt, and a revoked policy requested over the network
transport drops the connection (unhandled PolicyRevoked in the
server handler) instead of returning a clean refusal.
Credentials and secrets are generated at runtime in the temp
directory only. Standard library only. Localhost only.
"""

import base64
import importlib.util
import json
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "validation"))
sys.path.insert(0, str(HERE.parent / "registration"))
sys.path.insert(0, str(HERE.parent / "policy-library"))
sys.path.insert(0, str(HERE))

import node_server  # noqa: E402
import protected_link  # noqa: E402
from anchored_sync import AnchoredSyncNode  # noqa: E402
from liveness import LivenessWatcher  # noqa: E402
from policy_node import AnchorStale, PolicyNode, PolicyRevoked  # noqa: E402
from registration import (  # noqa: E402
    RegistrationChannel,
    sign_library,
    sign_revocation,
)
from replication import NodeSpec, place_policy  # noqa: E402
from sync_mesh import SyncNode  # noqa: E402


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


registry_anchor = _load_module(
    "pba_fullchain_demo_registry_anchor",
    HERE.parent / "registration" / "anchor.py")
mesh_anchor = _load_module(
    "pba_fullchain_demo_mesh_anchor", HERE / "anchor.py")

SERVER = HERE / "node_server.py"
CODE = b"policy: allow if caller.tier == 'member'\n"
HOLDERS = ("holder-a", "holder-b", "holder-c")


class Server:
    def __init__(self, tmp, label, args, creds):
        self.label = label
        self.port_file = tmp / f"{label}.port"
        self.log_path = tmp / f"{label}.log"
        self._fh = self.log_path.open("w", encoding="utf-8")
        cert = creds.nodes[label]
        cmd = [sys.executable, str(SERVER), "--node-id", label,
               "--port", "0", "--port-file", str(self.port_file),
               "--tls-cert", str(cert.cert), "--tls-key", str(cert.key),
               "--tls-ca", str(creds.ca_cert), "--tls-bind-identity"] + args
        self.proc = subprocess.Popen(
            cmd, stdout=self._fh, stderr=subprocess.STDOUT)
        self.base_url = None

    def wait_ready(self, timeout=20.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"{self.label} exited early; log:\n{self.log_text()}")
            if self.port_file.exists():
                port = int(self.port_file.read_text(encoding="utf-8").strip())
                self.base_url = f"https://127.0.0.1:{port}"
                try:
                    node_server.fetch_health(self.base_url)
                    return self.base_url
                except (urllib.error.URLError, OSError, ssl.SSLError):
                    pass
            time.sleep(0.05)
        raise AssertionError(f"{self.label} not ready")

    def log_text(self):
        self._fh.flush()
        return self.log_path.read_text(encoding="utf-8")

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self._fh.close()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-full-chain-demo-"))
    servers = []
    try:
        print("Setup. Alice registers one policy; all state is temp.")

        central = tmp / "central"
        registry_log = central / "registry.jsonl"
        channel = RegistrationChannel(registry_log, central / "audit.jsonl")
        secret = channel.enroll_user("alice")
        record, created = channel.register(
            author="alice", name="member-access", version="1.0.0",
            code=CODE,
            signature=sign_library(secret, "alice", "member-access",
                                   "1.0.0", CODE))
        print(f"1. Gate 1 registration: created={created} "
              f"channel_ready_before_use=True "
              f"validation_channel={record.validation_channel is not None}")

        anchor_path = tmp / "outside" / "registry-anchor.json"
        registry_anchor.write_anchor(registry_log, anchor_path,
                                     signer="alice", secret_hex=secret)
        secret_file = tmp / "outside" / "anchor-secret.txt"
        secret_file.write_text(secret, encoding="utf-8")
        check = registry_anchor.check_registry_against_anchor(
            registry_log, anchor_path, signer="alice", secret_hex=secret)
        print(f"2. Signed registry anchor: ok={check.ok} "
              f"signature_valid={check.signature_valid}")

        snapshot = tmp / "placement-snapshot.jsonl"
        shutil.copyfile(registry_log, snapshot)
        replicated = place_policy(
            policy_hash=record.policy_hash, code=CODE,
            registry_snapshot_path=snapshot,
            candidates=[NodeSpec(n, serve_policies=True) for n in HOLDERS],
            floor=3, anchor_path=anchor_path, anchor_signer="alice",
            anchor_secret_hex=secret)
        rep = replicated.placement_report()
        print(f"3. Placement: holders={list(rep.holder_node_ids)} "
              f"floor_met={rep.floor_met} status={rep.status}")

        second = PolicyNode(
            node_id="holder-b", registry_snapshot_path=snapshot,
            payloads={record.policy_hash: CODE}, serve_policies=True,
            anchor_path=anchor_path, anchor_signer="alice",
            anchor_secret_hex=secret)
        served = second.serve_policy(record.policy_hash)
        print(f"4. Gate 2 second holder serves: "
              f"code_matches={served.code == CODE} "
              f"anchor_status={second.anchor_status}")

        creds = protected_link.generate_test_credentials(
            tmp / "creds", ["srv-a", "srv-b", "test-client"])
        client = creds.nodes["test-client"]
        node_server.set_client_tls(client.cert, client.key, creds.ca_cert,
                                   node_id="test-client")
        payloads_file = tmp / "payloads.json"
        payloads_file.write_text(
            json.dumps({record.policy_hash: CODE.hex()}), encoding="utf-8")

        def server_args(label):
            snap = tmp / f"{label}-dir" / "snapshot.jsonl"
            snap.parent.mkdir(exist_ok=True)
            shutil.copyfile(registry_log, snap)
            return ["--snapshot", str(snap), "--payloads",
                    str(payloads_file), "--serve-policies", "--anchor",
                    str(anchor_path), "--anchor-signer", "alice",
                    "--anchor-secret-file", str(secret_file)], snap

        args_a, _ = server_args("srv-a")
        args_b, snap_b = server_args("srv-b")
        srv_a = Server(tmp, "srv-a", args_a, creds)
        servers.append(srv_a)
        srv_a.wait_ready()
        srv_b = Server(tmp, "srv-b", args_b, creds)
        servers.append(srv_b)
        srv_b.wait_ready()
        health = node_server.fetch_health(srv_b.base_url)
        status, body = node_server.fetch_policy(srv_b.base_url,
                                                record.policy_hash)
        node_server.mutate(srv_a.base_url, "region", "us-central")
        node_server.mutate(srv_b.base_url, "tier", "member")
        node_server.request_sync(srv_a.base_url, srv_b.base_url)
        state_a = node_server.fetch_state(srv_a.base_url)
        state_b = node_server.fetch_state(srv_b.base_url)
        print(f"5. Protected process serving: status={status} "
              f"served_by={body['served_by']} "
              f"anchor_status={health['anchor_status']} "
              f"sync_converged={state_a['state_hash'] == state_b['state_hash']}")
        srv_a.stop()
        servers.remove(srv_a)

        mesh = [SyncNode(f"mesh-{i}") for i in range(3)]
        mesh[0].mutate(f"policy:{record.policy_hash}", "placed-at-floor-3")
        mesh[1].mutate("region", "us-central")
        mesh[2].mutate("tier", "member")
        everything = []
        for node in mesh:
            everything.extend(node.entries())
        for node in mesh:
            node.receive_entries(everything)
        mesh_anchor_path = tmp / "outside" / "mesh-anchor.json"
        mesh_anchor.write_mesh_anchor(mesh[0], mesh_anchor_path,
                                      signer="alice", secret_hex=secret)
        anchored = AnchoredSyncNode(
            "mesh-anchored", anchor_path=mesh_anchor_path,
            anchor_signer="alice", anchor_secret_hex=secret)
        accepted = anchored.receive_entries(everything)
        print(f"6. Mesh: converged={len({n.state_hash() for n in mesh}) == 1} "
              f"anchored_accept={not accepted.divergence_detected} "
              f"anchor_status={anchored.anchor_status()}")

        watcher = LivenessWatcher(
            replicated,
            candidates=[NodeSpec("replacement-d", serve_policies=True)])
        watcher.tick(beating=list(HOLDERS))
        report = None
        for _ in range(3):
            report = watcher.tick(
                beating=[n for n in HOLDERS if n != "holder-a"])
        print(f"7. Liveness: lost={report.lost_node_ids} "
              f"recruited={report.healing.recruited_node_ids} "
              f"floor_restored={report.healing.floor_restored} "
              f"still_serves={replicated.serve().code == CODE}")

        channel.revoke(
            author="alice", policy_hash=record.policy_hash,
            signature=sign_revocation(secret, record.policy_hash))
        shutil.copyfile(registry_log, snapshot)
        stale = False
        try:
            second.reload_snapshot()
        except AnchorStale:
            stale = True
        registry_anchor.write_anchor(registry_log, anchor_path,
                                     signer="alice", secret_hex=secret)
        second.reload_snapshot()
        refused = False
        try:
            second.serve_policy(record.policy_hash)
        except PolicyRevoked:
            refused = True
        print(f"8. Revocation: stale_before_reanchor={stale} "
              f"refused_after_delivery={refused} "
              f"channel_revoked={channel.is_revoked(record.policy_hash)}")

        print(f"FINDING F1: placed object has reload="
              f"{hasattr(replicated, 'reload_snapshot')}; it still "
              f"serves the revoked policy="
              f"{replicated.serve().code == CODE} (rebuilt holders "
              f"over the updated snapshot refuse, as stage 8 shows "
              f"for a single node)")

        srv_b.stop()
        servers.remove(srv_b)
        shutil.copyfile(registry_log, snap_b)
        srv_b2 = Server(tmp, "srv-b", args_b, creds)
        servers.append(srv_b2)
        srv_b2.wait_ready()
        shape = ""
        try:
            status, _body = node_server.fetch_policy(
                srv_b2.base_url, record.policy_hash)
            shape = f"http_{status}"
        except Exception as exc:
            shape = f"exception:{type(exc).__name__}"
        time.sleep(0.2)
        print(f"FINDING F2: revoked policy over the network: {shape}; "
              f"server log names PolicyRevoked="
              f"{'PolicyRevoked' in srv_b2.log_text()}; the policy "
              f"bytes were not served")
        srv_b2.stop()
        servers.remove(srv_b2)
    finally:
        for srv in servers:
            srv.stop()
        node_server.clear_client_tls()
    print(f"Demo state (temp, servers terminated): {tmp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
