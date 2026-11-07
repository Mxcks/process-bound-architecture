"""
Demo for experiment 035 (sync-mesh case 24): nodes that
know their peers, and one driven sweep that converges a
set.

Every networked surface since experiment 007 has named
the peer's address on every call: converging a set of
servers took an outside driver holding every address and
driving each pair by hand, while the nodes themselves
knew nothing. This demo drives what case 24 builds.

Three node server processes (experiment 007's transport),
configured by peer LIST files, not by call-time addresses:

1. story-c starts with no list. story-b lists A and C.
   story-a lists only B. /health on A reports the
   configured list it holds as standing configuration.
2. Base entries appear at A. One /sync-all at A converges
   A with B; one /sync-all at B carries the base state to
   C. All three state hashes agree.
3. New entries appear at A only. One /sync-all at A: B
   converges, C provably still lacks the entries. One
   /sync-all at B: C converges. The set converged through
   the lists; no driver named an address at call time.
4. A fourth list, on a fresh server, puts a DEAD address
   first and B second: the sweep reports the dead peer
   unreachable and still converges B - one peer's outcome
   never stops the sweep - and /health carries the whole
   sweep as last_sync_all.

Run:  python3 packages/synchronization/demo_peer_list_sync_all.py
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


class Server:
    def __init__(self, tmp: Path, label: str, node_id: str,
                 extra_args=(), port: int = 0):
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
             "--port", str(port), "--port-file", str(self.port_file),
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
                url = f"http://127.0.0.1:{port}"
                try:
                    node_server.fetch_health(url)
                    return url
                except (AssertionError, urllib.error.URLError, OSError):
                    pass
            time.sleep(0.05)
        raise RuntimeError(f"{self.label} not ready in {timeout}s")

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.log_fh.close()


def peers_file(tmp: Path, name: str, urls) -> Path:
    path = tmp / name
    path.write_text("# configured peers\n" + "\n".join(urls) + "\n",
                    encoding="utf-8")
    return path


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        servers = []
        try:
            c = Server(tmp, "story-c", "story-c")
            servers.append(c)
            boot = Server(tmp, "story-a-boot", "story-a")
            a_url = boot.base_url
            a_port = int(a_url.rsplit(":", 1)[1])
            boot.stop()
            b = Server(tmp, "story-b", "story-b", extra_args=[
                "--peers", str(peers_file(
                    tmp, "b.peers", [a_url, c.base_url]))])
            servers.append(b)
            # A restarts on the boot port, so B's list names
            # A's real address: the lists are mutual.
            a = Server(tmp, "story-a", "story-a", extra_args=[
                "--peers", str(peers_file(tmp, "a.peers",
                                          [b.base_url]))],
                port=a_port)
            servers.append(a)

            health = node_server.fetch_health(a.base_url)
            print(f"1. A started with --peers: /health reports "
                  f"peers_configured={health['peers_configured']}, "
                  f"peers={health['peers']}")

            node_server.mutate(a.base_url, "greeting", "hello")
            node_server.mutate(a.base_url, "mode", "member")
            report = node_server.request_sync_all(a.base_url)
            print(f"2. sweep at A: status={report['status']} "
                  f"outcomes={[r['outcome'] for r in report['results']]}")
            report = node_server.request_sync_all(b.base_url)
            hashes = {
                s.node_id: node_server.fetch_state(s.base_url)["state_hash"]
                for s in (a, b, c)}
            print(f"   sweep at B: status={report['status']} "
                  f"outcomes={[r['outcome'] for r in report['results']]}; "
                  f"all three hashes equal: "
                  f"{len(set(hashes.values())) == 1}")

            node_server.mutate(a.base_url, "level", "3")
            node_server.mutate(a.base_url, "region", "north")
            node_server.request_sync_all(a.base_url)
            at_c = node_server.fetch_state(c.base_url)["state"]
            print(f"3. new entries at A, one sweep at A: B holds "
                  f"them, C does not yet (level in C: "
                  f"{'level' in at_c})")
            node_server.request_sync_all(b.base_url)
            final = {
                s.node_id: node_server.fetch_state(s.base_url)["state_hash"]
                for s in (a, b, c)}
            print(f"   one sweep at B: C converged; all three "
                  f"hashes equal: {len(set(final.values())) == 1}; "
                  f"C state: {node_server.fetch_state(c.base_url)['state']}")

            dead = Server(tmp, "dead-peer", "dead-peer")
            dead_url = dead.base_url
            dead.stop()
            d = Server(tmp, "sweep-d", "sweep-d", extra_args=[
                "--peers", str(peers_file(
                    tmp, "d.peers", [dead_url, b.base_url]))])
            servers.append(d)
            node_server.mutate(d.base_url, "via", "sweep")
            report = node_server.request_sync_all(d.base_url)
            print(f"4. dead peer first in the list: "
                  f"status={report['status']} outcomes="
                  f"{[r['outcome'] for r in report['results']]}; "
                  f"B holds the entry anyway: "
                  f"{node_server.fetch_state(b.base_url)['state'].get('via')}")
            health = node_server.fetch_health(d.base_url)
            print(f"   /health last_sync_all status: "
                  f"{health['last_sync_all']['status']}")
            print("DEMO OK: a set converged through peer lists "
                  "alone - startup configuration, driven sweeps, "
                  "per-peer reports, no call-time addresses.")
            return 0
        finally:
            for server in servers:
                server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
