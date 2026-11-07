"""
Process-Bound Architecture - process-level scale harness
(experiment 036).

Measures where the stack stands AT SIZE over the real server
processes: N `node_server.py` processes on localhost, each
serving policies from its own copy of a ~1,000-policy registry
snapshot, signed registry anchors on (experiment 019), mesh
state persistence on (experiment 033), ring peer lists
(experiment 035), driven sync-all convergence, a real SIGKILL
and restart recovery, and a revocation propagated by paired
delivery and server-to-server paired pushes (experiments
028/030). Python standard library only.

Experiment 009 measured the same stack IN PROCESS and stated
its limit: no network transport, no persistence or restart in
those numbers. This harness is 009's process-level sibling:
it changes NO package behavior, sets NO time target, and
claims no pass or fail on any number. It reports measurements
with their conditions, in 006/009's style: stage times from
the same run as the totals, medians as the stable figures.

The scenario, the configuration (which anchor/witness
options are on, and which are off and why), the measurements,
and the honest-ladder rule are stated BEFORE any run in
tests/test-cases/scale-run.md, case 1. The ladder:

    rung 1: 8 processes, 1,000 policies, 1,000 mesh entries
    rung 2: 8 processes, 1,000 policies,   500 mesh entries
    rung 3: 6 processes, 1,000 policies,   500 mesh entries
    rung 4: 4 processes,   500 policies,   250 mesh entries
    rung 5: 4 processes,   200 policies,   100 mesh entries

A rung fails on any phase error, any failed assertion, or a
spent phase budget (see BUDGETS); the ladder records the
failure's phase and its own text, cleans the rung's
processes up, and tries the next rung. The first rung to
complete carries the full measurements.

Run it:

    python3 packages/synchronization/process_scale_harness.py \
        --workdir-root <dir outside the repo>            # ladder
    python3 packages/synchronization/process_scale_harness.py \
        --rung 1 --workdir <dir>                         # one rung

The last line of a rung's output is `RESULT_JSON <json>`;
the ladder's output ends with `LADDER_JSON <json>`. The
workdir holds runtime-generated secrets (enrollment secrets
in per-process anchor secret files, exactly as the server
CLI requires); it must live outside the repo and is never
committed.

HONEST LIMITS (prototype, NOT production)
-----------------------------------------
- One shared VM, localhost only, plaintext HTTP (TLS off;
  experiments 014/017 measured the protected link's
  correctness, not its scale shape). Machine load is part
  of the recorded conditions: the load average at run start
  is in every result.
- The bytes-moved figure for convergence is an ESTIMATE:
  per-peer sync rounds counted across all sweeps, times two
  directions, times the converged /entries payload size.
  Bulk anti-entropy moves full entry lists both ways on
  every round, including no-op rounds; the estimate uses
  the final payload size for every round, which overstates
  the earliest rounds (whose merged sets were still
  growing toward the full set) and is labeled as such.
- Startup time is spawn-to-/health and includes the Python
  interpreter starting and importing the server, not only
  the snapshot's verify-and-load.
- The registry is generated with experiment 009's approach
  (its `measure_registration` does not return the enrollment
  secrets this scenario's anchor and revocation legs need,
  so generation is re-driven here over the same channel,
  the same round-robin author sharing, and the same
  deterministic code bodies; per-registration timings are
  recorded in 009's shape).
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import platform
import random
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# Path order matters: two packages ship an anchor.py, and the
# synchronization one must resolve first for anchored_sync
# (the collision policy_node.py's docstring describes). The
# script's own directory is already sys.path[0] and equals
# the synchronization path, so a plain "insert if absent"
# would leave registration ahead of it - remove and
# re-insert each path to force the order.
for _pkg in ("registration", "policy-library", "synchronization"):
    _path = str(_HERE.parent / _pkg)
    if _path in sys.path:
        sys.path.remove(_path)
    sys.path.insert(0, _path)
sys.path.insert(0, str(_HERE.parent / "validation"))

import node_server  # noqa: E402
from gate5_harness import TimingStats  # noqa: E402
from registration import (  # noqa: E402
    RegistrationChannel,
    sign_library,
    sign_revocation,
)


def _load_registry_anchor_module():
    """Load experiment 012's REGISTRY anchor module by file
    path under a private name, as policy_node.py and the
    suites do: two packages ship an anchor.py (registration,
    synchronization), so a plain `import anchor` may resolve
    to the wrong one depending on sys.path."""
    anchor_file = _HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_process_scale_registry_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


write_anchor = _load_registry_anchor_module().write_anchor

SERVER = _HERE / "node_server.py"
SEED = 20261106  # experiment 009's date seed
ANCHOR_SIGNER = "anchor-signer"

# (processes, policies, mesh entries), tried in order.
RUNGS = [
    (8, 1_000, 1_000),
    (8, 1_000, 500),
    (6, 1_000, 500),
    (4, 500, 250),
    (4, 200, 100),
]

# Phase budgets in seconds (tests/test-cases/scale-run.md).
BUDGETS = {
    "generation": 600.0,
    "startup_per_process": 60.0,
    "origination": 600.0,
    "convergence_rounds": 12,
    "convergence": 600.0,
    "push_each": 60.0,
    "rung_total": 1_800.0,
}

PORT_BASE = 18736


class PhaseFailure(Exception):
    """One phase of one rung failed; carries the phase name
    and the failure's own text for the ladder record."""

    def __init__(self, phase: str, message: str):
        super().__init__(f"{phase}: {message}")
        self.phase = phase
        self.message = message


def log(msg: str) -> None:
    print(msg, flush=True)


def code_for(user: str, index: int) -> bytes:
    """Experiment 009's deterministic code body."""
    return f"policy body {user} #{index}\n".encode("utf-8")


def stats_dict(samples_ns: list[int]) -> dict | None:
    if not samples_ns:
        return None
    return TimingStats.from_samples_ns(samples_ns).to_dict()


# -- server process management -----------------------------------------------


@dataclass
class ServerProc:
    index: int
    port: int
    base_url: str
    cmd: list[str]
    workdir: Path
    proc: subprocess.Popen | None = None
    _log_fh: object | None = None

    @property
    def label(self) -> str:
        return f"srv{self.index}"

    def start(self) -> float:
        """Spawn and wait for /health; returns spawn-to-ready
        wall seconds."""
        log_path = self.workdir / f"{self.label}.log"
        self._log_fh = log_path.open("w", encoding="utf-8")
        port_file = self.workdir / f"{self.label}.port"
        if port_file.exists():
            port_file.unlink()
        t0 = time.perf_counter()
        self.proc = subprocess.Popen(
            self.cmd, stdout=self._log_fh, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + BUDGETS["startup_per_process"]
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise PhaseFailure(
                    "startup",
                    f"{self.label} exited early rc={self.proc.returncode}; "
                    f"log tail: {self.log_tail()}")
            try:
                node_server.fetch_health(self.base_url)
                return time.perf_counter() - t0
            except Exception:
                time.sleep(0.05)
        raise PhaseFailure(
            "startup",
            f"{self.label} not answering /health within "
            f"{BUDGETS['startup_per_process']}s; log tail: {self.log_tail()}")

    def log_tail(self, lines: int = 15) -> str:
        try:
            text = (self.workdir / f"{self.label}.log").read_text(
                encoding="utf-8")
            return "\n".join(text.splitlines()[-lines:])
        except OSError:
            return "(no log)"

    def rss_kb(self) -> int | None:
        if self.proc is None or self.proc.poll() is not None:
            return None
        try:
            for line in Path(f"/proc/{self.proc.pid}/status").read_text(
                    encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
        except (OSError, ValueError, IndexError):
            return None
        return None

    def kill9(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL)
            self.proc.wait(timeout=10)

    def stop(self) -> None:
        try:
            if self.proc is not None and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=5)
        finally:
            if self._log_fh is not None:
                self._log_fh.close()
                self._log_fh = None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


def allocate_ports(count: int, base: int = PORT_BASE) -> list[int]:
    """Reserve `count` free localhost ports by holding the
    sockets until each server is spawned (released one by
    one in start_reserved)."""
    held: list[socket.socket] = []
    ports: list[int] = []
    candidate = base
    while len(ports) < count:
        s = socket.socket()
        # SO_REUSEADDR, matching the server's own socket
        # option (http.server.HTTPServer.allow_reuse_address):
        # a port whose previous holder left TIME_WAIT sockets
        # is genuinely available to the server, and the scan
        # must judge it the way the server's bind will.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", candidate))
        except OSError:
            s.close()
            candidate += 1
            continue
        held.append(s)
        ports.append(candidate)
        candidate += 1
    for s in held:
        s.close()
    return ports


def wait_port_free(port: int, timeout: float = 15.0) -> None:
    # The probe binds the way the server itself will bind:
    # with SO_REUSEADDR (http.server.HTTPServer sets
    # allow_reuse_address = 1). A plain bind reports a port
    # "busy" for the whole TIME_WAIT lifetime of the killed
    # server's accepted connections even though the server
    # could rebind it immediately - the first ladder attempt
    # failed every rung at exactly this check, after every
    # earlier phase had completed, and the fix is in this
    # probe, not in any package.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            s.close()
            return
        except OSError:
            s.close()
            time.sleep(0.1)
    raise PhaseFailure("kill_restart", f"port {port} not free "
                       f"within {timeout}s after SIGKILL")


def count_node_server_processes() -> int:
    """A /proc scan for node_server.py processes (any owner),
    reported beside the harness's own cleanup assertion."""
    me = str(os.getpid())
    count = 0
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit() or pid_dir.name == me:
            continue
        try:
            cmdline = (pid_dir / "cmdline").read_bytes()
        except OSError:
            continue
        if b"node_server.py" in cmdline:
            count += 1
    return count


# -- one rung ------------------------------------------------------------------


@dataclass
class Rung:
    processes: int
    policies: int
    mesh_entries: int
    workdir: Path
    servers: list[ServerProc] = field(default_factory=list)
    results: dict = field(default_factory=dict)

    def run(self) -> dict:
        t_rung = time.perf_counter()
        self.results = {
            "processes": self.processes,
            "policies_target": self.policies,
            "mesh_entries_target": self.mesh_entries,
            "machine": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "cpus": os.cpu_count(),
                "loadavg_at_start": [round(x, 2) for x in os.getloadavg()],
            },
        }
        try:
            self._generation()
            self._startup()
            self._origination()
            self._convergence()
            self._serving()
            self._kill_restart()
            self._revocation()
            self.results["total_s"] = round(time.perf_counter() - t_rung, 3)
            if self.results["total_s"] > BUDGETS["rung_total"]:
                raise PhaseFailure(
                    "budget",
                    f"rung total {self.results['total_s']}s exceeded "
                    f"{BUDGETS['rung_total']}s budget")
            return self.results
        finally:
            for srv in self.servers:
                srv.stop()
            own_alive = sum(1 for srv in self.servers if srv.alive())
            self.results["cleanup_own_alive"] = own_alive
            self.results["node_server_processes_after"] = (
                count_node_server_processes())
            log(f"cleanup: own processes still alive: {own_alive}; "
                f"node_server processes on machine: "
                f"{self.results['node_server_processes_after']}")

    # (a) generation -----------------------------------------------------

    def _generation(self) -> None:
        phase = "generation"
        t0 = time.perf_counter()
        workdir = self.workdir
        workdir.mkdir(parents=True, exist_ok=True)
        central = workdir / "central"
        self.registry_log = central / "registry.jsonl"
        self.channel = RegistrationChannel(
            self.registry_log, central / "audit.jsonl")
        users = 200 if self.policies % 200 == 0 else 100
        self.authors = [f"user-{i}" for i in range(users)]
        t_enroll = time.perf_counter()
        self.secrets = {u: self.channel.enroll_user(u) for u in self.authors}
        self.signer_secret = self.channel.enroll_user(ANCHOR_SIGNER)
        enroll_s = time.perf_counter() - t_enroll

        per_user = self.policies // users
        self.payloads: dict[str, bytes] = {}
        self.hashes: list[str] = []
        samples_ns: list[int] = []
        t_reg = time.perf_counter()
        for user in self.authors:
            for j in range(per_user):
                code = code_for(user, j)
                s0 = time.perf_counter_ns()
                record, created = self.channel.register(
                    author=user, name=f"policy-{j}", version="1.0.0",
                    code=code,
                    signature=sign_library(self.secrets[user], user,
                                           f"policy-{j}", "1.0.0", code))
                samples_ns.append(time.perf_counter_ns() - s0)
                if not created:
                    raise PhaseFailure(phase, f"duplicate registration "
                                       f"for {user} policy-{j}")
                self.payloads[record.policy_hash] = code
                self.hashes.append(record.policy_hash)
        register_s = time.perf_counter() - t_reg
        completed = len(self.hashes)
        if completed != per_user * users:
            raise PhaseFailure(phase, f"registered {completed}, "
                               f"expected {per_user * users}")
        self.results["policies_completed"] = completed
        (workdir / "payloads.json").write_text(json.dumps(
            {h: c.hex() for h, c in self.payloads.items()}), encoding="utf-8")
        self.payloads_file = workdir / "payloads.json"

        # One signed anchor per process over the base log, and
        # each process's own snapshot copy (experiment 030's
        # shape: every server consults its OWN anchor file).
        self.anchor_paths: list[Path] = []
        self.snapshot_paths: list[Path] = []
        log_bytes = self.registry_log.read_bytes()
        for i in range(self.processes):
            anchor_path = workdir / f"srv{i}-outside" / "registry-anchor.json"
            write_anchor(self.registry_log, anchor_path,
                         signer=ANCHOR_SIGNER, secret_hex=self.signer_secret)
            secret_file = anchor_path.parent / "anchor-secret.txt"
            secret_file.write_text(self.signer_secret, encoding="utf-8")
            self.anchor_paths.append(anchor_path)
            snap_dir = workdir / f"srv{i}-dir"
            snap_dir.mkdir(exist_ok=True)
            snap = snap_dir / "snapshot.jsonl"
            snap.write_bytes(log_bytes)
            self.snapshot_paths.append(snap)
        total_s = time.perf_counter() - t0
        if total_s > BUDGETS["generation"]:
            raise PhaseFailure(phase, f"generation took {total_s:.1f}s, "
                               f"budget {BUDGETS['generation']}s")
        self.results["generation"] = {
            "users": users,
            "enroll_s": round(enroll_s, 3),
            "register_total_s": round(register_s, 3),
            "per_registration": stats_dict(samples_ns),
            "registry_file_bytes": self.registry_log.stat().st_size,
            "payloads_file_bytes": self.payloads_file.stat().st_size,
            "phase_total_s": round(total_s, 3),
        }
        log(f"generation: {completed} policies by {users} users in "
            f"{register_s:.2f}s (median "
            f"{self.results['generation']['per_registration']['median_ms']:.3f} ms); "
            f"registry {self.results['generation']['registry_file_bytes']} B")

    # (b) startup ----------------------------------------------------------

    def _startup(self) -> None:
        ports = allocate_ports(self.processes)
        urls = [f"http://127.0.0.1:{p}" for p in ports]
        self.base_urls = urls
        # Ring peer lists: i lists i-1 and i+1 (mod N).
        peers_files: list[Path] = []
        for i in range(self.processes):
            peers = []
            for j in ((i - 1) % self.processes, (i + 1) % self.processes):
                if urls[j] not in peers:
                    peers.append(urls[j])
            pf = self.workdir / f"srv{i}-peers.txt"
            pf.write_text("".join(u + "\n" for u in peers), encoding="utf-8")
            peers_files.append(pf)
        startup_s: list[float] = []
        for i in range(self.processes):
            anchor_path = self.anchor_paths[i]
            cmd = [
                sys.executable, str(SERVER),
                "--node-id", f"srv{i}",
                "--port", str(ports[i]),
                "--port-file", str(self.workdir / f"srv{i}.port"),
                "--snapshot", str(self.snapshot_paths[i]),
                "--payloads", str(self.payloads_file),
                "--serve-policies",
                "--anchor", str(anchor_path),
                "--anchor-signer", ANCHOR_SIGNER,
                "--anchor-secret-file",
                str(anchor_path.parent / "anchor-secret.txt"),
                "--mesh-state",
                str(self.workdir / f"srv{i}-dir" / "mesh-state.jsonl"),
                "--peers", str(peers_files[i]),
            ]
            srv = ServerProc(index=i, port=ports[i], base_url=urls[i],
                             cmd=cmd, workdir=self.workdir)
            self.servers.append(srv)
            ready_s = srv.start()
            startup_s.append(ready_s)
            health = node_server.fetch_health(srv.base_url)
            if health.get("anchor_status") != "anchored":
                raise PhaseFailure(
                    "startup", f"srv{i} anchor_status="
                    f"{health.get('anchor_status')}, expected anchored")
            if health.get("snapshot_entries") != len(self.hashes):
                raise PhaseFailure(
                    "startup", f"srv{i} snapshot_entries="
                    f"{health.get('snapshot_entries')}, expected "
                    f"{len(self.hashes)}")
        self.results["startup"] = {
            "per_process_s": [round(s, 3) for s in startup_s],
            "max_s": round(max(startup_s), 3),
            "total_sequential_s": round(sum(startup_s), 3),
        }
        log(f"startup: {self.processes} processes ready; per-process "
            f"{[round(s, 2) for s in startup_s]}s")

    # (c) origination ------------------------------------------------------

    def _origination(self) -> None:
        phase = "origination"
        origin = self.servers[0].base_url
        samples_ns: list[int] = []
        t0 = time.perf_counter()
        for i in range(self.mesh_entries):
            s0 = time.perf_counter_ns()
            try:
                node_server.mutate(origin, f"scale-key-{i}",
                                   f"scale-value-{i}")
            except Exception as exc:
                raise PhaseFailure(phase, f"mutate {i} failed: {exc}")
            samples_ns.append(time.perf_counter_ns() - s0)
            if (time.perf_counter() - t0 > BUDGETS["origination"]):
                raise PhaseFailure(
                    phase, f"origination exceeded "
                    f"{BUDGETS['origination']}s at entry {i}")
        total_s = time.perf_counter() - t0
        health = node_server.fetch_health(origin)
        if health.get("entries") != self.mesh_entries:
            raise PhaseFailure(phase, f"origin holds "
                               f"{health.get('entries')} entries, expected "
                               f"{self.mesh_entries}")
        self.results["origination"] = {
            "entries": self.mesh_entries,
            "total_s": round(total_s, 3),
            "per_mutate": stats_dict(samples_ns),
            "first20_median_ms": stats_dict(samples_ns[:20])["median_ms"],
            "last20_median_ms": stats_dict(samples_ns[-20:])["median_ms"],
        }
        log(f"origination: {self.mesh_entries} entries at srv0 in "
            f"{total_s:.2f}s (median "
            f"{self.results['origination']['per_mutate']['median_ms']:.2f} ms, "
            f"last-20 median "
            f"{self.results['origination']['last20_median_ms']:.2f} ms)")

    # (d) convergence --------------------------------------------------------

    def _state_hashes(self) -> list[str]:
        return [node_server.fetch_state(s.base_url)["state_hash"]
                for s in self.servers]

    def _convergence(self) -> None:
        phase = "convergence"
        t0 = time.perf_counter()
        rounds: list[dict] = []
        per_peer_rounds = 0
        converged_round = None
        for r in range(1, BUDGETS["convergence_rounds"] + 1):
            rt0 = time.perf_counter()
            outcomes: list[str] = []
            for srv in self.servers:
                report = node_server.request_sync_all(srv.base_url)
                results = report.get("results", [])
                per_peer_rounds += len(results)
                outcomes.extend(
                    res.get("outcome", res.get("status", "?"))
                    for res in results)
            round_s = time.perf_counter() - rt0
            hashes = self._state_hashes()
            all_equal = len(set(hashes)) == 1
            rounds.append({"round": r, "wall_s": round(round_s, 3),
                           "all_hashes_equal": all_equal,
                           "peer_outcomes": outcomes})
            log(f"convergence round {r}: {round_s:.2f}s, "
                f"hashes equal: {all_equal}")
            if all_equal:
                converged_round = r
                break
            if time.perf_counter() - t0 > BUDGETS["convergence"]:
                raise PhaseFailure(phase, f"convergence exceeded "
                                   f"{BUDGETS['convergence']}s after {r} rounds")
        total_s = time.perf_counter() - t0
        if converged_round is None:
            raise PhaseFailure(
                phase, f"state hashes not equal after "
                f"{BUDGETS['convergence_rounds']} rounds: "
                f"{self._state_hashes()}")
        self.converged_hash = self._state_hashes()[0]
        for srv in self.servers:
            entries = node_server.fetch_health(srv.base_url).get("entries")
            if entries != self.mesh_entries:
                raise PhaseFailure(phase, f"{srv.label} holds {entries} "
                                   f"entries after convergence, expected "
                                   f"{self.mesh_entries}")
        # The full-set transfer unit: GET /entries at the origin.
        req = urllib.request.Request(self.servers[0].base_url + "/entries")
        with urllib.request.urlopen(req, timeout=30) as resp:
            entries_payload_bytes = len(resp.read())
        estimated_bytes = per_peer_rounds * 2 * entries_payload_bytes
        # The no-op sweep: confirming convergence still moves
        # the full lists both ways (bulk anti-entropy).
        t_noop = time.perf_counter()
        noop_report = node_server.request_sync_all(self.servers[0].base_url)
        noop_s = time.perf_counter() - t_noop
        rss = {srv.label: srv.rss_kb() for srv in self.servers}
        self.results["convergence"] = {
            "rounds": converged_round,
            "per_round": rounds,
            "total_s": round(total_s, 3),
            "per_peer_sync_rounds": per_peer_rounds,
            "entries_payload_bytes": entries_payload_bytes,
            "estimated_bytes_moved": estimated_bytes,
            "noop_sweep_at_origin_s": round(noop_s, 3),
            "noop_sweep_status": noop_report.get("status"),
            "rss_kb_per_process": rss,
            "state_hash": self.converged_hash,
        }
        log(f"convergence: {converged_round} round(s), {total_s:.2f}s; "
            f"/entries payload {entries_payload_bytes} B; estimated bytes "
            f"moved {estimated_bytes}; no-op sweep {noop_s:.2f}s")

    # (e) serving ------------------------------------------------------------

    def _serving(self) -> None:
        phase = "serving"
        rng = random.Random(SEED)

        def sample(server_indexes: list[int], count: int) -> list[int]:
            samples_ns: list[int] = []
            for _ in range(count):
                srv = self.servers[rng.choice(server_indexes)]
                policy_hash = rng.choice(self.hashes)
                expected = self.payloads[policy_hash]
                s0 = time.perf_counter_ns()
                status, body = node_server.fetch_policy(
                    srv.base_url, policy_hash)
                samples_ns.append(time.perf_counter_ns() - s0)
                if status != 200:
                    raise PhaseFailure(
                        phase, f"fetch from {srv.label} returned {status} "
                        f"for {policy_hash}: {body}")
                got = base64.b64decode(body["code_base64"])
                if got != expected:
                    raise PhaseFailure(
                        phase, f"fetch from {srv.label} returned wrong "
                        f"bytes for {policy_hash}")
            return samples_ns

        others = list(range(1, self.processes))
        other_samples = sample(others, 80)
        control_samples = sample([0], 20)
        self.results["serving"] = {
            "from_other_processes": stats_dict(other_samples),
            "control_from_origin": stats_dict(control_samples),
            "samples_other": len(other_samples),
            "samples_control": len(control_samples),
        }
        st = self.results["serving"]["from_other_processes"]
        log(f"serving: 80 fetches from non-origin processes, median "
            f"{st['median_ms']:.3f} ms, p95 {st['p95_ms']:.3f} ms, "
            f"max {st['max_ms']:.3f} ms")

    # (f) kill and recovery ----------------------------------------------------

    def _kill_restart(self) -> None:
        phase = "kill_restart"
        victim = self.servers[3]
        victim.kill9()
        wait_port_free(victim.port)
        restart_s = victim.start()
        health = node_server.fetch_health(victim.base_url)
        load = health.get("mesh_state_load") or {}
        if load.get("status") != "recovered":
            raise PhaseFailure(phase, f"victim mesh_state_load={load}, "
                               f"expected status recovered")
        if load.get("entries") != self.mesh_entries:
            raise PhaseFailure(phase, f"victim recovered "
                               f"{load.get('entries')} entries, expected "
                               f"{self.mesh_entries}")
        recovered_hash = node_server.fetch_state(
            victim.base_url)["state_hash"]
        if recovered_hash != self.converged_hash:
            raise PhaseFailure(phase, f"victim state hash after restart "
                               f"{recovered_hash} != converged "
                               f"{self.converged_hash}")
        rt0 = time.perf_counter()
        for srv in self.servers:
            node_server.request_sync_all(srv.base_url)
        reconverge_s = time.perf_counter() - rt0
        hashes = self._state_hashes()
        if len(set(hashes)) != 1:
            raise PhaseFailure(phase, f"hashes differ after the "
                               f"post-restart sweep round: {hashes}")
        self.results["kill_restart"] = {
            "victim": victim.label,
            "restart_s": round(restart_s, 3),
            "recovered_entries": load.get("entries"),
            "post_restart_sweep_round_s": round(reconverge_s, 3),
        }
        log(f"kill/restart: {victim.label} SIGKILLed and restarted in "
            f"{restart_s:.2f}s; recovered {load.get('entries')} entries, "
            f"hash exact; post-restart sweep round {reconverge_s:.2f}s")

    # (g) revocation -------------------------------------------------------------

    def _revocation(self) -> None:
        phase = "revocation"
        revoked_hash = self.hashes[0]
        revoked_author = self.authors[0]
        control_hash = self.hashes[-1]
        self.channel.revoke(
            author=revoked_author, policy_hash=revoked_hash,
            signature=sign_revocation(self.secrets[revoked_author],
                                      revoked_hash))
        # The anchor lifecycle for process 0 alone (030): its
        # anchor file is re-written over the grown log by the
        # same signer before the pair travels.
        write_anchor(self.registry_log, self.anchor_paths[0],
                     signer=ANCHOR_SIGNER, secret_hex=self.signer_secret)
        entries = [json.loads(line) for line in
                   self.registry_log.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
        anchor_obj = json.loads(
            self.anchor_paths[0].read_text(encoding="utf-8"))
        report = node_server.deliver_anchor(
            self.servers[0].base_url, anchor_obj, entries)
        if report.get("status") != "delivered":
            raise PhaseFailure(phase, f"paired delivery to srv0: {report}")
        push_times: list[float] = []
        for i in range(self.processes - 1):
            t0 = time.perf_counter()
            status, body = node_server.push_anchor(
                self.servers[i].base_url, self.servers[i + 1].base_url)
            push_s = time.perf_counter() - t0
            push_times.append(push_s)
            if push_s > BUDGETS["push_each"]:
                raise PhaseFailure(phase, f"push {i}->{i + 1} took "
                                   f"{push_s:.1f}s, budget "
                                   f"{BUDGETS['push_each']}s")
            if status != 200 or body.get("status") != "delivered":
                raise PhaseFailure(
                    phase, f"push srv{i}->srv{i + 1}: http {status}, "
                    f"body status {body.get('status')}, delivery "
                    f"{body.get('delivery')}")
        for srv in self.servers:
            status, body = node_server.fetch_policy(
                srv.base_url, revoked_hash)
            if status != 410 or body.get("error") != "policy_revoked":
                raise PhaseFailure(
                    phase, f"{srv.label} answers the revoked policy with "
                    f"{status} {body.get('error')}, expected 410 "
                    f"policy_revoked")
            status, body = node_server.fetch_policy(
                srv.base_url, control_hash)
            if status != 200 or base64.b64decode(
                    body["code_base64"]) != self.payloads[control_hash]:
                raise PhaseFailure(
                    phase, f"{srv.label} no longer serves the control "
                    f"policy exactly (status {status})")
        self.results["revocation"] = {
            "revoked_policy_hash": revoked_hash,
            "control_policy_hash": control_hash,
            "push_chain": [f"srv{i}->srv{i + 1}"
                           for i in range(self.processes - 1)],
            "per_push_s": [round(s, 3) for s in push_times],
            "push_total_s": round(sum(push_times), 3),
            "final_check": f"410 policy_revoked at all {self.processes} "
                           f"processes; control serves exactly at all",
        }
        log(f"revocation: paired delivery to srv0 + "
            f"{len(push_times)} pushes in {sum(push_times):.2f}s; "
            f"410 at every process, control serving")


# -- ladder ---------------------------------------------------------------------


def run_one_rung(rung_index: int, workdir: Path) -> int:
    processes, policies, mesh_entries = RUNGS[rung_index - 1]
    rung = Rung(processes=processes, policies=policies,
                mesh_entries=mesh_entries, workdir=workdir)
    log(f"=== RUNG {rung_index}: {processes} processes, {policies} "
        f"policies, {mesh_entries} mesh entries ===")
    try:
        results = rung.run()
    except PhaseFailure as exc:
        print(f"RUNG_FAILED phase={exc.phase} message={exc.message}",
              flush=True)
        partial = {"rung": rung_index, "outcome": "failed",
                   "phase": exc.phase, "message": exc.message,
                   "partial_results": rung.results}
        print("RESULT_JSON " + json.dumps(partial), flush=True)
        return 1
    except Exception as exc:  # any other failure is still evidence
        import traceback
        traceback.print_exc()
        print(f"RUNG_FAILED phase=unclassified message={exc!r}", flush=True)
        partial = {"rung": rung_index, "outcome": "failed",
                   "phase": "unclassified", "message": repr(exc),
                   "partial_results": rung.results}
        print("RESULT_JSON " + json.dumps(partial), flush=True)
        return 1
    results["rung"] = rung_index
    results["outcome"] = "completed"
    print("RESULT_JSON " + json.dumps(results), flush=True)
    return 0


def run_ladder(workdir_root: Path) -> int:
    attempts: list[dict] = []
    completed = None
    for rung_index in range(1, len(RUNGS) + 1):
        rung_dir = workdir_root / f"rung{rung_index}"
        rung_dir.mkdir(parents=True, exist_ok=True)
        out_path = rung_dir / "rung-output.txt"
        log(f"=== LADDER: trying rung {rung_index} "
            f"{RUNGS[rung_index - 1]} (output: {out_path}) ===")
        with out_path.open("w", encoding="utf-8") as fh:
            proc = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()),
                 "--rung", str(rung_index), "--workdir", str(rung_dir)],
                stdout=fh, stderr=subprocess.STDOUT)
        result = None
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("RESULT_JSON "):
                result = json.loads(line[len("RESULT_JSON "):])
        if result is None:
            result = {"rung": rung_index, "outcome": "failed",
                      "phase": "harness",
                      "message": f"no RESULT_JSON in rung output "
                                 f"(exit {proc.returncode})"}
        attempts.append({k: result.get(k) for k in
                         ("rung", "outcome", "phase", "message")})
        if result["outcome"] == "completed":
            completed = result
            break
    ladder = {"attempts": attempts, "completed_rung": (
        completed["rung"] if completed else None), "results": completed}
    print("LADDER_JSON " + json.dumps(ladder), flush=True)
    return 0 if completed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PBA process-level scale harness (experiment 036)")
    parser.add_argument("--rung", type=int, default=None,
                        help="run one rung (1-5) instead of the ladder")
    parser.add_argument("--workdir", type=Path, default=None,
                        help="work directory for --rung mode")
    parser.add_argument("--workdir-root", type=Path, default=None,
                        help="root under which the ladder makes "
                             "rung<N> work directories")
    args = parser.parse_args(argv)
    if args.rung is not None:
        if args.workdir is None:
            parser.error("--rung requires --workdir")
        return run_one_rung(args.rung, args.workdir)
    if args.workdir_root is None:
        parser.error("ladder mode requires --workdir-root")
    return run_ladder(args.workdir_root)


if __name__ == "__main__":
    raise SystemExit(main())
