"""
Automated tests for coordinated rollback across sync-mesh nodes
(experiment 013).

Each test maps to tests/test-cases/sync-mesh.md case 6 (written
before this code, in the detailed format of
tests/test-cases/policy-availability.md case 4). Run with either:

    python3 -m unittest discover -s packages/synchronization -v
    python3 packages/synchronization/test_coordinated_rollback.py

The gap these tests close: Gate 3 (experiment 004) proved a node
that detects divergence rolls back to its OWN last verified
checkpoint, and Gate 5 (experiment 006) proved the same for one
canary node. Neither proves the nodes agree on WHERE to roll back
to. Here a shared checkpoint is taken from a converged mesh, a
fault is forced with Gate 3's own fork machinery (build_entry, the
same builder Gate 5's harness uses), and a coordinator restores
every participating node to that one checkpoint. The negative
control is a node that skips the rollback: the checkpoint check
must name it divergent, not accept it. Standard library only;
nodes are in-process, coordination is an explicit call, and no
network signalling or consensus is claimed.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from anchor import origin_heads  # noqa: E402
from coordinated_rollback import (  # noqa: E402
    CoordinatedRollbackError,
    check_node_against_checkpoint,
    coordinate_rollback,
    divergent_node_ids,
    take_shared_checkpoint,
    verify_mesh_at_checkpoint,
)
from sync_mesh import GENESIS_HASH, SyncNode, build_entry  # noqa: E402


def _full_mesh_sync(nodes):
    """Sync every pair until a full round accepts nothing new.
    Same helper shape as test_sync_mesh.py."""
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


def _converged_nodes(count=3):
    """A small converged mesh: every node mutated, then full sync."""
    nodes = [SyncNode(f"node-{chr(ord('a') + i)}") for i in range(count)]
    nodes[0].mutate("mode", "normal")
    nodes[0].mutate("level", "1")
    if count > 1:
        nodes[1].mutate("region", "north")
    if count > 2:
        nodes[2].mutate("locale", "en-US")
    if count > 3:
        nodes[3].mutate("tier", "member")
    _full_mesh_sync(nodes)
    return nodes


class CoordinatedRollbackTests(unittest.TestCase):
    # Case 6, main outcome: a shared checkpoint is taken from a
    # converged mesh; the mesh moves on; a fork fault is injected;
    # the detecting node's LOCAL rollback lands on its own later
    # checkpoint, not the shared one; the coordinated rollback then
    # restores every node to the shared checkpoint exactly.
    def test_coordinated_rollback_restores_shared_checkpoint_on_all_nodes(self):
        a, b, c = _converged_nodes(3)

        checkpoint = take_shared_checkpoint([a, b, c])
        cp_state = dict(checkpoint.state)
        cp_hash = checkpoint.state_hash
        cp_ids = set(checkpoint.entry_ids)
        cp_heads = {o: dict(h) for o, h in checkpoint.origin_heads.items()}
        top_a = max((e for e in a.entries() if e.origin == "node-a"), key=lambda e: e.seq)
        self.assertEqual(cp_heads["node-a"], {"seq": 1, "head": top_a.entry_hash})
        self.assertEqual(set(cp_heads), {"node-a", "node-b", "node-c"})
        for node in (a, b, c):
            self.assertTrue(check_node_against_checkpoint(node, checkpoint).ok)

        # A shared checkpoint cannot be taken from nodes that do
        # not actually share a state.
        outsider = SyncNode("node-z")
        outsider.mutate("mode", "elsewhere")
        with self.assertRaises(CoordinatedRollbackError):
            take_shared_checkpoint([a, outsider])

        # The mesh moves on past the checkpoint, honestly.
        a.mutate("post-checkpoint-a", "from-a")
        b.mutate("post-checkpoint-b", "from-b")
        _full_mesh_sync([a, b, c])
        post_hash = a.state_hash()
        self.assertNotEqual(post_hash, cp_hash)
        for node in (a, b, c):
            self.assertFalse(check_node_against_checkpoint(node, checkpoint).ok)

        # Fault, of a kind Gate 3 already detects: a forked entry
        # claiming node-a's first slot with different content, built
        # with Gate 3's own builder, arriving through the normal
        # receive path of node-b.
        real_first = next(e for e in a.entries() if e.origin == "node-a" and e.seq == 0)
        fork = build_entry(
            origin="node-a", seq=0, lamport=real_first.lamport,
            key=real_first.key, value="split-brain", prev_hash=GENESIS_HASH,
        )
        self.assertTrue(fork.hash_verifies())
        result = b.receive_entries([fork])
        self.assertTrue(result.divergence_detected)
        self.assertTrue(result.rolled_back)
        self.assertEqual(result.reason, "forked_entry")

        # Local rollback alone does NOT reach the shared
        # checkpoint: node-b is back at its own last verified state
        # (the post-checkpoint converged state), which is a
        # different recovery point than the shared checkpoint.
        self.assertEqual(b.state_hash(), post_hash)
        self.assertNotEqual(b.state_hash(), cp_hash)

        # The recovery points drift further apart: node-a and
        # node-c keep working and sync with each other; node-b does
        # not take part.
        a.mutate("after-fault", "from-a")
        a.sync_with(c)
        self.assertEqual(a.state_hash(), c.state_hash())
        self.assertNotEqual(a.state_hash(), b.state_hash())

        # Coordinated rollback: every participating node is
        # restored to the ONE shared checkpoint.
        report = coordinate_rollback([a, b, c], checkpoint)
        self.assertTrue(report.all_restored)
        self.assertEqual(report.status, "coordinated_rollback_complete")
        self.assertEqual(report.checkpoint_state_hash, cp_hash)
        self.assertEqual([r.node_id for r in report.results], ["node-a", "node-b", "node-c"])
        for r in report.results:
            self.assertTrue(r.rolled_back)
            self.assertTrue(r.restored_exactly)
            self.assertEqual(r.state_hash, cp_hash)

        # Identical afterward, three ways plus chain heads, and
        # equal to the checkpoint itself - not merely to each other.
        for node in (a, b, c):
            self.assertEqual(node.materialized_state(), cp_state)
            self.assertEqual(node.state_hash(), cp_hash)
            self.assertEqual(node.entry_ids(), cp_ids)
            self.assertEqual(origin_heads(node), cp_heads)
            check = check_node_against_checkpoint(node, checkpoint)
            self.assertTrue(check.ok)
            self.assertEqual(check.status, "at_checkpoint")

    # Case 6, second half: after the coordinated rollback the mesh
    # still converges on new changes - including a mutation by a
    # node whose own post-checkpoint entries were rolled back, so
    # its chain must continue from the restored checkpoint head.
    def test_mesh_converges_on_new_changes_after_coordinated_rollback(self):
        a, b, c = _converged_nodes(3)
        checkpoint = take_shared_checkpoint([a, b, c])

        a.mutate("post-checkpoint", "from-a")
        _full_mesh_sync([a, b, c])
        self.assertNotEqual(a.state_hash(), checkpoint.state_hash)

        report = coordinate_rollback([a, b, c], checkpoint)
        self.assertTrue(report.all_restored)

        # node-a's own rolled-back entry is gone; its next mutation
        # must extend the restored node-a chain (seq and prev_hash
        # from the checkpoint head), or Gate 3's chain verification
        # would refuse it at every peer.
        resumed = a.mutate("resumed", "yes")
        top_checkpoint_a = max(
            (e for e in checkpoint.entries if e.origin == "node-a"),
            key=lambda e: e.seq,
        )
        self.assertEqual(resumed.seq, top_checkpoint_a.seq + 1)
        self.assertEqual(resumed.prev_hash, top_checkpoint_a.entry_hash)

        c.mutate("resumed-too", "also-yes")
        _full_mesh_sync([a, b, c])

        self.assertEqual(a.entry_ids(), b.entry_ids())
        self.assertEqual(b.entry_ids(), c.entry_ids())
        self.assertEqual(a.materialized_state(), b.materialized_state())
        self.assertEqual(b.materialized_state(), c.materialized_state())
        self.assertEqual(a.state_hash(), b.state_hash())
        self.assertEqual(b.state_hash(), c.state_hash())
        state = a.materialized_state()
        self.assertEqual(state["resumed"], "yes")
        self.assertEqual(state["resumed-too"], "also-yes")
        self.assertNotIn("post-checkpoint", state)
        for node in (a, b, c):
            self.assertEqual(node.divergence_log, [])

    # Case 6, negative control: a node that does NOT take part in
    # the coordinated rollback keeps its post-checkpoint state and
    # is detected as divergent afterward by the checkpoint check -
    # not silently accepted as being at the checkpoint. It rejoins
    # only by restoring the checkpoint itself.
    def test_negative_control_node_that_skips_rollback_is_detected_divergent(self):
        a, b, c, d = _converged_nodes(4)
        checkpoint = take_shared_checkpoint([a, b, c, d])
        cp_hash = checkpoint.state_hash

        a.mutate("post-checkpoint", "from-a")
        _full_mesh_sync([a, b, c, d])
        post_hash = a.state_hash()
        self.assertNotEqual(post_hash, cp_hash)

        # Only a, b, c roll back. d keeps the post-checkpoint state.
        report = coordinate_rollback([a, b, c], checkpoint)
        self.assertTrue(report.all_restored)
        self.assertEqual(d.state_hash(), post_hash)

        checks = verify_mesh_at_checkpoint([a, b, c, d], checkpoint)
        by_id = {check.node_id: check for check in checks}
        for node_id in ("node-a", "node-b", "node-c"):
            self.assertTrue(by_id[node_id].ok)
            self.assertEqual(by_id[node_id].status, "at_checkpoint")
        d_check = by_id["node-d"]
        self.assertFalse(d_check.ok)
        self.assertEqual(d_check.status, "divergent")
        self.assertFalse(d_check.entry_ids_match)
        self.assertFalse(d_check.state_matches)
        self.assertFalse(d_check.heads_match)
        self.assertEqual(divergent_node_ids(checks), ["node-d"])

        # The divergent set cannot be re-certified as sharing a
        # checkpoint while d is in it.
        with self.assertRaises(CoordinatedRollbackError):
            take_shared_checkpoint([a, b, c, d])

        # Rejoin is by restoring the checkpoint, not by syncing the
        # post-checkpoint state back in: after d restores, it is at
        # the checkpoint and the whole mesh converges on new work.
        d_report = coordinate_rollback([d], checkpoint)
        self.assertTrue(d_report.all_restored)
        self.assertTrue(check_node_against_checkpoint(d, checkpoint).ok)

        b.mutate("after-rejoin", "from-b")
        _full_mesh_sync([a, b, c, d])
        self.assertEqual(a.state_hash(), d.state_hash())
        self.assertEqual(a.materialized_state()["after-rejoin"], "from-b")
        self.assertEqual(d.materialized_state()["after-rejoin"], "from-b")


if __name__ == "__main__":
    unittest.main(verbosity=2)
