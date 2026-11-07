"""
Automated tests for unattended operation: a server runs its
own maintenance cycle on an interval
(tests/test-cases/sync-mesh.md, case 25), built in
experiment 037.

Every surface through case 24 is caller-driven: the snapshot
check (case 13), the pushes (cases 16/19/21), and the
peer-list sweep (case 24) happen only when a driver calls.
This suite asserts the close case 25 states:

- A node server started with --auto-cycle-interval <seconds>
  (minimum 0.2; below it the start refuses naming
  auto_cycle_interval_invalid) runs its maintenance cycle on
  that schedule with no driver calls after start. Each cycle
  runs, in the order case 25 states (experiment 023's
  delivery-before-liveness order first), snapshot_check
  (case 13's check_snapshot, the identical function),
  liveness (a peer observation in experiment 016's
  vocabulary under its threshold), registry_push (case 19's
  paired-push round, the identical round), and sync_all
  (case 24's sweep, the identical sweep). /health reports
  auto_cycle_configured, auto_cycle_interval,
  auto_cycle_cycles, and last_auto_cycle.
- The story over three running servers: peer lists as in
  case 24, cycle ON; one initial mutate at A only; with NO
  further driver calls anywhere, B and then C converge.
  The test polls only read surfaces with a bounded wait
  and reports the cycles and wall time convergence took.
- The revocation leg: the grown pair delivered ONCE to one
  server (case 17) is pushed onward by that server's own
  cycles; the revoked policy refuses 410 at every server,
  a control policy still serves.
- The control: with the cycle OFF, the same story does not
  converge and /health reports 0 cycles.
- Refusal composition: a cycle whose snapshot check is
  refused anchor_stale still runs its later stages (the
  mesh sweep converges in the same cycles), the refusal is
  carried on /health, the cycle count keeps advancing, and
  after the same-signer re-anchor a later cycle reloads
  with no driver calls.
- Shutdown: an in-process node's cycle thread starts,
  advances its counter, stops on request, and is no longer
  alive; spawned servers are cleaned up by terminate, as
  in every server suite.

Timing honesty: cycles run on short REAL intervals (0.5 s)
and every wait is bounded polling of read surfaces with
generous timeouts, never a fixed sleep assumed to be
enough; cycle counts are read from /health, not inferred
from elapsed time.

Run with either:

    python3 -m unittest discover -s packages/synchronization -v -p "test_unattended_operation.py"
    python3 packages/synchronization/test_unattended_operation.py

Standard library only. Localhost only; nothing leaves this
machine.
"""

import base64
import json
import shutil
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
from registration import (  # noqa: E402
    RegistrationChannel,
    sign_library,
    sign_revocation,
)


def _load_registry_anchor():
    import importlib.util
    anchor_file = HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_unattended_test_registry_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


write_anchor = _load_registry_anchor().write_anchor

SERVER = HERE / "node_server.py"
ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
BOB_CODE = b"policy: allow if caller.tier == 'guest'\n"
INTERVAL = "0.5"


class ServerProcess:
    """One node_server.py child process (plaintext), reliably
    cleaned up. Mirrors test_peer_list_sync_all.py's helper,
    with an explicit port for the boot trick."""

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
                try:
                    port = int(self.port_file.read_text(
                        encoding="utf-8").strip())
                except ValueError:
                    time.sleep(0.05)
                    continue
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


def wait_until(predicate, timeout: float = 45.0, interval: float = 0.25):
    """Bounded polling of a read-only predicate. Returns the
    elapsed seconds when it first holds; raises AssertionError
    with the timeout when it never does."""
    start = time.monotonic()
    deadline = start + timeout
    while time.monotonic() < deadline:
        if predicate():
            return time.monotonic() - start
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


class AutoCycleSetup(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _start(self, label, node_id, args=(), port=0):
        server = ServerProcess(self, self.tmp, label, node_id,
                               list(args), port=port)
        server.wait_ready()
        return server

    def _peers_file(self, name, urls):
        path = self.tmp / name
        path.write_text("# peers\n" + "\n".join(urls) + "\n",
                        encoding="utf-8")
        return path


class HealthAndStartTests(AutoCycleSetup):

    # Case 25: /health carries the cycle standing in both
    # configurations; with the cycle on, cycles complete with
    # no driver calls and the last cycle report carries the
    # stated order and all four stage reports.
    def test_health_reports_cycle_standing_on_and_off(self):
        off = self._start("off-a", "off-a")
        health = node_server.fetch_health(off.base_url)
        self.assertFalse(health["auto_cycle_configured"])
        self.assertIsNone(health["auto_cycle_interval"])
        self.assertEqual(health["auto_cycle_cycles"], 0)
        self.assertIsNone(health["last_auto_cycle"])

        on = self._start("on-a", "on-a",
                         ["--auto-cycle-interval", INTERVAL])
        health = node_server.fetch_health(on.base_url)
        self.assertTrue(health["auto_cycle_configured"])
        self.assertEqual(health["auto_cycle_interval"], 0.5)

        elapsed = wait_until(
            lambda: node_server.fetch_health(
                on.base_url)["auto_cycle_cycles"] >= 2,
            timeout=20)
        health = node_server.fetch_health(on.base_url)
        last = health["last_auto_cycle"]
        self.assertIsNotNone(last)
        self.assertEqual(last["order"],
                         ["snapshot_check", "liveness",
                          "registry_push", "sync_all"])
        self.assertEqual(last["snapshot_check"]["status"], "no_snapshot")
        self.assertEqual(last["liveness"]["status"], "no_peers")
        self.assertEqual(last["registry_push"]["status"], "no_snapshot")
        self.assertEqual(last["sync_all"]["status"], "no_peers")
        self.assertGreaterEqual(health["auto_cycle_cycles"], 2)
        # The off server is unchanged through its life.
        health_off = node_server.fetch_health(off.base_url)
        self.assertEqual(health_off["auto_cycle_cycles"], 0)
        self.assertIsNone(health_off["last_auto_cycle"])
        print(f"AUTO CYCLE health: on-server completed "
              f"{health['auto_cycle_cycles']} cycles unattended "
              f"in {elapsed:.1f}s of polling; order and stage "
              f"reports carried on /health; off-server at 0")

    # Case 25, refused start: an interval below the stated
    # minimum refuses the start naming
    # auto_cycle_interval_invalid and serves nothing.
    def test_interval_below_minimum_refuses_the_start(self):
        port_file = self.tmp / "bad.port"
        log_path = self.tmp / "bad.log"
        with log_path.open("w", encoding="utf-8") as log_fh:
            proc = subprocess.Popen(
                [sys.executable, str(SERVER),
                 "--node-id", "bad-auto",
                 "--port", "0",
                 "--port-file", str(port_file),
                 "--auto-cycle-interval", "0.1"],
                stdout=log_fh, stderr=subprocess.STDOUT)
            rc = proc.wait(timeout=15)
        self.assertNotEqual(rc, 0)
        self.assertFalse(port_file.exists())
        self.assertIn("auto_cycle_interval_invalid",
                      log_path.read_text(encoding="utf-8"))
        print("AUTO CYCLE refused start: an interval below "
              "the 0.2s minimum exits nonzero naming "
              "auto_cycle_interval_invalid, serving nothing")

    # Case 25, shutdown: an in-process node's cycle thread
    # advances the counter, stops on request, and is no
    # longer alive afterward; the count is stable after stop.
    def test_inprocess_cycle_thread_starts_and_stops(self):
        node = node_server.NetworkNode(
            node_id="inproc-auto", auto_cycle_interval=0.3)
        self.addCleanup(node.stop_auto_cycle)
        self.assertTrue(node.auto_cycle_configured)
        node.start_auto_cycle()
        wait_until(lambda: node.auto_cycle_cycles >= 2, timeout=10)
        node.stop_auto_cycle()
        self.assertIsNotNone(node._auto_cycle_thread)
        self.assertFalse(node._auto_cycle_thread.is_alive())
        stopped_at = node.auto_cycle_cycles
        time.sleep(0.8)
        self.assertEqual(node.auto_cycle_cycles, stopped_at)
        print(f"AUTO CYCLE shutdown: in-process thread ran "
              f"{stopped_at} cycles, stopped on request, and "
              f"is not alive; the count is stable after stop")


class ConvergenceTests(AutoCycleSetup):

    # Case 25, the story: three servers, cycle ON, lists as
    # in case 24. One mutate at A; NO further driver calls;
    # B and C converge through their own cycles.
    def test_three_servers_converge_unattended(self):
        c = self._start("auto-c", "auto-c",
                        ["--auto-cycle-interval", INTERVAL])
        boot = self._start("auto-a-boot", "auto-a",
                           ["--auto-cycle-interval", INTERVAL])
        a_url = boot.base_url
        a_port = int(a_url.rsplit(":", 1)[1])
        boot.stop()
        b_peers = self._peers_file("b.peers", [a_url, c.base_url])
        b = self._start("auto-b", "auto-b",
                        ["--peers", str(b_peers),
                         "--auto-cycle-interval", INTERVAL])
        a_peers = self._peers_file("a.peers", [b.base_url])
        a = self._start("auto-a", "auto-a",
                        ["--peers", str(a_peers),
                         "--auto-cycle-interval", INTERVAL],
                        port=a_port)
        self.assertEqual(a.base_url, a_url)

        # Base state at A, carried unattended (setup uses the
        # same unattended path the story asserts).
        node_server.mutate(a.base_url, "greeting", "hello")
        wait_until(lambda: len({
            node_server.fetch_state(s.base_url)["state_hash"]
            for s in (a, b, c)}) == 1, timeout=45)

        # The story: new entries at A only, one initial call.
        node_server.mutate(a.base_url, "level", "3")
        node_server.mutate(a.base_url, "region", "north")
        self.assertNotIn(
            "level", node_server.fetch_state(c.base_url)["state"])

        started = time.monotonic()
        elapsed = wait_until(lambda: len({
            node_server.fetch_state(s.base_url)["state_hash"]
            for s in (a, b, c)}) == 1
            and node_server.fetch_state(
                c.base_url)["state"].get("level") == "3",
            timeout=45)
        wall = time.monotonic() - started
        health_b = node_server.fetch_health(b.base_url)
        health_a = node_server.fetch_health(a.base_url)
        self.assertEqual(
            node_server.fetch_state(c.base_url)["state"],
            {"greeting": "hello", "level": "3", "region": "north"})
        print(f"AUTO CYCLE convergence: B and C converged to "
              f"A with no driver calls after the mutate; wall "
              f"{wall:.1f}s (poll-observed), A at cycle "
              f"{health_a['auto_cycle_cycles']}, B at cycle "
              f"{health_b['auto_cycle_cycles']} when observed")

    # Case 25, the control: cycle OFF, lists configured, the
    # same wait does NOT converge, and /health reports the
    # cycle unconfigured with 0 cycles.
    def test_cycle_off_control_does_not_converge(self):
        b = self._start("ctl-b", "ctl-b")
        a_peers = self._peers_file("ctl-a.peers", [b.base_url])
        a = self._start("ctl-a", "ctl-a", ["--peers", str(a_peers)])
        node_server.mutate(a.base_url, "greeting", "hello")
        time.sleep(3.0)
        self.assertNotIn(
            "greeting", node_server.fetch_state(b.base_url)["state"])
        health = node_server.fetch_health(a.base_url)
        self.assertFalse(health["auto_cycle_configured"])
        self.assertEqual(health["auto_cycle_cycles"], 0)
        self.assertIsNone(health["last_auto_cycle"])
        print("AUTO CYCLE control: with the cycle off, B "
              "still lacks A's entry after 3s and A reports "
              "0 cycles - the cycle did the story's work")


class RegistrySetup(AutoCycleSetup):
    """Alice's and bob's genuine registry through the real
    Gate 1 channel, as in test_paired_push."""

    def setUp(self):
        super().setUp()
        tmp = self.tmp
        central = tmp / "central"
        self.registry_log = central / "registry.jsonl"
        self.channel = RegistrationChannel(
            self.registry_log, central / "audit.jsonl")
        self.alice_secret = self.channel.enroll_user("alice")
        self.bob_secret = self.channel.enroll_user("bob")
        self.alice_record, created = self.channel.register(
            author="alice", name="member-access", version="1.0.0",
            code=ALICE_CODE,
            signature=sign_library(self.alice_secret, "alice",
                                   "member-access", "1.0.0", ALICE_CODE))
        assert created
        self.bob_record, created = self.channel.register(
            author="bob", name="guest-access", version="1.0.0",
            code=BOB_CODE,
            signature=sign_library(self.bob_secret, "bob",
                                   "guest-access", "1.0.0", BOB_CODE))
        assert created
        self.payloads_file = tmp / "payloads.json"
        self.payloads_file.write_text(json.dumps({
            self.alice_record.policy_hash: ALICE_CODE.hex(),
            self.bob_record.policy_hash: BOB_CODE.hex(),
        }), encoding="utf-8")

    def _anchor_path(self, name):
        return self.tmp / f"{name}-outside" / "registry-anchor.json"

    def _secret_file(self, name, secret):
        path = self._anchor_path(name).parent / "anchor-secret.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secret, encoding="utf-8")
        return path

    def _signed_anchor_for(self, *names):
        for name in names:
            write_anchor(self.registry_log, self._anchor_path(name),
                         signer="alice", secret_hex=self.alice_secret)

    def _snapshot_copy(self, name):
        dest_dir = self.tmp / f"{name}-dir"
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / "snapshot.jsonl"
        shutil.copyfile(self.registry_log, dest)
        return dest

    def _server_args(self, name, snapshot, extra=()):
        args = [
            "--snapshot", str(snapshot),
            "--payloads", str(self.payloads_file),
            "--serve-policies",
            "--anchor", str(self._anchor_path(name)),
            "--anchor-signer", "alice",
            "--anchor-secret-file", str(self._secret_file(
                name, self.alice_secret)),
            "--auto-cycle-interval", INTERVAL,
        ]
        args += list(extra)
        return args

    def _revoke_alice(self):
        revocation, created = self.channel.revoke(
            author="alice",
            policy_hash=self.alice_record.policy_hash,
            signature=sign_revocation(self.alice_secret,
                                      self.alice_record.policy_hash))
        assert created

    def _entries_of(self, path):
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def _anchor_of(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def _policy_status(self, base_url, record):
        status, _body = node_server.fetch_policy(
            base_url, record.policy_hash)
        return status

    def _assert_serves(self, base_url):
        status, body = node_server.fetch_policy(
            base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), ALICE_CODE)

    def _assert_revoked(self, base_url):
        status, body = node_server.fetch_policy(
            base_url, self.alice_record.policy_hash)
        self.assertEqual(status, 410)
        self.assertEqual(body["error"], "policy_revoked")
        status, body = node_server.fetch_policy(
            base_url, self.bob_record.policy_hash)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(body["code_base64"]), BOB_CODE)


class RevocationPropagationTests(RegistrySetup):

    # Case 25, the revocation leg: pair delivered ONCE to A;
    # no further driver calls; B flips to 410 through A's
    # automatic registry pushes.
    def test_revocation_propagates_unattended(self):
        # Mutual peer lists via the boot trick.
        boot = self._start("rev-a-boot", "rev-a",
                           ["--auto-cycle-interval", INTERVAL])
        a_url = boot.base_url
        a_port = int(a_url.rsplit(":", 1)[1])
        boot.stop()
        self._signed_anchor_for("rev-a", "rev-b")
        snap_b = self._snapshot_copy("rev-b")
        b_peers = self._peers_file("rev-b.peers", [a_url])
        b = self._start("rev-b", "rev-b",
                        self._server_args(
                            "rev-b", snap_b,
                            extra=["--peers", str(b_peers)]))
        snap_a = self._snapshot_copy("rev-a")
        a_peers = self._peers_file("rev-a.peers", [b.base_url])
        a = self._start("rev-a", "rev-a",
                        self._server_args(
                            "rev-a", snap_a,
                            extra=["--peers", str(a_peers)]),
                        port=a_port)
        self.assertEqual(a.base_url, a_url)
        self._assert_serves(a.base_url)
        self._assert_serves(b.base_url)

        # Revoke; lifecycle for A alone; pair delivered ONCE
        # to A - the last driver call in this leg.
        self._revoke_alice()
        self._signed_anchor_for("rev-a")
        report = node_server.deliver_anchor(
            a.base_url, self._anchor_of(self._anchor_path("rev-a")),
            self._entries_of(self.registry_log))
        self.assertEqual(report["status"], "delivered")
        self._assert_revoked(a.base_url)
        self._assert_serves(b.base_url)

        started = time.monotonic()
        elapsed = wait_until(
            lambda: self._policy_status(
                b.base_url, self.alice_record) == 410,
            timeout=45)
        wall = time.monotonic() - started
        self._assert_revoked(b.base_url)
        self._assert_revoked(a.base_url)
        health_a = node_server.fetch_health(a.base_url)
        health_b = node_server.fetch_health(b.base_url)
        self.assertEqual(health_b["snapshot_entries"], 3)
        print(f"AUTO CYCLE revocation: B flipped to 410 with "
              f"no driver calls after the single paired "
              f"delivery to A; wall {wall:.1f}s (poll-observed), "
              f"A at cycle {health_a['auto_cycle_cycles']}, B "
              f"at cycle {health_b['auto_cycle_cycles']}")


class RefusedCycleTests(RegistrySetup):

    # Case 25, refusal composition: growth into the snapshot
    # file without the re-anchor is refused anchor_stale by
    # the automatic cycles; the mesh stage of the same cycles
    # still converges; the count keeps advancing; after the
    # re-anchor a later cycle reloads unattended.
    def test_refused_snapshot_cycle_still_runs_and_recovers(self):
        peer = self._start("ref-peer", "ref-peer",
                           ["--auto-cycle-interval", INTERVAL])
        self._signed_anchor_for("ref-s")
        snap_s = self._snapshot_copy("ref-s")
        peers = self._peers_file("ref-s.peers", [peer.base_url])
        s = self._start("ref-s", "ref-s",
                        self._server_args(
                            "ref-s", snap_s,
                            extra=["--peers", str(peers)]))
        self._assert_serves(s.base_url)

        # A mesh entry at the peer, and the revocation growth
        # delivered into S's snapshot FILE with no re-anchor.
        node_server.mutate(peer.base_url, "mood", "calm")
        self._revoke_alice()
        shutil.copyfile(self.registry_log, snap_s)

        def refused_and_sweeping():
            health = node_server.fetch_health(s.base_url)
            last = health["last_auto_cycle"]
            if not last:
                return False
            return (
                last["snapshot_check"]["status"] == "reload_refused"
                and last["snapshot_check"]["reason"] == "anchor_stale"
                and health["auto_cycle_cycles"] >= 2
                and node_server.fetch_state(
                    s.base_url)["state"].get("mood") == "calm")

        elapsed = wait_until(refused_and_sweeping, timeout=45)
        health = node_server.fetch_health(s.base_url)
        self.assertEqual(
            health["last_snapshot_check"]["reason"], "anchor_stale")
        cycles_at_refusal = health["auto_cycle_cycles"]
        # The previous view still serves: the stale window.
        self._assert_serves(s.base_url)
        print(f"AUTO CYCLE refusal: snapshot stage refused "
              f"anchor_stale across {cycles_at_refusal} cycles "
              f"while the same cycles' sweeps carried the "
              f"peer's mesh entry in; previous view serving")

        # Fix the cause outside the process (the re-anchor),
        # then NO driver calls: a later cycle reloads.
        self._signed_anchor_for("ref-s")
        elapsed2 = wait_until(
            lambda: self._policy_status(
                s.base_url, self.alice_record) == 410,
            timeout=45)
        self._assert_revoked(s.base_url)
        # The 410 flip is visible the moment the reloading
        # cycle's snapshot stage stands, mid-cycle; the cycle
        # counter advances when that cycle COMPLETES. Wait
        # for the completion before comparing counts.
        wait_until(
            lambda: node_server.fetch_health(
                s.base_url)["auto_cycle_cycles"] > cycles_at_refusal,
            timeout=45)
        health = node_server.fetch_health(s.base_url)
        self.assertGreater(health["auto_cycle_cycles"],
                           cycles_at_refusal)
        print(f"AUTO CYCLE refusal recovery: after the "
              f"same-signer re-anchor, a later automatic cycle "
              f"reloaded unattended and the policy now refuses "
              f"410; cycles now {health['auto_cycle_cycles']}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
