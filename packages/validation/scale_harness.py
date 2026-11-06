"""
Process-Bound Architecture - scale harness (experiment 009).

Measures where the prototype stack (Gates 1-5 plus networked nodes
and revocation) stands AT SIZE, with real numbers from the machine
running it. Python standard library only. No network beyond
in-process objects, no database, no dependencies. Builds on the
earlier packages without modifying them:

- Gate 1 (`packages/registration/registration.py`): registration at
  volume through the real RegistrationChannel, and the cost of
  verifying the full registry hash chain at 200 / 1,000 / 5,000
  entries.
- Gate 3 (`packages/synchronization/sync_mesh.py`): sync
  convergence at size - 10 nodes, up to 10,000 total entries,
  delivered in shuffled per-origin batches.
- Gate 2 (`packages/policy-library/policy_node.py`): the verified
  serve path against a large registry snapshot.
- Gate 5 (`packages/validation/gate5_harness.py`): the canary's
  end-to-end lineage measurement (registry chain + mesh lineage +
  verified serve), repeated across a grid of registry sizes and
  mesh sizes, reported against the 1 ms target from
  docs/architecture/06 honestly: the miss factor is printed, and
  the time is broken down per stage so it is visible where it goes.

WHAT "SHUFFLED DELIVERY" MEANS HERE (stated, not hidden)
--------------------------------------------------------
Gate 3's receive path verifies the merged entry set as a whole:
per-origin chains must be contiguous from genesis. A batch that
leaves a gap in an origin's chain is refused as a broken chain, by
design. So delivery here shuffles everything the design allows to
be shuffled: the per-origin batches arrive in a seeded-random
order, and the entries inside each batch are in seeded-random
order. What is NOT shuffled is batch granularity: one origin's
complete chain per batch. Incremental, gap-tolerant streaming is
not something the Gate 3 prototype supports, and this harness does
not pretend it measured that.

SIZES AND THE TIME BUDGET
-------------------------
The full profile targets the sizes above. Each registration size
has a soft wall-clock budget (REGISTRATION_BUDGET_S): if building a
registry exceeds it, that size is recorded as incomplete with the
count reached, larger sizes are skipped, and the report carries
the completed sizes and the trend. The harness never forces a size
that is not finishing.

HONEST LIMITS (prototype, NOT production)
-----------------------------------------
- One shared VM, in-process Python objects, localhost files. There
  is no network transport in these measurements (experiment 007's
  localhost nodes are a separate, correctness-only result), no
  concurrent users, no persistence/restart, and timing includes
  Python interpreter and JSON costs a production runtime
  (Wasm/eBPF, per the proposals) would not have, while excluding
  network costs it would have.
- Registration writes through the Gate 1 channel exactly as built:
  every register() call re-reads the whole registry log from disk
  (that is what the code does), so the per-registration cost at
  size includes that O(entries) re-read. The harness measures the
  prototype as it stands; it does not optimize around it.
- The 1 ms lineage figure is a target the measurements are reported
  against, never a claim. At these sizes the end-to-end number is
  expected to miss it by a wide factor; the report says by how
  much, per stage.
"""

from __future__ import annotations

import json
import platform
import random
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _pkg in ("registration", "policy-library", "synchronization"):
    _path = str(_HERE.parent / _pkg)
    if _path not in sys.path:
        sys.path.insert(0, _path)
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from gate5_harness import (  # noqa: E402
    TARGET_LINEAGE_MS,
    TimingStats,
    verify_entry_lineage,
    verify_mesh_lineage,
)
from policy_node import PolicyNode, verify_registry_snapshot  # noqa: E402
from registration import RegistrationChannel, sign_library  # noqa: E402
from sync_mesh import GENESIS_HASH, SyncNode, build_entry  # noqa: E402


# -- size profiles -----------------------------------------------------------

USERS = 200
REGISTRATION_SIZES = (200, 1_000, 5_000)
REGISTRATION_BUDGET_S = 300.0  # per size; see module docstring
SYNC_POINTS = ((10, 200), (10, 1_000))  # (node_count, mutations per node)
CANARY_REGISTRY_SIZES = (200, 1_000, 5_000)
CANARY_MESH_SIZES = (100, 1_000, 10_000)
CANARY_MESH_ORIGINS = 10
SERVE_REPEATS = 300
DELIVERY_SEED = 20261106


def canary_iterations(mesh_entries: int) -> int:
    if mesh_entries >= 10_000:
        return 15
    if mesh_entries >= 1_000:
        return 30
    return 50


def machine_info() -> dict:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processors": platform.processor() or None,
    }


def _code_for(user: str, index: int) -> bytes:
    return f"policy body {user} #{index}\n".encode("utf-8")


# -- 1. registration at volume -------------------------------------------------


@dataclass
class RegistrationScaleResult:
    entries_target: int
    entries_completed: int
    users: int
    completed: bool
    enroll_s: float
    register_total_s: float
    per_registration: TimingStats | None
    verify_live: TimingStats | None  # verify_registry_chain on the live channel
    verify_reload: TimingStats | None  # fresh channel load + verify, from disk
    registry_file_bytes: int
    snapshot_path: str = ""
    payloads: dict = field(default_factory=dict, repr=False)
    sample_hashes: list = field(default_factory=list, repr=False)

    def to_dict(self) -> dict:
        return {
            "entries_target": self.entries_target,
            "entries_completed": self.entries_completed,
            "users": self.users,
            "completed": self.completed,
            "enroll_s": self.enroll_s,
            "register_total_s": self.register_total_s,
            "per_registration": self.per_registration.to_dict() if self.per_registration else None,
            "verify_live_ms": self.verify_live.to_dict() if self.verify_live else None,
            "verify_reload_ms": self.verify_reload.to_dict() if self.verify_reload else None,
            "registry_file_bytes": self.registry_file_bytes,
        }


def measure_registration(
    workdir: str | Path,
    total_entries: int,
    *,
    users: int = USERS,
    budget_s: float = REGISTRATION_BUDGET_S,
    verify_repeats: int = 5,
) -> RegistrationScaleResult:
    """Enroll `users` users and register `total_entries` policies
    through the real channel (users share the entries round-robin:
    each registers total_entries // users). Times enrollment, every
    registration individually, and full-chain verification both on
    the live channel and on a fresh channel reloaded from disk."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    registry_log = workdir / "registry.jsonl"
    audit_log = workdir / "audit.jsonl"
    channel = RegistrationChannel(registry_log, audit_log)

    user_ids = [f"user-{i}" for i in range(users)]
    start = time.perf_counter()
    secrets = {u: channel.enroll_user(u) for u in user_ids}
    enroll_s = time.perf_counter() - start

    per_user = total_entries // users
    samples_ns: list[int] = []
    payloads: dict[str, bytes] = {}
    sample_hashes: list[str] = []
    completed = 0
    aborted = False
    register_start = time.perf_counter()
    for u_i, user in enumerate(user_ids):
        if aborted:
            break
        for j in range(per_user):
            name = f"policy-{j}"
            code = _code_for(user, j)
            t0 = time.perf_counter_ns()
            record, created = channel.register(
                author=user,
                name=name,
                version="1.0.0",
                code=code,
                signature=sign_library(secrets[user], user, name, "1.0.0", code),
            )
            samples_ns.append(time.perf_counter_ns() - t0)
            assert created, "scale registrations must all be new policies"
            payloads[record.policy_hash] = code
            if j == 0:
                sample_hashes.append(record.policy_hash)
            completed += 1
            if completed % 500 == 0 and time.perf_counter() - register_start > budget_s:
                aborted = True
                break
    register_total_s = time.perf_counter() - register_start

    verify_live = verify_reload = None
    if completed:
        live_samples = []
        for _ in range(verify_repeats):
            t0 = time.perf_counter_ns()
            ok = channel.verify_registry_chain()
            live_samples.append(time.perf_counter_ns() - t0)
            assert ok, "registry chain must verify at size"
        verify_live = TimingStats.from_samples_ns(live_samples)

        reload_samples = []
        for _ in range(3):
            t0 = time.perf_counter_ns()
            fresh = RegistrationChannel(registry_log, workdir / "audit-reload.jsonl")
            ok = fresh.verify_registry_chain()
            reload_samples.append(time.perf_counter_ns() - t0)
            assert ok, "reloaded registry chain must verify at size"
        verify_reload = TimingStats.from_samples_ns(reload_samples)

    return RegistrationScaleResult(
        entries_target=total_entries,
        entries_completed=completed,
        users=users,
        completed=not aborted and completed == per_user * users,
        enroll_s=enroll_s,
        register_total_s=register_total_s,
        per_registration=TimingStats.from_samples_ns(samples_ns) if samples_ns else None,
        verify_live=verify_live,
        verify_reload=verify_reload,
        registry_file_bytes=registry_log.stat().st_size if registry_log.exists() else 0,
        snapshot_path=str(registry_log),
        payloads=payloads,
        sample_hashes=sample_hashes,
    )


# -- 2. sync convergence at size -----------------------------------------------


@dataclass
class SyncScaleResult:
    node_count: int
    mutations_per_node: int
    total_entries: int
    mutate_total_s: float
    delivery_total_s: float
    per_node_delivery_s: tuple
    converged: bool
    state_hash: str
    entries_per_node: tuple

    def to_dict(self) -> dict:
        return {
            "node_count": self.node_count,
            "mutations_per_node": self.mutations_per_node,
            "total_entries": self.total_entries,
            "mutate_total_s": self.mutate_total_s,
            "delivery_total_s": self.delivery_total_s,
            "per_node_delivery_s": list(self.per_node_delivery_s),
            "converged": self.converged,
            "state_hash": self.state_hash,
            "entries_per_node": list(self.entries_per_node),
        }


def measure_sync(
    node_count: int,
    mutations_per_node: int,
    *,
    seed: int = DELIVERY_SEED,
) -> SyncScaleResult:
    """Every node mutates locally (no lock, no coordinator), then
    every node receives every origin's entries: per-origin batches
    in a seeded shuffled order, entries shuffled within each batch
    (see the module docstring for why batches are whole chains).
    Convergence is all nodes holding all entries with one identical
    state hash."""
    nodes = [SyncNode(f"node-{i}") for i in range(node_count)]
    start = time.perf_counter()
    for i, node in enumerate(nodes):
        for j in range(mutations_per_node):
            node.mutate(f"key-{i}-{j}", f"value-{j}")
    mutate_total_s = time.perf_counter() - start

    by_origin: dict[str, list] = {}
    for node in nodes:
        for entry in node.entries():
            if entry.origin == node.node_id:
                by_origin.setdefault(entry.origin, []).append(entry)

    per_node_delivery: list[float] = []
    delivery_start = time.perf_counter()
    for idx, node in enumerate(nodes):
        rng = random.Random(seed + idx)
        origins = list(by_origin)
        rng.shuffle(origins)
        t0 = time.perf_counter()
        for origin in origins:
            batch = list(by_origin[origin])
            rng.shuffle(batch)
            result = node.receive_entries(batch)
            assert not result.divergence_detected, (
                f"clean shuffled delivery must not diverge (node {node.node_id}, "
                f"origin {origin}: {result.reason})"
            )
        per_node_delivery.append(time.perf_counter() - t0)
    delivery_total_s = time.perf_counter() - delivery_start

    hashes = {n.state_hash() for n in nodes}
    counts = tuple(len(n.entries()) for n in nodes)
    total = node_count * mutations_per_node
    return SyncScaleResult(
        node_count=node_count,
        mutations_per_node=mutations_per_node,
        total_entries=total,
        mutate_total_s=mutate_total_s,
        delivery_total_s=delivery_total_s,
        per_node_delivery_s=tuple(per_node_delivery),
        converged=len(hashes) == 1 and all(c == total for c in counts),
        state_hash=next(iter(hashes)) if len(hashes) == 1 else "",
        entries_per_node=counts,
    )


# -- 3. serve path at size -------------------------------------------------------


@dataclass
class ServeScaleResult:
    registry_entries: int
    snapshot_construct_ms: float  # PolicyNode build: verify chain + load view
    serve: TimingStats
    served_ok: bool

    def to_dict(self) -> dict:
        return {
            "registry_entries": self.registry_entries,
            "snapshot_construct_ms": self.snapshot_construct_ms,
            "serve_ms": self.serve.to_dict(),
            "served_ok": self.served_ok,
        }


def measure_serve(
    registration: RegistrationScaleResult,
    *,
    repeats: int = SERVE_REPEATS,
) -> ServeScaleResult:
    """Build an opted-in PolicyNode over the (large) registry
    snapshot and time verified serves of distinct policies spread
    across the whole registry."""
    hashes = sorted(registration.payloads)
    if not hashes:
        raise ValueError("no registered policies to serve")
    step = max(1, len(hashes) // min(repeats, len(hashes)))
    targets = hashes[::step][:repeats]

    t0 = time.perf_counter_ns()
    node = PolicyNode(
        node_id="scale-serving-node",
        registry_snapshot_path=registration.snapshot_path,
        payloads=registration.payloads,
        serve_policies=True,
    )
    construct_ms = (time.perf_counter_ns() - t0) / 1e6

    samples_ns: list[int] = []
    served_ok = True
    i = 0
    while len(samples_ns) < repeats:
        target = targets[i % len(targets)]
        i += 1
        t0 = time.perf_counter_ns()
        served = node.serve_policy(target)
        samples_ns.append(time.perf_counter_ns() - t0)
        served_ok = served_ok and served.code == registration.payloads[target]
    return ServeScaleResult(
        registry_entries=registration.entries_completed,
        snapshot_construct_ms=construct_ms,
        serve=TimingStats.from_samples_ns(samples_ns),
        served_ok=served_ok,
    )


# -- 4. the Gate 5 canary at size ------------------------------------------------


def build_canary_mesh_node(total_entries: int, origins: int = CANARY_MESH_ORIGINS) -> SyncNode:
    """A canary node holding `total_entries` mesh entries from
    `origins` origins, built through the real receive path (entries
    built with Gate 3's own builder, delivered in one shuffled
    batch). Lineage verification over the result is Gate 5's own
    verify_mesh_lineage, unchanged."""
    per_origin = total_entries // origins
    entries = []
    for o in range(origins):
        origin = f"canary-origin-{o}"
        prev_hash = GENESIS_HASH
        for seq in range(per_origin):
            entry = build_entry(
                origin=origin,
                seq=seq,
                lamport=seq + 1,
                key=f"{origin}-key-{seq}",
                value=f"value-{seq}",
                prev_hash=prev_hash,
            )
            entries.append(entry)
            prev_hash = entry.entry_hash
    random.Random(DELIVERY_SEED).shuffle(entries)
    node = SyncNode("canary-node")
    result = node.receive_entries(entries)
    assert not result.divergence_detected, "canary mesh build must converge cleanly"
    return node


@dataclass
class CanaryScaleResult:
    registry_entries: int
    mesh_entries: int
    iterations: int
    stage_registry: TimingStats
    stage_mesh: TimingStats
    stage_serve: TimingStats
    end_to_end: TimingStats
    per_transformation: TimingStats
    target_ms: float
    end_to_end_meets_target: bool
    per_transformation_meets_target: bool
    miss_factor: float  # end-to-end median / target; >1 means the target is missed
    all_verified: bool

    def to_dict(self) -> dict:
        return {
            "registry_entries": self.registry_entries,
            "mesh_entries": self.mesh_entries,
            "iterations": self.iterations,
            "stage_registry_ms": self.stage_registry.to_dict(),
            "stage_mesh_ms": self.stage_mesh.to_dict(),
            "stage_serve_ms": self.stage_serve.to_dict(),
            "end_to_end_ms": self.end_to_end.to_dict(),
            "per_transformation_ms": self.per_transformation.to_dict(),
            "target_ms": self.target_ms,
            "end_to_end_meets_target": self.end_to_end_meets_target,
            "per_transformation_meets_target": self.per_transformation_meets_target,
            "miss_factor": self.miss_factor,
            "all_verified": self.all_verified,
        }


def measure_canary_at_size(
    registration: RegistrationScaleResult,
    mesh_entries: int,
    *,
    iterations: int | None = None,
) -> CanaryScaleResult:
    """Gate 5's timed lineage path at one (registry, mesh) size:
    per iteration, time each stage separately AND the whole path -
    registry snapshot chain verification (Gate 2's standalone
    verifier), full mesh lineage verification (Gate 5's own
    function), and one verified serve (Gate 2). Plus one
    per-transformation lineage timing per mesh entry."""
    if iterations is None:
        iterations = canary_iterations(mesh_entries)
    snapshot_path = Path(registration.snapshot_path)
    target = registration.sample_hashes[0]
    serving_node = PolicyNode(
        node_id="canary-serving-node",
        registry_snapshot_path=snapshot_path,
        payloads=registration.payloads,
        serve_policies=True,
    )
    mesh_node = build_canary_mesh_node(mesh_entries)

    registry_samples: list[int] = []
    mesh_samples: list[int] = []
    serve_samples: list[int] = []
    e2e_samples: list[int] = []
    all_verified = True
    for _ in range(iterations):
        e2e_start = time.perf_counter_ns()
        t0 = time.perf_counter_ns()
        entries = verify_registry_snapshot(snapshot_path)
        registry_samples.append(time.perf_counter_ns() - t0)
        ok_registry = any(e["policy_hash"] == target for e in entries)

        t0 = time.perf_counter_ns()
        ok_mesh = verify_mesh_lineage(mesh_node)
        mesh_samples.append(time.perf_counter_ns() - t0)

        t0 = time.perf_counter_ns()
        served = serving_node.serve_policy(target)
        serve_samples.append(time.perf_counter_ns() - t0)
        ok_serve = served.code == registration.payloads[target]

        e2e_samples.append(time.perf_counter_ns() - e2e_start)
        all_verified = all_verified and ok_registry and ok_mesh and ok_serve

    per_entry_samples: list[int] = []
    by_origin: dict[str, list] = {}
    for entry in mesh_node.entries():
        by_origin.setdefault(entry.origin, []).append(entry)
    for chain in by_origin.values():
        chain.sort(key=lambda e: e.seq)
        prev_hash = GENESIS_HASH
        for entry in chain:
            t0 = time.perf_counter_ns()
            ok = verify_entry_lineage(entry, prev_hash)
            per_entry_samples.append(time.perf_counter_ns() - t0)
            assert ok, "clean canary entries must verify at size"
            prev_hash = entry.entry_hash

    end_to_end = TimingStats.from_samples_ns(e2e_samples)
    return CanaryScaleResult(
        registry_entries=registration.entries_completed,
        mesh_entries=len(mesh_node.entries()),
        iterations=iterations,
        stage_registry=TimingStats.from_samples_ns(registry_samples),
        stage_mesh=TimingStats.from_samples_ns(mesh_samples),
        stage_serve=TimingStats.from_samples_ns(serve_samples),
        end_to_end=end_to_end,
        per_transformation=TimingStats.from_samples_ns(per_entry_samples),
        target_ms=TARGET_LINEAGE_MS,
        end_to_end_meets_target=end_to_end.median_ms < TARGET_LINEAGE_MS,
        per_transformation_meets_target=(
            TimingStats.from_samples_ns(per_entry_samples).median_ms < TARGET_LINEAGE_MS
        ),
        miss_factor=end_to_end.median_ms / TARGET_LINEAGE_MS,
        all_verified=all_verified,
    )


# -- the whole suite -------------------------------------------------------------


def run_scale_suite(workdir: str | Path, *, profile: str = "full") -> dict:
    """Run all four measurements and return one results dict.
    profile="small" runs modest sizes for the automated tests."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    if profile == "small":
        reg_sizes, users = (40,), 8
        sync_points = ((4, 25),)
        serve_repeats = 40
        canary_grid = ((40, 100),)
        budget = 60.0
    elif profile == "full":
        reg_sizes, users = REGISTRATION_SIZES, USERS
        sync_points = SYNC_POINTS
        serve_repeats = SERVE_REPEATS
        canary_grid = tuple(
            (r, m) for r in CANARY_REGISTRY_SIZES for m in CANARY_MESH_SIZES
        )
        budget = REGISTRATION_BUDGET_S
    else:
        raise ValueError(f"unknown profile: {profile}")

    results: dict = {"profile": profile, "machine": machine_info()}

    registrations: dict[int, RegistrationScaleResult] = {}
    reg_results = []
    skip_rest = False
    for size in reg_sizes:
        if skip_rest:
            reg_results.append(
                {"entries_target": size, "skipped": True,
                 "reason": "an earlier size exceeded its time budget"}
            )
            continue
        res = measure_registration(
            workdir / f"registry-{size}", size, users=users, budget_s=budget
        )
        registrations[res.entries_completed] = res
        reg_results.append(res.to_dict())
        if not res.completed:
            skip_rest = True
    results["registration"] = reg_results

    results["sync"] = [
        measure_sync(n, per).to_dict() for n, per in sync_points
    ]

    results["serve"] = [
        measure_serve(registrations[size], repeats=serve_repeats).to_dict()
        for size in sorted(registrations)
        if registrations[size].completed
    ]

    canary_results = []
    for reg_size, mesh_size in canary_grid:
        reg = registrations.get(reg_size)
        if reg is None or not reg.completed:
            canary_results.append(
                {"registry_entries": reg_size, "mesh_entries": mesh_size,
                 "skipped": True, "reason": "registry size not completed"}
            )
            continue
        canary_results.append(measure_canary_at_size(reg, mesh_size).to_dict())
    results["canary"] = canary_results
    return results


# -- CLI -------------------------------------------------------------------------


def _fmt_stats(stats: dict | None) -> str:
    if stats is None:
        return "n/a"
    return (
        f"min {stats['min_ms']:.3f} ms, median {stats['median_ms']:.3f} ms, "
        f"mean {stats['mean_ms']:.3f} ms, p95 {stats['p95_ms']:.3f} ms, "
        f"max {stats['max_ms']:.3f} ms (n={stats['count']})"
    )


def print_report(results: dict) -> None:
    m = results["machine"]
    print(f"machine: python {m['python']}, {m['platform']}")
    print()
    print("=== 1. Registration at volume ===")
    for r in results["registration"]:
        if r.get("skipped"):
            print(f"  {r['entries_target']} entries: SKIPPED ({r['reason']})")
            continue
        print(
            f"  {r['entries_completed']} entries, {r['users']} users, "
            f"completed={r['completed']}, file {r['registry_file_bytes']} bytes"
        )
        print(f"    enroll all users: {r['enroll_s']:.3f} s")
        print(
            f"    register total: {r['register_total_s']:.3f} s; "
            f"per registration: {_fmt_stats(r['per_registration'])}"
        )
        print(f"    verify full chain (live channel): {_fmt_stats(r['verify_live_ms'])}")
        print(f"    verify full chain (reload from disk): {_fmt_stats(r['verify_reload_ms'])}")
    print()
    print("=== 2. Sync convergence at size (shuffled delivery) ===")
    for s in results["sync"]:
        per_node = ", ".join(f"{v:.3f}" for v in s["per_node_delivery_s"])
        print(
            f"  {s['node_count']} nodes x {s['mutations_per_node']} = "
            f"{s['total_entries']} entries: mutate {s['mutate_total_s']:.3f} s, "
            f"delivery {s['delivery_total_s']:.3f} s (per node: {per_node}), "
            f"converged={s['converged']}, entries per node={s['entries_per_node']}"
        )
        print(f"    shared state hash: {s['state_hash']}")
    print()
    print("=== 3. Serve path at size ===")
    for s in results["serve"]:
        print(
            f"  registry {s['registry_entries']} entries: snapshot verify+load "
            f"{s['snapshot_construct_ms']:.3f} ms; verified serve: "
            f"{_fmt_stats(s['serve_ms'])}; served_ok={s['served_ok']}"
        )
    print()
    print(f"=== 4. Gate 5 canary lineage at size (target {TARGET_LINEAGE_MS} ms) ===")
    for c in results["canary"]:
        if c.get("skipped"):
            print(
                f"  registry {c['registry_entries']}, mesh {c['mesh_entries']}: "
                f"SKIPPED ({c['reason']})"
            )
            continue
        print(
            f"  registry {c['registry_entries']}, mesh {c['mesh_entries']}, "
            f"{c['iterations']} iterations, all_verified={c['all_verified']}"
        )
        print(f"    stage registry chain: {_fmt_stats(c['stage_registry_ms'])}")
        print(f"    stage mesh lineage:   {_fmt_stats(c['stage_mesh_ms'])}")
        print(f"    stage verified serve: {_fmt_stats(c['stage_serve_ms'])}")
        print(f"    end to end:           {_fmt_stats(c['end_to_end_ms'])}")
        print(f"    per transformation:   {_fmt_stats(c['per_transformation_ms'])}")
        print(
            f"    end-to-end meets 1 ms target: {c['end_to_end_meets_target']} "
            f"(miss factor x{c['miss_factor']:.1f}); per-transformation meets "
            f"target: {c['per_transformation_meets_target']}"
        )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    profile = "full"
    out_path = None
    workdir = None
    while argv:
        arg = argv.pop(0)
        if arg == "--profile":
            profile = argv.pop(0)
        elif arg == "--out":
            out_path = argv.pop(0)
        elif arg == "--workdir":
            workdir = argv.pop(0)
        else:
            raise SystemExit(f"unknown argument: {arg}")
    if workdir is None:
        workdir = tempfile.mkdtemp(prefix="pba-scale-")
    results = run_scale_suite(workdir, profile=profile)
    print_report(results)
    print()
    print(f"scale state left in: {workdir}")
    if out_path:
        Path(out_path).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(f"results JSON: {out_path}")
    ok = (
        all(r.get("completed", False) for r in results["registration"] if not r.get("skipped"))
        and all(s["converged"] for s in results["sync"])
        and all(s["served_ok"] for s in results["serve"])
        and all(c.get("all_verified", False) for c in results["canary"] if not c.get("skipped"))
    )
    print("scale run sane:", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
