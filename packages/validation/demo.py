"""
Runnable walkthrough of the Gate 5 verification gate prototype.

Builds the canary scenario (Gate 1 registration, Gate 2 serving
node, Gate 3 mesh under load), measures real lineage times, injects
a Byzantine fork and a tampered entry, watches the halt-and-rollback
path fire, and prints the gate's single verdict with the measured
benchmark numbers stated plainly, whichever way they land.

    python3 packages/validation/demo.py

Standard library only. All state lives in a temp directory that is
left on disk and printed at the end.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate5_harness import Gate5Harness  # noqa: E402


def fmt(stats) -> str:
    return (
        f"min {stats.min_ms:.4f} ms, median {stats.median_ms:.4f} ms, "
        f"mean {stats.mean_ms:.4f} ms, p95 {stats.p95_ms:.4f} ms, "
        f"max {stats.max_ms:.4f} ms (n={stats.count})"
    )


def main() -> int:
    harness = Gate5Harness()
    outcomes = {}

    print("=== Outcome 1: canary measures end-to-end lineage time under load ===")
    canary = harness.run_canary(iterations=200)
    print(
        f"load: {canary.mesh_entries} mesh entries, "
        f"{canary.registry_entries} registry entries, "
        f"{canary.iterations} timed runs"
    )
    print(f"end-to-end lineage verification: {fmt(canary.end_to_end)}")
    print(f"per transformation (one entry):  {fmt(canary.per_transformation)}")
    print(f"target from docs/architecture/06: under {canary.target_ms} ms")
    print(
        "per-transformation median meets target:",
        canary.per_transformation_meets_target,
    )
    print("end-to-end median meets target:     ", canary.end_to_end_meets_target)
    print("policy served verified on every run:", canary.served_ok)
    outcomes[1] = canary.served_ok and canary.end_to_end.count == 200

    print()
    print("=== Outcome 2: forced fault halts rollout and rolls back exactly ===")
    checkpoint = harness.take_checkpoint()
    print(f"checkpoint state hash: {checkpoint.state_hash}")
    for kind in ("forked", "tampered"):
        fault = harness.inject_fault(kind, checkpoint)
        print(
            f"{kind}: divergence={fault.divergence_detected} "
            f"(reason={fault.reason}), rolled_back={fault.rolled_back}, "
            f"restored_exactly={fault.restored_exactly}, "
            f"halt={fault.halt_triggered}"
        )
    print(f"rollout state now: {harness.rollout_state}")
    outcomes[2] = (
        harness.rollout_state == "halted_and_rolled_back"
        and checkpoint.matches(harness.canary_node)
    )

    print()
    print("=== Outcome 3: one verdict from measured evidence ===")
    verdict = Gate5Harness().run_gate(iterations=50)
    for check in verdict.checks:
        print(f"  [{'OK' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
    b = verdict.benchmark
    print(
        f"benchmark: per-transformation median {b['per_transformation_median_ms']:.4f} ms "
        f"(target {b['target_ms']} ms, met: {b['per_transformation_meets_target']}); "
        f"end-to-end median {b['end_to_end_median_ms']:.4f} ms "
        f"(met: {b['end_to_end_meets_target']})"
    )
    print("gate verdict:", "PASS" if verdict.passed else "FAIL")
    outcomes[3] = verdict.passed

    print()
    for n in (1, 2, 3):
        print(f"outcome {n}: {outcomes[n]}")
    print(f"Demo state left in: {harness.workdir}")
    return 0 if all(outcomes.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
