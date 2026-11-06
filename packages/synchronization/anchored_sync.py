"""
Process-Bound Architecture - anchor consultation for Gate 3 sync
(experiment 015, test case 8 in tests/test-cases/registration.md;
cross-reference case 8 in tests/test-cases/sync-mesh.md).

Python standard library only. Builds on Gate 3's SyncNode without
modifying its receive logic: AnchoredSyncNode is a SyncNode that
consults experiment 012's mesh anchor (anchor.py in this package,
its check functions reused as-is) before accepting a peer's
entries as converged.

THE GAP THIS CLOSES
-------------------
Experiment 012 built the mesh anchor and named its own limit:
nothing consulted it automatically. Gate 3's receive_entries
judges entries by hash recompute, fork comparison, and chain
linkage alone - all internal judgements - so a peer whose origin
chain was rewritten from genesis (internally consistent, and
never seen in its original form by the victim) is accepted, as
experiment 012's negative control demonstrated against a plain
node. Serving had the same gap on the registry chain; the policy
node's half is packages/policy-library/policy_node.py (case 8).

WHAT THIS PROTOTYPE DEMONSTRATES
---------------------------------
1. An AnchoredSyncNode is constructed with an anchor path. On
   every receive_entries, the batch is first merged by Gate 3's
   own verification, unchanged (a tampered, forked, or
   broken-chain batch diverges exactly as before, anchor or not).
   If the merge succeeds, the MERGED state - this node's entries
   plus the accepted batch, which is the state the node would be
   accepting the peer as converged into - is checked against the
   anchor with experiment 012's check_node_against_anchor.
2. anchored: the merged state is exactly the anchored entry set.
   The merge stands, and the SyncResult is Gate 3's own success
   result, unchanged.
3. anchor_mismatch: the merged state is not the anchored state
   and is not an honest extension of it - in particular a peer
   whose origin chain was rewritten from genesis. The merge is
   rolled back to the node's pre-receive entries through
   sync_mesh's restore_to_entries primitive (experiment 013), a
   divergence event with reason anchor_mismatch is logged, and
   the returned SyncResult reports divergence_detected=True,
   rolled_back=True, reason anchor_mismatch, accepted=0: the
   peer is flagged divergent and NOT accepted.
4. anchor_stale: the merged state GREW HONESTLY past the anchor.
   This is distinguished from a rewrite by the anchor itself:
   every anchored origin head is still present in the merged
   state with the same sequence and hash (the anchored entries
   are intact), and the merged state holds more entries than the
   anchor. Growth is refused the same way, under its own reason -
   never reported as a mismatch, never silently accepted as
   anchored - until the new state is re-anchored, which is
   exactly the lifecycle experiment 012 stated for its check.
   After re-anchoring, the same receive is accepted and the
   nodes converge.
5. No anchor path (anchor_path=None): no consultation happens,
   anchor_status() reports "no_anchor", and behavior is exactly
   the pre-015 behavior - a plain SyncNode and an unanchored
   AnchoredSyncNode accept the rewritten chain alike, which the
   tests state plainly because that acceptance is the hole this
   experiment closes only where an anchor is configured.

sync_with needs no override: SyncNode.sync_with snapshots both
sides, then calls each side's receive_entries, so an anchored
node's side of a sync goes through the consultation above. The
PEER's side is judged by the peer's own configuration: a peer
with no anchor accepts as before. Consultation is per-node,
matching how the anchor is held: by whoever runs the check.

HONEST LIMITS (prototype, NOT production)
-----------------------------------------
- The anchor pins ONE state. An anchored node therefore refuses
  every merge that moves past the anchored state - honest or
  not - until the new state is re-anchored by whoever holds the
  anchor. That is the stated lifecycle (experiment 012), and it
  makes the anchor a convergence gate, not an ambient check: at
  prototype scale re-anchoring is a manual step by the operator,
  exactly as it was for the check in experiment 012. Nothing here
  automates anchor distribution or decides WHO may re-anchor.
- A configured anchor that cannot be read (missing or malformed
  file) refuses the merge the same way (rolled back, divergence
  reason anchor_unreadable) and the MeshAnchorError then
  propagates: a caller who asked for an anchor is never silently
  synced without one, and the node's state is left as it was.
- Consultation is on the merged state as a whole, in-process,
  against a local stand-in anchor file that is unsigned and
  unwitnessed (all of experiment 012's anchor limits stand). The
  experiment 007/014 server processes do not construct anchored
  nodes, so networked sync does not consult a mesh anchor (their
  --anchor, added in experiment 018, consults the registry anchor
  for their snapshot at startup instead). Since experiment 018
  the Gate 5 verdict DOES take anchor evidence into account when
  configured, and its mesh classification is this module's
  anchor_status(), reused by the harness.
"""

from __future__ import annotations

from pathlib import Path

from anchor import (
    MeshAnchorError,
    check_node_against_anchor,
    read_mesh_anchor,
)
from sync_mesh import DivergenceEvent, SyncNode, SyncResult


class AnchoredSyncNode(SyncNode):
    """A SyncNode that consults an experiment 012 mesh anchor
    before accepting a peer's entries as converged.

    anchor_path=None (the default) makes this node behave exactly
    as a plain SyncNode: the anchor is opt-in, and its absence is
    not an error.
    """

    def __init__(self, node_id: str, anchor_path: str | Path | None = None):
        super().__init__(node_id)
        self.anchor_path = Path(anchor_path) if anchor_path is not None else None

    # -- introspection ----------------------------------------------------

    def anchor_status(self) -> str:
        """This node's own standing against its configured anchor:
        "no_anchor", "anchored", "anchor_stale", or
        "anchor_mismatch", by the same classification receive uses.
        Raises MeshAnchorError if a configured anchor is unreadable.
        """
        if self.anchor_path is None:
            return "no_anchor"
        check = check_node_against_anchor(self, self.anchor_path)
        if check.ok:
            return "anchored"
        return "anchor_stale" if self._is_honest_extension() else "anchor_mismatch"

    def _is_honest_extension(self) -> bool:
        """True when this node's state contains everything the
        anchor pins, intact, plus more: every anchored origin head
        is present here with the same sequence and hash, and this
        node holds more entries than the anchor recorded. A state
        missing an anchored origin, or holding a different hash at
        an anchored (origin, seq), is not an extension - it is a
        different history."""
        anchor = read_mesh_anchor(self.anchor_path)
        if len(self.entries()) <= anchor.entry_count:
            return False
        held = {(e.origin, e.seq): e.entry_hash for e in self.entries()}
        return all(
            held.get((origin, head["seq"])) == head["head"]
            for origin, head in anchor.origin_heads.items()
        )

    # -- anti-entropy with anchor consultation ------------------------------

    def receive_entries(self, incoming) -> SyncResult:
        """Merge a batch, then consult the anchor on the merged
        state before accepting it (module docstring, steps 1-5).

        Gate 3's own divergence handling runs first and is
        unchanged. Only a merge Gate 3 accepted reaches the anchor
        consultation; a consultation refusal rolls the node back
        to its pre-receive entry set via restore_to_entries and
        reports the refusal as a divergence under the anchor's own
        reason (anchor_mismatch or anchor_stale).
        """
        if self.anchor_path is None:
            return super().receive_entries(incoming)

        pre_entries = self.entries()
        result = super().receive_entries(incoming)
        if result.divergence_detected:
            return result

        try:
            check = check_node_against_anchor(self, self.anchor_path)
            status = (
                "anchored"
                if check.ok
                else "anchor_stale"
                if self._is_honest_extension()
                else "anchor_mismatch"
            )
        except MeshAnchorError:
            # A configured anchor that cannot be read refuses the
            # merge like any other consultation failure, and the
            # error then reaches the caller: never a silent sync.
            self.restore_to_entries(pre_entries)
            self.divergence_log.append(
                DivergenceEvent(
                    reason="anchor_unreadable",
                    detail=f"configured mesh anchor unreadable: {self.anchor_path}",
                    entry_id=None,
                )
            )
            raise

        if status == "anchored":
            return result

        self.restore_to_entries(pre_entries)
        self.divergence_log.append(
            DivergenceEvent(
                reason=status,
                detail=(
                    f"merged state refused by anchor consultation: {status} "
                    f"(anchored state {check.anchored_state_hash}, "
                    f"presented state {check.presented_state_hash})"
                ),
                entry_id=None,
            )
        )
        return SyncResult(
            accepted=0,
            divergence_detected=True,
            rolled_back=True,
            reason=status,
            state_hash=self.state_hash(),
        )
