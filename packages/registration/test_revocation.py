"""
Automated tests for revocation (tests/test-cases/registration.md,
case 6), built in experiment 008 on top of the Gate 1 prototype.

Case 6 outcomes covered here, on the registration/boundary side:
1. The policy's author (only the author, authenticated by their
   channel enrollment) can revoke a registered policy.
2. After revocation the ExecutionBoundary refuses the policy and its
   validation channel is no longer honored.
3. The revocation is a NEW entry in the hash-chained record; the
   original registration entry is never deleted or rewritten, and
   the chain still verifies.
5. A non-author cannot revoke someone else's policy (rejected and
   audit-logged).

Outcome 4 (a node holding a stale pre-revocation snapshot) lives on
the serving side and is tested in
packages/policy-library/test_revocation_serving.py (named to avoid a
unittest-discovery module-name collision with this file).

Run with either:

    python3 -m unittest discover -s packages/registration -v
    python3 packages/registration/test_revocation.py

Standard library only.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from registration import (  # noqa: E402
    BlockedRevoked,
    BlockedUnregistered,
    ExecutionBoundary,
    RegistrationChannel,
    RevocationRejected,
    policy_hash_for,
    sign_library,
    sign_revocation,
)

ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
BOB_CODE = b"policy: deny if payload.size > 1000000\n"


class RevocationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.registry_log = tmp / "registry.jsonl"
        self.audit_log = tmp / "audit.jsonl"
        self.channel = RegistrationChannel(self.registry_log, self.audit_log)
        self.boundary = ExecutionBoundary(self.channel)
        # Alice registers her policy; keep her secret for revoking.
        self.alice_secret = self.channel.enroll_user("alice")
        signature = sign_library(
            self.alice_secret, "alice", "member-access", "1.0.0", ALICE_CODE
        )
        self.record, created = self.channel.register(
            author="alice",
            name="member-access",
            version="1.0.0",
            code=ALICE_CODE,
            signature=signature,
        )
        assert created
        self.policy_hash = self.record.policy_hash

    def tearDown(self):
        self._tmp.cleanup()

    def _registry_lines(self):
        return [
            line
            for line in self.registry_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _revocation_rejections(self, reason=None):
        events = [
            e
            for e in self.channel.read_audit_events()
            if e.get("event") == "revocation_rejected"
        ]
        if reason is not None:
            events = [e for e in events if e.get("reason") == reason]
        return events

    def _revoke_as_alice(self):
        return self.channel.revoke(
            author="alice",
            policy_hash=self.policy_hash,
            signature=sign_revocation(self.alice_secret, self.policy_hash),
        )

    # Case 6, outcome 1 + 2: the author can revoke; afterwards the
    # boundary refuses the policy and the validation channel is gone.
    def test_author_can_revoke_and_boundary_and_validation_refuse(self):
        # Before revocation everything stands.
        admitted = self.boundary.admit(policy_hash=self.policy_hash, code=ALICE_CODE)
        self.assertEqual(admitted.policy_hash, self.policy_hash)
        self.assertEqual(
            self.channel.validation_channel_for(self.policy_hash),
            self.record.validation_channel,
        )
        self.assertFalse(self.channel.is_revoked(self.policy_hash))

        revocation, created = self._revoke_as_alice()
        self.assertTrue(created)
        self.assertEqual(revocation.policy_hash, self.policy_hash)
        self.assertEqual(revocation.author, "alice")
        self.assertEqual(revocation.log_index, 1)  # after the registration entry
        self.assertTrue(self.channel.is_revoked(self.policy_hash))
        self.assertEqual(self.channel.get_revocation(self.policy_hash), revocation)

        # Boundary refuses, specifically as revoked (which is also a
        # BlockedUnregistered for callers that treat refusals alike),
        # even though the presented code matches the registered code.
        with self.assertRaises(BlockedRevoked):
            self.boundary.admit(policy_hash=self.policy_hash, code=ALICE_CODE)
        with self.assertRaises(BlockedUnregistered):
            self.boundary.admit(policy_hash=self.policy_hash, code=ALICE_CODE)

        # Validation channel is no longer honored.
        self.assertIsNone(self.channel.validation_channel_for(self.policy_hash))

        # The standing survives a reload from the log alone: a fresh
        # channel over the same files (no enrollment secrets) still
        # knows the policy is revoked and still refuses it.
        reloaded = RegistrationChannel(self.registry_log, self.audit_log)
        self.assertTrue(reloaded.is_revoked(self.policy_hash))
        self.assertIsNone(reloaded.validation_channel_for(self.policy_hash))
        with self.assertRaises(BlockedRevoked):
            ExecutionBoundary(reloaded).admit(
                policy_hash=self.policy_hash, code=ALICE_CODE
            )

        # An unrelated live policy is untouched by alice's revocation.
        bob_secret = self.channel.enroll_user("bob")
        bob_record, _ = self.channel.register(
            author="bob",
            name="payload-size-cap",
            version="1.0.0",
            code=BOB_CODE,
            signature=sign_library(bob_secret, "bob", "payload-size-cap", "1.0.0", BOB_CODE),
        )
        self.assertFalse(self.channel.is_revoked(bob_record.policy_hash))
        self.assertEqual(
            self.boundary.admit(policy_hash=bob_record.policy_hash, code=BOB_CODE),
            bob_record,
        )

    # Case 6, outcome 3: revocation is a new chain entry; the original
    # registration entry is byte-identical afterwards and the chain
    # still verifies.
    def test_revocation_is_new_chain_entry_original_untouched(self):
        lines_before = self._registry_lines()
        self.assertEqual(len(lines_before), 1)
        original_entry = json.loads(lines_before[0])
        self.assertEqual(original_entry["event"], "registered")

        revocation, created = self._revoke_as_alice()
        self.assertTrue(created)

        lines_after = self._registry_lines()
        self.assertEqual(len(lines_after), 2)
        # The original entry was not deleted or rewritten: same bytes.
        self.assertEqual(lines_after[0], lines_before[0])
        # The new entry is the revocation, chained to the original.
        rev_entry = json.loads(lines_after[1])
        self.assertEqual(rev_entry["event"], "revoked")
        self.assertEqual(rev_entry["policy_hash"], self.policy_hash)
        self.assertEqual(rev_entry["author"], "alice")
        self.assertEqual(rev_entry["revoked_by"], "alice")
        self.assertEqual(rev_entry["log_index"], 1)
        self.assertEqual(rev_entry["prev_hash"], original_entry["entry_hash"])
        self.assertEqual(rev_entry["entry_hash"], revocation.record_hash)

        # The chain verifies with the revocation entry in it, live and
        # reloaded, and the historical registration record is intact.
        self.assertTrue(self.channel.verify_registry_chain())
        reloaded = RegistrationChannel(self.registry_log, self.audit_log)
        self.assertTrue(reloaded.verify_registry_chain())
        self.assertEqual(reloaded.get_record(self.policy_hash), self.record)
        self.assertEqual(reloaded.get_revocation(self.policy_hash), revocation)

        # Revoking again is idempotent: same revocation record, no
        # third entry is written.
        again, created_again = self._revoke_as_alice()
        self.assertFalse(created_again)
        self.assertEqual(again, revocation)
        self.assertEqual(len(self._registry_lines()), 2)
        self.assertTrue(self.channel.verify_registry_chain())

    # Case 6, outcome 5: a non-author cannot revoke someone else's
    # policy. Bob is enrolled and signs correctly *as bob*; he is
    # rejected because he is not the author, and it is audit-logged.
    def test_non_author_cannot_revoke(self):
        bob_secret = self.channel.enroll_user("bob")
        with self.assertRaises(RevocationRejected):
            self.channel.revoke(
                author="bob",
                policy_hash=self.policy_hash,
                signature=sign_revocation(bob_secret, self.policy_hash),
            )
        rejected = self._revocation_rejections("not_author")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["author"], "bob")
        self.assertEqual(rejected[0]["policy_author"], "alice")
        self.assertEqual(rejected[0]["policy_hash"], self.policy_hash)

        # Bob forging alice's identity fares no better: the signature
        # is checked against alice's enrollment, not bob's.
        with self.assertRaises(RevocationRejected):
            self.channel.revoke(
                author="alice",
                policy_hash=self.policy_hash,
                signature=sign_revocation(bob_secret, self.policy_hash),
            )
        self.assertEqual(len(self._revocation_rejections("bad_signature")), 1)

        # Nothing changed: policy not revoked, no chain entry added,
        # boundary still admits, validation channel still honored.
        self.assertFalse(self.channel.is_revoked(self.policy_hash))
        self.assertEqual(len(self._registry_lines()), 1)
        self.assertEqual(
            self.boundary.admit(policy_hash=self.policy_hash, code=ALICE_CODE),
            self.record,
        )
        self.assertEqual(
            self.channel.validation_channel_for(self.policy_hash),
            self.record.validation_channel,
        )

    # Case 6, outcome 1's authentication half: the author's name alone
    # is not enough. A bad revocation signature, an unenrolled caller,
    # and revoking a policy that was never registered are all rejected
    # and audit-logged, and none of them writes a chain entry.
    def test_revocation_requires_valid_signature_and_real_policy(self):
        good = sign_revocation(self.alice_secret, self.policy_hash)
        bad = ("0" if good[0] != "0" else "1") + good[1:]
        with self.assertRaises(RevocationRejected):
            self.channel.revoke(
                author="alice", policy_hash=self.policy_hash, signature=bad
            )
        self.assertEqual(len(self._revocation_rejections("bad_signature")), 1)

        # A registration signature is not a revocation signature
        # (domain separation): replaying one is a bad signature.
        registration_signature = sign_library(
            self.alice_secret, "alice", "member-access", "1.0.0", ALICE_CODE
        )
        with self.assertRaises(RevocationRejected):
            self.channel.revoke(
                author="alice",
                policy_hash=self.policy_hash,
                signature=registration_signature,
            )
        self.assertEqual(len(self._revocation_rejections("bad_signature")), 2)

        with self.assertRaises(RevocationRejected):
            self.channel.revoke(
                author="mallory",  # never enrolled
                policy_hash=self.policy_hash,
                signature=good,
            )
        self.assertEqual(len(self._revocation_rejections("unknown_author")), 1)

        never_registered = policy_hash_for("alice", "ghost", "0.0.1", b"x")
        with self.assertRaises(RevocationRejected):
            self.channel.revoke(
                author="alice",
                policy_hash=never_registered,
                signature=sign_revocation(self.alice_secret, never_registered),
            )
        self.assertEqual(len(self._revocation_rejections("unknown_policy")), 1)

        self.assertFalse(self.channel.is_revoked(self.policy_hash))
        self.assertEqual(len(self._registry_lines()), 1)
        self.assertTrue(self.channel.verify_registry_chain())


if __name__ == "__main__":
    unittest.main(verbosity=2)
