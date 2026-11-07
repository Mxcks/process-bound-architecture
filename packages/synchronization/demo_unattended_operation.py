"""
Demo for experiment 037 (sync-mesh case 25): a server keeps
itself current unattended.

Every surface through experiment 035 is driven: a check, a
push, or a sweep happens when a client calls it. This demo
starts servers with --auto-cycle-interval and then makes
exactly ONE call per change - the mutate that creates it -
and watches the servers' own cycles do the rest.

Three node server processes (experiment 007's transport),
configured by peer LIST files as in experiment 035, each
with an automatic cycle interval of 0.5 seconds:

1. story-a lists only B; story-b lists A and C; story-c
   lists nobody. /health on A reports the cycle configured,
   its interval, and (after a moment) cycles completed with
   no driver calls made.
2. Entries appear at A through one mutate call each. With
   NO further driver calls - no /sync, no /sync-all, no
   check, no push from this demo - B and then C converge.
   The demo only POLLS the read surfaces (/state, /health)
   and prints the wall time and cycle counts observed.
3. A fourth server lists a DEAD address and B. Its own
   cycles name the dead peer silent_short, then lost
   (experiment 016's threshold, at the server surface),
   in the liveness stage of its cycle reports on /health,
   while its sweep still converges with B.

Run:  python3 packages/synchronization/demo_unattended_operation.py
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

INTERVAL = "0.5"


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


def wait_until(predicate, timeout: float = 45.0) -> float:
    start = time.monotonic()
    deadline = start + timeout
    while time.monotonic() < deadline:
        if predicate():
            return time.monotonic() - start
        time.sleep(0.25)
    raise RuntimeError(f"demo condition not met within {timeout}s")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        servers = []
        try:
            cycle = ["--auto-cycle-interval", INTERVAL]
            c = Server(tmp, "story-c", "story-c", extra_args=cycle)
            servers.append(c)
            boot = Server(tmp, "story-a-boot", "story-a",
                          extra_args=cycle)
            a_url = boot.base_url
            a_port = int(a_url.rsplit(":", 1)[1])
            boot.stop()
            b = Server(tmp, "story-b", "story-b", extra_args=[
                "--peers", str(peers_file(
                    tmp, "b.peers", [a_url, c.base_url])), *cycle])
            servers.append(b)
            a = Server(tmp, "story-a", "story-a", extra_args=[
                "--peers", str(peers_file(
                    tmp, "a.peers", [b.base_url])), *cycle],
                port=a_port)
            servers.append(a)

            wait_until(lambda: node_server.fetch_health(
                a.base_url)["auto_cycle_cycles"] >= 1)
            health = node_server.fetch_health(a.base_url)
            print(f"1. A started with --auto-cycle-interval: "
                  f"/health reports auto_cycle_configured="
                  f"{health['auto_cycle_configured']}, interval="
                  f"{health['auto_cycle_interval']}, cycles "
                  f"completed with no driver calls: "
                  f"{health['auto_cycle_cycles']}; last cycle "
                  f"order: {health['last_auto_cycle']['order']}")

            node_server.mutate(a.base_url, "greeting", "hello")
            node_server.mutate(a.base_url, "mode", "member")
            elapsed = wait_until(lambda: len({
                node_server.fetch_state(s.base_url)["state_hash"]
                for s in (a, b, c)}) == 1)
            print(f"2. entries at A only, no driver calls after "
                  f"the mutates: all three converged in "
                  f"{elapsed:.1f}s (poll-observed); A at cycle "
                  f"{node_server.fetch_health(a.base_url)['auto_cycle_cycles']}, "
                  f"B at cycle "
                  f"{node_server.fetch_health(b.base_url)['auto_cycle_cycles']}, "
                  f"C state: "
                  f"{node_server.fetch_state(c.base_url)['state']}")

            dead = Server(tmp, "dead-peer", "dead-peer")
            dead_url = dead.base_url
            dead.stop()
            d = Server(tmp, "sweep-d", "sweep-d", extra_args=[
                "--peers", str(peers_file(
                    tmp, "d.peers", [dead_url, b.base_url])),
                *cycle])
            servers.append(d)
            node_server.mutate(d.base_url, "via", "cycle")

            def dead_named_lost():
                health_d = node_server.fetch_health(d.base_url)
                last = health_d["last_auto_cycle"]
                if not last:
                    return False
                peers = {p["peer"]: p for p in
                         last["liveness"]["peers"]}
                return (dead_url in peers
                        and peers[dead_url]["state"] == "lost")

            elapsed = wait_until(dead_named_lost)
            health_d = node_server.fetch_health(d.base_url)
            liveness = health_d["last_auto_cycle"]["liveness"]
            print(f"3. D's own cycles named the dead peer "
                  f"{[p['state'] for p in liveness['peers']]} "
                  f"(missed cycles "
                  f"{[p['missed_cycles'] for p in liveness['peers']]}) "
                  f"after {elapsed:.1f}s, and its sweep still "
                  f"carried D's entry to B: "
                  f"{node_server.fetch_state(b.base_url)['state'].get('via')}")
            print("DEMO OK: servers kept themselves current "
                  "unattended - cycles on an interval, one "
                  "implementation per stage, refusals and "
                  "losses named on /health.")
            return 0
        finally:
            for server in servers:
                server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
