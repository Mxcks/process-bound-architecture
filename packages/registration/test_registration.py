"""
Automated tests for the Gate 1 registration prototype.

Each test maps to a Gate 1 outcome in
docs/architecture/08-outcomes-that-unlock-the-next-build.md and to
tests/test-cases/registration.md. Run with either:

    python3 -m unittest discover -s packages/registration -v
    python3 packages/registration/test_registration.py

Standard library only.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from registration import (  # noqa: E402
    BlockedUnregistered,
    ExecutionBoundary,
    RegistrationChannel,
    RegistrationRejected,
    policy_hash_for,
    sign_library,
)

ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
BOB_CODE = b"policy: deny if payload.size > 1000000\n"


class Gate1RegistrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.registry_log = tmp / "registry.jsonl"
        self.audit_log = tmp / "audit.jsonl"
        self.channel = RegistrationChannel(self.registry_log, self.audit_log)
        self.boundary = ExecutionBoundary(self.channel)

    def tearDown(self):
        self._tmp.cleanup()

    def _enroll_and_sign(self, user, name, version, code):
        secret = self.channel.enroll_user(user)
        return sign_library(secret, user, name, version, code)

    # Outcome 1: authenticated channel exists before any use.
    def test_channel_exists_before_use(self):
        self.assertTrue(self.channel.is_ready())
        self.assertTrue(self.registry_log.exists())
        self.assertTrue(self.audit_log.exists())
        # An empty, freshly created chain verifies.
        self.assertTrue(self.channel.verify_registry_chain())
        # Nobody is enrolled yet, so registration work is refused by the
        # pre-existing channel, and that refusal is audit-logged.
        with self.assertRaises(RegistrationRejected):
            self.channel.register(
                author="mallory",
                name="sneaky",
                version="0.1.0",
                code=b"anything",
                signature="00" * 32,
            )
        reasons = [e.get("reason") for e in self.channel.read_audit_events()]
        self.assertIn("unknown_author", reasons)

    # Outcome 2 (+ test-case 3): any user can register their own library,
    # and the registered library becomes loadable at the boundary.
    def test_any_user_can_register_own_library(self):
        for user, name, code in (
            ("alice", "member-access", ALICE_CODE),
            ("bob", "payload-size-cap", BOB_CODE),
        ):
            signature = self._enroll_and_sign(user, name, "1.0.0", code)
            record, created = self.channel.register(
                author=user, name=name, version="1.0.0", code=code, signature=signature
            )
            self.assertTrue(created)
            self.assertEqual(record.author, user)
            self.assertEqual(record.policy_hash, policy_hash_for(user, name, "1.0.0", code))
            admitted = self.boundary.admit(policy_hash=record.policy_hash, code=code)
            self.assertEqual(admitted.policy_hash, record.policy_hash)

    # Outcome 3 (+ test-case 2): bad signature rejected AND logged.
    def test_bad_signature_rejected_and_logged(self):
        secret = self.channel.enroll_user("alice")
        good = sign_library(secret, "alice", "member-access", "1.0.0", ALICE_CODE)
        bad = ("0" if good[0] != "0" else "1") + good[1:]
        with self.assertRaises(RegistrationRejected):
            self.channel.register(
                author="alice",
                name="member-access",
                version="1.0.0",
                code=ALICE_CODE,
                signature=bad,
            )
        rejected = [
            e
            for e in self.channel.read_audit_events()
            if e.get("event") == "rejected" and e.get("reason") == "bad_signature"
        ]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["author"], "alice")
        # Nothing was registered as a side effect of the rejected attempt.
        ph = policy_hash_for("alice", "member-access", "1.0.0", ALICE_CODE)
        self.assertIsNone(self.channel.get_record(ph))

    # Outcome 4 (+ test-case 1): unregistered code blocked at the boundary.
    def test_unregistered_blocked_at_execution_boundary(self):
        ph = policy_hash_for("alice", "never-registered", "1.0.0", ALICE_CODE)
        with self.assertRaises(BlockedUnregistered):
            self.boundary.admit(policy_hash=ph, code=ALICE_CODE)

        # Even a real policy hash is blocked if the presented code differs
        # from the code that was registered under it.
        signature = self._enroll_and_sign("alice", "member-access", "1.0.0", ALICE_CODE)
        record, _ = self.channel.register(
            author="alice",
            name="member-access",
            version="1.0.0",
            code=ALICE_CODE,
            signature=signature,
        )
        with self.assertRaises(BlockedUnregistered):
            self.boundary.admit(policy_hash=record.policy_hash, code=b"tampered code")

    # Outcome 5 (+ test-case 4): one deterministic, tamper-evident record.
    def test_one_deterministic_tamper_evident_record(self):
        signature = self._enroll_and_sign("alice", "member-access", "1.0.0", ALICE_CODE)
        record1, created1 = self.channel.register(
            author="alice",
            name="member-access",
            version="1.0.0",
            code=ALICE_CODE,
            signature=signature,
        )
        record2, created2 = self.channel.register(
            author="alice",
            name="member-access",
            version="1.0.0",
            code=ALICE_CODE,
            signature=signature,
        )
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(record1, record2)

        lines = [
            line
            for line in self.registry_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(lines), 1)  # exactly one record was written
        self.assertTrue(self.channel.verify_registry_chain())

        # A fresh channel over the same log loads the same record and
        # verifies the same chain: verification works later, by anyone
        # holding the log, without the enrollment secrets.
        reloaded = RegistrationChannel(self.registry_log, self.audit_log)
        self.assertTrue(reloaded.verify_registry_chain())
        self.assertEqual(reloaded.get_record(record1.policy_hash), record1)

        # Tampering with the stored record is detected by the chain.
        entry = json.loads(lines[0])
        entry["name"] = "forged-name"
        self.registry_log.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
        self.assertFalse(self.channel.verify_registry_chain())

    # Outcome 6 (+ test-case 5): validation channel bound only on registration.
    def test_validation_channel_bound_only_on_registration(self):
        ph = policy_hash_for("alice", "member-access", "1.0.0", ALICE_CODE)
        # Before registration: no channel.
        self.assertIsNone(self.channel.validation_channel_for(ph))

        # After a rejected attempt: still no channel.
        secret = self.channel.enroll_user("alice")
        with self.assertRaises(RegistrationRejected):
            self.channel.register(
                author="alice",
                name="member-access",
                version="1.0.0",
                code=ALICE_CODE,
                signature="ff" * 32,
            )
        self.assertIsNone(self.channel.validation_channel_for(ph))

        # After a good registration: a channel is bound, and it is the
        # same deterministic value when read back from a reloaded log.
        signature = sign_library(secret, "alice", "member-access", "1.0.0", ALICE_CODE)
        record, _ = self.channel.register(
            author="alice",
            name="member-access",
            version="1.0.0",
            code=ALICE_CODE,
            signature=signature,
        )
        self.assertTrue(record.validation_channel.startswith("val-"))
        self.assertEqual(self.channel.validation_channel_for(ph), record.validation_channel)
        reloaded = RegistrationChannel(self.registry_log, self.audit_log)
        self.assertEqual(
            reloaded.validation_channel_for(ph), record.validation_channel
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
