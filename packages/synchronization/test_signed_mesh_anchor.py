"""
Automated tests for signed mesh anchors with a kept history,
and for signed-anchor consultation in Gate 3 sync
(experiment 019).

Each test maps to tests/test-cases/sync-mesh.md case 11 (the
mesh half of registration.md case 10, written before this
code). Run with either:

    python3 -m unittest discover -s packages/synchronization -v
    python3 packages/synchronization/test_signed_mesh_anchor.py

The signing identity is a real Gate 1 enrollment: a
RegistrationChannel in a temp dir enrolls alice and bob at
runtime inside the tests and their secrets sign the mesh
anchor with the same domain-separated HMAC scheme as the
registry anchor (registration case 10). Nothing secret is
committed.

The re-anchor rule is registration case 10's, stated before
the build: only the identity that signed the current anchor
may re-anchor it; a different enrolled identity's attempt is
refused and logged beside the anchor, and accepted re-anchors
append signed, hash-chained history entries inside the anchor
file. An AnchoredSyncNode configured with a signing identity
refuses a merged state whose anchor signature does not verify
as a divergence named anchor_signature_invalid - distinct
from anchor_mismatch and anchor_stale. With no identity
configured, mesh anchors are judged by their heads exactly as
in cases 5 and 8. Standard library only.
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
    read_anchor_audit,
    read_mesh_anchor,
    verify_mesh_anchor_history,
    verify_node_against_anchor,
    write_mesh_anchor,
)
from anchored_sync import AnchoredSyncNode  # noqa: E402
from registration import RegistrationChannel  # noqa: E402
from sync_mesh import SyncNode  # noqa: E402


class SignedMeshAnchorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.anchor_path = self.tmp / "outside" / "mesh-anchor.json"

        channel = RegistrationChannel(
            self.tmp / "registry.jsonl", self.tmp / "audit.jsonl"
        )
        self.alice_secret = channel.enroll_user("alice")
        self.bob_secret = channel.enroll_user("bob")

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

    def _write_signed(self, node):
        return write_mesh_anchor(
            node, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )

    def _tamper_anchor_file(self, mutate):
        data = json.loads(self.anchor_path.read_text(encoding="utf-8"))
        mutate(data)
        self.anchor_path.write_text(
            json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )

    # Case 11, main outcome: a signed mesh anchor records its
    # signer, a converged node verifies against it with the
    # signature checked, and the kept history starts at
    # sequence 0 with the heads, state, and signer.
    def test_signed_mesh_anchor_verifies_and_history_starts(self):
        a, b = self._honest_pair()
        anchor = self._write_signed(a)
        self.assertEqual(anchor.signer, "alice")
        self.assertTrue(anchor.signature)
        self.assertEqual(len(anchor.history), 1)
        entry = anchor.history[0]
        self.assertEqual(entry["seq"], 0)
        self.assertEqual(entry["signer"], "alice")
        self.assertEqual(entry["state_hash"], a.state_hash())
        self.assertEqual(entry["origin_heads"], anchor.origin_heads)

        on_disk = read_mesh_anchor(self.anchor_path)
        self.assertEqual(on_disk, anchor)
        self.assertTrue(
            verify_mesh_anchor_history(
                self.anchor_path, signer="alice", secret_hex=self.alice_secret
            )
        )
        self.assertTrue(
            verify_node_against_anchor(
                b, self.anchor_path,
                signer="alice", secret_hex=self.alice_secret,
            )
        )
        check = check_node_against_anchor(
            b, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "anchored")
        self.assertTrue(check.signature_checked)
        self.assertTrue(check.signature_valid)

    # Case 11: missing and tampered signatures are
    # anchor_signature_invalid when an identity is configured -
    # never a valid anchor, never a mismatch.
    def test_missing_and_tampered_signature_is_signature_invalid(self):
        a, _b = self._honest_pair()
        write_mesh_anchor(a, self.anchor_path)  # unsigned
        check = check_node_against_anchor(
            a, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_signature_invalid")
        self.assertFalse(check.signature_valid)

        self._write_signed(a)

        def flip_signature(data):
            sig = data["signature"]
            data["signature"] = ("0" if sig[0] != "0" else "1") + sig[1:]

        self._tamper_anchor_file(flip_signature)
        check = check_node_against_anchor(
            a, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_signature_invalid")
        self.assertNotEqual(check.status, "anchor_mismatch")

    # Case 11, re-anchor rule: the same identity appends history
    # after honest growth; a different enrolled identity is
    # refused, logged, and leaves the anchor unchanged.
    def test_reanchor_same_identity_appends_different_refused_logged(self):
        a, b = self._honest_pair()
        self._write_signed(a)
        a.mutate("mode", "admin")
        a.sync_with(b)

        anchor = write_mesh_anchor(
            a, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.assertEqual(len(anchor.history), 2)
        self.assertEqual([e["seq"] for e in anchor.history], [0, 1])
        self.assertEqual([e["signer"] for e in anchor.history],
                         ["alice", "alice"])
        self.assertEqual(anchor.history[1]["state_hash"], a.state_hash())
        self.assertTrue(
            verify_mesh_anchor_history(
                self.anchor_path, signer="alice", secret_hex=self.alice_secret
            )
        )

        before = self.anchor_path.read_bytes()
        a.mutate("mode", "owner")
        with self.assertRaises(MeshAnchorError):
            write_mesh_anchor(
                a, self.anchor_path,
                signer="bob", secret_hex=self.bob_secret,
            )
        self.assertEqual(self.anchor_path.read_bytes(), before)
        events = read_anchor_audit(self.anchor_path)
        rejected = [e for e in events if e.get("event") == "reanchor_rejected"]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], "different_signer")
        self.assertEqual(rejected[0]["attempted_signer"], "bob")
        self.assertEqual(rejected[0]["current_signer"], "alice")

    # Case 11, kept history: editing an old history entry is
    # caught by history verification.
    def test_tampered_history_entry_is_caught(self):
        a, b = self._honest_pair()
        self._write_signed(a)
        a.mutate("mode", "admin")
        a.sync_with(b)
        write_mesh_anchor(
            a, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.assertTrue(
            verify_mesh_anchor_history(
                self.anchor_path, signer="alice", secret_hex=self.alice_secret
            )
        )
        self._tamper_anchor_file(
            lambda data: data["history"][0].update(state_hash="f" * 64)
        )
        self.assertFalse(verify_mesh_anchor_history(self.anchor_path))
        check = check_node_against_anchor(
            a, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_signature_invalid")

    # Case 11, Gate 3 consultation: an AnchoredSyncNode with a
    # signing identity accepts a genuine peer exactly as in
    # case 8, and refuses a peer when the anchor's signature
    # does not verify - divergence reason
    # anchor_signature_invalid, rolled back, nothing accepted.
    def test_anchored_sync_node_verifies_signature(self):
        a, _b = self._honest_pair()
        self._write_signed(a)

        victim = AnchoredSyncNode(
            "victim", anchor_path=self.anchor_path,
            anchor_signer="alice", anchor_secret_hex=self.alice_secret,
        )
        result = victim.receive_entries(a.entries())
        self.assertFalse(result.divergence_detected)
        self.assertEqual(result.accepted, 3)
        self.assertEqual(victim.anchor_status(), "anchored")

        def flip_signature(data):
            sig = data["signature"]
            data["signature"] = ("0" if sig[0] != "0" else "1") + sig[1:]

        self._tamper_anchor_file(flip_signature)
        victim2 = AnchoredSyncNode(
            "victim-2", anchor_path=self.anchor_path,
            anchor_signer="alice", anchor_secret_hex=self.alice_secret,
        )
        result = victim2.receive_entries(a.entries())
        self.assertTrue(result.divergence_detected)
        self.assertTrue(result.rolled_back)
        self.assertEqual(result.reason, "anchor_signature_invalid")
        self.assertEqual(result.accepted, 0)
        self.assertEqual(victim2.entries(), [])
        self.assertEqual(
            [e.reason for e in victim2.divergence_log],
            ["anchor_signature_invalid"],
        )
        self.assertEqual(victim2.anchor_status(), "anchor_signature_invalid")

    # Case 11, backward compatibility: with no identity
    # configured, a signed mesh anchor is judged by its heads
    # exactly as in cases 5 and 8 - an unconfigured node accepts
    # the genuine peer, signature unchecked.
    def test_no_identity_configured_judges_signed_anchor_by_heads(self):
        a, _b = self._honest_pair()
        self._write_signed(a)
        victim = AnchoredSyncNode("victim", anchor_path=self.anchor_path)
        result = victim.receive_entries(a.entries())
        self.assertFalse(result.divergence_detected)
        self.assertEqual(victim.anchor_status(), "anchored")
        check = check_node_against_anchor(a, self.anchor_path)
        self.assertTrue(check.ok)
        self.assertFalse(check.signature_checked)


if __name__ == "__main__":
    unittest.main(verbosity=2)
