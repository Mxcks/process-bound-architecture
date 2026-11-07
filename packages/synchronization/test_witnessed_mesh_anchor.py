"""
Automated tests for witnessed mesh anchors, and for
witnessed-anchor consultation in Gate 3 sync (experiment
029).

Each test maps to tests/test-cases/sync-mesh.md case 18 (the
mesh half of registration.md case 11, written before this
code). Run with either:

    python3 -m unittest discover -s packages/synchronization -v
    python3 packages/synchronization/test_witnessed_mesh_anchor.py

The identities are real Gate 1 enrollments: a
RegistrationChannel in a temp dir enrolls alice, bob, and
carol at runtime inside the tests, and their secrets sign
and countersign the mesh anchor with the same
domain-separated HMAC scheme as the registry anchor
(registration case 11). Nothing secret is committed.

The countersignature rules are registration case 11's: the
witness is a second enrolled identity distinct from the
signer; the countersignature covers the signer's identity
and signature with the per-origin heads, the state hash,
the count, the history sequence, and the previous history
hash; and succession is unchanged (case 10's same-identity
rule fires first - a different signer is refused at write
however genuinely the witness countersigns). An
AnchoredSyncNode configured with a witness identity refuses
a merged state whose anchor fails the witness check as a
divergence named anchor_witness_invalid - distinct from
anchor_signature_invalid, anchor_mismatch, and
anchor_stale. With no witness configured, mesh anchors are
judged exactly as in cases 11 (mesh) / 10 (registry).
Standard library only.
"""

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
    read_anchor_audit,
    read_mesh_anchor,
    verify_mesh_anchor_history,
    write_mesh_anchor,
)
from anchored_sync import AnchoredSyncNode  # noqa: E402
from registration import RegistrationChannel  # noqa: E402
from sync_mesh import SyncNode  # noqa: E402


class WitnessedMeshAnchorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.anchor_path = self.tmp / "outside" / "mesh-anchor.json"

        channel = RegistrationChannel(
            self.tmp / "registry.jsonl", self.tmp / "audit.jsonl"
        )
        self.alice_secret = channel.enroll_user("alice")
        self.bob_secret = channel.enroll_user("bob")
        self.carol_secret = channel.enroll_user("carol")

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

    def _write_witnessed(self, node):
        return write_mesh_anchor(
            node, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
            witness="carol", witness_secret_hex=self.carol_secret,
        )

    def _check_witnessed(self, node):
        return check_node_against_anchor(
            node, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
            witness="carol", witness_secret_hex=self.carol_secret,
        )

    # Case 18, main outcome: a countersigned mesh anchor
    # records its witness, a converged node verifies against
    # it with BOTH judgements checked, and the kept history
    # starts at sequence 0 naming the witness in its entry.
    def test_countersigned_mesh_anchor_verifies(self):
        a, _b = self._honest_pair()
        anchor = self._write_witnessed(a)
        self.assertEqual(anchor.signer, "alice")
        self.assertEqual(anchor.witness, "carol")
        self.assertTrue(anchor.witness_signature)
        self.assertEqual(len(anchor.history), 1)
        entry = anchor.history[0]
        self.assertEqual(entry["witness"], "carol")
        self.assertEqual(entry["witness_signature"],
                         anchor.witness_signature)
        self.assertEqual(entry["state_hash"], a.state_hash())

        on_disk = read_mesh_anchor(self.anchor_path)
        self.assertEqual(on_disk, anchor)
        self.assertTrue(
            verify_mesh_anchor_history(
                self.anchor_path, signer="alice",
                secret_hex=self.alice_secret,
            )
        )
        check = self._check_witnessed(a)
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "anchored")
        self.assertTrue(check.signature_valid)
        self.assertTrue(check.witness_checked)
        self.assertTrue(check.witness_valid)
        print("WITNESSED MESH ANCHOR: alice signs, carol "
              "countersigns; a converged node verifies with "
              "both judgements recorded")

    # Case 18: a signer-alone mesh anchor under a
    # witness-configured check is anchor_witness_invalid,
    # and an AnchoredSyncNode configured with the witness
    # refuses a merge over it as a divergence under the same
    # name (rolled back, nothing accepted), reporting the
    # same name as its standing; against the countersigned
    # anchor the same node accepts and converges exactly as
    # in case 8.
    def test_signer_alone_refused_at_check_and_in_sync(self):
        a, b = self._honest_pair()
        write_mesh_anchor(
            a, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        check = self._check_witnessed(a)
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_witness_invalid")
        self.assertTrue(check.signature_valid)
        self.assertFalse(check.witness_valid)

        node = AnchoredSyncNode(
            "node-c", self.anchor_path,
            anchor_signer="alice", anchor_secret_hex=self.alice_secret,
            anchor_witness="carol",
            anchor_witness_secret_hex=self.carol_secret,
        )
        result = node.receive_entries(a.entries())
        self.assertTrue(result.divergence_detected)
        self.assertTrue(result.rolled_back)
        self.assertEqual(result.accepted, 0)
        self.assertEqual(result.reason, "anchor_witness_invalid")
        self.assertEqual(node.entries(), [])
        self.assertEqual(node.anchor_status(), "anchor_witness_invalid")
        reasons = [e.reason for e in node.divergence_log]
        self.assertIn("anchor_witness_invalid", reasons)
        print("WITNESSED MESH ANCHOR refusal: a merge over a "
              "signer-alone anchor refused as a divergence "
              "named anchor_witness_invalid, rolled back")

        # Countersigned: the same node accepts and converges.
        self.anchor_path.unlink()
        self._write_witnessed(a)
        result = node.receive_entries(a.entries())
        self.assertFalse(result.divergence_detected)
        self.assertEqual(node.state_hash(), b.state_hash())
        self.assertEqual(node.anchor_status(), "anchored")

    # Case 18, succession unchanged: bob re-anchoring over
    # alice's countersigned anchor - signing correctly as
    # himself, with carol's genuine countersignature on his
    # anchor - is refused at write as different_signer, the
    # refusal is logged beside the anchor, and the anchor is
    # unchanged. A witness equal to the signer is refused at
    # write, as in registration case 11.
    def test_succession_and_write_discipline_hold(self):
        a, _b = self._honest_pair()
        self._write_witnessed(a)
        before = self.anchor_path.read_bytes()
        with self.assertRaises(MeshAnchorError):
            write_mesh_anchor(
                a, self.anchor_path,
                signer="bob", secret_hex=self.bob_secret,
                witness="carol", witness_secret_hex=self.carol_secret,
            )
        self.assertEqual(self.anchor_path.read_bytes(), before)
        events = read_anchor_audit(self.anchor_path)
        rejected = [e for e in events if e.get("event") == "reanchor_rejected"]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], "different_signer")

        with self.assertRaises(MeshAnchorError):
            write_mesh_anchor(
                a, self.anchor_path,
                signer="alice", secret_hex=self.alice_secret,
                witness="alice", witness_secret_hex=self.alice_secret,
            )
        self.assertEqual(self.anchor_path.read_bytes(), before)

    # Case 18, backward compatibility: with no witness
    # configured, a countersigned mesh anchor is judged
    # exactly as case 11 (mesh) built - signer and heads -
    # and an unconfigured AnchoredSyncNode accepts a genuine
    # peer over it.
    def test_no_witness_configured_judges_as_before(self):
        a, b = self._honest_pair()
        self._write_witnessed(a)
        check = check_node_against_anchor(
            a, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.assertTrue(check.ok)
        self.assertFalse(check.witness_checked)

        node = AnchoredSyncNode(
            "node-c", self.anchor_path,
            anchor_signer="alice", anchor_secret_hex=self.alice_secret,
        )
        result = node.receive_entries(a.entries())
        self.assertFalse(result.divergence_detected)
        self.assertEqual(node.state_hash(), b.state_hash())
        self.assertEqual(node.anchor_status(), "anchored")


if __name__ == "__main__":
    unittest.main(verbosity=2)
