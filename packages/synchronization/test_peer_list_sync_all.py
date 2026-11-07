"""
Automated tests for a configured peer list with a driven
sync-all (tests/test-cases/sync-mesh.md, case 24), built in
experiment 035.

Cases 4 and 7 networked the mesh one pair at a time: every
/sync call names the peer's address, and every networked
experiment since has stated the same remaining transport gap -
no discovery. A node holds no standing notion of who its
peers are, so converging a set takes an outside driver that
knows every address. This suite asserts the close case 24
states:

- A node server started with --peers <path> (a plain-text
  list, one peer base URL per line, '#' comments and blank
  lines ignored, trailing slashes stripped, exact duplicates
  dropped) holds the parsed list as standing configuration:
  /health reports peers_configured and the peers list itself,
  beside last_sync_all.
- A peers file that cannot be read, or a content line that
  is not an http(s) base URL, refuses the start naming
  peers_unreadable, before anything is served - the
  crl_unreadable / mesh_state_unreadable family discipline.
- POST /sync-all sweeps the configured peers IN LIST ORDER,
  each round the identical per-peer sync /sync performs (one
  code path, two callers), and answers 200 with one result
  per peer classified as converged, refused (under the
  refusal's own name), unreachable, transport_failed, or
  self; one peer's outcome never stops the sweep, and the
  whole report is what /health then carries as last_sync_all.
- The story over three running servers: A listing only B,
  B listing A and C, all standing on one base state; new
  entries at A only; one sweep at A converges A with B (C
  provably lacks the entries), one sweep at B carries them
  to C; no driver named an address at call time, and the
  final state hashes are equal on all three.
- With mesh anchors configured (case 15), a listed peer
  whose state was rewritten is refused in its result under
  the anchor's own name - anchor_mismatch while the sweeper
  holds nothing of that origin - and the rewritten state is
  adopted by no process through the sweep; the honest peer
  in the same list still converges. Honest growth is
  refused as anchor_stale through the sweep and converges
  after re-anchoring. (Once the sweeper holds the genuine
  origin chain, the same rewritten peer is refused one
  layer earlier, as Gate 3's forked_entry: case 15's stated
  order, Gate 3 first, consultation second.)
- A peer entry naming the server itself (its own bound
  base URL) is skipped and reported as self, in order.
- A server started without --peers behaves exactly as
  before: /health reports peers_configured false and peers
  null, the sweep answers no_peers with an empty result
  list, and /sync is unchanged.

Process cleanup is part of the contract, as in experiments
007-034: every spawned server is registered with addCleanup
the moment it starts. No credentials or secrets are used:
the suite is plaintext, at localhost prototype scale, like
experiment 007's.

Run with either:

    python3 -m unittest discover -s packages/synchronization -v -p "test_peer_list_sync_all.py"
    python3 packages/synchronization/test_peer_list_sync_all.py

Standard library only. Localhost only; nothing leaves this
machine.
"""

import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "policy-library"))
sys.path.insert(0, str(HERE.parent / "registration"))

import node_server  # noqa: E402
from sync_mesh import SyncNode  # noqa: E402


def _load_mesh_anchor():
    """Load experiment 012's MESH anchor module by file path
    under a private name, as in test_mesh_anchor_wire: two
    packages ship an anchor.py, so a plain import may resolve
    to the wrong one."""
    import importlib.util
    anchor_file = HERE / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_peer_list_sync_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


write_mesh_anchor = _load_mesh_anchor().write_mesh_anchor

SERVER = HERE / "node_server.py"


class ServerProcess:
    """One node_server.py child process (plaintext), reliably
    cleaned up. Mirrors test_mesh_anchor_wire.py's helper."""

    def __init__(self, test, workdir: Path, label: str, node_id: str,
                 args: list[str], port: int = 0):
        self.label = label
        self.node_id = node_id
        self.port_file = workdir / f"{label}.port"
        self.log_path = workdir / f"{label}.log"
        self._log_fh = self.log_path.open("w", encoding="utf-8")
        cmd = [
            sys.executable, str(SERVER),
            "--node-id", node_id,
            "--port", str(port),
            "--port-file", str(self.port_file),
        ]
        cmd += args
        self.proc = subprocess.Popen(
            cmd, stdout=self._log_fh, stderr=subprocess.STDOUT)
        test.addCleanup(self.stop)
        self.base_url = None

    def wait_ready(self, timeout: float = 20.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"server {self.label} exited early "
                    f"rc={self.proc.returncode}; log:\n{self.log_text()}")
            if self.port_file.exists():
                port = int(self.port_file.read_text(encoding="utf-8").strip())
                self.base_url = f"http://127.0.0.1:{port}"
                try:
                    node_server.fetch_health(self.base_url)
                    return self.base_url
                except (AssertionError, urllib.error.URLError, OSError):
                    pass
            time.sleep(0.05)
        raise AssertionError(f"server {self.label} not ready in {timeout}s")

    def log_text(self) -> str:
        self._log_fh.flush()
        return self.log_path.read_text(encoding="utf-8")

    def stop(self) -> None:
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


def view_of(entries) -> SyncNode:
    """An in-process node holding exactly `entries`, rebuilt
    through experiment 013's restore primitive: the view a
    mesh anchor is written from."""
    view = SyncNode("mesh-view")
    view.restore_to_entries(entries)
    return view


class PeerListSetup(unittest.TestCase):
    """Shared setup: a temp dir and the server driver."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _start(self, label, node_id, args=(), port=0):
        server = ServerProcess(self, self.tmp, label, node_id,
                               list(args), port=port)
        server.wait_ready()
        return server

    def _peers_file(self, name, urls, extra_lines=()):
        path = self.tmp / name
        lines = ["# peer list written by the test", ""]
        lines += list(urls)
        lines += list(extra_lines)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


class PeerListSyncAllTests(PeerListSetup):

    # Case 24: the list is standing configuration, reported
    # whole on /health beside a null last_sync_all until the
    # first sweep is driven. Comments, blank lines, and a
    # duplicate line in the file are parsed away; a trailing
    # slash on a listed URL is stripped.
    def test_health_reports_configured_peer_list(self):
        b = self._start("peer-b", "peer-b")
        c = self._start("peer-c", "peer-c")
        peers = self._peers_file(
            "a.peers", [b.base_url + "/", c.base_url],
            extra_lines=[b.base_url])  # exact duplicate: dropped
        a = self._start("peer-a", "peer-a", ["--peers", str(peers)])

        health = node_server.fetch_health(a.base_url)
        self.assertTrue(health["peers_configured"])
        self.assertEqual(health["peers"], [b.base_url, c.base_url])
        self.assertIsNone(health["last_sync_all"])
        for plain in (b, c):
            plain_health = node_server.fetch_health(plain.base_url)
            self.assertFalse(plain_health["peers_configured"])
            self.assertIsNone(plain_health["peers"])
        print("PEER LIST health: --peers parsed at startup "
              "(comments, blank lines, duplicate, trailing "
              "slash handled) and reported whole on /health")

    # Case 24, the story: three servers, A listing only B,
    # B listing A and C, all standing on one base state.
    # New entries appear at A only; one sweep at A converges
    # A with B while C provably lacks the entries; one sweep
    # at B carries them to C. No driver named an address at
    # call time, and the final hashes are equal everywhere.
    def test_three_servers_converge_through_their_lists(self):
        c = self._start("story-c", "story-c")
        boot = self._start("story-a-boot", "story-a")
        a_url = boot.base_url
        a_port = int(a_url.rsplit(":", 1)[1])
        boot.stop()
        b_peers = self._peers_file("b.peers", [a_url, c.base_url])
        b = self._start("story-b", "story-b", ["--peers", str(b_peers)])
        a_peers = self._peers_file("a.peers", [b.base_url])
        # A restarts on the boot process's port, so the URL B
        # was configured with is A's URL: the lists are mutual.
        a = self._start("story-a", "story-a", ["--peers", str(a_peers)],
                        port=a_port)
        self.assertEqual(a.base_url, a_url)

        # Base state, converged through the lists themselves.
        node_server.mutate(a.base_url, "greeting", "hello")
        node_server.mutate(a.base_url, "mode", "member")
        report = node_server.request_sync_all(a.base_url)
        self.assertEqual(report["status"], "converged")
        self.assertEqual([r["peer"] for r in report["results"]],
                         [b.base_url])
        self.assertEqual(report["results"][0]["outcome"], "converged")
        report = node_server.request_sync_all(b.base_url)
        self.assertEqual(report["status"], "converged")
        self.assertEqual([r["outcome"] for r in report["results"]],
                         ["converged", "converged"])
        base_hash = node_server.fetch_state(c.base_url)["state_hash"]
        self.assertEqual(base_hash,
                         node_server.fetch_state(a.base_url)["state_hash"])
        self.assertEqual(base_hash,
                         node_server.fetch_state(b.base_url)["state_hash"])

        # New entries at A only.
        node_server.mutate(a.base_url, "level", "3")
        node_server.mutate(a.base_url, "region", "north")
        self.assertNotIn("level",
                         node_server.fetch_state(b.base_url)["state"])
        self.assertNotIn("level",
                         node_server.fetch_state(c.base_url)["state"])

        # One sweep at A: A and B converge; C still lacks them.
        report = node_server.request_sync_all(a.base_url)
        self.assertEqual(report["status"], "converged")
        self.assertEqual(report["results"][0]["outcome"], "converged")
        self.assertEqual(report["state_hash"],
                         node_server.fetch_state(b.base_url)["state_hash"])
        self.assertIn("level",
                      node_server.fetch_state(b.base_url)["state"])
        self.assertNotIn("level",
                         node_server.fetch_state(c.base_url)["state"])
        print("PEER LIST story: one sweep at A converged A "
              "with B; C provably still lacks the new entries")

        # One sweep at B carries them to C.
        report = node_server.request_sync_all(b.base_url)
        self.assertEqual(report["status"], "converged")
        self.assertEqual([r["peer"] for r in report["results"]],
                         [a.base_url, c.base_url])
        self.assertEqual([r["outcome"] for r in report["results"]],
                         ["converged", "converged"])
        final = node_server.fetch_state(a.base_url)["state_hash"]
        self.assertEqual(final,
                         node_server.fetch_state(b.base_url)["state_hash"])
        self.assertEqual(final,
                         node_server.fetch_state(c.base_url)["state_hash"])
        self.assertEqual(
            node_server.fetch_state(c.base_url)["state"],
            {"greeting": "hello", "mode": "member",
             "level": "3", "region": "north"})
        print("PEER LIST story: one sweep at B carried the "
              "entries to C; all three state hashes are equal, "
              "and no driver named an address at call time")

    # Case 24, independence: a dead first peer is reported
    # unreachable and the sweep still attempts and converges
    # the second; the top status is partial, and /health
    # carries the whole sweep as last_sync_all.
    def test_unreachable_peer_does_not_stop_the_sweep(self):
        b = self._start("live-b", "live-b")
        dead = self._start("dead-peer", "dead-peer")
        dead_url = dead.base_url
        dead.stop()
        peers = self._peers_file("a.peers", [dead_url, b.base_url])
        a = self._start("sweep-a", "sweep-a", ["--peers", str(peers)])

        node_server.mutate(a.base_url, "greeting", "hello")
        report = node_server.request_sync_all(a.base_url)
        self.assertEqual(report["status"], "partial")
        self.assertEqual([r["peer"] for r in report["results"]],
                         [dead_url, b.base_url])
        self.assertEqual(report["results"][0]["outcome"], "unreachable")
        self.assertEqual(report["results"][1]["outcome"], "converged")
        self.assertEqual(node_server.fetch_state(b.base_url)["state"],
                         {"greeting": "hello"})
        health = node_server.fetch_health(a.base_url)
        self.assertEqual(
            [r["outcome"] for r in health["last_sync_all"]["results"]],
            ["unreachable", "converged"])
        self.assertEqual(health["last_sync_all"]["status"], "partial")
        print("PEER LIST independence: the dead peer is "
              "reported unreachable, the live peer converged in "
              "the same sweep, and /health carries the sweep")

    # Case 24, configuration edge: a server started without
    # --peers reports itself unconfigured, the sweep answers
    # no_peers with an empty result list, and /sync is the
    # surface it always was.
    def test_no_peer_list_behaves_exactly_as_before(self):
        a = self._start("plain-a", "plain-a")
        b = self._start("plain-b", "plain-b")
        health = node_server.fetch_health(a.base_url)
        self.assertFalse(health["peers_configured"])
        self.assertIsNone(health["peers"])

        report = node_server.request_sync_all(a.base_url)
        self.assertEqual(report["status"], "no_peers")
        self.assertEqual(report["results"], [])

        node_server.mutate(a.base_url, "greeting", "hello")
        result = node_server.request_sync(a.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        self.assertEqual(node_server.fetch_state(b.base_url)["state"],
                         {"greeting": "hello"})
        print("PEER LIST no_peers: an unconfigured server "
              "answers the sweep no_peers and /sync is unchanged")

    # Case 24, self: a list naming the server's own bound
    # address reports that entry as self, in order, makes no
    # contact with it, and still syncs the real peer.
    def test_self_entry_is_skipped_and_reported(self):
        b = self._start("other-b", "other-b")
        boot = self._start("self-a-boot", "self-a")
        own_url = boot.base_url
        own_port = int(own_url.rsplit(":", 1)[1])
        boot.stop()
        peers = self._peers_file("a.peers", [own_url, b.base_url])
        # Restarted on the boot process's port: the list's
        # first entry is this process's own bound address.
        a = self._start("self-a", "self-a", ["--peers", str(peers)],
                        port=own_port)
        self.assertEqual(a.base_url, own_url)

        node_server.mutate(a.base_url, "greeting", "hello")
        report = node_server.request_sync_all(a.base_url)
        self.assertEqual([r["peer"] for r in report["results"]],
                         [own_url, b.base_url])
        self.assertEqual(report["results"][0]["outcome"], "self")
        self.assertIsNone(report["results"][0]["reason"])
        self.assertEqual(report["results"][1]["outcome"], "converged")
        self.assertEqual(report["status"], "converged")
        self.assertEqual(node_server.fetch_state(b.base_url)["state"],
                         {"greeting": "hello"})
        print("PEER LIST self: the server's own address is "
              "skipped and reported as self; the real peer "
              "converged in the same sweep")

    # Case 24, refused start: a content line that is not an
    # http(s) URL, and a peers path that does not exist, each
    # exit nonzero naming peers_unreadable and serve nothing
    # (no port file is ever written).
    def test_bad_peer_list_refuses_the_start(self):
        bad = self.tmp / "bad.peers"
        bad.write_text("http://127.0.0.1:9\nnot-a-url\n",
                       encoding="utf-8")
        for label, peers_arg in (
                ("garbage", bad),
                ("missing", self.tmp / "no-such.peers")):
            port_file = self.tmp / f"{label}.port"
            log_path = self.tmp / f"{label}.log"
            with log_path.open("w", encoding="utf-8") as log_fh:
                proc = subprocess.Popen(
                    [sys.executable, str(SERVER),
                     "--node-id", f"bad-{label}",
                     "--port", "0",
                     "--port-file", str(port_file),
                     "--peers", str(peers_arg)],
                    stdout=log_fh, stderr=subprocess.STDOUT)
                rc = proc.wait(timeout=15)
            self.assertNotEqual(rc, 0)
            self.assertFalse(port_file.exists())
            log = log_path.read_text(encoding="utf-8")
            self.assertIn("peers_unreadable", log)
        print("PEER LIST peers_unreadable: a garbage line and "
              "a missing file each refuse the start by name, "
              "serving nothing")


class AnchoredPeerListTests(PeerListSetup):

    def setUp(self):
        super().setUp()
        self.anchor_path = self.tmp / "outside" / "mesh-anchor.json"

    def _write_anchor_from(self, holder):
        entries = node_server.fetch_entries(holder.base_url)
        return write_mesh_anchor(view_of(entries), self.anchor_path)

    # Case 24, the anchored leg: the sweeper X holds nothing,
    # lists a rewritten peer first and an honest peer second,
    # both under one mesh anchor over the honest state. The
    # sweep refuses the rewrite as anchor_mismatch, converges
    # the honest peer, and the rewrite is adopted by nobody.
    # Honest growth is then refused as anchor_stale through
    # the sweep - the rewritten peer now refused one layer
    # earlier, as forked_entry, because X holds the genuine
    # origin chain (case 15's order: Gate 3 first) - and
    # after re-anchoring the same sweep converges the honest
    # leg.
    def test_anchored_sweep_refuses_rewrite_and_stale_then_converges(self):
        ha = self._start("mesh-a", "mesh-a")
        hb = self._start("mesh-b", "mesh-b")
        node_server.mutate(ha.base_url, "greeting", "hello")
        node_server.mutate(ha.base_url, "mode", "member")
        node_server.mutate(hb.base_url, "region", "north")
        result = node_server.request_sync(ha.base_url, hb.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        honest_hash = node_server.fetch_state(hb.base_url)["state_hash"]
        self._write_anchor_from(hb)
        ha.stop()

        attacker = self._start("mesh-attacker", "mesh-a")
        node_server.mutate(attacker.base_url, "greeting", "pwned")
        node_server.mutate(attacker.base_url, "mode", "admin")

        peers = self._peers_file(
            "x.peers", [attacker.base_url, hb.base_url])
        x = self._start("mesh-x", "mesh-x",
                        ["--mesh-anchor", str(self.anchor_path),
                         "--peers", str(peers)])

        report = node_server.request_sync_all(x.base_url)
        self.assertEqual(report["status"], "partial")
        self.assertEqual([r["outcome"] for r in report["results"]],
                         ["refused", "converged"])
        self.assertEqual(report["results"][0]["reason"],
                         "anchor_mismatch")
        self.assertEqual(report["results"][0]["self"]["accepted"], 0)
        state = node_server.fetch_state(x.base_url)
        self.assertEqual(state["state_hash"], honest_hash)
        self.assertEqual(state["state"],
                         {"greeting": "hello", "mode": "member",
                          "region": "north"})
        self.assertEqual(node_server.fetch_state(hb.base_url)["state"],
                         state["state"])
        health = node_server.fetch_health(x.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        print("PEER LIST anchored: the sweep refused the "
              "rewritten peer as anchor_mismatch, converged "
              "the honest peer, and the rewrite was adopted "
              "by nobody")

        node_server.mutate(hb.base_url, "level", "3")
        report = node_server.request_sync_all(x.base_url)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["results"][0]["outcome"], "refused")
        self.assertEqual(report["results"][0]["reason"], "forked_entry")
        self.assertEqual(report["results"][1]["outcome"], "refused")
        self.assertEqual(report["results"][1]["reason"], "anchor_stale")
        self.assertEqual(node_server.fetch_state(x.base_url)["state_hash"],
                         honest_hash)
        print("PEER LIST anchored: honest growth refused "
              "through the sweep as anchor_stale; the rewrite "
              "now refused one layer earlier, as forked_entry")

        self._write_anchor_from(hb)
        report = node_server.request_sync_all(x.base_url)
        self.assertEqual(report["results"][0]["outcome"], "refused")
        self.assertEqual(report["results"][1]["outcome"], "converged")
        self.assertEqual(
            node_server.fetch_state(x.base_url)["state_hash"],
            node_server.fetch_state(hb.base_url)["state_hash"])
        health = node_server.fetch_health(x.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        self.assertNotIn(
            "pwned",
            node_server.fetch_state(hb.base_url)["state"].values())
        print("PEER LIST anchored: after re-anchoring, the "
              "same sweep converges the honest leg; the "
              "rewritten values are nowhere in the honest set")


if __name__ == "__main__":
    unittest.main(verbosity=2)
