"""
Runnable demo of anchor consultation in Gate 3 sync
(experiment 015).

    python3 packages/synchronization/demo_anchor_consultation.py

Walks through sync-mesh test case 8 in order and prints what
happened: an anchored node accepts a peer holding exactly the
anchored entry set and converges; the negative control - an
origin chain rewritten from genesis, internally consistent - is
accepted by a node with no anchor (the pre-015 behavior, stated)
and flagged divergent (anchor_mismatch), not accepted, by the
anchored node; and honest growth past the anchor is refused as
anchor_stale until the grown state is re-anchored, after which
the same sync lands. All state is in a temporary directory that
holds only the anchor file. Nothing is deployed.
"""

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from anchor import write_mesh_anchor  # noqa: E402  (exp 012 mesh anchor)
from anchored_sync import AnchoredSyncNode  # noqa: E402
from sync_mesh import SyncNode  # noqa: E402


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-anchor-consult-sync-demo-"))
    anchor_path = tmp / "outside" / "mesh-anchor.json"

    a = SyncNode("node-a")
    b = SyncNode("node-b")
    a.mutate("greeting", "hello")
    a.mutate("mode", "member")
    b.mutate("region", "north")
    a.sync_with(b)
    write_mesh_anchor(a, anchor_path)
    print("Setup. Honest nodes converged; mesh anchor written outside the nodes.")

    # 1. Genuine peer: accepted, converged, nothing flagged.
    victim = AnchoredSyncNode("victim", anchor_path=anchor_path)
    result = victim.receive_entries(a.entries())
    print("1. Anchored node accepted the genuine peer:",
          not result.divergence_detected,
          "- converged:", victim.state_hash() == a.state_hash(),
          "- anchor_status:", victim.anchor_status())

    # 2. Negative control: a rewritten origin chain. A node with
    # no anchor accepts it (pre-015); the anchored node flags it
    # anchor_mismatch and keeps its state.
    attacker = SyncNode("node-a")
    attacker.mutate("greeting", "pwned")
    attacker.mutate("mode", "admin")
    plain = SyncNode("plain-victim")
    plain_result = plain.receive_entries(attacker.entries())
    print("2. Plain node accepted the rewrite (pre-015 behavior):",
          not plain_result.divergence_detected)
    # A FRESH anchored node is the case the anchor exists for: it
    # never saw the original entries, so Gate 3's fork detection
    # has nothing to compare against - only the anchor can judge.
    fresh = AnchoredSyncNode("fresh-victim", anchor_path=anchor_path)
    result = fresh.receive_entries(attacker.entries())
    print("   Anchored node flagged the rewrite:", result.divergence_detected,
          "- reason:", result.reason, "- rolled back:", result.rolled_back,
          "- accepted nothing:", fresh.entries() == [])

    # 3. Honest growth: refused as anchor_stale until re-anchored.
    a.mutate("mode", "admin")
    result = victim.receive_entries(a.entries())
    print("3. Anchored node refused honest growth:", result.divergence_detected,
          "- reason:", result.reason,
          "- state still:", victim.materialized_state()["mode"])
    write_mesh_anchor(a, anchor_path)
    result = victim.receive_entries(a.entries())
    print("   After re-anchoring: accepted:", not result.divergence_detected,
          "- converged on grown state:", victim.state_hash() == a.state_hash())

    print("Demo state left in:", tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
