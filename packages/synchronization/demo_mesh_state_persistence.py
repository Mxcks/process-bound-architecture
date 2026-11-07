"""
Demo for experiment 033 (sync-mesh case 22): a server's
mesh state survives its own process death.

Every surface since experiment 007 has run on mesh state
held in the server process's memory alone: kill the
process and its unreplicated entries were gone; what
survived a kill was exactly what had already synced to
another process. This demo drives the durable record
case 22 builds.

Two node server processes (experiment 007's transport):

1. mesh-a starts with --mesh-state over a file that does
   not exist yet: /health reports the load as "fresh",
   and the file appears only when the first mutation is
   accepted - mesh-a mutates twice, entries no other
   process has ever seen.
2. mesh-a is KILLED (SIGKILL - no cleanup, no graceful
   shutdown) and restarted over the same state file:
   /health reports the load as "recovered" with the same
   entry count and state hash, and /state is exactly
   what the killed process held. Those unreplicated
   entries are back from the file alone.
3. A peer that starts empty syncs with the restarted
   mesh-a and learns the recovered entries over /sync,
   with no re-mutation anywhere: persistence plus the
   mesh's own anti-entropy moves them.
4. The load is judged, not trusted: the state file is
   tampered with by hand (one entry's value edited, its
   hash kept) and the next restart REFUSES to start -
   the process exits nonzero naming tampered_entry, and
   serves nothing. The file is left as it was found.

Run:  python3 packages/synchronization/demo_mesh_state_persistence.py
Standard library only. Localhost only.
"""

import json
import subprocess
import sys
import tempfile
import time
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import node_server  # noqa: E402


class Server:
    def __init__(self, tmp: Path, label: str, node_id: str,
                 extra_args=()):
        self.label = label
        self.node_id = node_id
        self.dir = tmp / label
        self.dir.mkdir(parents=True, exist_ok=True)
        self.port_file = self.dir / "port.txt"
        self.port_file.unlink(missing_ok=True)
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

    def kill(self) -> None:
        """Real death: SIGKILL, no cleanup."""
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=5)

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
    tmp = Path(tempfile.mkdtemp(prefix="pba-demo-mesh-state-"))
    state_path = tmp / "mesh-a.mesh-state"
    servers = []
    try:
        args = ["--mesh-state", str(state_path)]
        a = Server(tmp, "mesh-a", "mesh-a", args)
        servers.append(a)
        health = node_server.fetch_health(a.base_url)
        print(f"1. fresh start: mesh_state_configured="
              f"{health['mesh_state_configured']}, load="
              f"{health['mesh_state_load']}; state file exists="
              f"{state_path.exists()}")
        node_server.mutate(a.base_url, "greeting", "hello")
        node_server.mutate(a.base_url, "mode", "member")
        before = node_server.fetch_state(a.base_url)
        print(f"   two mutations accepted; state file exists="
              f"{state_path.exists()} with "
              f"{len(state_path.read_text(encoding='utf-8').splitlines())} "
              f"entries; state hash {before['state_hash'][:16]}...")

        a.kill()
        a2 = Server(tmp, "mesh-a-restarted", "mesh-a", args)
        servers.append(a2)
        health = node_server.fetch_health(a2.base_url)
        after = node_server.fetch_state(a2.base_url)
        print(f"2. killed with SIGKILL and restarted: load="
              f"{health['mesh_state_load']}; state identical="
              f"{after['state_hash'] == before['state_hash'] and after['state'] == before['state']}")

        b = Server(tmp, "mesh-b", "mesh-b")
        servers.append(b)
        result = node_server.request_sync(b.base_url, a2.base_url)
        b_state = node_server.fetch_state(b.base_url)
        print(f"3. an empty peer synced with the restarted "
              f"process: divergence={result['self']['divergence_detected']}, "
              f"peer state={b_state['state']}")
        b.stop()
        a2.kill()

        lines = state_path.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[0])
        entry["value"] = "forged"
        lines[0] = json.dumps(entry, sort_keys=True)
        state_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log_path = tmp / "mesh-a-tampered.log"
        with log_path.open("w", encoding="utf-8") as log_fh:
            proc = subprocess.Popen(
                [sys.executable, str(HERE / "node_server.py"),
                 "--node-id", "mesh-a", "--port", "0",
                 "--port-file", str(tmp / "tampered.port"),
                 *args],
                stdout=log_fh, stderr=subprocess.STDOUT)
            rc = proc.wait(timeout=20)
        log = log_path.read_text(encoding="utf-8")
        named = "tampered_entry" in log and "MeshStateRefused" in log
        print(f"4. tampered state file: restart exit code={rc}, "
              f"refusal named tampered_entry={named}; nothing served")
        assert rc != 0 and named
        return 0
    finally:
        for server in servers:
            server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
