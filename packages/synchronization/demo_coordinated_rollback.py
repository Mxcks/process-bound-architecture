"""
Runnable walkthrough of coordinated rollback (experiment 013).

    python3 packages/synchronization/demo_coordinated_rollback.py

Three in-process nodes converge and take a shared checkpoint, the
mesh moves on, a forked entry (Gate 3's split-brain fault) is
detected by one node and its local rollback lands somewhere else
than the shared checkpoint, and a coordinated rollback then
restores all three to that one checkpoint. A fourth node that
skips the rollback is shown divergent by the checkpoint check,
restores the checkpoint itself, and rejoins. Prints the case 6
outcomes as True/False at the end. Registers nothing, needs no
files. Standard library only.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from coordinated_rollback import (  # noqa: E402
    check_node_against_checkpoint,
    coordinate_rollback,
    divergent_node_ids,
    take_shared_checkpoint,
    verify_mesh_at_checkpoint,
)
from sync_mesh import GENESIS_HASH, SyncNode, build_entry  # noqa: E402


def _full_mesh_sync(nodes):
    for _ in range(10):
        accepted = 0
        for i, a in enumerate(nodes):
            for b in nodes[i + 1:]:
                ra, rb = a.sync_with(b)
                assert not ra.divergence_detected and not rb.divergence_detected
                accepted += ra.accepted + rb.accepted
        if accepted == 0:
            return
    raise AssertionError("mesh did not quiesce in 10 rounds")


def main() -> int:
    outcomes = {}

    print("=== Setup: four nodes converge, shared checkpoint taken ===")
    a, b, c, d = (SyncNode(f"node-{x}") for x in "abcd")
    a.mutate("mode", "normal")
    a.mutate("level", "1")
    b.mutate("region", "north")
    c.mutate("locale", "en-US")
    d.mutate("tier", "member")
    _full_mesh_sync([a, b, c, d])
    checkpoint = take_shared_checkpoint([a, b, c, d])
    print("checkpoint state:", checkpoint.state)
    print("checkpoint heads:", checkpoint.origin_heads)
    outcomes["checkpoint_shared"] = all(
        check_node_against_checkpoint(n, checkpoint).ok for n in (a, b, c, d)
    )
    print("all four at the checkpoint:", outcomes["checkpoint_shared"])

    print()
    print("=== The mesh moves on, then a fork fault is injected ===")
    a.mutate("post-checkpoint", "from-a")
    _full_mesh_sync([a, b, c, d])
    post_hash = a.state_hash()
    real_first = next(e for e in a.entries() if e.origin == "node-a" and e.seq == 0)
    fork = build_entry(
        origin="node-a", seq=0, lamport=real_first.lamport,
        key=real_first.key, value="split-brain", prev_hash=GENESIS_HASH,
    )
    result = b.receive_entries([fork])
    print("node-b sync result:", result.to_dict())
    print("node-b after LOCAL rollback is at the post-checkpoint state,"
          " not the shared checkpoint:",
          b.state_hash() == post_hash and b.state_hash() != checkpoint.state_hash)
    outcomes["local_rollback_misses_shared_checkpoint"] = (
        result.divergence_detected
        and result.rolled_back
        and b.state_hash() == post_hash
        and b.state_hash() != checkpoint.state_hash
    )

    print()
    print("=== Coordinated rollback of node-a, node-b, node-c ===")
    a.mutate("after-fault", "from-a")
    a.sync_with(c)
    report = coordinate_rollback([a, b, c], checkpoint)
    print("report:", report.to_dict())
    outcomes["coordinated_rollback"] = report.all_restored and all(
        n.state_hash() == checkpoint.state_hash for n in (a, b, c)
    )
    print("participants restored to the checkpoint:", outcomes["coordinated_rollback"])

    print()
    print("=== Negative control: node-d skipped the rollback ===")
    checks = verify_mesh_at_checkpoint([a, b, c, d], checkpoint)
    for check in checks:
        print("check:", check.to_dict())
    print("divergent nodes:", divergent_node_ids(checks))
    outcomes["skipped_node_detected"] = divergent_node_ids(checks) == ["node-d"]
    print("node-d detected divergent, not accepted:", outcomes["skipped_node_detected"])

    print()
    print("=== Node-d restores the checkpoint itself and rejoins ===")
    coordinate_rollback([d], checkpoint)
    b.mutate("after-rejoin", "from-b")
    _full_mesh_sync([a, b, c, d])
    outcomes["rejoin_and_converge"] = (
        a.state_hash() == b.state_hash() == c.state_hash() == d.state_hash()
        and d.materialized_state()["after-rejoin"] == "from-b"
    )
    print("all four converge on new work:", outcomes["rejoin_and_converge"])

    print()
    print("Coordinated rollback outcomes:", outcomes)
    return 0 if all(outcomes.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
