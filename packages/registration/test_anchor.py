"""
Automated tests for the external chain anchor (experiment 012).

Each test maps to tests/test-cases/registration.md case 7 (external
chain anchor, written before this code). Run with either:

    python3 -m unittest discover -s packages/registration -v
    python3 packages/registration/test_anchor.py

Setup mirrors test_registration.py: a real Gate 1 channel registers
real libraries in a temp dir. The anchor file is written to a
DIFFERENT directory than the registry (the prototype stand-in for
"outside the system": a file inside the registry's own directory
would be rewritten together with the registry and would prove
nothing). The negative control builds a second, fully self-
consistent registry in another directory and presents its file as
the registry: it passes the chain's own internal verification and
must fail the anchor check. Standard library only.
"""

import shutil
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
    verify_registry_against_anchor,
    write_anchor,
)
from registration import RegistrationChannel, sign_library  # noqa: E402

ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
MALLORY_CODE = b"policy: allow if caller.tier == 'attacker'\n"


class ExternalAnchorTests(unittest.TestCase):
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
        secret = channel.enroll_user("alice")
        bob_secret = channel.enroll_user("bob")
        sig_a = sign_library(secret, "alice", "member-access", "1.0.0", ALICE_CODE)
        self.record_a, _ = channel.register(
            author="alice", name="member-access", version="1.0.0",
            code=ALICE_CODE, signature=sig_a,
        )
        sig_b = sign_library(
            bob_secret, "bob", "billing-export", "0.2.0", b"policy: billing\n"
        )
        self.record_b, _ = channel.register(
            author="bob", name="billing-export", version="0.2.0",
            code=b"policy: billing\n", signature=sig_b,
        )
        assert channel.verify_registry_chain()

    def tearDown(self):
        self._tmp.cleanup()

    def _rewrite_from_genesis(self) -> Path:
        """A fully rewritten, internally consistent registry file.

        A second channel in its own directory registers different
        content; its chain recomputes cleanly from genesis, so the
        chain's own verification passes on it. It is NOT the chain
        that was anchored.
        """
        other = self.tmp / "rewritten-home"
        other_channel = RegistrationChannel(
            other / "registry.jsonl", other / "audit.jsonl"
        )
        secret = other_channel.enroll_user("mallory")
        sig = sign_library(secret, "mallory", "member-access", "1.0.0", MALLORY_CODE)
        other_channel.register(
            author="mallory", name="member-access", version="1.0.0",
            code=MALLORY_CODE, signature=sig,
        )
        assert other_channel.verify_registry_chain()
        rewritten = self.tmp / "presented-rewritten.jsonl"
        shutil.copyfile(other / "registry.jsonl", rewritten)
        return rewritten

    # Case 7, main outcome: the chain head (latest entry hash) and
    # the entry count are written to an anchor file outside the
    # registry's directory, and the genuine registry verifies
    # against that outside anchor.
    def test_anchor_captures_head_outside_registry_and_genuine_registry_verifies(self):
        anchor = write_anchor(self.registry_log, self.anchor_path)
        self.assertEqual(anchor.entry_count, 2)
        self.assertEqual(anchor.chain_head, self.record_b.record_hash)
        # The anchor lives outside the registry's own directory.
        self.assertNotEqual(self.anchor_path.parent, self.registry_log.parent)
        self.assertTrue(self.anchor_path.exists())

        on_disk = read_anchor(self.anchor_path)
        self.assertEqual(on_disk, anchor)

        self.assertTrue(
            verify_registry_against_anchor(self.registry_log, self.anchor_path)
        )
        check = check_registry_against_anchor(self.registry_log, self.anchor_path)
        self.assertTrue(check.internal_verifies)
        self.assertTrue(check.head_matches)
        self.assertTrue(check.count_matches)
        self.assertTrue(check.ok)
        self.assertEqual(check.status, "anchored")

        # A Gate 2 snapshot copy is the same chain: it verifies too.
        snapshot = self.tmp / "snapshot-copy.jsonl"
        shutil.copyfile(self.registry_log, snapshot)
        self.assertTrue(verify_registry_against_anchor(snapshot, self.anchor_path))

        # Refusing to anchor inside the registry's own directory is
        # part of the design, not a detail: that file would be
        # rewritten with the registry and prove nothing.
        with self.assertRaises(AnchorError):
            write_anchor(self.registry_log, self.registry_dir / "anchor.json")

    # Case 7, negative control: a registry rewritten from genesis,
    # internally consistent so its own chain verification passes,
    # must FAIL the anchor check. This is the exact gap experiments
    # 002-010 named: internal verification alone cannot catch it.
    def test_negative_control_full_rewrite_passes_internal_but_fails_anchor(self):
        write_anchor(self.registry_log, self.anchor_path)
        rewritten = self._rewrite_from_genesis()

        # Internal verification alone says the rewrite is fine.
        presented = RegistrationChannel(rewritten, self.tmp / "audit2.jsonl")
        self.assertTrue(presented.verify_registry_chain())

        # The anchor check is the one that catches it.
        self.assertFalse(verify_registry_against_anchor(rewritten, self.anchor_path))
        check = check_registry_against_anchor(rewritten, self.anchor_path)
        self.assertTrue(check.internal_verifies)
        self.assertFalse(check.head_matches)
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "anchor_mismatch")

    # Case 7: a single edited entry is caught too - by internal
    # verification first, and the anchor check reports that honestly
    # instead of claiming an anchor mismatch.
    def test_single_entry_tamper_fails_internal_and_anchor_check(self):
        write_anchor(self.registry_log, self.anchor_path)
        lines = self.registry_log.read_text(encoding="utf-8").splitlines()
        tampered = self.tmp / "tampered.jsonl"
        edited = lines[0].replace("member-access", "member-hacked")
        self.assertNotEqual(edited, lines[0])
        tampered.write_text("\n".join([edited] + lines[1:]) + "\n", encoding="utf-8")

        self.assertFalse(verify_registry_against_anchor(tampered, self.anchor_path))
        check = check_registry_against_anchor(tampered, self.anchor_path)
        self.assertFalse(check.internal_verifies)
        self.assertFalse(check.ok)
        self.assertEqual(check.status, "internal_verification_failed")

    # Case 7, boundaries: the anchor pins head AND length. A
    # truncated presentation (the genuine prefix, internally valid)
    # and an extended presentation (genuine, then one more real
    # registration appended after anchoring) both fail; re-anchoring
    # after the extension makes the extended registry verify, which
    # is the anchor's lifecycle: anchor again when the head moves.
    def test_anchor_pins_length_truncation_and_extension_fail_until_reanchored(self):
        write_anchor(self.registry_log, self.anchor_path)

        lines = self.registry_log.read_text(encoding="utf-8").splitlines()
        truncated = self.tmp / "truncated.jsonl"
        truncated.write_text(lines[0] + "\n", encoding="utf-8")
        self.assertTrue(
            RegistrationChannel(truncated, self.tmp / "a3.jsonl").verify_registry_chain()
        )
        check = check_registry_against_anchor(truncated, self.anchor_path)
        self.assertTrue(check.internal_verifies)
        self.assertFalse(check.count_matches)
        self.assertFalse(check.ok)

        secret_c = self.channel.enroll_user("carol")
        sig_c = sign_library(
            secret_c, "carol", "late-addition", "1.0.0", b"policy: late\n"
        )
        self.channel.register(
            author="carol", name="late-addition", version="1.0.0",
            code=b"policy: late\n", signature=sig_c,
        )
        self.assertTrue(self.channel.verify_registry_chain())
        check = check_registry_against_anchor(self.registry_log, self.anchor_path)
        self.assertTrue(check.internal_verifies)
        self.assertFalse(check.head_matches)
        self.assertFalse(check.ok)

        write_anchor(self.registry_log, self.anchor_path)
        self.assertTrue(
            verify_registry_against_anchor(self.registry_log, self.anchor_path)
        )

        # No anchor file at all is an error the caller sees, never a
        # silent pass.
        with self.assertRaises(AnchorError):
            verify_registry_against_anchor(
                self.registry_log, self.tmp / "no-such-anchor.json"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
