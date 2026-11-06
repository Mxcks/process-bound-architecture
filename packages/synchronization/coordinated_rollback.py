"""
Process-Bound Architecture - coordinated rollback across sync-mesh
nodes (experiment 013, test case 6 in tests/test-cases/sync-mesh.md).

Python standard library only. Builds on Gate 3's SyncNode without
re-implementing it: the fault that triggers a rollback here is
detected by Gate 3's own receive path, the restore is Gate 3's new
restore_to_entries primitive, and the chain heads a checkpoint is
judged by are experiment 012's origin_heads, reused from anchor.py.

THE GAP THIS CLOSES
-------------------
Gate 3 (experiment 004) proved automated rollback, and Gate 5
(experiment 006) proved it again for one canary node - but in both,
rollback is LOCAL: a node restores its own last verified
checkpoint. Checkpoints are taken after every successful merge and
mutation, so nodes that last verified different states hold
different checkpoints. When a fault is found after the mesh has
moved on, local rollback alone can leave every node at a different
recovery point, each one "correct" by its own record and diverged
from the others. Nothing in experiments 002-012 proved the nodes
agree on WHERE to roll back to.

WHAT THIS PROTOTYPE DEMONSTRATES
---------------------------------
1. A shared checkpoint is captured from a converged mesh - and
   only from one: take_shared_checkpoint compares every node's
   entry set, materialized state, and state hash against the
   first node's and raises CoordinatedRollbackError naming the
   node that does not share them. The checkpoint carries the entry
   set itself, the state, the state hash, and each origin's chain
   head (top sequence and its entry hash, experiment 012's shape),
   so "the same checkpoint" is four checkable facts, not one.
2. After the checkpoint the mesh moves on honestly, a forked entry
   (Gate 3 case 3's fault, built with build_entry and injected
   through the normal receive path) is detected by one node, and
   that node's local rollback lands on its own later checkpoint -
   observably NOT the shared checkpoint.
3. coordinate_rollback restores every participating node to the
   shared checkpoint via restore_to_entries. Success is verified
   per node against the checkpoint itself (entry set, state,
   state hash, chain heads all exactly equal), and the report says
   so per node; a node is never reported restored on another
   node's evidence.
4. The mesh keeps working afterward: Gate 3 sync converges the
   restored nodes on new changes, and a node whose own entries
   were rolled back mutates again on its restored chain head.
5. NEGATIVE CONTROL: a node that does not take part keeps its
   post-checkpoint state. verify_mesh_at_checkpoint names it
   divergent - entry set, state, and chain heads each reported as
   mismatched - the divergent set cannot be re-certified as
   sharing a checkpoint, and the node rejoins only by restoring
   the checkpoint itself, never by its divergent state being
   accepted as the checkpoint.

HONEST LIMITS (prototype, NOT production)
-----------------------------------------
- Coordination is an explicit function call by whoever runs the
  prototype, in-process, over SyncNode objects on one machine.
  Nothing signals nodes over a network (experiment 007's server
  processes do not carry this), nothing triggers a rollback
  automatically on a peer's divergence, and there is no vote,
  quorum, or consensus on which checkpoint is the shared one: the
  checkpoint is the state take_shared_checkpoint verified the
  given nodes shared at capture time, and choosing WHEN to take
  one, and when to roll back to it, is the caller's.
- Rolling back discards every post-checkpoint entry on the
  participating nodes, legitimate ones included: the tests move
  the mesh on with honest mutations and the rollback removes them
  along with the fault's context. That is what "back to the
  pre-fault checkpoint" means here, stated plainly - recovery of
  legitimate post-checkpoint work (replay, re-sync from a node
  that kept it) is not built; a node that kept the post-checkpoint
  state is treated as divergent, per the negative control, even
  though its entries are honest and its chains verify.
- Detection of a skipped rollback is by checkpoint comparison
  (this module's check), not by Gate 3 sync: the skipped node's
  extra entries are valid chain entries, and a rolled-back node
  that synced with it BEFORE the check would accept them and move
  off the checkpoint again. The order the prototype proves is
  check first, exclude or restore the divergent node, then resume
  sync; a coordinator that enforces that order by itself is not
  built.
- The fault in the tests is one fork arriving through one node's
  receive path. A fault that left different nodes holding
  different FORKED histories of the same origin (true split-brain
  at rollback time) is out of scope: restore_to_entries would
  still put every participant on the checkpoint set, but no test
  constructs that state, because Gate 3's receive path refuses
  forks on sight and it cannot arise through sync.
- No persistence, no restart recovery, no performance numbers:
  the checkpoint lives in the caller's memory and is gone with
  the process, exactly like the nodes' own checkpoints.
"""

from __future__ import annotations

from dataclasses import dataclass

from anchor import origin_heads
from sync_mesh import StateEntry, SyncNode


class CoordinatedRollbackError(Exception):
    """A shared checkpoint cannot be taken or certified from the
    given nodes: they do not share one state."""


@dataclass(frozen=True)
class SharedCheckpoint:
    """One entry set a set of nodes was verified to share.

    entries is the set itself, in the deterministic total order
    SyncNode.entries() returns; state, state_hash, entry_ids, and
    origin_heads are the four facts a node is later judged against.
    """

    entries: tuple[StateEntry, ...]
    state: dict
    state_hash: str
    entry_ids: frozenset
    origin_heads: dict[str, dict]

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "state_hash": self.state_hash,
            "entry_ids": sorted(self.entry_ids),
            "origin_heads": self.origin_heads,
            "entry_count": len(self.entries),
        }


def take_shared_checkpoint(nodes: list[SyncNode]) -> SharedCheckpoint:
    """Capture a shared checkpoint from a converged set of nodes.

    Every node must hold exactly the first node's entry set,
    materialized state, and state hash; the first node that does
    not is named in the CoordinatedRollbackError raised. A
    checkpoint is a fact verified at capture time, not a state any
    node merely asserts.
    """
    if not nodes:
        raise CoordinatedRollbackError("no nodes given: nothing shares a checkpoint")
    reference = nodes[0]
    reference_ids = reference.entry_ids()
    reference_state = reference.materialized_state()
    reference_hash = reference.state_hash()
    for node in nodes[1:]:
        if (
            node.entry_ids() != reference_ids
            or node.materialized_state() != reference_state
            or node.state_hash() != reference_hash
        ):
            raise CoordinatedRollbackError(
                f"node {node.node_id} does not share node "
                f"{reference.node_id}'s state: entry sets, materialized "
                "state, or state hash differ, so no shared checkpoint "
                "exists for this set"
            )
    heads = {origin: dict(h) for origin, h in origin_heads(reference).items()}
    return SharedCheckpoint(
        entries=tuple(reference.entries()),
        state=dict(reference_state),
        state_hash=reference_hash,
        entry_ids=frozenset(reference_ids),
        origin_heads=heads,
    )


def matches_checkpoint(node: SyncNode, checkpoint: SharedCheckpoint) -> bool:
    """Exact equality with the checkpoint, four ways: entry set,
    materialized state, state hash, and per-origin chain heads."""
    return (
        node.entry_ids() == checkpoint.entry_ids
        and node.materialized_state() == checkpoint.state
        and node.state_hash() == checkpoint.state_hash
        and origin_heads(node) == checkpoint.origin_heads
    )


@dataclass(frozen=True)
class NodeCheckpointCheck:
    """One node's standing against a shared checkpoint, with each
    judgement kept separate so a divergence says WHAT diverged."""

    node_id: str
    entry_ids_match: bool
    state_matches: bool
    heads_match: bool

    @property
    def ok(self) -> bool:
        return self.entry_ids_match and self.state_matches and self.heads_match

    @property
    def status(self) -> str:
        return "at_checkpoint" if self.ok else "divergent"

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "entry_ids_match": self.entry_ids_match,
            "state_matches": self.state_matches,
            "heads_match": self.heads_match,
            "ok": self.ok,
            "status": self.status,
        }


def check_node_against_checkpoint(
    node: SyncNode, checkpoint: SharedCheckpoint
) -> NodeCheckpointCheck:
    """Check one node against a shared checkpoint: its entry set,
    its materialized state and state hash, and its per-origin
    chain heads must all match exactly."""
    return NodeCheckpointCheck(
        node_id=node.node_id,
        entry_ids_match=node.entry_ids() == checkpoint.entry_ids,
        state_matches=(
            node.materialized_state() == checkpoint.state
            and node.state_hash() == checkpoint.state_hash
        ),
        heads_match=origin_heads(node) == checkpoint.origin_heads,
    )


def verify_mesh_at_checkpoint(
    nodes: list[SyncNode], checkpoint: SharedCheckpoint
) -> tuple[NodeCheckpointCheck, ...]:
    """Check every given node against the checkpoint, in order."""
    return tuple(check_node_against_checkpoint(node, checkpoint) for node in nodes)


def divergent_node_ids(checks: tuple[NodeCheckpointCheck, ...]) -> list[str]:
    """The ids of the nodes a checkpoint verification found
    divergent, in the order they were checked."""
    return [check.node_id for check in checks if not check.ok]


@dataclass(frozen=True)
class NodeRollbackResult:
    node_id: str
    rolled_back: bool
    restored_exactly: bool
    state_hash: str

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "rolled_back": self.rolled_back,
            "restored_exactly": self.restored_exactly,
            "state_hash": self.state_hash,
        }


@dataclass(frozen=True)
class CoordinatedRollbackReport:
    """The outcome of one coordinated rollback: one result per
    participating node, each verified against the checkpoint
    itself after that node's restore ran."""

    checkpoint_state_hash: str
    results: tuple[NodeRollbackResult, ...]

    @property
    def all_restored(self) -> bool:
        return bool(self.results) and all(r.restored_exactly for r in self.results)

    @property
    def status(self) -> str:
        return "coordinated_rollback_complete" if self.all_restored else "rollback_incomplete"

    def to_dict(self) -> dict:
        return {
            "checkpoint_state_hash": self.checkpoint_state_hash,
            "results": [r.to_dict() for r in self.results],
            "all_restored": self.all_restored,
            "status": self.status,
        }


def coordinate_rollback(
    nodes: list[SyncNode], checkpoint: SharedCheckpoint
) -> CoordinatedRollbackReport:
    """Restore every given node to the shared checkpoint.

    Each node is restored through Gate 3's restore_to_entries,
    which verifies the checkpoint set before keeping it, and each
    node's standing is then checked against the checkpoint itself:
    restored_exactly is that node's own post-restore comparison,
    never inferred from a peer's. Nodes not in the list are not
    touched - and are not at the checkpoint afterward unless they
    already were (see the negative control in the tests).
    """
    results = []
    for node in nodes:
        result = node.restore_to_entries(list(checkpoint.entries))
        results.append(
            NodeRollbackResult(
                node_id=node.node_id,
                rolled_back=result.rolled_back and not result.divergence_detected,
                restored_exactly=matches_checkpoint(node, checkpoint),
                state_hash=node.state_hash(),
            )
        )
    return CoordinatedRollbackReport(
        checkpoint_state_hash=checkpoint.state_hash,
        results=tuple(results),
    )
