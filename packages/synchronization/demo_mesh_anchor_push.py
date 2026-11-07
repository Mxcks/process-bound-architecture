"""
Demo for experiment 032 (sync-mesh case 21): one server
pushes its MESH anchor to a peer.

Experiment 031 gave a running server a mesh anchor
RECEIVING surface (POST /deliver-mesh-anchor), but the
sending end was a client: no server sent its own mesh
anchor onward. This demo drives the sender's half,
experiment 032's POST /push-mesh-anchor.

Two node server processes (experiment 007's transport)
converge over three mesh entries; the mesh anchor over
that state is written by alice (experiment 012), signed
with her enrollment secret (experiment 019), and BOTH
servers are restarted consulting their own copy of it
(experiment 026). Then, with neither server restarted
again and no hand touching mesh-b's mesh anchor file after
its anchored start:

1. The mesh grows honestly AT THE SENDER (mesh-a mutates
   once more). mesh-b's sync toward mesh-a is refused as a
   divergence named anchor_stale - mesh-b's anchor pins
   the older head and the merged state grew honestly past
   it (case 15's control, at the peer).
2. mesh-a's mesh anchor is advanced by the proper
   lifecycle AT THE SENDER: alice re-anchors the grown
   head from mesh-a's own anchor file, extending its kept
   history - an operator act at the sender's own file.
3. ONE driven mesh anchor push from mesh-a delivers the
   advanced anchor: the push reports mesh-b's delivery
   report (delivered, counts 3 -> 4), and mesh-b's mesh
   anchor file ends byte-for-byte the anchor mesh-a sent
   - which is byte-for-byte mesh-a's own anchor file.
4. The same sync now converges, and both /health surfaces
   report the new standing: mesh-a's last_mesh_anchor_push,
   mesh-b's last_mesh_anchor_delivery, both anchored.
5. A second identical push reports no_change and rewrites
   nothing at mesh-b.
6. The finding, stated as in cases 17/20: a successor
   mesh anchor over a REWRITTEN chain, written from the
   sender's CURRENT anchor file with alice's secret, is
   one this push would carry and mesh-b's unmodified
   experiment 031 judgement would accept - the sender
   legitimately holds the signer's secret, and custody of
   that one secret is the whole fence at this surface
   too; the countersigned form (experiment 029) moves the
   fence to two secrets where a witness is configured.
   The push adds no weakness and removes none: it
   transports a completed lifecycle, judged at the peer.

Run:  python3 packages/synchronization/demo_mesh_anchor_push.py
Standard library only. Localhost only.
"""

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
        "pba_demo_mesh_anchor_push_sync_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mesh_anchor = _load_mesh_anchor()
write_mesh_anchor = mesh_anchor.write_mesh_anchor


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
    tmp = Path(tempfile.mkdtemp(prefix="pba-mesh-anchor-push-demo-"))
    central = tmp / "central"
    channel = RegistrationChannel(
        central / "registry.jsonl", central / "audit.jsonl")
    alice_secret = channel.enroll_user("alice")
    channel.enroll_user("bob")

    outside = tmp / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    secret_file = outside / "alice-secret.txt"
    secret_file.write_text(alice_secret, encoding="utf-8")
    anchor_a = tmp / "mesh-a" / "mesh-anchor.json"
    anchor_b = tmp / "mesh-b" / "mesh-anchor.json"

    servers = []
    try:
        a = Server(tmp, "mesh-a-plain", "mesh-a")
        b = Server(tmp, "mesh-b-plain", "mesh-b")
        servers += [a, b]
        node_server.mutate(a.base_url, "greeting", "hello")
        node_server.mutate(a.base_url, "mode", "member")
        node_server.mutate(b.base_url, "region", "north")
        node_server.request_sync(a.base_url, b.base_url)
        entries = node_server.fetch_entries(b.base_url)
        write_mesh_anchor(view_of(entries), anchor_a,
                          signer="alice", secret_hex=alice_secret)
        write_mesh_anchor(view_of(entries), anchor_b,
                          signer="alice", secret_hex=alice_secret)
        a.stop()
        servers.remove(a)
        a = Server(tmp, "mesh-a", "mesh-a", extra_args=[
            "--mesh-anchor", str(anchor_a),
            "--mesh-anchor-signer", "alice",
            "--mesh-anchor-secret-file", str(secret_file)])
        servers.append(a)
        node_server.request_sync(a.base_url, b.base_url)
        b.stop()
        servers.remove(b)
        b = Server(tmp, "mesh-b", "mesh-b", extra_args=[
            "--mesh-anchor", str(anchor_b),
            "--mesh-anchor-signer", "alice",
            "--mesh-anchor-secret-file", str(secret_file)])
        servers.append(b)
        node_server.request_sync(b.base_url, a.base_url)
        health_a = node_server.fetch_health(a.base_url)
        health_b = node_server.fetch_health(b.base_url)
        print(f"Setup. mesh-a (pid {a.proc.pid}) and mesh-b "
              f"(pid {b.proc.pid}) both run anchored under "
              f"alice's signed mesh anchor over the 3-entry "
              f"converged state: mesh-a "
              f"mesh_anchor_status={health_a['mesh_anchor_status']}, "
              f"mesh-b mesh_anchor_status="
              f"{health_b['mesh_anchor_status']}; mesh-a's "
              f"last_mesh_anchor_push="
              f"{health_a['last_mesh_anchor_push']}")

        node_server.mutate(a.base_url, "level", "3")
        result = node_server.request_sync(b.base_url, a.base_url)
        print(f"1. The mesh grew at the sender (mesh-a); "
              f"mesh-b's sync toward it is refused: "
              f"divergence={result['self']['divergence_detected']}, "
              f"reason={result['self']['reason']} - mesh-b's "
              f"anchor pins the older head")

        write_mesh_anchor(
            view_of(node_server.fetch_entries(a.base_url)),
            anchor_a, signer="alice", secret_hex=alice_secret)
        print(f"2. mesh-a's mesh anchor advanced at the "
              f"sender: alice re-anchored the grown head "
              f"from mesh-a's own anchor file, its kept "
              f"history extended by the lifecycle's one write")

        status, push = node_server.push_mesh_anchor(
            a.base_url, b.base_url)
        delivery = push["delivery"]
        print(f"3. ONE mesh anchor push from mesh-a: HTTP "
              f"{status}, status={push['status']} - the "
              f"peer's delivery report: entries "
              f"{delivery['before_count']} -> "
              f"{delivery['after_count']}, history sequence "
              f"{delivery['before_seq']} -> "
              f"{delivery['after_seq']}; mesh-b's anchor "
              f"file byte-for-byte the anchor mesh-a sent: "
              f"{anchor_b.read_bytes() == anchor_a.read_bytes()}")

        result = node_server.request_sync(b.base_url, a.base_url)
        health_a = node_server.fetch_health(a.base_url)
        health_b = node_server.fetch_health(b.base_url)
        print(f"4. The same sync now: "
              f"divergence={result['self']['divergence_detected']}; "
              f"health: mesh-a last_mesh_anchor_push="
              f"{health_a['last_mesh_anchor_push']['status']}, "
              f"mesh-b last_mesh_anchor_delivery="
              f"{health_b['last_mesh_anchor_delivery']['status']}, "
              f"both mesh_anchor_status anchored: "
              f"{health_a['mesh_anchor_status'] == 'anchored' and health_b['mesh_anchor_status'] == 'anchored'}")

        status, push = node_server.push_mesh_anchor(
            a.base_url, b.base_url)
        print(f"5. A second identical push: HTTP {status}, "
              f"status={push['status']} - the peer already "
              f"stands on the identical anchor; nothing "
              f"rewritten at mesh-b")

        print(f"6. FINDING (stated, as in cases 17/20): a "
              f"successor mesh anchor over a rewritten "
              f"chain, written from the sender's current "
              f"anchor file with alice's secret, is one "
              f"this push would carry and the peer's "
              f"unmodified experiment 031 judgement would "
              f"accept - custody of the signer's secret is "
              f"the whole fence at this surface too")
        print(f"   Neither server was restarted after its "
              f"anchored start: mesh-a pid {a.proc.pid}, "
              f"mesh-b pid {b.proc.pid} throughout, and no "
              f"hand touched mesh-b's mesh anchor file")
    finally:
        for server in servers:
            server.stop()
    print(f"Demo state left in: {tmp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
