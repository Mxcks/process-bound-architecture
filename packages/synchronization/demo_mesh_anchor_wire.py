"""
Demo for experiment 026 (sync-mesh case 15): mesh-anchor
consultation over the wire for server processes.

Two node server processes (experiment 007's transport) mutate
and sync and converge. A mesh anchor (experiment 012) is
written over the converged state. Then:

1. mesh-a is restarted with --mesh-anchor. It holds nothing
   (mesh state is process memory), so /health classifies its
   standing as anchor_mismatch; one /sync toward mesh-b,
   which kept the converged state, lands it exactly on the
   anchored state - the consultation ran inside the running
   process's own receive - and /health reports "anchored".
2. mesh-b mutates once more, honestly. The next /sync is
   refused as anchor_stale: growth past the anchor is not
   adopted and not called a mismatch. After the grown state
   is re-anchored, the same /sync converges.
3. A fresh server (mesh-victim) started with the same
   --mesh-anchor is pushed a rewritten mesh-a chain over
   POST /entries - same origin, different mutations, every
   hash recomputing. It refuses the batch as anchor_mismatch
   and adopts nothing: the refusal an in-process
   AnchoredSyncNode has made since experiment 015, now made
   by a running server process over the wire.

Run:  python3 packages/synchronization/demo_mesh_anchor_wire.py
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

import node_server  # noqa: E402
from anchor import write_mesh_anchor  # noqa: E402  (exp 012 mesh anchor)
from sync_mesh import SyncNode  # noqa: E402

SERVER = HERE / "node_server.py"


def wait_ready(proc, port_file: Path, timeout: float = 20.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early rc={proc.returncode}")
        if port_file.exists():
            port = int(port_file.read_text(encoding="utf-8").strip())
            base_url = f"http://127.0.0.1:{port}"
            try:
                node_server.fetch_health(base_url)
                return base_url
            except (AssertionError, urllib.error.URLError, OSError):
                pass
        time.sleep(0.05)
    raise RuntimeError("server not ready")


def spawn(tmp: Path, label: str, node_id: str, args=()):
    port_file = tmp / f"{label}.port"
    log_path = tmp / f"{label}.log"
    log_fh = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(SERVER), "--node-id", node_id,
         "--port", "0", "--port-file", str(port_file), *args],
        stdout=log_fh, stderr=subprocess.STDOUT)
    return proc, wait_ready(proc, port_file), log_fh


def stop(proc, log_fh) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    log_fh.close()


def view_of(entries) -> SyncNode:
    view = SyncNode("mesh-view")
    view.restore_to_entries(entries)
    return view


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-mesh-anchor-wire-demo-"))
    anchor_path = tmp / "outside" / "mesh-anchor.json"
    procs = []
    try:
        proc_a, base_a, fh_a = spawn(tmp, "mesh-a", "mesh-a")
        procs.append((proc_a, fh_a))
        proc_b, base_b, fh_b = spawn(tmp, "mesh-b", "mesh-b")
        procs.append((proc_b, fh_b))
        node_server.mutate(base_a, "greeting", "hello")
        node_server.mutate(base_a, "mode", "member")
        node_server.mutate(base_b, "region", "north")
        result = node_server.request_sync(base_a, base_b)
        print(f"Setup. Two plain servers converged: "
              f"state_hash matches: "
              f"{result['state_hash'] == node_server.fetch_state(base_b)['state_hash']}")
        write_mesh_anchor(
            view_of(node_server.fetch_entries(base_b)), anchor_path)
        print(f"   mesh anchor written over the converged state "
              f"(3 entries, held outside both processes)")

        stop(proc_a, fh_a)
        procs.remove((proc_a, fh_a))
        proc_a2, base_a2, fh_a2 = spawn(
            tmp, "mesh-a-anchored", "mesh-a",
            ["--mesh-anchor", str(anchor_path)])
        procs.append((proc_a2, fh_a2))
        health = node_server.fetch_health(base_a2)
        print(f"1. mesh-a restarted with --mesh-anchor: holds "
              f"{health['entries']} entries, mesh_anchor_status="
              f"{health['mesh_anchor_status']} (not yet the "
              f"anchored state)")
        result = node_server.request_sync(base_a2, base_b)
        health = node_server.fetch_health(base_a2)
        print(f"   one /sync toward mesh-b: accepted="
              f"{result['self']['accepted']}, divergence="
              f"{result['self']['divergence_detected']} - "
              f"mesh_anchor_status={health['mesh_anchor_status']}")

        node_server.mutate(base_b, "level", "3")
        result = node_server.request_sync(base_a2, base_b)
        print(f"2. Honest growth, one /sync: divergence="
              f"{result['self']['divergence_detected']}, reason="
              f"{result['self']['reason']} - the anchored "
              f"server still holds "
              f"{node_server.fetch_health(base_a2)['entries']} "
              f"entries")
        write_mesh_anchor(
            view_of(node_server.fetch_entries(base_b)), anchor_path)
        result = node_server.request_sync(base_a2, base_b)
        print(f"   re-anchored, /sync again: accepted="
              f"{result['self']['accepted']}, mesh_anchor_status="
              f"{node_server.fetch_health(base_a2)['mesh_anchor_status']}")

        proc_v, base_v, fh_v = spawn(
            tmp, "mesh-victim", "mesh-victim",
            ["--mesh-anchor", str(anchor_path)])
        procs.append((proc_v, fh_v))
        rogue = SyncNode("mesh-a")
        rogue.mutate("greeting", "pwned")
        rogue.mutate("mode", "admin")
        result = node_server.push_entries(base_v, rogue.entries())
        state = node_server.fetch_state(base_v)
        print(f"3. Rewritten mesh-a chain pushed to a fresh "
              f"anchored server: divergence="
              f"{result['divergence_detected']}, reason="
              f"{result['reason']}, accepted={result['accepted']} "
              f"- victim holds {len(state['entry_ids'])} entries, "
              f"mesh_anchor_status="
              f"{node_server.fetch_health(base_v)['mesh_anchor_status']}")
    finally:
        for proc, fh in procs:
            stop(proc, fh)
    print(f"Demo state left in: {tmp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
