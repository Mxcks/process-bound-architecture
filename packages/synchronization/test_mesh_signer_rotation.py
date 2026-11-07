"""
Automated tests for signer rotation on the mesh anchor, and
for rotation consultation in Gate 3 sync (experiment 038).

Each test maps to tests/test-cases/sync-mesh.md case 26 (the
mesh half of registration.md case 12, written before this
code). Run with either:

    python3 -m unittest discover -s packages/synchronization -v
    python3 packages/synchronization/test_mesh_signer_rotation.py

The identities are real Gate 1 enrollments: a
RegistrationChannel in a temp dir enrolls alice, bob, and
erin at runtime inside the tests, and their secrets sign the
mesh anchor with the same domain-separated HMAC scheme as
the registry anchor (registration case 12). Nothing secret
is committed.

The rotation rules are registration case 12's, in the mesh
chain's own terms: the rotation record's effective point
carries the mesh anchor's state hash and entry count instead
of a registry chain head; the same two signatures (the
current signer signs, the successor countersigns accepting);
the same refusal names (anchor_signer_retired,
anchor_rotation_signer_invalid, anchor_rotation_invalid,
anchor_rotation_mismatch); the same judgement order
(internal, rotation, signature, witness, heads). An
AnchoredSyncNode configured with the lineage secrets judges
a rotated mesh anchor under that rule; with no lineage
configured its judgement is exactly cases 11/18's. Standard
library only.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# Appended, not inserted: `anchor` in this suite must resolve
# to THIS package's anchor.py; the registration package (for
# RegistrationChannel enrollment) is found later in the path.
sys.path.append(str(HERE.parent / "registration"))

from anchor import (  # noqa: E402  (synchronization anchor, exp 012)
    MeshAnchorError,
    check_node_against_anchor,
    make_mesh_anchor_rotation,
    read_anchor_audit,
    read_mesh_anchor,
    verify_mesh_anchor_history,
    write_mesh_anchor,
)
from anchored_sync import AnchoredSyncNode  # noqa: E402
from registration import RegistrationChannel  # noqa: E402
from sync_mesh import SyncNode  # noqa: E402


class MeshSignerRotationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.anchor_path = self.tmp / "outside" / "mesh-anchor.json"

        channel = RegistrationChannel(
            self.tmp / "registry.jsonl", self.tmp / "audit.jsonl"
        )
        self.alice_secret = channel.enroll_user("alice")
        self.bob_secret = channel.enroll_user("bob")
        self.erin_secret = channel.enroll_user("erin")

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

    def _lineage(self):
        return {
            "alice": self.alice_secret,
            "bob": self.bob_secret,
            "erin": self.erin_secret,
        }

    def _check_lineage(self, node, path=None, **extra):
        return check_node_against_anchor(
            node, path or self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
            signer_secrets=self._lineage(), **extra,
        )

    def _rotate(self, node, anchor_before_state=None):
        """Alice anchors the pair's state; the state grows;
        bob's first write applies the alice -> bob rotation
        over the grown state. Returns (rotation, grown_node).
        The grown node is `node` itself, mutated once more."""
        write_mesh_anchor(
            node, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret)
        node.mutate("tier", "gold")
        rotation = make_mesh_anchor_rotation(
            from_signer="alice", from_secret_hex=self.alice_secret,
            to_signer="bob", to_secret_hex=self.bob_secret,
            rotation_seq=0, effective_anchor_seq=1,
            effective_state_hash=node.state_hash(),
            effective_entry_count=len(node.entries()),
        )
        anchor = write_mesh_anchor(
            node, self.anchor_path,
            signer="bob", secret_hex=self.bob_secret,
            rotation=rotation,
            predecessor_secret_hex=self.alice_secret)
        return rotation, anchor

    # Case 26, main outcome: the mesh rotation applies, the
    # segmented history (alice, then bob) verifies segment by
    # segment, and a lineage-configured check of the converged
    # node is anchored with bob named current.
    def test_mesh_rotation_applies_and_verifies(self):
        a, _b = self._honest_pair()
        rotation, anchor = self._rotate(a)
        self.assertEqual(anchor.signer, "bob")
        self.assertEqual(len(anchor.rotations), 1)
        carried = anchor.rotations[0]
        self.assertEqual(carried["effective_state_hash"],
                         a.state_hash())
        self.assertEqual(carried["effective_entry_count"],
                         len(a.entries()))
        self.assertTrue(carried["signature"])
        self.assertTrue(carried["countersignature"])
        self.assertEqual(
            [e["signer"] for e in anchor.history], ["alice", "bob"])
        on_disk = read_mesh_anchor(self.anchor_path)
        self.assertEqual(on_disk, anchor)
        self.assertTrue(
            verify_mesh_anchor_history(
                self.anchor_path, signer_secrets=self._lineage()))
        check = self._check_lineage(a)
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "anchored")
        self.assertTrue(check.rotation_checked)
        self.assertTrue(check.rotation_valid)
        self.assertEqual(check.current_signer, "bob")
        print("MESH ROTATION: alice rotates to bob; the "
              "segmented mesh history verifies and the "
              "converged node is anchored")

    # Case 26: the pre-rotation mesh state and anchor still
    # verify for a verifier holding the rotation record, and
    # post-point entries by the retired signer are refused as
    # anchor_signer_retired.
    def test_mesh_pre_rotation_and_retired(self):
        a, _b = self._honest_pair()
        write_mesh_anchor(
            a, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret)
        before_bytes = self.anchor_path.read_bytes()
        before_state = (a.state_hash(), len(a.entries()))

        a.mutate("tier", "gold")
        rotation = make_mesh_anchor_rotation(
            from_signer="alice", from_secret_hex=self.alice_secret,
            to_signer="bob", to_secret_hex=self.bob_secret,
            rotation_seq=0, effective_anchor_seq=1,
            effective_state_hash=a.state_hash(),
            effective_entry_count=len(a.entries()),
        )
        write_mesh_anchor(
            a, self.anchor_path,
            signer="bob", secret_hex=self.bob_secret,
            rotation=rotation,
            predecessor_secret_hex=self.alice_secret)

        before_path = self.tmp / "outside" / "mesh-before.json"
        # A node at exactly the pre-rotation state: rebuild it
        # from the same mutations under the same node ids (an
        # entry's origin is its node's id, so the ids are part
        # of the state being rebuilt).
        x = SyncNode("node-a")
        y = SyncNode("node-b")
        x.mutate("greeting", "hello")
        x.mutate("mode", "member")
        y.mutate("region", "north")
        x.sync_with(y)
        self.assertEqual(x.state_hash(), before_state[0])
        before_path.write_bytes(before_bytes)
        check = self._check_lineage(
            x, path=before_path, rotations=[rotation])
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "anchored")
        self.assertEqual(check.current_signer, "alice")

        # Retired: alice honestly extends her pre-rotation
        # anchor over the grown state; judged against the held
        # rotation it is refused as retired.
        retired_path = self.tmp / "outside" / "mesh-retired.json"
        retired_path.write_bytes(before_bytes)
        write_mesh_anchor(
            a, retired_path,
            signer="alice", secret_hex=self.alice_secret)
        check = self._check_lineage(
            a, path=retired_path, rotations=[rotation])
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_signer_retired")
        self.assertNotEqual(check.status, "anchor_signature_invalid")
        print("MESH ROTATION: pre-rotation state accepted; "
              "the retired signer's extension refused as "
              "anchor_signer_retired")

    # Case 26, the write path's refusal shapes on the mesh
    # chain, each named and audit-logged: non-current signer,
    # missing countersignature, wrong effective state.
    def test_mesh_rotation_write_refusals(self):
        a, _b = self._honest_pair()
        write_mesh_anchor(
            a, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret)
        before = self.anchor_path.read_bytes()
        a.mutate("tier", "gold")
        state = a.state_hash()
        count = len(a.entries())

        def attempt(rotation, signer, secret, predecessor):
            with self.assertRaises(MeshAnchorError) as ctx:
                write_mesh_anchor(
                    a, self.anchor_path,
                    signer=signer, secret_hex=secret,
                    rotation=rotation,
                    predecessor_secret_hex=predecessor)
            self.assertEqual(self.anchor_path.read_bytes(), before)
            return str(ctx.exception)

        message = attempt(
            make_mesh_anchor_rotation(
                from_signer="bob", from_secret_hex=self.bob_secret,
                to_signer="erin", to_secret_hex=self.erin_secret,
                rotation_seq=0, effective_anchor_seq=1,
                effective_state_hash=state,
                effective_entry_count=count),
            "erin", self.erin_secret, self.bob_secret)
        self.assertIn("anchor_rotation_signer_invalid", message)

        rotation = make_mesh_anchor_rotation(
            from_signer="alice", from_secret_hex=self.alice_secret,
            to_signer="bob", to_secret_hex=self.bob_secret,
            rotation_seq=0, effective_anchor_seq=1,
            effective_state_hash=state, effective_entry_count=count)
        no_counter = dict(rotation)
        del no_counter["countersignature"]
        message = attempt(no_counter, "bob", self.bob_secret,
                          self.alice_secret)
        self.assertIn("anchor_rotation_invalid", message)
        self.assertNotIn("anchor_rotation_signer_invalid", message)

        message = attempt(
            make_mesh_anchor_rotation(
                from_signer="alice",
                from_secret_hex=self.alice_secret,
                to_signer="bob", to_secret_hex=self.bob_secret,
                rotation_seq=0, effective_anchor_seq=1,
                effective_state_hash="0" * 64,
                effective_entry_count=count),
            "bob", self.bob_secret, self.alice_secret)
        self.assertIn("anchor_rotation_mismatch", message)

        events = read_anchor_audit(self.anchor_path)
        rejected = [e for e in events
                    if e.get("event") == "rotation_rejected"]
        self.assertEqual(
            [e["reason"] for e in rejected],
            ["anchor_rotation_signer_invalid",
             "anchor_rotation_invalid",
             "anchor_rotation_mismatch"])
        print("MESH ROTATION write refusals: non-current "
              "signer, missing countersignature, wrong state "
              "- each named and logged")

    # Case 26, Gate 3 consultation: an AnchoredSyncNode
    # configured with the lineage secrets reports the rotated
    # state as anchored; the same node without a lineage
    # configuration judges the rotated anchor as experiment
    # 019 judges a signed one its identity cannot verify.
    def test_anchored_sync_node_with_lineage(self):
        a, _b = self._honest_pair()
        self._rotate(a)

        node = AnchoredSyncNode(
            "node-c", self.anchor_path,
            anchor_signer="alice",
            anchor_secret_hex=self.alice_secret,
            anchor_signer_secrets=self._lineage())
        # Bring the node to the anchored (rotated) state.
        result = node.receive_entries(a.entries())
        self.assertFalse(result.divergence_detected)
        self.assertEqual(node.anchor_status(), "anchored")

        plain = AnchoredSyncNode(
            "node-d", self.anchor_path,
            anchor_signer="alice",
            anchor_secret_hex=self.alice_secret)
        result = plain.receive_entries(a.entries())
        self.assertTrue(result.divergence_detected)
        self.assertEqual(result.reason, "anchor_signature_invalid")
        print("MESH ROTATION consultation: the lineage-"
              "configured anchored node accepts the rotated "
              "state; without a lineage it refuses as "
              "anchor_signature_invalid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
