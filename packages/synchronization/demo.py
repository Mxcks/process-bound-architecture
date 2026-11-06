"""
Runnable walkthrough of the Gate 3 synchronization mesh prototype.

Registers nothing and needs no files: three in-process nodes mutate
concurrently, converge, survive a partition and rejoin, and detect a
forked (split-brain) entry and roll back. Prints the three Gate 3
outcomes as True/False at the end.

    python3 packages/synchronization/demo.py

Standard library only.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync_mesh import GENESIS_HASH, SyncNode, build_entry  # noqa: E402


def main() -> int:
    outcomes = {}

    print("=== Outcome 1: concurrent mutations converge, no central lock ===")
    a, b, c = SyncNode("node-a"), SyncNode("node-b"), SyncNode("node-c")
    a.mutate("region", "us-central")
    b.mutate("tier", "member")
    c.mutate("locale", "en-US")
    a.mutate("shared", "from-a")
    c.mutate("shared", "from-c")
    everything = a.entries() + b.entries() + c.entries()
    for node in (a, b, c):
        node.receive_entries(everything)
    print("states:", a.materialized_state())
    print("hashes equal:", a.state_hash() == b.state_hash() == c.state_hash())
    outcomes[1] = (
        a.materialized_state() == b.materialized_state() == c.materialized_state()
        and a.state_hash() == b.state_hash() == c.state_hash()
    )

    print()
    print("=== Outcome 2: partitioned node rejoins and converges ===")
    b.mutate("while-c-away", "written-by-b")
    a.sync_with(b)
    c.mutate("while-away", "written-by-c")
    print("during partition, c differs:", a.state_hash() != c.state_hash())
    for x, y in ((a, b), (a, c), (b, c)):
        x.sync_with(y)
    print("after rejoin, states:", a.materialized_state())
    outcomes[2] = (
        a.state_hash() == b.state_hash() == c.state_hash()
        and a.materialized_state()["while-c-away"] == "written-by-b"
        and a.materialized_state()["while-away"] == "written-by-c"
    )
    print("outcome 2:", outcomes[2])

    print()
    print("=== Outcome 3: split-brain fork detected, automated rollback ===")
    before = b.materialized_state()
    real_first = next(e for e in a.entries() if e.origin == "node-a" and e.seq == 0)
    fork = build_entry(
        origin="node-a", seq=0, lamport=1,
        key=real_first.key, value="split-brain", prev_hash=GENESIS_HASH,
    )
    result = b.receive_entries([fork])
    print("sync result:", result.to_dict())
    print("divergence log:", [e.to_dict() for e in b.divergence_log])
    print("state unchanged after rollback:", b.materialized_state() == before)
    outcomes[3] = (
        result.divergence_detected
        and result.rolled_back
        and result.reason == "forked_entry"
        and b.materialized_state() == before
    )

    print()
    print("Gate 3 outcomes:", outcomes)
    return 0 if all(outcomes.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
