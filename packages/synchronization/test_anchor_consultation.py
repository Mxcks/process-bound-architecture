"""
Automated tests for anchor consultation in Gate 3 sync
(experiment 015).

Each test maps to tests/test-cases/sync-mesh.md case 8, whose
detailed form is tests/test-cases/registration.md case 8 (written
before this code). Run with either:

    python3 -m unittest discover -s packages/synchronization -v
    python3 packages/synchronization/test_anchor_consultation.py

An AnchoredSyncNode is a SyncNode constructed with a mesh anchor
path (experiment 012's anchor for per-origin chain heads). It
consults that anchor - check_node_against_anchor, reused - on the
merged state before accepting a peer's entries as converged.

The negative control is experiment 012's: an origin's whole chain
rewritten from genesis with different values; every hash
recomputes, and a node with no anchor accepts it (asserted again
here, as the pre-015 behavior this case claims did not move). An
anchored node must flag it divergent with reason anchor_mismatch,
accept nothing, and keep its prior state.

Honest growth is asserted as the case states it: a merged state
that contains everything anchored, intact, plus one honestly
appended entry is anchor_stale - refused and flagged under that
name, not as a mismatch and not silently accepted - until the new
state is re-anchored, after which the same entries are accepted
and the nodes converge. Standard library only.
"""

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from anchor import write_mesh_anchor  # noqa: E402  (exp 012 mesh anchor)
from anchored_sync import AnchoredSyncNode  # noqa: E402
from sync_mesh import SyncNode  # noqa: E402


class AnchorConsultationSyncTests(unittest.TestCase):
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

    def _rewritten_attacker(self):
        """Experiment 012's mesh negative control: same origin id
        as the honest node-a, different values, every hash
        recomputing on its own chain."""
        attacker = SyncNode("node-a")
        attacker.mutate("greeting", "pwned")
        attacker.mutate("mode", "admin")
        assert all(e.hash_verifies() for e in attacker.entries())
        return attacker

    # Case 8, main outcome: a peer holding exactly the anchored
    # entry set is accepted by an anchored node, which converges
    # to the anchored state, with no divergence recorded.
    def test_genuine_peer_accepted_and_converged_with_anchor(self):
        a, _b = self._honest_pair()
        write_mesh_anchor(a, self.anchor_path)

        victim = AnchoredSyncNode("victim", anchor_path=self.anchor_path)
        result = victim.receive_entries(a.entries())
        self.assertFalse(result.divergence_detected)
        self.assertFalse(result.rolled_back)
        self.assertEqual(result.accepted, 3)
        self.assertEqual(victim.entry_ids(), a.entry_ids())
        self.assertEqual(victim.state_hash(), a.state_hash())
        self.assertEqual(victim.materialized_state(), a.materialized_state())
        self.assertEqual(victim.divergence_log, [])
        self.assertEqual(victim.anchor_status(), "anchored")

    # Case 8, negative control: the rewritten origin chain is
    # flagged divergent as anchor_mismatch and NOT accepted; the
    # anchored node's state stays exactly what it was (empty here:
    # a fresh node that would otherwise have taken the rewrite as
    # its whole view of node-a).
    def test_rewritten_peer_flagged_anchor_mismatch_not_accepted(self):
        a, _b = self._honest_pair()
        write_mesh_anchor(a, self.anchor_path)
        attacker = self._rewritten_attacker()

        victim = AnchoredSyncNode("victim", anchor_path=self.anchor_path)
        result = victim.receive_entries(attacker.entries())
        self.assertTrue(result.divergence_detected)
        self.assertTrue(result.rolled_back)
        self.assertEqual(result.reason, "anchor_mismatch")
        self.assertEqual(result.accepted, 0)
        self.assertEqual(victim.entries(), [])
        self.assertEqual(victim.materialized_state(), {})
        self.assertEqual(
            [e.reason for e in victim.divergence_log], ["anchor_mismatch"]
        )

    # Case 8, no anchor: sync behavior is exactly the pre-015
    # behavior. An AnchoredSyncNode with no anchor path, like a
    # plain SyncNode, accepts the internally consistent rewritten
    # chain - stated plainly, because that acceptance is the hole
    # 015 closes only where an anchor is configured.
    def test_no_anchor_behavior_is_exactly_pre_015(self):
        a, _b = self._honest_pair()
        write_mesh_anchor(a, self.anchor_path)
        attacker = self._rewritten_attacker()

        plain = SyncNode("victim")
        result = plain.receive_entries(attacker.entries())
        self.assertFalse(result.divergence_detected)
        self.assertEqual(plain.materialized_state()["mode"], "admin")

        unanchored = AnchoredSyncNode("victim-2", anchor_path=None)
        result = unanchored.receive_entries(attacker.entries())
        self.assertFalse(result.divergence_detected)
        self.assertFalse(result.rolled_back)
        self.assertEqual(unanchored.materialized_state()["mode"], "admin")
        self.assertEqual(unanchored.anchor_status(), "no_anchor")

    # Case 8, honest growth: after anchoring, one honest mutation
    # on the honest side makes the merged state an extension of
    # the anchored state. The anchored node refuses it as
    # anchor_stale (state unchanged, nothing accepted); after the
    # grown state is re-anchored, the same entries are accepted
    # and the nodes converge on the grown state.
    def test_honest_growth_is_anchor_stale_until_reanchored(self):
        a, _b = self._honest_pair()
        write_mesh_anchor(a, self.anchor_path)

        victim = AnchoredSyncNode("victim", anchor_path=self.anchor_path)
        victim.receive_entries(a.entries())
        converged_hash = victim.state_hash()

        a.mutate("mode", "admin")
        result = victim.receive_entries(a.entries())
        self.assertTrue(result.divergence_detected)
        self.assertTrue(result.rolled_back)
        self.assertEqual(result.reason, "anchor_stale")
        self.assertEqual(result.accepted, 0)
        self.assertEqual(victim.state_hash(), converged_hash)
        self.assertEqual(victim.materialized_state()["mode"], "member")
        self.assertEqual(
            [e.reason for e in victim.divergence_log], ["anchor_stale"]
        )

        # Re-anchor the grown state (from the node holding it, as
        # in experiment 012's lifecycle), then the same sync lands.
        write_mesh_anchor(a, self.anchor_path)
        result = victim.receive_entries(a.entries())
        self.assertFalse(result.divergence_detected)
        self.assertEqual(victim.state_hash(), a.state_hash())
        self.assertEqual(victim.materialized_state()["mode"], "admin")
        self.assertEqual(victim.anchor_status(), "anchored")


if __name__ == "__main__":
    unittest.main(verbosity=2)
