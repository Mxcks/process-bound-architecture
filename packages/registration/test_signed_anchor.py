"""
Automated tests for signed anchors with a kept history
(experiment 019).

Each test maps to tests/test-cases/registration.md case 10
(signed anchors with a kept history, written before this code).
Run with either:

    python3 -m unittest discover -s packages/registration -v
    python3 packages/registration/test_signed_anchor.py

Setup mirrors test_anchor.py: a real Gate 1 channel registers
real libraries in a temp dir, and enrollment secrets are issued
at runtime inside the tests (nothing secret is committed). A
signed anchor reuses Gate 1's signing machinery: the signer's
enrollment secret, HMAC-SHA256, a domain-separated canonical
encoding (sign_anchor, in the style of sign_revocation).

The re-anchor rule was stated in case 10 before the build:
re-anchoring a signed anchor requires a valid signature from
the SAME identity that signed the current anchor; a different
enrolled identity's attempt is refused and logged in an anchor
audit log beside the anchor file, and the anchor is unchanged.
Accepted re-anchors append to a signed, hash-chained history
kept inside the anchor file instead of overwriting silently.

A signature failure is its own status, anchor_signature_invalid:
distinct from anchor_mismatch and anchor_stale, and never a
valid anchor. With no signing identity configured, unsigned
anchors behave exactly as experiment 012 built them.
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


class SignedAnchorTests(unittest.TestCase):
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

    def _grow_registry(self):
        secret_c = self.channel.enroll_user("carol")
        sig_c = sign_library(
            secret_c, "carol", "late-addition", "1.0.0", b"policy: late\n"
        )
        record_c, _ = self.channel.register(
            author="carol", name="late-addition", version="1.0.0",
            code=b"policy: late\n", signature=sig_c,
        )
        assert self.channel.verify_registry_chain()
        return record_c

    def _tamper_anchor_file(self, mutate):
        data = json.loads(self.anchor_path.read_text(encoding="utf-8"))
        mutate(data)
        self.anchor_path.write_text(
            json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )

    # Case 10, main outcome: a signed anchor records its signer
    # and signature, the genuine registry verifies against it
    # with the signature checked, and its kept history starts at
    # sequence 0 naming the head, the count, and the signer.
    def test_signed_anchor_records_signer_and_verifies_with_history(self):
        anchor = self._write_signed()
        self.assertEqual(anchor.signer, "alice")
        self.assertTrue(anchor.signature)
        self.assertEqual(anchor.chain_head, self.record_b.record_hash)
        self.assertEqual(anchor.entry_count, 2)

        on_disk = read_anchor(self.anchor_path)
        self.assertEqual(on_disk, anchor)
        self.assertEqual(len(on_disk.history), 1)
        entry = on_disk.history[0]
        self.assertEqual(entry["seq"], 0)
        self.assertEqual(entry["signer"], "alice")
        self.assertEqual(entry["chain_head"], self.record_b.record_hash)
        self.assertEqual(entry["entry_count"], 2)
        self.assertTrue(entry["signature"])

        self.assertTrue(verify_anchor_history(self.anchor_path))
        self.assertTrue(
            verify_anchor_history(
                self.anchor_path, signer="alice", secret_hex=self.alice_secret
            )
        )
        check = check_registry_against_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "anchored")
        self.assertTrue(check.signature_checked)
        self.assertTrue(check.signature_valid)
        self.assertTrue(
            verify_registry_against_anchor(
                self.registry_log, self.anchor_path,
                signer="alice", secret_hex=self.alice_secret,
            )
        )

    # Case 10: a missing signature, a signature checked with the
    # wrong secret, a tampered signature, and a head edited after
    # signing are each anchor_signature_invalid - never a valid
    # anchor, and never reported as mismatch or stale.
    def test_missing_wrong_and_tampered_signature_is_signature_invalid(self):
        # Missing: an unsigned anchor consulted WITH an identity.
        write_anchor(self.registry_log, self.anchor_path)
        check = check_registry_against_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_signature_invalid")
        self.assertTrue(check.signature_checked)
        self.assertFalse(check.signature_valid)

        # Wrong secret: bob's secret cannot verify alice's anchor.
        self._write_signed()
        check = check_registry_against_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.bob_secret,
        )
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_signature_invalid")
        # Wrong claimed signer: the anchor was signed by alice.
        check = check_registry_against_anchor(
            self.registry_log, self.anchor_path,
            signer="bob", secret_hex=self.bob_secret,
        )
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_signature_invalid")

        # Tampered signature in the file.
        def flip_signature(data):
            sig = data["signature"]
            data["signature"] = ("0" if sig[0] != "0" else "1") + sig[1:]

        self._tamper_anchor_file(flip_signature)
        check = check_registry_against_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_signature_invalid")

        # Head edited after signing (the file-level re-anchor
        # attack experiments 012-018 named): the signature no
        # longer covers the head, so this is a signature failure,
        # not a head mismatch. (The tampered file above must be
        # removed first: re-anchoring over it is itself refused,
        # because the existing signature does not verify.)
        self.anchor_path.unlink()
        self._write_signed()
        self._tamper_anchor_file(
            lambda data: data.update(chain_head="f" * 64)
        )
        check = check_registry_against_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_signature_invalid")
        self.assertNotEqual(check.status, "anchor_mismatch")

    # Case 10, re-anchor rule (same identity): after honest
    # growth, the same signer re-anchors; the history grows by
    # append (sequences 0 and 1, both alice), the new head is
    # the second entry's, the history verifies, and the grown
    # registry is anchored again.
    def test_reanchor_same_identity_appends_to_kept_history(self):
        self._write_signed()
        record_c = self._grow_registry()

        anchor = write_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.assertEqual(anchor.chain_head, record_c.record_hash)
        self.assertEqual(anchor.entry_count, 3)
        self.assertEqual(len(anchor.history), 2)
        self.assertEqual([e["seq"] for e in anchor.history], [0, 1])
        self.assertEqual([e["signer"] for e in anchor.history],
                         ["alice", "alice"])
        self.assertEqual(anchor.history[0]["chain_head"],
                         self.record_b.record_hash)
        self.assertEqual(anchor.history[1]["chain_head"], record_c.record_hash)

        self.assertTrue(
            verify_anchor_history(
                self.anchor_path, signer="alice", secret_hex=self.alice_secret
            )
        )
        check = check_registry_against_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "anchored")

    # Case 10, re-anchor rule (different identity): bob, enrolled
    # and signing correctly as himself, is REFUSED; the anchor is
    # byte-for-byte the anchor alice left, and the refusal is in
    # the anchor audit log beside the anchor file.
    def test_reanchor_different_identity_refused_and_logged(self):
        self._write_signed()
        before = self.anchor_path.read_bytes()
        self._grow_registry()

        with self.assertRaises(AnchorError):
            write_anchor(
                self.registry_log, self.anchor_path,
                signer="bob", secret_hex=self.bob_secret,
            )
        self.assertEqual(self.anchor_path.read_bytes(), before)

        events = read_anchor_audit(self.anchor_path)
        rejected = [e for e in events if e.get("event") == "reanchor_rejected"]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], "different_signer")
        self.assertEqual(rejected[0]["attempted_signer"], "bob")
        self.assertEqual(rejected[0]["current_signer"], "alice")

        # The same-identity re-anchor still works afterward.
        write_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.assertEqual(len(read_anchor(self.anchor_path).history), 2)

    # Case 10, kept history: editing an old history entry's head
    # is caught by history verification (hash chain and
    # signature), and a consultation with an identity configured
    # treats the tampered history as anchor_signature_invalid.
    def test_tampered_history_entry_is_caught(self):
        self._write_signed()
        self._grow_registry()
        write_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.assertTrue(
            verify_anchor_history(
                self.anchor_path, signer="alice", secret_hex=self.alice_secret
            )
        )

        self._tamper_anchor_file(
            lambda data: data["history"][0].update(chain_head="e" * 64)
        )
        self.assertFalse(verify_anchor_history(self.anchor_path))
        self.assertFalse(
            verify_anchor_history(
                self.anchor_path, signer="alice", secret_hex=self.alice_secret
            )
        )
        check = check_registry_against_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_signature_invalid")

    # Case 10, backward compatibility: with NO signing identity
    # configured anywhere, unsigned anchors behave exactly as in
    # experiment 012 (including the overwrite re-anchor
    # lifecycle), and a signed anchor is judged by its head
    # exactly as an unsigned one is.
    def test_unsigned_anchors_unchanged_when_no_identity_configured(self):
        write_anchor(self.registry_log, self.anchor_path)
        check = check_registry_against_anchor(
            self.registry_log, self.anchor_path
        )
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "anchored")
        self.assertFalse(check.signature_checked)

        self._grow_registry()
        check = check_registry_against_anchor(
            self.registry_log, self.anchor_path
        )
        self.assertFalse(check.ok)
        write_anchor(self.registry_log, self.anchor_path)
        self.assertTrue(
            verify_registry_against_anchor(self.registry_log, self.anchor_path)
        )

        signed = self._write_signed()
        self.assertEqual(signed.signer, "alice")
        check = check_registry_against_anchor(
            self.registry_log, self.anchor_path
        )
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "anchored")
        self.assertFalse(check.signature_checked)


if __name__ == "__main__":
    unittest.main(verbosity=2)
