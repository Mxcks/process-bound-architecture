"""
Automated tests for the Gate 3 synchronization mesh prototype.

Each test maps to a Gate 3 outcome in
docs/architecture/08-outcomes-that-unlock-the-next-build.md and to
tests/test-cases/sync-mesh.md (cases 1-3; case 4, the sub-millisecond
lineage benchmark, is Gate 5 work and is not claimed here). Run with
either:

    python3 -m unittest discover -s packages/synchronization -v
    python3 packages/synchronization/test_sync_mesh.py

Standard library only. Nodes are in-process; partition is modeled by
simply not calling sync, which is stated honestly in the README.
"""

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync_mesh import (  # noqa: E402
    GENESIS_HASH,
    SyncNode,
    build_entry,
    tamper_entry,
)


def _full_mesh_sync(nodes):
    """Sync every pair until a full round accepts nothing new."""
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


class Gate3SyncMeshTests(unittest.TestCase):
    # Outcome 1 (sync-mesh case 1): concurrent mutations across nodes
    # converge with no central lock. Mutations happen first, entirely
    # independently, on three nodes - including concurrent writes to
    # the SAME key - and only then are entries exchanged, each node
    # receiving the others' entries in a different order.
    def test_concurrent_mutations_converge_no_central_lock(self):
        a, b, c = SyncNode("node-a"), SyncNode("node-b"), SyncNode("node-c")

        a.mutate("region", "us-central")
        a.mutate("feature-x", "on")
        a.mutate("shared-counter-note", "from-a")
        b.mutate("tier", "member")
        b.mutate("feature-y", "off")
        b.mutate("shared-counter-note", "from-b")
        c.mutate("locale", "en-US")
        c.mutate("feature-x", "off")  # concurrent conflict with node-a
        c.mutate("shared-counter-note", "from-c")

        all_entries = a.entries() + b.entries() + c.entries()
        self.assertEqual(len(all_entries), 9)

        # Each node receives the full set in a different shuffled
        # order (its own entries are already-held duplicates).
        rng = random.Random(20261106)
        for node in (a, b, c):
            shuffled = list(all_entries)
            rng.shuffle(shuffled)
            result = node.receive_entries(shuffled)
            self.assertFalse(result.divergence_detected)
            self.assertFalse(result.rolled_back)

        # Identical entry sets, identical materialized state,
        # identical state hashes - convergence, observed three ways.
        self.assertEqual(a.entry_ids(), b.entry_ids())
        self.assertEqual(b.entry_ids(), c.entry_ids())
        self.assertEqual(len(a.entry_ids()), 9)
        self.assertEqual(a.materialized_state(), b.materialized_state())
        self.assertEqual(b.materialized_state(), c.materialized_state())
        self.assertEqual(a.state_hash(), b.state_hash())
        self.assertEqual(b.state_hash(), c.state_hash())

        # Deterministic conflict resolution: every entry has lamport
        # 1..3 per node; in the (lamport, origin, seq) order the last
        # writers are node-c's seq-2 entries, on every node alike.
        state = a.materialized_state()
        self.assertEqual(state["shared-counter-note"], "from-c")
        self.assertEqual(state["feature-x"], "off")
        self.assertEqual(state["region"], "us-central")
        self.assertEqual(state["tier"], "member")
        self.assertEqual(state["locale"], "en-US")

        # EAI is attached per the PBA-EAI-v1 schema on every entry.
        for entry in a.entries():
            self.assertEqual(len(entry.eai), 64)
            self.assertTrue(entry.hash_verifies())

    # Outcome 2 (sync-mesh case 2): a partitioned node rejoins and
    # converges. Node-c is cut off (no sync calls involve it) while
    # node-a and node-b keep mutating and syncing with each other;
    # node-c also mutates locally during the partition, including a
    # conflicting write. On rejoin, one full mesh sync converges all
    # three with no entries lost.
    def test_partitioned_node_rejoins_and_converges(self):
        a, b, c = SyncNode("node-a"), SyncNode("node-b"), SyncNode("node-c")

        a.mutate("base", "v1")
        _full_mesh_sync([a, b, c])
        self.assertEqual(a.state_hash(), c.state_hash())

        # Partition: c is simply never synced. a and b keep working.
        a.mutate("during-partition-a", "1")
        b.mutate("during-partition-b", "2")
        a.sync_with(b)
        a.mutate("shared", "written-by-a-during-partition")
        a.sync_with(b)
        # c, isolated, mutates on its own - including the same key.
        c.mutate("during-partition-c", "3")
        c.mutate("shared", "written-by-c-during-partition")

        # While partitioned, the views genuinely differ.
        self.assertNotEqual(a.state_hash(), c.state_hash())
        self.assertNotIn("during-partition-c", a.materialized_state())
        self.assertNotIn("during-partition-a", c.materialized_state())

        # Rejoin: full mesh sync, no rollback anywhere.
        _full_mesh_sync([a, b, c])

        self.assertEqual(a.entry_ids(), b.entry_ids())
        self.assertEqual(b.entry_ids(), c.entry_ids())
        self.assertEqual(a.materialized_state(), b.materialized_state())
        self.assertEqual(b.materialized_state(), c.materialized_state())
        self.assertEqual(a.state_hash(), b.state_hash())
        self.assertEqual(b.state_hash(), c.state_hash())
        self.assertEqual(a.divergence_log, [])
        self.assertEqual(c.divergence_log, [])

        state = a.materialized_state()
        self.assertEqual(state["base"], "v1")
        self.assertEqual(state["during-partition-a"], "1")
        self.assertEqual(state["during-partition-b"], "2")
        self.assertEqual(state["during-partition-c"], "3")
        # The conflicting key resolves to one value, the same on all
        # nodes (which one is decided by the total order, explained
        # in the module docstring, not by arrival order).
        self.assertIn(
            state["shared"],
            {"written-by-a-during-partition", "written-by-c-during-partition"},
        )
        self.assertEqual(state["shared"], b.materialized_state()["shared"])
        self.assertEqual(state["shared"], c.materialized_state()["shared"])

    # Outcome 3 (sync-mesh case 3): split-brain / divergence is
    # detected and triggers automated rollback. Three divergence
    # shapes are exercised against a node whose converged state is
    # its checkpoint: a tampered entry, a forked entry (split-brain:
    # two valid histories for the same origin slot), and an entry
    # with a broken chain link. Each time: the batch is refused, the
    # SyncResult reports divergence_detected and rolled_back, a
    # divergence event is logged, and the node's state is exactly
    # its pre-attack converged state.
    def test_divergence_detected_and_triggers_rollback(self):
        a, b = SyncNode("node-a"), SyncNode("node-b")
        a.mutate("mode", "normal")
        a.mutate("level", "1")
        _full_mesh_sync([a, b])
        converged_state = b.materialized_state()
        converged_hash = b.state_hash()
        converged_ids = b.entry_ids()
        real_first = next(e for e in a.entries() if e.seq == 0)

        # (a) Tampered entry: value changed, hash kept.
        tampered = tamper_entry(real_first, value="forged")
        self.assertFalse(tampered.hash_verifies())
        result = b.receive_entries([tampered])
        self.assertTrue(result.divergence_detected)
        self.assertTrue(result.rolled_back)
        self.assertEqual(result.reason, "tampered_entry")
        self.assertEqual(result.accepted, 0)

        # (b) Forked entry: split-brain. Same (origin, seq) and same
        # prev_hash as the real first entry, different content, and
        # its own hash individually valid - detection must come from
        # the identity conflict, not a hash error.
        fork = build_entry(
            origin="node-a", seq=0, lamport=1,
            key="mode", value="split-brain", prev_hash=GENESIS_HASH,
        )
        self.assertTrue(fork.hash_verifies())
        self.assertNotEqual(fork.entry_hash, real_first.entry_hash)
        result = b.receive_entries([fork])
        self.assertTrue(result.divergence_detected)
        self.assertTrue(result.rolled_back)
        self.assertEqual(result.reason, "forked_entry")

        # (c) Broken chain: a fresh entry claiming to extend node-a's
        # history but linking to a predecessor hash that is not the
        # real head of node-a's chain.
        broken = build_entry(
            origin="node-a", seq=2, lamport=99,
            key="extra", value="x", prev_hash="f" * 64,
        )
        self.assertTrue(broken.hash_verifies())
        result = b.receive_entries([broken])
        self.assertTrue(result.divergence_detected)
        self.assertTrue(result.rolled_back)
        self.assertEqual(result.reason, "broken_chain")

        # After all three attacks: exact rollback, every time. The
        # converged state, hash, and entry set are untouched, none of
        # the divergent entries were kept, and all three divergences
        # are on the record.
        self.assertEqual(b.materialized_state(), converged_state)
        self.assertEqual(b.state_hash(), converged_hash)
        self.assertEqual(b.entry_ids(), converged_ids)
        self.assertEqual(
            [e.reason for e in b.divergence_log],
            ["tampered_entry", "forked_entry", "broken_chain"],
        )

        # And the node is not bricked: an honest sync afterwards
        # still works and converges on new legitimate state.
        a.mutate("after", "still-works")
        _full_mesh_sync([a, b])
        self.assertEqual(b.materialized_state()["after"], "still-works")
        self.assertEqual(a.state_hash(), b.state_hash())


if __name__ == "__main__":
    unittest.main(verbosity=2)
