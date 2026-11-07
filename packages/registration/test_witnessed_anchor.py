"""
Automated tests for witnessed anchors: a second identity
countersigns the anchor (experiment 029).

Each test maps to tests/test-cases/registration.md case 11
(witnessed anchors, written before this code; the mesh half
is sync-mesh.md case 18). Run with either:

    python3 -m unittest discover -s packages/registration -v
    python3 packages/registration/test_witnessed_anchor.py

Setup mirrors test_signed_anchor.py: a real Gate 1 channel
registers real libraries in a temp dir, and enrollment
secrets are issued at runtime inside the tests (nothing
secret is committed). A witnessed anchor extends experiment
019's signing: the signer signs as in case 10, and a second
enrolled identity, the witness, countersigns THAT signed
anchor with the same HMAC-SHA256 scheme over a
domain-separated canonical encoding of its own
(canonical_anchor_witness_bytes, prefix
"PBA-ANCHOR-WITNESS-v1"), covering the signer's identity and
signature with the head, the count, the history sequence,
and the previous history entry's hash. The countersignature
is part of the kept history entry for that write and is
mirrored at the anchor's top level.

A consultation configured with a witness identity (witness +
its verification secret, both or neither, case 10's opt-in
style) REQUIRES the countersignature: a signer-alone anchor,
a countersignature by a different identity, and a tampered
countersignature are each anchor_witness_invalid - distinct
from anchor_signature_invalid (which precedes it: a bad
signature is a signature failure even when the witness is
also wrong), from anchor_mismatch, and from anchor_stale.
With no witness configured, judgement is exactly case 10's.
Standard library only.
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
    read_anchor,
    read_anchor_audit,
    verify_anchor_history,
    verify_registry_against_anchor,
    write_anchor,
)
from registration import RegistrationChannel, sign_library  # noqa: E402

ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"


class WitnessedAnchorTests(unittest.TestCase):
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
        self.carol_secret = channel.enroll_user("carol")
        sig_a = sign_library(
            self.alice_secret, "alice", "member-access", "1.0.0", ALICE_CODE
        )
        self.record_a, _ = channel.register(
            author="alice", name="member-access", version="1.0.0",
            code=ALICE_CODE, signature=sig_a,
        )
        sig_b = sign_library(
            self.bob_secret, "bob", "billing-export", "0.2.0", b"policy: billing\n"
        )
        self.record_b, _ = channel.register(
            author="bob", name="billing-export", version="0.2.0",
            code=b"policy: billing\n", signature=sig_b,
        )
        assert channel.verify_registry_chain()

    def tearDown(self):
        self._tmp.cleanup()

    def _write_signed(self):
        return write_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )

    def _write_witnessed(self, path=None):
        return write_anchor(
            self.registry_log, path or self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
            witness="carol", witness_secret_hex=self.carol_secret,
        )

    def _grow_registry(self):
        secret_d = self.channel.enroll_user("dave")
        sig_d = sign_library(
            secret_d, "dave", "late-addition", "1.0.0", b"policy: late\n"
        )
        record_d, _ = self.channel.register(
            author="dave", name="late-addition", version="1.0.0",
            code=b"policy: late\n", signature=sig_d,
        )
        assert self.channel.verify_registry_chain()
        return record_d

    def _tamper_anchor_file(self, mutate, path=None):
        path = path or self.anchor_path
        data = json.loads(path.read_text(encoding="utf-8"))
        mutate(data)
        path.write_text(
            json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )

    def _check_witnessed(self, path=None):
        return check_registry_against_anchor(
            self.registry_log, path or self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
            witness="carol", witness_secret_hex=self.carol_secret,
        )

    # Case 11, main outcome: a countersigned anchor records
    # its witness and countersignature (top level and inside
    # the kept history entry's hashed body), and a check
    # configured with that witness verifies it: anchored,
    # with signature_checked/valid and witness_checked/valid
    # all recorded true.
    def test_countersigned_anchor_records_witness_and_verifies(self):
        anchor = self._write_witnessed()
        self.assertEqual(anchor.signer, "alice")
        self.assertEqual(anchor.witness, "carol")
        self.assertTrue(anchor.witness_signature)
        self.assertEqual(anchor.chain_head, self.record_b.record_hash)
        self.assertEqual(anchor.entry_count, 2)

        on_disk = read_anchor(self.anchor_path)
        self.assertEqual(on_disk, anchor)
        self.assertEqual(len(on_disk.history), 1)
        entry = on_disk.history[0]
        self.assertEqual(entry["witness"], "carol")
        self.assertEqual(entry["witness_signature"],
                         anchor.witness_signature)
        self.assertEqual(entry["signer"], "alice")

        self.assertTrue(
            verify_anchor_history(
                self.anchor_path, signer="alice",
                secret_hex=self.alice_secret,
            )
        )
        check = self._check_witnessed()
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "anchored")
        self.assertTrue(check.signature_checked)
        self.assertTrue(check.signature_valid)
        self.assertTrue(check.witness_checked)
        self.assertTrue(check.witness_valid)
        self.assertEqual(check.witness, "carol")
        self.assertTrue(
            verify_registry_against_anchor(
                self.registry_log, self.anchor_path,
                signer="alice", secret_hex=self.alice_secret,
                witness="carol", witness_secret_hex=self.carol_secret,
            )
        )
        print("WITNESSED ANCHOR: alice signs, carol "
              "countersigns; a witness-configured check is "
              "anchored with both judgements recorded")

    # Case 11: the three witness failures are each
    # anchor_witness_invalid under a witness-configured
    # check - an anchor signed by the signer alone, an anchor
    # countersigned by a different identity than the
    # configured witness, and a tampered countersignature -
    # and the signature judgement precedes the witness
    # judgement (a tampered SIGNATURE on a countersigned
    # anchor is anchor_signature_invalid, not a witness
    # failure).
    def test_missing_wrong_and_tampered_witness_is_witness_invalid(self):
        # Missing: alice signs alone (exactly a case 10
        # anchor); the signer's own judgement passes and the
        # witness judgement is the one that refuses.
        self._write_signed()
        check = self._check_witnessed()
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_witness_invalid")
        self.assertTrue(check.signature_valid)
        self.assertTrue(check.witness_checked)
        self.assertFalse(check.witness_valid)
        self.assertNotEqual(check.status, "anchor_signature_invalid")
        self.assertNotEqual(check.status, "anchor_mismatch")

        # Wrong witness: bob countersigns alice's anchor
        # (genuinely, with his own secret); the configured
        # witness is carol.
        other_path = self.anchor_dir / "other-anchor.json"
        write_anchor(
            self.registry_log, other_path,
            signer="alice", secret_hex=self.alice_secret,
            witness="bob", witness_secret_hex=self.bob_secret,
        )
        check = self._check_witnessed(path=other_path)
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_witness_invalid")
        self.assertEqual(check.witness, "bob")

        # Tampered countersignature: one character flipped at
        # the top level - the countersignature the check
        # verifies - is a witness failure under the same name.
        self.anchor_path.unlink(missing_ok=True)
        self._write_witnessed()

        def flip_top(data):
            sig = data["witness_signature"]
            data["witness_signature"] = (
                "0" if sig[0] != "0" else "1") + sig[1:]

        self._tamper_anchor_file(flip_top)
        check = self._check_witnessed()
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_witness_invalid")

        # The same flip INSIDE a history entry is a different
        # fact, named in case 10's discipline: the
        # countersignature is part of the entry's hashed body,
        # so editing it breaks the entry hash, and a broken
        # history chain is anchor_signature_invalid - the
        # signature judgement, which runs first.
        self.anchor_path.unlink()
        self._write_witnessed()

        def flip_entry(data):
            sig = data["history"][0]["witness_signature"]
            data["history"][0]["witness_signature"] = (
                "0" if sig[0] != "0" else "1") + sig[1:]

        self._tamper_anchor_file(flip_entry)
        check = self._check_witnessed()
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_signature_invalid")

        # Precedence: a tampered SIGNER signature on a
        # countersigned anchor is anchor_signature_invalid -
        # the signature judgement runs first, as in case 10.
        self.anchor_path.unlink()
        self._write_witnessed()

        def flip_signature(data):
            sig = data["signature"]
            data["signature"] = ("0" if sig[0] != "0" else "1") + sig[1:]

        self._tamper_anchor_file(flip_signature)
        check = self._check_witnessed()
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_signature_invalid")
        print("WITNESSED ANCHOR refusals: signer-alone, "
              "wrong-witness, and tampered countersignature "
              "are anchor_witness_invalid; a tampered signer "
              "signature stays anchor_signature_invalid")

    # Case 11, write discipline: the witness is a SECOND
    # identity - witness equal to signer is refused at write
    # time - and the witness arguments are both-or-neither
    # and require a signer, in case 10's opt-in style.
    def test_witness_write_discipline(self):
        with self.assertRaises(AnchorError):
            write_anchor(
                self.registry_log, self.anchor_path,
                signer="alice", secret_hex=self.alice_secret,
                witness="alice", witness_secret_hex=self.alice_secret,
            )
        with self.assertRaises(AnchorError):
            write_anchor(
                self.registry_log, self.anchor_path,
                signer="alice", secret_hex=self.alice_secret,
                witness="carol",
            )
        with self.assertRaises(AnchorError):
            write_anchor(
                self.registry_log, self.anchor_path,
                signer="alice", secret_hex=self.alice_secret,
                witness_secret_hex=self.carol_secret,
            )
        with self.assertRaises(AnchorError):
            write_anchor(
                self.registry_log, self.anchor_path,
                witness="carol", witness_secret_hex=self.carol_secret,
            )
        self.assertFalse(self.anchor_path.exists())

    # Case 11: same-signer re-anchor, countersigned, appends
    # to the kept history (each entry naming its witness),
    # and the grown registry is anchored under a
    # witness-configured check. And succession is unchanged:
    # bob signing as himself, with carol's genuine
    # countersignature on his anchor, is still refused as
    # different_signer and logged - the countersignature is
    # not a succession instrument.
    def test_reanchor_countersigned_appends_and_succession_holds(self):
        self._write_witnessed()
        record_d = self._grow_registry()
        anchor = self._write_witnessed()
        self.assertEqual(anchor.chain_head, record_d.record_hash)
        self.assertEqual(anchor.entry_count, 3)
        self.assertEqual(len(anchor.history), 2)
        self.assertEqual([e["seq"] for e in anchor.history], [0, 1])
        self.assertEqual([e["witness"] for e in anchor.history],
                         ["carol", "carol"])
        check = self._check_witnessed()
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "anchored")

        before = self.anchor_path.read_bytes()
        with self.assertRaises(AnchorError):
            write_anchor(
                self.registry_log, self.anchor_path,
                signer="bob", secret_hex=self.bob_secret,
                witness="carol", witness_secret_hex=self.carol_secret,
            )
        self.assertEqual(self.anchor_path.read_bytes(), before)
        events = read_anchor_audit(self.anchor_path)
        rejected = [e for e in events if e.get("event") == "reanchor_rejected"]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], "different_signer")
        self.assertEqual(rejected[0]["attempted_signer"], "bob")
        self.assertEqual(rejected[0]["current_signer"], "alice")
        print("WITNESSED ANCHOR succession: bob's anchor, "
              "genuinely countersigned by the witness, is "
              "still refused as different_signer and logged")

    # Case 11, backward compatibility: with NO witness
    # configured, judgement is exactly case 10's - a
    # countersigned anchor verifies as a signed anchor, a
    # signer-alone anchor verifies, and the check records
    # that no witness was checked. A witness-ONLY
    # configuration (no signer identity) still enforces the
    # countersignature on its own terms.
    def test_no_witness_configured_judges_as_case_10(self):
        self._write_witnessed()
        check = check_registry_against_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "anchored")
        self.assertFalse(check.witness_checked)
        self.assertIsNone(check.witness_valid)

        # Witness-only: the countersignature judgement stands
        # on its own (the countersigned anchor verifies; a
        # signer-alone anchor is witness-invalid).
        check = check_registry_against_anchor(
            self.registry_log, self.anchor_path,
            witness="carol", witness_secret_hex=self.carol_secret,
        )
        self.assertTrue(check.ok)
        self.assertFalse(check.signature_checked)
        self.assertTrue(check.witness_valid)

        self.anchor_path.unlink()
        self._write_signed()
        check = check_registry_against_anchor(
            self.registry_log, self.anchor_path,
            witness="carol", witness_secret_hex=self.carol_secret,
        )
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_witness_invalid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
