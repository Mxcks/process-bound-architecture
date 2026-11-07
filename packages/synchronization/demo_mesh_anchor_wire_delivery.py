"""
Demo for experiment 031 (sync-mesh case 20): a running server
receives its MESH anchor over the wire.

Two node server processes (experiment 007's transport) converge
over three mesh entries; the mesh anchor over that state is
written by alice (experiment 012), signed with her enrollment
secret (experiment 019), and mesh-a is restarted consulting it
(experiment 026). Then, with mesh-a never restarted again and
its mesh anchor file never written by hand after startup:

1. The mesh anchor it already holds, delivered again over the
   wire, reports no_change.
2. mesh-b mutates; a sync is refused as a divergence named
   anchor_stale - mesh-a's anchor pins the older head and the
   merged state grew honestly past it (case 15's control).
3. The deliverer follows the anchor lifecycle at the source
   (the anchor file copied, alice re-anchors the grown head
   over mesh-b's state) and delivers the successor anchor
   over the wire: delivered - no hand placed the file at
   mesh-a.
4. The same sync now converges, and /health reports
   mesh_anchor_status anchored with the delivery's report
   beside it as last_mesh_anchor_delivery.
5. The ORIGINAL anchor (a kept history one entry shorter) is
   delivered: refused as anchor_regression - an earlier
   version of the server's own anchor file - the refusal
   audit-logged beside the mesh anchor, the file unchanged.
6. The finding, printed as it happens: a successor anchor
   over a REWRITTEN chain (the same keys, different values,
   the same length), written from mesh-a's CURRENT anchor
   file with alice's secret by case 10's own write path, is
   ACCEPTED - and mesh-a's next sync with the honest mesh-b
   is refused anchor_mismatch, its state now judged against
   the rewritten chain's anchor. What stops a rewrite from
   being blessed at this surface is the custody of the
   signer's secret, and nothing else; the wire adds no
   weakness, and the weakness is experiment 019's recorded
   symmetric-model limit - case 20's twin of case 17's
   finding for the registry half.

Run:  python3 packages/synchronization/demo_mesh_anchor_wire_delivery.py
Standard library only. Localhost only.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "registration"))

import node_server  # noqa: E402
from registration import RegistrationChannel  # noqa: E402
from sync_mesh import SyncNode  # noqa: E402


def _load_mesh_anchor():
    """Experiment 012's MESH anchor module, loaded by file
    path under a private name (two packages ship an
    anchor.py), as in the test suites."""
    import importlib.util
    anchor_file = HERE / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_demo_mesh_anchor_wire_sync_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mesh_anchor = _load_mesh_anchor()
write_mesh_anchor = mesh_anchor.write_mesh_anchor
read_anchor_audit = mesh_anchor.read_anchor_audit


def anchor_dict(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def view_of(entries) -> SyncNode:
    view = SyncNode("demo-view")
    result = view.restore_to_entries(list(entries))
    assert not result.divergence_detected
    return view


class Server:
    def __init__(self, tmp: Path, label: str, node_id: str,
                 extra_args=()):
        self.label = label
        self.node_id = node_id
        self.dir = tmp / label
        self.dir.mkdir(parents=True, exist_ok=True)
        self.port_file = self.dir / "port.txt"
        self.log_fh = (self.dir / "server.log").open(
            "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, str(HERE / "node_server.py"),
             "--node-id", node_id,
             "--port", "0", "--port-file", str(self.port_file),
             *extra_args],
            stdout=self.log_fh, stderr=subprocess.STDOUT)
        self.base_url = self._wait_ready()

    def _wait_ready(self, timeout: float = 20.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"{self.label} exited early "
                    f"rc={self.proc.returncode}")
            if self.port_file.exists():
                try:
                    port = int(self.port_file.read_text(
                        encoding="utf-8").strip())
                except ValueError:
                    time.sleep(0.05)
                    continue
                base_url = f"http://127.0.0.1:{port}"
                try:
                    node_server.fetch_health(base_url)
                    return base_url
                except (AssertionError, urllib.error.URLError,
                        OSError):
                    pass
            time.sleep(0.05)
        raise RuntimeError(f"{self.label} not ready")

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.log_fh.close()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-mesh-anchor-wire-demo-"))
    central = tmp / "central"
    channel = RegistrationChannel(
        central / "registry.jsonl", central / "audit.jsonl")
    alice_secret = channel.enroll_user("alice")
    channel.enroll_user("bob")

    outside = tmp / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    secret_file = outside / "alice-secret.txt"
    secret_file.write_text(alice_secret, encoding="utf-8")
    anchor_path = tmp / "mesh-a" / "mesh-anchor.json"

    servers = []
    try:
        a = Server(tmp, "mesh-a-plain", "mesh-a")
        b = Server(tmp, "mesh-b", "mesh-b")
        servers += [a, b]
        node_server.mutate(a.base_url, "greeting", "hello")
        node_server.mutate(a.base_url, "mode", "member")
        node_server.mutate(b.base_url, "region", "north")
        node_server.request_sync(a.base_url, b.base_url)
        write_mesh_anchor(
            view_of(node_server.fetch_entries(b.base_url)),
            anchor_path, signer="alice", secret_hex=alice_secret)
        original_anchor = tmp / "original-mesh-anchor.json"
        shutil.copyfile(anchor_path, original_anchor)
        a.stop()
        servers.remove(a)
        a = Server(tmp, "mesh-a", "mesh-a", extra_args=[
            "--mesh-anchor", str(anchor_path),
            "--mesh-anchor-signer", "alice",
            "--mesh-anchor-secret-file", str(secret_file)])
        servers.append(a)
        node_server.request_sync(a.base_url, b.base_url)
        health = node_server.fetch_health(a.base_url)
        print(f"Setup. mesh-a (pid {a.proc.pid}) runs anchored "
              f"under alice's signed mesh anchor over the "
              f"3-entry converged state: mesh_anchor_status="
              f"{health['mesh_anchor_status']}")

        report = node_server.deliver_mesh_anchor(
            a.base_url, anchor_dict(anchor_path))
        print(f"1. The mesh anchor it already holds, delivered "
              f"again: {report['status']} - nothing written")

        node_server.mutate(b.base_url, "level", "3")
        result = node_server.request_sync(a.base_url, b.base_url)
        print(f"2. mesh-b grew; the sync is refused: "
              f"divergence={result['self']['divergence_detected']}, "
              f"reason={result['self']['reason']} - the anchor "
              f"pins the older head")

        staging = tmp / "staging"
        staging.mkdir(exist_ok=True)
        successor = staging / "successor-mesh-anchor.json"
        shutil.copyfile(anchor_path, successor)
        write_mesh_anchor(
            view_of(node_server.fetch_entries(b.base_url)),
            successor, signer="alice", secret_hex=alice_secret)
        report = node_server.deliver_mesh_anchor(
            a.base_url, anchor_dict(successor))
        print(f"3. The successor anchor, delivered over the "
              f"wire: {report['status']} - entries "
              f"{report['before_count']} -> "
              f"{report['after_count']}, history sequence "
              f"{report['before_seq']} -> {report['after_seq']}; "
              f"no hand placed the file at mesh-a")

        result = node_server.request_sync(a.base_url, b.base_url)
        health = node_server.fetch_health(a.base_url)
        print(f"4. The same sync now: "
              f"divergence={result['self']['divergence_detected']}; "
              f"health: mesh_anchor_status="
              f"{health['mesh_anchor_status']}, "
              f"last_mesh_anchor_delivery="
              f"{health['last_mesh_anchor_delivery']['status']}")

        anchor_now = anchor_path.read_bytes()
        report = node_server.deliver_mesh_anchor(
            a.base_url, anchor_dict(original_anchor))
        events = read_anchor_audit(anchor_path)
        print(f"5. The ORIGINAL anchor delivered: "
              f"{report['status']} / {report['reason']} - an "
              f"earlier version of the server's own anchor "
              f"file; anchor file unchanged: "
              f"{anchor_path.read_bytes() == anchor_now}; audit "
              f"log's last event: {events[-1]['event']} / "
              f"{events[-1]['reason']}")

        rogue_a = SyncNode("mesh-a")
        rogue_a.mutate("greeting", "pwned")
        rogue_a.mutate("mode", "admin")
        rogue_b = SyncNode("mesh-b")
        rogue_b.mutate("region", "south")
        rogue_anchor = staging / "rogue-mesh-anchor.json"
        shutil.copyfile(anchor_path, rogue_anchor)
        write_mesh_anchor(
            view_of([*rogue_a.entries(), *rogue_b.entries()]),
            rogue_anchor, signer="alice",
            secret_hex=alice_secret)
        report = node_server.deliver_mesh_anchor(
            a.base_url, anchor_dict(rogue_anchor))
        result = node_server.request_sync(a.base_url, b.base_url)
        print(f"6. FINDING: a successor anchor over the "
              f"REWRITTEN chain, written from mesh-a's current "
              f"anchor file with alice's secret: "
              f"{report['status']} - and the next honest sync "
              f"is refused: "
              f"divergence={result['self']['divergence_detected']}, "
              f"reason={result['self']['reason']}. What stops a "
              f"rewrite from being blessed at this surface is "
              f"the custody of the signer's secret, and "
              f"nothing else")
        print(f"   mesh-a was never restarted after its "
              f"anchored start: pid {a.proc.pid} throughout, "
              f"and its mesh anchor file was never written by "
              f"hand after startup")
    finally:
        for server in servers:
            server.stop()
    print(f"Demo state left in: {tmp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
