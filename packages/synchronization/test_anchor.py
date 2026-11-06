"""
Automated tests for the external anchor of sync-mesh chain heads
(experiment 012).

Each test maps to tests/test-cases/sync-mesh.md case 5, whose
detailed form is tests/test-cases/registration.md case 7 (written
before this code). Run with either:

    python3 -m unittest discover -s packages/synchronization -v
    python3 packages/synchronization/test_anchor.py

The registry chain is not the only chain the docs name: every
sync-mesh origin carries its own hash chain (sync_mesh.py), and its
docstring names the same gap - one origin's entire chain rewritten
from genesis, shown to a node that never saw the original, verifies
on its own. The mesh anchor writes each origin's head (top sequence
number and its entry hash) plus the node's state hash to a file
outside the node's own state, and verification compares a presented
node against that anchor. The negative control builds a node with
the same origin id and different mutations: internally consistent,
and it must fail the anchor check. Standard library only.
"""

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from anchor import (  # noqa: E402
    check_node_against_anchor,
    verify_node_against_anchor,
    write_mesh_anchor,
)
from sync_mesh import SyncNode  # noqa: E402


class MeshAnchorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.anchor_path = self.tmp / "outside" / "mesh-anchor.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _honest_pair(self):
        a = SyncNode("node-a")
        b = SyncNode("node-b")
        a.mutate("greeting", "hello")
        a.mutate("mode", "member")
        b.mutate("region", "north")
        a.sync_with(b)
        return a, b

    # Case 5, main outcome: per-origin heads and the state hash are
    # anchored outside the node, and any node holding exactly the
    # anchored entry set - the origin node itself, or a peer that
    # converged to the same set - verifies against the anchor.
    def test_mesh_anchor_captures_origin_heads_and_converged_nodes_verify(self):
        a, b = self._honest_pair()
        anchor = write_mesh_anchor(a, self.anchor_path)
        self.assertEqual(set(anchor.origin_heads), {"node-a", "node-b"})
        self.assertEqual(anchor.origin_heads["node-a"]["seq"], 1)
        self.assertEqual(anchor.origin_heads["node-b"]["seq"], 0)
        top_a = max(
            (e for e in a.entries() if e.origin == "node-a"), key=lambda e: e.seq
        )
        self.assertEqual(anchor.origin_heads["node-a"]["head"], top_a.entry_hash)
        self.assertEqual(anchor.state_hash, a.state_hash())

        self.assertTrue(verify_node_against_anchor(a, self.anchor_path))
        self.assertTrue(verify_node_against_anchor(b, self.anchor_path))
        check = check_node_against_anchor(b, self.anchor_path)
        self.assertTrue(check.heads_match)
        self.assertTrue(check.state_matches)
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "anchored")

        # A node that honestly moved on (one more mutation, synced)
        # no longer matches the old anchor: the anchor pins a head,
        # it does not bless a history.
        a.mutate("mode", "admin")
        a.sync_with(b)
        check = check_node_against_anchor(a, self.anchor_path)
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_mismatch")

    # Case 5, negative control: an origin's whole chain rewritten
    # from genesis with different values. Every entry hash recomputes
    # and the rewritten node's own chain verification (the same
    # checks receive_entries runs) passes - internal verification
    # alone cannot catch it. The anchor check must fail it.
    def test_negative_control_rewritten_origin_chain_passes_internal_but_fails_anchor(self):
        a, _b = self._honest_pair()
        write_mesh_anchor(a, self.anchor_path)

        attacker = SyncNode("node-a")
        attacker.mutate("greeting", "pwned")
        attacker.mutate("mode", "admin")
        # Internally consistent: every hash recomputes, and a fresh
        # node accepts the rewritten entries with no divergence.
        self.assertTrue(all(e.hash_verifies() for e in attacker.entries()))
        victim = SyncNode("victim")
        result = victim.receive_entries(attacker.entries())
        self.assertFalse(result.divergence_detected)

        self.assertFalse(verify_node_against_anchor(attacker, self.anchor_path))
        check = check_node_against_anchor(attacker, self.anchor_path)
        self.assertFalse(check.heads_match)
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_mismatch")


if __name__ == "__main__":
    unittest.main(verbosity=2)
