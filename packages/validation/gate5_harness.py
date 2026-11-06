"""
Process-Bound Architecture - Gate 5 verification gate prototype.

The first code in the validation layer (packages/validation), and the
harness layer 06 (Verification and Thresholds) describes: a canary
that measures, a forced fault that must halt and roll back, and one
verdict computed from the measured evidence. Python standard library
only. No network, no database, no dependencies.

Builds on the earlier gates without modifying them:
- Gate 1 (`packages/registration/registration.py`): the policy under
  test is registered through the real RegistrationChannel.
- Gate 2 (`packages/policy-library/policy_node.py`): the policy is
  served by an opted-in PolicyNode from a copied registry snapshot,
  verified from the record alone.
- Gate 3 (`packages/synchronization/sync_mesh.py`): the delivery
  state lives on SyncNodes that converge with no central lock; the
  forced fault is a forked entry built with Gate 3's own builder.
- Gate 4 (`packages/policy-library/replication.py`) is not needed by
  the gate itself; the canary scenario is a single serving node plus
  a converged mesh, stated as a limit below.

WHAT THIS PROTOTYPE DEMONSTRATES (the Gate 5 outcomes)
------------------------------------------------------
1. A canary run actually measures end-to-end lineage time. The path
   measured, per run, is the lineage verification of the whole
   scenario: re-verify the registry snapshot's hash chain (Gate 2's
   standalone verifier), re-verify the mesh lineage of the canary
   node (every entry's EAI and hash recompute, every per-origin
   chain link), and serve the policy through Gate 2's verified
   serve. Per-transformation time (one entry's lineage: EAI
   recompute + hash recompute + chain-link check) is measured
   separately, per entry. All numbers are real measurements from
   this machine via time.perf_counter_ns(); nothing is estimated.
   The 1.0 ms figure from docs/architecture/06 is a TARGET the
   measurements are reported against (fields named *_meets_target),
   never a claim: in this Python prototype the per-transformation
   number is expected to land under it and the end-to-end number
   over it, and the report says whichever is true.

2. A forced fault halts rollout and rolls back, observed. After a
   clean baseline and a checkpoint, a Byzantine-style forked entry
   (individually valid hash, false history: Gate 3's fork case) is
   injected into the canary node. The harness observes Gate 3's
   divergence detection, marks the rollout HALTED, and the state
   after rollback is compared to the checkpoint exactly:
   materialized state, state hash, and the entry-id set must all be
   identical. A tampered entry (hash does not recompute) is injected
   the same way as a second fault case.

3. One pass/fail verdict from measured evidence. The verdict is
   computed in code from the check results (canary path completed,
   lineage actually measured, clean run divergence-free, fault
   detected, rollback exact, halt triggered). Any failed check makes
   the verdict FAIL, and the checks carry their evidence as detail
   strings. The sub-millisecond benchmark is reported inside the
   verdict but is NOT one of the pass checks: per
   docs/architecture/08, Gate 5's outcome is that lineage is
   *measured* and faults *halt and roll back*, with the number as a
   benchmark to report honestly, not a promise the gate certifies.

HONEST LIMITS (prototype, NOT production)
-----------------------------------------
- Everything is in-process on one machine: the "canary" is a local
  scenario (one registration channel used at setup, one serving
  node, four mesh nodes), not a staged edge deployment, and "load"
  is a converged mesh of 100 entries, not thousands of concurrent
  users. The source proposals' scale claims are not demonstrated.
- Rollback and halt are the Gate 3 node's local rollback plus a
  harness flag. There is no coordinated mesh-wide rollback, no
  rollout system to halt, and no signalling to other nodes beyond
  the SyncResult the injection returns.
- The fault is injected by calling receive_entries with a bad entry,
  the same path sync uses. It proves detection and rollback on that
  path; it is not an adversarial network attacker, and Gate 3's
  stated limit stands: a fork is only detected once both versions
  meet, and a full-chain rewrite with the original suppressed would
  verify on its own (no external anchor exists yet).
- Timing is single-machine perf_counter_ns() around Python function
  calls. It includes interpreter and JSON costs a production runtime
  (Wasm/eBPF, per the proposals) would not have, and excludes
  network costs a real deployment would have. The numbers describe
  this prototype on this machine, nothing else.
- The gate judges this one scenario. It does not verify proofs
  (zk-SNARK / Merkle attestations from the proposals are unbuilt),
  does not check liveness or availability (Gate 4's reports do),
  and revocation still does not exist anywhere in the stack.
"""

from __future__ import annotations

import hashlib
import shutil
import statistics
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

from policy_node import PolicyNode, verify_registry_snapshot  # noqa: E402
from registration import RegistrationChannel, sign_library  # noqa: E402
from sync_mesh import (  # noqa: E402
    GENESIS_HASH,
    SyncNode,
    StateEntry,
    SyncResult,
    build_entry,
    compute_eai,
    tamper_entry,
)


TARGET_LINEAGE_MS = 1.0  # docs/architecture/06 benchmark: measure, never assume

CANARY_AUTHOR = "alice"
CANARY_POLICY_NAME = "member-access"
CANARY_POLICY_VERSION = "1.0.0"
CANARY_CODE = b"policy: allow if caller.tier == 'member'\n"


# -- timing -----------------------------------------------------------------


@dataclass(frozen=True)
class TimingStats:
    """Summary of real measured samples (nanoseconds in, ms out)."""

    count: int
    min_ms: float
    median_ms: float
    mean_ms: float
    p95_ms: float
    max_ms: float

    @classmethod
    def from_samples_ns(cls, samples_ns: list[int]) -> "TimingStats":
        if not samples_ns:
            raise ValueError("no timing samples")
        ms = sorted(s / 1e6 for s in samples_ns)
        p95_index = min(len(ms) - 1, int(0.95 * len(ms)))
        return cls(
            count=len(ms),
            min_ms=ms[0],
            median_ms=statistics.median(ms),
            mean_ms=statistics.fmean(ms),
            p95_ms=ms[p95_index],
            max_ms=ms[-1],
        )

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "min_ms": self.min_ms,
            "median_ms": self.median_ms,
            "mean_ms": self.mean_ms,
            "p95_ms": self.p95_ms,
            "max_ms": self.max_ms,
        }


# -- lineage verification (the path the canary times) -------------------------


def verify_entry_lineage(entry: StateEntry, expected_prev_hash: str) -> bool:
    """One transformation's lineage: its Execution-Attached Identity
    recomputes (PBA-EAI-v1), its hash recomputes from its body, and
    it links to its origin-chain predecessor."""
    if entry.prev_hash != expected_prev_hash:
        return False
    if compute_eai(entry.origin, entry.seq) != entry.eai:
        return False
    return entry.hash_verifies()


def verify_mesh_lineage(node: SyncNode) -> bool:
    """Full lineage of one node's entry set: every entry's lineage
    verifies and every origin chain is contiguous from genesis."""
    by_origin: dict[str, list[StateEntry]] = {}
    for entry in node.entries():
        by_origin.setdefault(entry.origin, []).append(entry)
    for chain in by_origin.values():
        chain.sort(key=lambda e: e.seq)
        prev_hash = GENESIS_HASH
        for expected_seq, entry in enumerate(chain):
            if entry.seq != expected_seq:
                return False
            if not verify_entry_lineage(entry, prev_hash):
                return False
            prev_hash = entry.entry_hash
    return True


# -- reports ------------------------------------------------------------------


@dataclass(frozen=True)
class CanaryReport:
    iterations: int
    mesh_entries: int  # the load: lineage size verified per run
    registry_entries: int
    end_to_end: TimingStats
    per_transformation: TimingStats
    target_ms: float
    end_to_end_meets_target: bool  # median < target
    per_transformation_meets_target: bool  # median < target
    served_ok: bool
    end_to_end_samples_ns: tuple[int, ...]
    per_transformation_samples_ns: tuple[int, ...]

    def to_dict(self) -> dict:
        return {
            "iterations": self.iterations,
            "mesh_entries": self.mesh_entries,
            "registry_entries": self.registry_entries,
            "end_to_end": self.end_to_end.to_dict(),
            "per_transformation": self.per_transformation.to_dict(),
            "target_ms": self.target_ms,
            "end_to_end_meets_target": self.end_to_end_meets_target,
            "per_transformation_meets_target": self.per_transformation_meets_target,
            "served_ok": self.served_ok,
        }


@dataclass(frozen=True)
class Checkpoint:
    state: dict
    state_hash: str
    entry_ids: frozenset

    def matches(self, node: SyncNode) -> bool:
        """Exact equality: same materialized state, same state hash,
        same entry set. Nothing weaker counts as rollback."""
        return (
            node.materialized_state() == self.state
            and node.state_hash() == self.state_hash
            and node.entry_ids() == self.entry_ids
        )


@dataclass(frozen=True)
class FaultReport:
    kind: str  # "forked" | "tampered"
    divergence_detected: bool
    reason: str | None
    rolled_back: bool
    restored_exactly: bool
    halt_triggered: bool

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "divergence_detected": self.divergence_detected,
            "reason": self.reason,
            "rolled_back": self.rolled_back,
            "restored_exactly": self.restored_exactly,
            "halt_triggered": self.halt_triggered,
        }


@dataclass(frozen=True)
class GateCheck:
    name: str
    ok: bool
    detail: str

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class GateVerdict:
    """The gate's one verdict: computed from the checks, in code.

    passed is exactly all(check.ok). The benchmark block reports the
    measured lineage numbers against the sub-millisecond target and
    is deliberately NOT part of passed (see module docstring).
    """

    passed: bool
    checks: tuple[GateCheck, ...]
    benchmark: dict
    canary: dict | None
    faults: tuple[dict, ...] = field(default=tuple)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": [c.to_dict() for c in self.checks],
            "benchmark": self.benchmark,
            "canary": self.canary,
            "faults": list(self.faults),
        }


# -- the harness ----------------------------------------------------------------


class Gate5Harness:
    """One canary scenario plus the gate run over it.

    Scenario: a policy registered through Gate 1, a snapshot copied
    to an opted-in Gate 2 serving node, and a Gate 3 mesh of
    `mesh_node_count` nodes that each made `mutations_per_node`
    mutations and then converged. The canary node is mesh node 0:
    the node the gate watches, times, faults, and rolls back.
    """

    def __init__(
        self,
        workdir: str | Path | None = None,
        *,
        mesh_node_count: int = 4,
        mutations_per_node: int = 25,
    ):
        if workdir is None:
            workdir = tempfile.mkdtemp(prefix="pba-gate5-")
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.mesh_node_count = mesh_node_count
        self.mutations_per_node = mutations_per_node

        self.rollout_state = "not_started"  # -> proceeding -> halted_and_rolled_back
        self._built = False
        self.record = None
        self.serving_node: PolicyNode | None = None
        self.mesh: list[SyncNode] = []
        self.snapshot_path: Path | None = None

    @property
    def canary_node(self) -> SyncNode:
        return self.mesh[0]

    # -- scenario ---------------------------------------------------------

    def build_scenario(self) -> None:
        central = self.workdir / "central"
        registry_log = central / "registry.jsonl"
        audit_log = central / "audit.jsonl"
        channel = RegistrationChannel(registry_log, audit_log)

        # The policy under test, plus two more registrations so the
        # registry chain the canary re-verifies has real length.
        alice_key = channel.enroll_user(CANARY_AUTHOR)
        signature = sign_library(
            alice_key, CANARY_AUTHOR, CANARY_POLICY_NAME, CANARY_POLICY_VERSION, CANARY_CODE
        )
        self.record, created = channel.register(
            author=CANARY_AUTHOR,
            name=CANARY_POLICY_NAME,
            version=CANARY_POLICY_VERSION,
            code=CANARY_CODE,
            signature=signature,
        )
        assert created
        bob_secret = channel.enroll_user("bob")
        for name, code in (
            ("rate-limit", b"policy: limit 100/min\n"),
            ("audit-trail", b"policy: log every access\n"),
        ):
            channel.register(
                author="bob",
                name=name,
                version="1.0.0",
                code=code,
                signature=sign_library(bob_secret, "bob", name, "1.0.0", code),
            )

        # Distribution: the serving node's own copy of the record.
        node_dir = self.workdir / "serving-node"
        node_dir.mkdir(exist_ok=True)
        self.snapshot_path = node_dir / "registry-snapshot.jsonl"
        shutil.copyfile(registry_log, self.snapshot_path)
        self.serving_node = PolicyNode(
            node_id="serving-node",
            registry_snapshot_path=self.snapshot_path,
            payloads={self.record.policy_hash: CANARY_CODE},
            serve_policies=True,
        )

        # Delivery state on the mesh: every node mutates locally
        # (no lock, no coordinator), then all entries are exchanged.
        self.mesh = [SyncNode(f"edge-{i}") for i in range(self.mesh_node_count)]
        for i, node in enumerate(self.mesh):
            for j in range(self.mutations_per_node):
                if j == 0:
                    node.mutate(
                        f"policy:{self.record.policy_hash}",
                        f"delivered-by-edge-{i}",
                    )
                elif j % 5 == 0:
                    node.mutate("shared-key", f"from-edge-{i}-at-{j}")
                else:
                    node.mutate(f"edge-{i}-key-{j}", f"value-{j}")
        everything: list[StateEntry] = []
        for node in self.mesh:
            everything.extend(node.entries())
        for node in self.mesh:
            result = node.receive_entries(everything)
            assert not result.divergence_detected, "clean scenario must converge"

        self.rollout_state = "proceeding"
        self._built = True

    # -- the timed path ------------------------------------------------------

    def verify_lineage_path_once(self) -> tuple[int, bool]:
        """One end-to-end lineage verification. Returns (ns, ok):
        registry chain, canary-node mesh lineage, verified serve."""
        start = time.perf_counter_ns()
        entries = verify_registry_snapshot(self.snapshot_path)
        registry_ok = any(
            e["policy_hash"] == self.record.policy_hash for e in entries
        )
        mesh_ok = verify_mesh_lineage(self.canary_node)
        served = self.serving_node.serve_policy(self.record.policy_hash)
        serve_ok = served.code == CANARY_CODE and served.record_hash == self.record.record_hash
        elapsed = time.perf_counter_ns() - start
        return elapsed, (registry_ok and mesh_ok and serve_ok)

    def run_canary(self, iterations: int = 100) -> CanaryReport:
        """Measure the lineage path `iterations` times, plus one
        lineage timing per mesh entry (per transformation)."""
        if not self._built:
            self.build_scenario()

        end_to_end_samples: list[int] = []
        served_ok = True
        for _ in range(iterations):
            ns, ok = self.verify_lineage_path_once()
            end_to_end_samples.append(ns)
            served_ok = served_ok and ok

        # Per-transformation: time each entry's lineage verification
        # individually, with its real chain predecessor.
        per_entry_samples: list[int] = []
        by_origin: dict[str, list[StateEntry]] = {}
        for entry in self.canary_node.entries():
            by_origin.setdefault(entry.origin, []).append(entry)
        for chain in by_origin.values():
            chain.sort(key=lambda e: e.seq)
            prev_hash = GENESIS_HASH
            for entry in chain:
                start = time.perf_counter_ns()
                ok = verify_entry_lineage(entry, prev_hash)
                per_entry_samples.append(time.perf_counter_ns() - start)
                assert ok, "clean canary entries must verify"
                prev_hash = entry.entry_hash

        end_to_end = TimingStats.from_samples_ns(end_to_end_samples)
        per_transformation = TimingStats.from_samples_ns(per_entry_samples)
        registry_entries = verify_registry_snapshot(self.snapshot_path)
        return CanaryReport(
            iterations=iterations,
            mesh_entries=len(self.canary_node.entries()),
            registry_entries=len(registry_entries),
            end_to_end=end_to_end,
            per_transformation=per_transformation,
            target_ms=TARGET_LINEAGE_MS,
            end_to_end_meets_target=end_to_end.median_ms < TARGET_LINEAGE_MS,
            per_transformation_meets_target=(
                per_transformation.median_ms < TARGET_LINEAGE_MS
            ),
            served_ok=served_ok,
            end_to_end_samples_ns=tuple(end_to_end_samples),
            per_transformation_samples_ns=tuple(per_entry_samples),
        )

    # -- checkpoint, fault, rollback -------------------------------------------

    def take_checkpoint(self) -> Checkpoint:
        node = self.canary_node
        return Checkpoint(
            state=node.materialized_state(),
            state_hash=node.state_hash(),
            entry_ids=frozenset(node.entry_ids()),
        )

    def inject_fault(self, kind: str, checkpoint: Checkpoint) -> FaultReport:
        """Inject one bad entry into the canary node through the
        normal receive path and observe detection, halt, rollback."""
        node = self.canary_node
        target = next(e for e in node.entries() if e.origin != node.node_id)
        if kind == "forked":
            # Byzantine-style: an individually valid hash claiming
            # the same place in another origin's history, with
            # different content. Not a hash error; a false history.
            bad = build_entry(
                origin=target.origin,
                seq=target.seq,
                lamport=target.lamport,
                key=target.key,
                value=f"BYZANTINE: rewritten by a bad actor (was {target.value!r})",
                prev_hash=target.prev_hash,
            )
        elif kind == "tampered":
            bad = tamper_entry(target, value="TAMPERED: value changed, hash kept")
        else:
            raise ValueError(f"unknown fault kind: {kind}")

        result: SyncResult = node.receive_entries([bad])
        if result.divergence_detected and result.rolled_back:
            self.rollout_state = "halted_and_rolled_back"
        return FaultReport(
            kind=kind,
            divergence_detected=result.divergence_detected,
            reason=result.reason,
            rolled_back=result.rolled_back,
            restored_exactly=checkpoint.matches(node),
            halt_triggered=self.rollout_state == "halted_and_rolled_back",
        )

    # -- the gate ----------------------------------------------------------------

    def run_gate(self, iterations: int = 100) -> GateVerdict:
        checks: list[GateCheck] = []
        canary: CanaryReport | None = None
        faults: list[FaultReport] = []

        if not self._built:
            try:
                self.build_scenario()
            except Exception as exc:  # a broken scenario is a failed gate
                checks.append(
                    GateCheck("scenario_built", False, f"{type(exc).__name__}: {exc}")
                )
                return self._verdict(checks, canary, faults)
        checks.append(
            GateCheck(
                "scenario_built",
                True,
                "policy registered (Gate 1), serving node holds a verified "
                "snapshot (Gate 2), mesh converged (Gate 3)",
            )
        )

        converged = len({n.state_hash() for n in self.mesh}) == 1
        clean = all(not n.divergence_log for n in self.mesh)
        checks.append(
            GateCheck(
                "clean_run_no_divergence",
                converged and clean,
                f"mesh state hashes equal: {converged}; "
                f"divergence events in clean run: "
                f"{sum(len(n.divergence_log) for n in self.mesh)}",
            )
        )

        try:
            canary = self.run_canary(iterations=iterations)
        except Exception as exc:
            checks.append(
                GateCheck(
                    "canary_path_completed", False, f"{type(exc).__name__}: {exc}"
                )
            )
            checks.append(
                GateCheck("lineage_measured", False, "canary did not complete")
            )
            return self._verdict(checks, canary, faults)

        checks.append(
            GateCheck(
                "canary_path_completed",
                canary.served_ok,
                "register -> sync -> serve ran end to end; served bytes "
                "match the registered policy on every timed run",
            )
        )
        checks.append(
            GateCheck(
                "lineage_measured",
                canary.end_to_end.count == iterations
                and canary.per_transformation.count == canary.mesh_entries
                and canary.end_to_end.median_ms > 0,
                f"{canary.end_to_end.count} end-to-end samples and "
                f"{canary.per_transformation.count} per-transformation samples, "
                f"real perf_counter_ns measurements from this machine",
            )
        )

        checkpoint = self.take_checkpoint()
        for kind in ("forked", "tampered"):
            fault = self.inject_fault(kind, checkpoint)
            faults.append(fault)
            checks.append(
                GateCheck(
                    f"forced_fault_{kind}_detected",
                    fault.divergence_detected and fault.reason is not None,
                    f"receive_entries reported divergence "
                    f"(reason={fault.reason}) for the injected {kind} entry",
                )
            )
            checks.append(
                GateCheck(
                    f"rollback_exact_after_{kind}",
                    fault.rolled_back and fault.restored_exactly,
                    "state, state hash, and entry set identical to the "
                    "pre-fault checkpoint"
                    if fault.restored_exactly
                    else "post-fault state differs from the checkpoint",
                )
            )
        checks.append(
            GateCheck(
                "halt_triggered",
                self.rollout_state == "halted_and_rolled_back",
                f"rollout_state={self.rollout_state}",
            )
        )
        return self._verdict(checks, canary, faults)

    def _verdict(
        self,
        checks: list[GateCheck],
        canary: CanaryReport | None,
        faults: list[FaultReport],
    ) -> GateVerdict:
        benchmark = {
            "target_ms": TARGET_LINEAGE_MS,
            "per_transformation_median_ms": (
                canary.per_transformation.median_ms if canary else None
            ),
            "per_transformation_meets_target": (
                canary.per_transformation_meets_target if canary else None
            ),
            "end_to_end_median_ms": canary.end_to_end.median_ms if canary else None,
            "end_to_end_meets_target": (
                canary.end_to_end_meets_target if canary else None
            ),
            "note": (
                "Benchmark reported from measurement, per docs/architecture/08 "
                "it is not a pass check and not a promise."
            ),
        }
        return GateVerdict(
            passed=all(c.ok for c in checks),
            checks=tuple(checks),
            benchmark=benchmark,
            canary=canary.to_dict() if canary else None,
            faults=tuple(f.to_dict() for f in faults),
        )


def state_fingerprint(node: SyncNode) -> str:
    """Short digest of a node's state hash, for demo printing."""
    return hashlib.sha256(node.state_hash().encode("utf-8")).hexdigest()[:16]
