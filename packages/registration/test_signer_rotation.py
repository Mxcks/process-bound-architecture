"""
Automated tests for anchor signer rotation (experiment 038).

Each test maps to tests/test-cases/registration.md case 12
(anchor signer rotation, written before this code; the mesh
and wire halves are sync-mesh.md case 26). Run with either:

    python3 -m unittest discover -s packages/registration -v
    python3 packages/registration/test_signer_rotation.py

Setup mirrors test_witnessed_anchor.py: a real Gate 1 channel
registers real libraries in a temp dir, and enrollment
secrets are issued at runtime inside the tests (nothing
secret is committed).

Case 12 in one paragraph: a rotation record
(PBA-ANCHOR-ROTATION-v1) names the current signer, the
successor, and the point it takes effect at (the anchor
history sequence of the successor's first entry, with the
chain head and entry count that entry anchors); the current
signer signs it (PBA-ANCHOR-ROTATE-v1) and the successor
countersigns accepting it (PBA-ANCHOR-ROTATE-ACCEPT-v1), and
both signatures are required. The successor's first write
presents the record; the anchor then carries a rotations
list beside its kept history, and a check configured with
the lineage secrets (signer_secrets) judges the history
segment by segment: entries before the point under the old
signer, entries at and after it under the successor. An
anchor carrying post-point entries by the retired signer is
anchor_signer_retired; a rotation by a non-current signer
is anchor_rotation_signer_invalid; a rotation whose
signatures do not stand (a missing countersignature
included) is anchor_rotation_invalid; a rotation whose
stated head does not match the entry at its point is
anchor_rotation_mismatch. The witness rule of case 11 is
unchanged across a rotation. With no lineage configuration,
judgement is exactly cases 10/11's. Standard library only.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from anchor import (  # noqa: E402
    AnchorError,
    check_registry_against_anchor,
    make_anchor_rotation,
    read_anchor,
    read_anchor_audit,
    verify_anchor_history,
    write_anchor,
)
from registration import RegistrationChannel, sign_library  # noqa: E402

ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"


class SignerRotationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.tmp = tmp

        self.registry_dir = tmp / "registry-home"
        self.anchor_dir = tmp / "outside-anchor-home"
        self.registry_log = self.registry_dir / "registry.jsonl"
        self.anchor_path = self.anchor_dir / "registry-anchor.json"

        channel = RegistrationChannel(
            self.registry_log, self.registry_dir / "audit.jsonl"
        )
        self.channel = channel
        self.alice_secret = channel.enroll_user("alice")
        self.bob_secret = channel.enroll_user("bob")
        self.erin_secret = channel.enroll_user("erin")
        self.carol_secret = channel.enroll_user("carol")
        self.mallory_secret = channel.enroll_user("mallory")
        sig_a = sign_library(
            self.alice_secret, "alice", "member-access", "1.0.0", ALICE_CODE
        )
        self.record_a, _ = channel.register(
            author="alice", name="member-access", version="1.0.0",
            code=ALICE_CODE, signature=sig_a,
        )
        sig_b = sign_library(
            self.bob_secret, "bob", "billing-export", "0.2.0",
            b"policy: billing\n",
        )
        self.record_b, _ = channel.register(
            author="bob", name="billing-export", version="0.2.0",
            code=b"policy: billing\n", signature=sig_b,
        )
        assert channel.verify_registry_chain()
        # The pre-rotation registry, kept as a snapshot file so
        # the pre-rotation anchor can be checked against the
        # state it actually pinned.
        self.snapshot_before = tmp / "registry-before.jsonl"
        self.snapshot_before.write_bytes(self.registry_log.read_bytes())

        write_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.anchor_before_bytes = self.anchor_path.read_bytes()

    def tearDown(self):
        self._tmp.cleanup()

    # -- helpers -----------------------------------------------------

    def _grow_registry(self, author="dave", name="late-addition"):
        secret = self.channel.enroll_user(author)
        code = f"policy: {name}\n".encode("utf-8")
        sig = sign_library(secret, author, name, "1.0.0", code)
        record, _ = self.channel.register(
            author=author, name=name, version="1.0.0",
            code=code, signature=sig,
        )
        assert self.channel.verify_registry_chain()
        return record

    def _lineage(self, *names):
        secrets = {
            "alice": self.alice_secret,
            "bob": self.bob_secret,
            "erin": self.erin_secret,
            "carol": self.carol_secret,
            "mallory": self.mallory_secret,
        }
        return {name: secrets[name] for name in names}

    def _check_lineage(self, path=None, log=None, **extra):
        return check_registry_against_anchor(
            log or self.registry_log, path or self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
            signer_secrets=self._lineage("alice", "bob", "erin"),
            **extra,
        )

    def _rotate_alice_to_bob(self, record, count=3, **write_kwargs):
        """Grow-then-rotate: the rotation takes effect at
        anchor sequence 1, over the registry head `record`
        heads, and bob's first write applies it."""
        rotation = make_anchor_rotation(
            from_signer="alice", from_secret_hex=self.alice_secret,
            to_signer="bob", to_secret_hex=self.bob_secret,
            rotation_seq=0, effective_anchor_seq=1,
            effective_chain_head=record.record_hash,
            effective_entry_count=count,
        )
        anchor = write_anchor(
            self.registry_log, self.anchor_path,
            signer="bob", secret_hex=self.bob_secret,
            rotation=rotation,
            predecessor_secret_hex=self.alice_secret,
            **write_kwargs,
        )
        return rotation, anchor

    # Case 12, main outcome: the rotation record carries both
    # signatures; bob's first write applies it (rotations list
    # recorded in the anchor, history segmented alice then
    # bob); a lineage-configured check is anchored with the
    # rotation judgement recorded and bob named current; the
    # segmented history verifies; and bob's later writes are
    # ordinary same-identity writes that keep verifying.
    def test_rotation_applies_and_segmented_history_verifies(self):
        record = self._grow_registry()
        rotation, anchor = self._rotate_alice_to_bob(record)

        self.assertEqual(anchor.signer, "bob")
        self.assertEqual(len(anchor.rotations), 1)
        carried = anchor.rotations[0]
        self.assertEqual(carried["from_signer"], "alice")
        self.assertEqual(carried["to_signer"], "bob")
        self.assertEqual(carried["rotation_seq"], 0)
        self.assertEqual(carried["effective_anchor_seq"], 1)
        self.assertEqual(carried["effective_chain_head"],
                         record.record_hash)
        self.assertEqual(carried["effective_entry_count"], 3)
        self.assertTrue(carried["signature"])
        self.assertTrue(carried["countersignature"])
        self.assertEqual(
            [e["signer"] for e in anchor.history], ["alice", "bob"])

        on_disk = read_anchor(self.anchor_path)
        self.assertEqual(on_disk, anchor)
        self.assertEqual(len(on_disk.rotations), 1)

        check = self._check_lineage()
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "anchored")
        self.assertTrue(check.rotation_checked)
        self.assertTrue(check.rotation_valid)
        self.assertIsNone(check.rotation_status)
        self.assertEqual(check.current_signer, "bob")
        self.assertTrue(check.signature_valid)

        self.assertTrue(
            verify_anchor_history(
                self.anchor_path,
                signer_secrets=self._lineage("alice", "bob"),
            )
        )

        # Bob's later writes are ordinary case 10 writes; over
        # a rotated anchor they present the lineage secrets so
        # the segmented history can be verified at the write.
        record2 = self._grow_registry(author="frank",
                                      name="second-addition")
        anchor = write_anchor(
            self.registry_log, self.anchor_path,
            signer="bob", secret_hex=self.bob_secret,
            signer_secrets=self._lineage("alice", "bob"),
        )
        self.assertEqual(anchor.entry_count, 4)
        self.assertEqual(
            [e["signer"] for e in anchor.history],
            ["alice", "bob", "bob"])
        check = self._check_lineage()
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "anchored")
        self.assertEqual(check.current_signer, "bob")
        print("ROTATION: alice rotates to bob; the segmented "
              "history verifies segment by segment and bob's "
              "later writes are ordinary same-identity writes")

    # Case 12: history signed before the rotation stays
    # valid. The pre-rotation anchor, checked against the
    # pre-rotation registry by a verifier HOLDING the rotation
    # record, is anchored exactly as case 10 judges it, its
    # current signer still alice. A held record that disagrees
    # with the anchor's own is anchor_rotation_invalid.
    def test_pre_rotation_anchor_accepted_with_held_rotation(self):
        record = self._grow_registry()
        rotation, _ = self._rotate_alice_to_bob(record)

        before_path = self.anchor_dir / "anchor-before.json"
        before_path.write_bytes(self.anchor_before_bytes)
        check = check_registry_against_anchor(
            self.snapshot_before, before_path,
            signer="alice", secret_hex=self.alice_secret,
            signer_secrets=self._lineage("alice", "bob"),
            rotations=[rotation],
        )
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "anchored")
        self.assertTrue(check.rotation_valid)
        self.assertEqual(check.current_signer, "alice")

        disagreeing = dict(rotation)
        disagreeing["to_signer"] = "erin"
        check = check_registry_against_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
            signer_secrets=self._lineage("alice", "bob", "erin"),
            rotations=[disagreeing],
        )
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_rotation_invalid")
        print("ROTATION: the pre-rotation anchor still "
              "verifies for a verifier holding the rotation; "
              "a disagreeing held record is refused")

    # Case 12: an anchor carrying entries at or after the
    # rotation point signed by the RETIRED signer is refused
    # as anchor_signer_retired - ahead of the signature
    # judgement, under that name and no other. Here alice
    # herself (or anyone holding her secret) honestly extends
    # the pre-rotation anchor over the grown registry: every
    # signature is genuine, and the refusal is the rotation.
    def test_retired_signer_refused_anchor_signer_retired(self):
        record = self._grow_registry()
        rotation, _ = self._rotate_alice_to_bob(record)

        retired_path = self.anchor_dir / "anchor-retired.json"
        retired_path.write_bytes(self.anchor_before_bytes)
        write_anchor(
            self.registry_log, retired_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        check = self._check_lineage(
            path=retired_path, rotations=[rotation])
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_signer_retired")
        self.assertTrue(check.rotation_checked)
        self.assertFalse(check.rotation_valid)
        self.assertEqual(check.rotation_status,
                         "anchor_signer_retired")
        self.assertNotEqual(check.status, "anchor_signature_invalid")
        self.assertNotEqual(check.status, "anchor_mismatch")
        print("ROTATION: the retired signer's honestly "
              "extended anchor is refused as "
              "anchor_signer_retired")

    # Case 12, the write path's refusal shapes, each named,
    # each leaving the anchor byte-for-byte unchanged and
    # audit-logged as rotation_rejected: a rotation signed by
    # a signer who is not current (anchor_rotation_signer_
    # invalid), a rotation missing the successor's
    # countersignature and one whose signature does not
    # verify (anchor_rotation_invalid), and a rotation whose
    # stated point is not the write being made - wrong head,
    # wrong sequence (anchor_rotation_mismatch).
    def test_rotation_write_refusal_shapes(self):
        record = self._grow_registry()
        head = record.record_hash

        def attempt(rotation, signer, secret, predecessor):
            before = self.anchor_path.read_bytes()
            with self.assertRaises(AnchorError) as ctx:
                write_anchor(
                    self.registry_log, self.anchor_path,
                    signer=signer, secret_hex=secret,
                    rotation=rotation,
                    predecessor_secret_hex=predecessor,
                )
            self.assertEqual(self.anchor_path.read_bytes(), before)
            return str(ctx.exception)

        # Non-current signer: bob is not the anchor's signer,
        # so a bob -> erin rotation is not bob's to make.
        message = attempt(
            make_anchor_rotation(
                from_signer="bob", from_secret_hex=self.bob_secret,
                to_signer="erin", to_secret_hex=self.erin_secret,
                rotation_seq=0, effective_anchor_seq=1,
                effective_chain_head=head, effective_entry_count=3),
            "erin", self.erin_secret, self.bob_secret)
        self.assertIn("anchor_rotation_signer_invalid", message)

        # Missing countersignature: the record without its
        # acceptance half is not a rotation.
        rotation = make_anchor_rotation(
            from_signer="alice", from_secret_hex=self.alice_secret,
            to_signer="bob", to_secret_hex=self.bob_secret,
            rotation_seq=0, effective_anchor_seq=1,
            effective_chain_head=head, effective_entry_count=3)
        unsigned_acceptance = dict(rotation)
        del unsigned_acceptance["countersignature"]
        message = attempt(unsigned_acceptance, "bob",
                          self.bob_secret, self.alice_secret)
        self.assertIn("anchor_rotation_invalid", message)
        self.assertNotIn("anchor_rotation_signer_invalid", message)

        # Tampered statement signature.
        tampered = dict(rotation)
        sig = tampered["signature"]
        tampered["signature"] = ("0" if sig[0] != "0" else "1") + sig[1:]
        message = attempt(tampered, "bob", self.bob_secret,
                          self.alice_secret)
        self.assertIn("anchor_rotation_invalid", message)

        # Wrong effective head: the rotation names the
        # pre-growth head, not the head this write anchors.
        message = attempt(
            make_anchor_rotation(
                from_signer="alice",
                from_secret_hex=self.alice_secret,
                to_signer="bob", to_secret_hex=self.bob_secret,
                rotation_seq=0, effective_anchor_seq=1,
                effective_chain_head=self.record_b.record_hash,
                effective_entry_count=3),
            "bob", self.bob_secret, self.alice_secret)
        self.assertIn("anchor_rotation_mismatch", message)

        # Wrong effective sequence.
        message = attempt(
            make_anchor_rotation(
                from_signer="alice",
                from_secret_hex=self.alice_secret,
                to_signer="bob", to_secret_hex=self.bob_secret,
                rotation_seq=0, effective_anchor_seq=2,
                effective_chain_head=head, effective_entry_count=3),
            "bob", self.bob_secret, self.alice_secret)
        self.assertIn("anchor_rotation_mismatch", message)

        events = read_anchor_audit(self.anchor_path)
        rejected = [e for e in events
                    if e.get("event") == "rotation_rejected"]
        self.assertEqual(len(rejected), 5)
        self.assertEqual(
            [e["reason"] for e in rejected],
            ["anchor_rotation_signer_invalid",
             "anchor_rotation_invalid",
             "anchor_rotation_invalid",
             "anchor_rotation_mismatch",
             "anchor_rotation_mismatch"])
        # And the genuine rotation still lands afterward.
        self._rotate_alice_to_bob(record)
        print("ROTATION write refusals: non-current signer, "
              "missing countersignature, tampered signature, "
              "wrong head, wrong sequence - each named, each "
              "logged, the anchor unchanged")

    # Case 12: succession continues from the successor. A
    # second rotation (bob -> erin) chains the same way, each
    # judged against the signer current at its point, and the
    # three-segment history verifies. A rotation record in a
    # delivered anchor whose from_signer is not the signer
    # current at its point (alice again, after bob) is
    # refused at the check as anchor_rotation_signer_invalid
    # however genuinely alice signed it.
    def test_second_rotation_chains(self):
        record = self._grow_registry()
        self._rotate_alice_to_bob(record)
        record2 = self._grow_registry(author="frank",
                                      name="second-addition")
        rotation2 = make_anchor_rotation(
            from_signer="bob", from_secret_hex=self.bob_secret,
            to_signer="erin", to_secret_hex=self.erin_secret,
            rotation_seq=1, effective_anchor_seq=2,
            effective_chain_head=record2.record_hash,
            effective_entry_count=4,
        )
        anchor = write_anchor(
            self.registry_log, self.anchor_path,
            signer="erin", secret_hex=self.erin_secret,
            rotation=rotation2,
            predecessor_secret_hex=self.bob_secret,
            signer_secrets=self._lineage("alice", "bob"),
        )
        self.assertEqual(
            [e["signer"] for e in anchor.history],
            ["alice", "bob", "erin"])
        self.assertEqual(len(anchor.rotations), 2)
        check = self._check_lineage()
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "anchored")
        self.assertEqual(check.current_signer, "erin")

        # A competing later rotation from alice (seq 2 in a
        # crafted copy, genuinely signed by alice, countersigned
        # by mallory, pointing at the tip): alice is not the
        # signer current at that point, so the check refuses
        # the crafted anchor under the signer name.
        crafted = json.loads(self.anchor_path.read_text(
            encoding="utf-8"))
        bogus = make_anchor_rotation(
            from_signer="alice", from_secret_hex=self.alice_secret,
            to_signer="mallory",
            to_secret_hex=self.mallory_secret,
            rotation_seq=2, effective_anchor_seq=3,
            effective_chain_head=record2.record_hash,
            effective_entry_count=4,
        )
        crafted["rotations"] = crafted["rotations"] + [bogus]
        crafted_path = self.anchor_dir / "anchor-crafted.json"
        crafted_path.write_text(
            json.dumps(crafted, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        check = self._check_lineage(path=crafted_path)
        self.assertFalse(check.ok)
        self.assertEqual(check.status,
                         "anchor_rotation_signer_invalid")
        print("ROTATION: the second rotation chains from "
              "the successor; a later rotation from the "
              "retired signer is refused as "
              "anchor_rotation_signer_invalid")

    # Case 12, witness interplay: where a witness is
    # configured, rotation changes who signs and nothing
    # else. The successor's rotated write countersigned by
    # the configured witness verifies with the witness
    # judgement recorded; the successor's later write WITHOUT
    # the countersignature is anchor_witness_invalid at a
    # witness-configured check, its rotation judgement valid.
    def test_witness_requirement_unchanged_across_rotation(self):
        anchor_path = self.anchor_dir / "witnessed-anchor.json"
        write_anchor(
            self.registry_log, anchor_path,
            signer="alice", secret_hex=self.alice_secret,
            witness="carol", witness_secret_hex=self.carol_secret,
        )
        record = self._grow_registry()
        rotation = make_anchor_rotation(
            from_signer="alice", from_secret_hex=self.alice_secret,
            to_signer="bob", to_secret_hex=self.bob_secret,
            rotation_seq=0, effective_anchor_seq=1,
            effective_chain_head=record.record_hash,
            effective_entry_count=3,
        )
        write_anchor(
            self.registry_log, anchor_path,
            signer="bob", secret_hex=self.bob_secret,
            rotation=rotation,
            predecessor_secret_hex=self.alice_secret,
            witness="carol", witness_secret_hex=self.carol_secret,
        )

        def check_witnessed():
            return check_registry_against_anchor(
                self.registry_log, anchor_path,
                signer="alice", secret_hex=self.alice_secret,
                signer_secrets=self._lineage("alice", "bob"),
                witness="carol",
                witness_secret_hex=self.carol_secret,
            )

        check = check_witnessed()
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "anchored")
        self.assertTrue(check.rotation_valid)
        self.assertTrue(check.witness_valid)

        self._grow_registry(author="frank", name="second-addition")
        write_anchor(
            self.registry_log, anchor_path,
            signer="bob", secret_hex=self.bob_secret,
            signer_secrets=self._lineage("alice", "bob"),
        )
        check = check_witnessed()
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_witness_invalid")
        self.assertTrue(check.rotation_valid)
        self.assertTrue(check.signature_valid)
        print("ROTATION + WITNESS: the successor's writes "
              "need the configured witness exactly as the "
              "original signer's did")

    # Case 12, backward compatibility: an unrotated anchor
    # under a signer-only configuration is exactly case 10's
    # judgement (anchored, no rotation judgement recorded);
    # with no identity at all a rotated anchor is judged by
    # its head, as experiment 019 judges any signed anchor;
    # and a signer-only configuration over a ROTATED anchor
    # cannot verify its segmented history, which is a
    # signature failure, not a silent pass.
    def test_no_lineage_configuration_behaves_as_before(self):
        check = check_registry_against_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.assertTrue(check.ok)
        self.assertFalse(check.rotation_checked)
        self.assertIsNone(check.rotation_valid)

        record = self._grow_registry()
        self._rotate_alice_to_bob(record)

        check = check_registry_against_anchor(
            self.registry_log, self.anchor_path)
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "anchored")
        self.assertFalse(check.rotation_checked)

        check = check_registry_against_anchor(
            self.registry_log, self.anchor_path,
            signer="bob", secret_hex=self.bob_secret,
        )
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_signature_invalid")
        print("ROTATION backward compatibility: unrotated "
              "anchors judge exactly as case 10; a rotated "
              "anchor with no identity is judged by its head")


if __name__ == "__main__":
    unittest.main(verbosity=2)
