"""
Automated tests for mesh state persistence for server
processes (tests/test-cases/sync-mesh.md, case 22), built
in experiment 033.

Every surface since experiment 007 has run on mesh state
that lives in the server process's memory alone: kill the
process and its unreplicated entries are gone; what
survives a kill is exactly what had already synced to
another process. Case 22 gives the mesh side the durable
record the registry side has had since Gate 1. This suite
asserts the server-side close:

- A node server started with --mesh-state (a startup flag
  in --mesh-anchor's style, independent of --snapshot and
  of --mesh-anchor) persists every accepted mesh change -
  its own mutations through /mutate and the merges its one
  receive accepts - inside the mesh operation itself,
  before the driving response returns, and only when the
  state actually moved. Each write is case 14's
  replace-from-verified-candidate discipline: the full
  entry set serialized to a candidate file in the wire's
  own entry format, the candidate judged by Gate 3's own
  whole-set verification (a scratch node's
  restore_to_entries), and only a candidate that stands
  os.replace'd over the state file.
- A fresh start over no file yet reports the load as
  "fresh" on /health (mesh_state_configured /
  mesh_state_load, beside the mesh anchor fields) and
  creates the file on the first accepted change. A
  restart over the file recovers exactly: same entries,
  same state hash, same materialized state, reported as
  "recovered". Entries that existed only in the killed
  process and never synced anywhere are back after its
  restart - proven by a peer learning them from the
  restarted process over /sync, with no re-mutation.
  Merges persist exactly as own mutations do, and a
  refused receive rewrites nothing, byte for byte.
- The load is judged, never trusted: the file's entries
  are judged by the mesh's own whole-set verification
  (restore_to_entries) before the process serves
  anything. A tampered entry refuses the start as
  tampered_entry, a forked chain as forked_entry, a
  mid-line truncation as mesh_state_unreadable - each a
  MeshStateRefused out of the node's construction, the
  process exiting nonzero naming the refusal, and no
  partially recovered state ever served.
- With a mesh anchor also configured, a RECOVERED state
  is consulted against the anchor before the process
  serves, by the node's own anchor_status()
  classification, and a recovered state the anchor
  refuses refuses the start under the anchor's own
  names: honest growth recovered past the anchor is
  anchor_stale until the grown state is re-anchored, a
  foreign chain recovered under the anchor is
  anchor_mismatch. A first start with no state file yet
  gates on nothing and starts empty exactly as case 15
  left it.
- A server started without --mesh-state behaves exactly
  as today: memory only, a restart empty, /health
  reporting mesh_state_configured false and
  mesh_state_load null.

Process kills here are real: servers are killed with
SIGKILL (no cleanup, no graceful shutdown) and restarted
as new processes over the same state file. Process
cleanup is part of the contract, as in experiments
007-032: every spawned server is registered with
addCleanup the moment it starts, and no leg ever runs
more than two server processes at once. No credential or
secret is committed; this suite needs none.

Run with either:

    python3 -m unittest discover -s packages/synchronization -v -p "test_mesh_state_persistence.py"
    python3 packages/synchronization/test_mesh_state_persistence.py

Standard library only. Localhost only; nothing leaves this
machine.
"""

import json
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
from sync_mesh import SyncNode, build_entry, tamper_entry  # noqa: E402


def _load_mesh_anchor():
    """Load experiment 012's MESH anchor module by file path
    under a private name, as test_mesh_anchor_wire does:
    two packages ship an anchor.py, and this suite puts the
    registration package on sys.path too, so a plain
    `import anchor` may resolve to the wrong one."""
    import importlib.util
    anchor_file = HERE / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_mesh_state_sync_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


write_mesh_anchor = _load_mesh_anchor().write_mesh_anchor

SERVER = HERE / "node_server.py"


class ServerProcess:
    """One node_server.py child process, reliably cleaned
    up. Mirrors test_mesh_anchor_wire.py's helper, with a
    real kill (SIGKILL: death without cleanup) beside the
    graceful stop, because this case is about what
    survives the process dying."""

    def __init__(self, test, workdir: Path, label: str, node_id: str,
                 args: list[str]):
        self.label = label
        self.node_id = node_id
        self.port_file = workdir / f"{label}.port"
        self.log_path = workdir / f"{label}.log"
        self.port_file.unlink(missing_ok=True)
        self._log_fh = self.log_path.open("w", encoding="utf-8")
        cmd = [
            sys.executable, str(SERVER),
            "--node-id", node_id,
            "--port", "0",
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
                except (ValueError, OSError):
                    port = None  # port file mid-write; retry
                if port:
                    self.base_url = f"http://127.0.0.1:{port}"
                    try:
                        node_server.fetch_health(self.base_url)
                        return self.base_url
                    except (AssertionError, urllib.error.URLError,
                            OSError):
                        pass
            time.sleep(0.05)
        raise AssertionError(f"server {self.label} not ready in {timeout}s")

    def log_text(self) -> str:
        self._log_fh.flush()
        return self.log_path.read_text(encoding="utf-8")

    def kill(self) -> None:
        """Real death: SIGKILL, no cleanup handlers, no
        graceful shutdown - the process simply stops
        existing, as in a crash or a kill -9."""
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=5)

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


def read_state_entries(state_path: Path):
    """The persisted entry set, deserialized with the
    wire's own entry decoding - the same reading a
    restarting process performs."""
    entries = []
    for line in state_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(node_server.entry_from_dict(json.loads(line)))
    return entries


def view_of(entries) -> SyncNode:
    """An in-process node holding exactly `entries`,
    rebuilt through experiment 013's restore primitive:
    the view a mesh anchor is written from."""
    view = SyncNode("mesh-view")
    view.restore_to_entries(entries)
    return view


class MeshStatePersistenceSetup(unittest.TestCase):
    """Shared setup: a temp dir and the state paths inside
    it. Mesh-only servers need no registry, no anchor, and
    no credentials except where the anchor gate leg
    configures one."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    # -- helpers -----------------------------------------------------

    def _start(self, label, node_id, args=()):
        server = ServerProcess(self, self.tmp, label, node_id,
                               list(args))
        server.wait_ready()
        return server

    def _start_refused(self, label, node_id, args=()):
        """Start a process that must refuse to start: it
        exits nonzero before serving, naming the refusal
        in its log. Returns the log text."""
        log_path = self.tmp / f"{label}.log"
        port_file = self.tmp / f"{label}.port"
        port_file.unlink(missing_ok=True)
        with log_path.open("w", encoding="utf-8") as log_fh:
            proc = subprocess.Popen(
                [sys.executable, str(SERVER),
                 "--node-id", node_id,
                 "--port", "0",
                 "--port-file", str(port_file), *args],
                stdout=log_fh, stderr=subprocess.STDOUT)
            try:
                rc = proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
                self.fail(f"server {label} did not exit; "
                          f"expected a refused start")
        log = log_path.read_text(encoding="utf-8")
        self.assertNotEqual(rc, 0,
                            f"server {label} started; expected a "
                            f"refused start; log:\n{log}")
        self.assertFalse(port_file.exists(),
                         f"server {label} wrote a port file; a "
                         f"refused start serves nothing")
        return log


class PlaintextMeshStatePersistenceTests(MeshStatePersistenceSetup):

    # Case 22, the fresh start and exact recovery: a state
    # file that does not exist yet is reported "fresh", the
    # file is created on the first accepted change (not at
    # startup), the mutations are on disk in the wire's own
    # entry format before their responses are even read,
    # and after a SIGKILL the restarted process recovers
    # the same entries, the same state hash, and the same
    # materialized state, reported "recovered".
    def test_fresh_start_creates_file_and_recovers_exactly(self):
        state_path = self.tmp / "a.mesh-state"
        a = self._start("persist-a", "persist-a",
                        ["--mesh-state", str(state_path)])
        health = node_server.fetch_health(a.base_url)
        self.assertTrue(health["mesh_state_configured"])
        self.assertEqual(health["mesh_state_load"]["status"], "fresh")
        self.assertEqual(health["mesh_state_load"]["entries"], 0)
        self.assertFalse(state_path.exists())

        node_server.mutate(a.base_url, "greeting", "hello")
        self.assertTrue(state_path.exists())
        node_server.mutate(a.base_url, "mode", "member")
        before = node_server.fetch_state(a.base_url)
        self.assertEqual(len(read_state_entries(state_path)), 2)
        self.assertEqual(
            {e.entry_id for e in read_state_entries(state_path)},
            set(before["entry_ids"]))
        persisted_hash = node_server.fetch_health(a.base_url)["state_hash"]
        self.assertEqual(persisted_hash, before["state_hash"])

        a.kill()
        a2 = self._start("persist-a2", "persist-a",
                         ["--mesh-state", str(state_path)])
        health = node_server.fetch_health(a2.base_url)
        self.assertEqual(health["mesh_state_load"]["status"], "recovered")
        self.assertEqual(health["mesh_state_load"]["entries"], 2)
        self.assertEqual(health["mesh_state_load"]["state_hash"],
                         before["state_hash"])
        after = node_server.fetch_state(a2.base_url)
        self.assertEqual(after["state_hash"], before["state_hash"])
        self.assertEqual(after["state"], before["state"])
        self.assertEqual(after["entry_ids"], before["entry_ids"])
        print("MESH STATE PERSISTENCE: fresh start created "
              "the state file on first change; after SIGKILL "
              "the restart recovered the same 2 entries, "
              "state hash, and materialized state")

    # Case 22, the point of the case: entries that existed
    # ONLY in the killed process - never synced anywhere -
    # are back after its restart, and a peer learns them
    # from the restarted process over /sync with no
    # re-mutation anywhere.
    def test_unreplicated_entries_survive_and_peer_learns_them(self):
        state_path = self.tmp / "solo.mesh-state"
        a = self._start("solo-a", "solo-a",
                        ["--mesh-state", str(state_path)])
        node_server.mutate(a.base_url, "solo-1", "only-here")
        node_server.mutate(a.base_url, "solo-2", "also-only-here")
        solo_hash = node_server.fetch_state(a.base_url)["state_hash"]
        a.kill()

        a2 = self._start("solo-a2", "solo-a",
                         ["--mesh-state", str(state_path)])
        b = self._start("solo-b", "solo-b")
        result = node_server.request_sync(b.base_url, a2.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        b_state = node_server.fetch_state(b.base_url)
        self.assertEqual(b_state["state_hash"], solo_hash)
        self.assertEqual(b_state["state"], {
            "solo-1": "only-here", "solo-2": "also-only-here"})
        self.assertEqual(set(b_state["entry_ids"]),
                         {"solo-a:0", "solo-a:1"})
        print("MESH STATE PERSISTENCE: entries that never "
              "left the killed process were recovered from "
              "its state file and a peer learned them from "
              "the restarted process over /sync")

    # Case 22, merges persist as own mutations do: state
    # gained by syncing is in the file, survives the kill,
    # and is still the state after restart. A refused
    # receive (a tampered entry pushed at the process)
    # rewrites nothing, byte for byte.
    def test_merges_persist_and_refused_receive_rewrites_nothing(self):
        a_state = self.tmp / "merge-a.mesh-state"
        b_state = self.tmp / "merge-b.mesh-state"
        a = self._start("merge-a", "merge-a",
                        ["--mesh-state", str(a_state)])
        b = self._start("merge-b", "merge-b",
                        ["--mesh-state", str(b_state)])
        node_server.mutate(a.base_url, "from-a", "1")
        node_server.mutate(b.base_url, "from-b", "2")
        result = node_server.request_sync(a.base_url, b.base_url)
        self.assertFalse(result["self"]["divergence_detected"])
        merged = node_server.fetch_state(a.base_url)
        self.assertEqual(merged["state"],
                         {"from-a": "1", "from-b": "2"})
        self.assertEqual(len(read_state_entries(a_state)), 2)

        bytes_before = a_state.read_bytes()
        victim = node_server.fetch_entries(b.base_url)[0]
        result = node_server.push_entries(
            a.base_url, [tamper_entry(victim, value="forged")])
        self.assertTrue(result["divergence_detected"])
        self.assertEqual(result["reason"], "tampered_entry")
        self.assertEqual(a_state.read_bytes(), bytes_before)

        a.kill()
        a2 = self._start("merge-a2", "merge-a",
                         ["--mesh-state", str(a_state)])
        after = node_server.fetch_state(a2.base_url)
        self.assertEqual(after["state_hash"], merged["state_hash"])
        self.assertEqual(after["state"], merged["state"])
        print("MESH STATE PERSISTENCE: a merge the process "
              "accepted is in its state file and survives "
              "its kill; a refused receive rewrote nothing")

    # Case 22, the load is judged: an entry edited in the
    # state file (value changed, hash kept - the textbook
    # tamper) refuses the restart under Gate 3's own name,
    # the process exiting nonzero and serving nothing.
    def test_tampered_state_file_refuses_the_start(self):
        state_path = self.tmp / "tamper.mesh-state"
        a = self._start("tamper-a", "tamper-a",
                        ["--mesh-state", str(state_path)])
        node_server.mutate(a.base_url, "greeting", "hello")
        a.stop()

        lines = state_path.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[0])
        entry["value"] = "forged"
        lines[0] = json.dumps(entry, sort_keys=True)
        state_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        bytes_before = state_path.read_bytes()

        log = self._start_refused("tamper-a2", "tamper-a",
                                  ["--mesh-state", str(state_path)])
        self.assertIn("MeshStateRefused", log)
        self.assertIn("tampered_entry", log)
        self.assertEqual(state_path.read_bytes(), bytes_before)
        print("MESH STATE PERSISTENCE refusal: a tampered "
              "entry in the state file refused the start as "
              "tampered_entry; nothing was served")

    # Case 22, the load is judged: a forked chain in the
    # file - two entries claiming one (origin, seq), each
    # individually well-hashed (built with the mesh's own
    # entry builder) - refuses the start as forked_entry.
    def test_forked_state_file_refuses_the_start(self):
        state_path = self.tmp / "fork.mesh-state"
        first = build_entry(origin="fork-a", seq=0, lamport=1,
                            key="greeting", value="hello",
                            prev_hash="0" * 64)
        fork = build_entry(origin="fork-a", seq=0, lamport=1,
                           key="greeting", value="pwned",
                           prev_hash="0" * 64)
        state_path.write_text(
            "".join(json.dumps(node_server.entry_to_dict(e),
                               sort_keys=True) + "\n"
                    for e in (first, fork)),
            encoding="utf-8")

        log = self._start_refused("fork-a", "fork-a",
                                  ["--mesh-state", str(state_path)])
        self.assertIn("MeshStateRefused", log)
        self.assertIn("forked_entry", log)
        print("MESH STATE PERSISTENCE refusal: a forked "
              "chain in the state file refused the start as "
              "forked_entry; nothing was served")

    # Case 22, the load is judged: a file cut mid-line -
    # the partial-write shape a torn file would take -
    # does not parse and refuses the start as
    # mesh_state_unreadable.
    def test_truncated_state_file_refuses_the_start(self):
        state_path = self.tmp / "trunc.mesh-state"
        a = self._start("trunc-a", "trunc-a",
                        ["--mesh-state", str(state_path)])
        node_server.mutate(a.base_url, "greeting", "hello")
        node_server.mutate(a.base_url, "mode", "member")
        a.kill()

        raw = state_path.read_bytes()
        state_path.write_bytes(raw[:-7])  # mid-line cut of the last entry

        log = self._start_refused("trunc-a2", "trunc-a",
                                  ["--mesh-state", str(state_path)])
        self.assertIn("MeshStateRefused", log)
        self.assertIn("mesh_state_unreadable", log)
        print("MESH STATE PERSISTENCE refusal: a state "
              "file cut mid-line refused the start as "
              "mesh_state_unreadable; nothing was served")

    # Case 22, the anchor gate on recovery: a recovered
    # state that is exactly the anchored state starts and
    # reports anchored; honest growth recovered past the
    # anchor refuses the start as anchor_stale until the
    # grown state is re-anchored; a foreign chain recovered
    # under the same anchor refuses as anchor_mismatch.
    def test_anchor_gate_on_recovery(self):
        state_path = self.tmp / "gate.mesh-state"
        anchor_path = self.tmp / "outside" / "mesh-anchor.json"
        a = self._start("gate-a", "gate-a",
                        ["--mesh-state", str(state_path)])
        node_server.mutate(a.base_url, "greeting", "hello")
        node_server.mutate(a.base_url, "mode", "member")
        a.kill()
        write_mesh_anchor(view_of(read_state_entries(state_path)),
                          anchor_path)

        anchored_args = ["--mesh-state", str(state_path),
                         "--mesh-anchor", str(anchor_path)]
        a2 = self._start("gate-a2", "gate-a", anchored_args)
        health = node_server.fetch_health(a2.base_url)
        self.assertEqual(health["mesh_state_load"]["status"], "recovered")
        self.assertEqual(health["mesh_state_load"]["entries"], 2)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        node_server.mutate(a2.base_url, "region", "north")
        a2.kill()

        log = self._start_refused("gate-a3", "gate-a", anchored_args)
        self.assertIn("MeshStateRefused", log)
        self.assertIn("anchor_stale", log)
        print("MESH STATE PERSISTENCE gate: honest growth "
              "recovered past the mesh anchor refused the "
              "start as anchor_stale")

        write_mesh_anchor(view_of(read_state_entries(state_path)),
                          anchor_path)
        a4 = self._start("gate-a4", "gate-a", anchored_args)
        health = node_server.fetch_health(a4.base_url)
        self.assertEqual(health["mesh_anchor_status"], "anchored")
        self.assertEqual(health["entries"], 3)
        a4.stop()

        foreign = view_of([])
        foreign.mutate("greeting", "pwned")
        state_path.write_text(
            "".join(json.dumps(node_server.entry_to_dict(e),
                               sort_keys=True) + "\n"
                    for e in foreign.entries()),
            encoding="utf-8")
        log = self._start_refused("gate-a5", "gate-a", anchored_args)
        self.assertIn("MeshStateRefused", log)
        self.assertIn("anchor_mismatch", log)
        print("MESH STATE PERSISTENCE gate: after "
              "re-anchoring the same restart started "
              "anchored over 3 entries; a foreign chain "
              "recovered under the anchor refused as "
              "anchor_mismatch")

    # Case 22, the unchanged default: without --mesh-state
    # a server is memory-only exactly as since experiment
    # 007 - /health reports the configuration edge, and a
    # restart after a kill starts empty.
    def test_no_state_file_is_memory_only_as_today(self):
        a = self._start("plain-a", "plain-a")
        health = node_server.fetch_health(a.base_url)
        self.assertFalse(health["mesh_state_configured"])
        self.assertIsNone(health["mesh_state_load"])
        node_server.mutate(a.base_url, "greeting", "hello")
        a.kill()

        a2 = self._start("plain-a2", "plain-a")
        health = node_server.fetch_health(a2.base_url)
        self.assertEqual(health["entries"], 0)
        self.assertEqual(node_server.fetch_state(a2.base_url)["state"], {})
        print("MESH STATE PERSISTENCE control: without "
              "--mesh-state the restart starts empty, "
              "exactly the pre-033 behavior")


if __name__ == "__main__":
    unittest.main()
