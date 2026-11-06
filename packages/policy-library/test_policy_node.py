"""
Automated tests for the Gate 2 decentralized policy prototype.

Each test maps to a Gate 2 outcome in
docs/architecture/08-outcomes-that-unlock-the-next-build.md. Run with
either:

    python3 -m unittest discover -s packages/policy-library -v
    python3 packages/policy-library/test_policy_node.py

Registration (setup only) uses the Gate 1 package; serving never does.
Standard library only.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "registration"))

from policy_node import (  # noqa: E402
    NotServing,
    PolicyNode,
    PolicyNotFound,
    TamperedRecord,
)
from registration import RegistrationChannel, sign_library  # noqa: E402

ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"


class OfflineChannel:
    """Stand-in for the central registration channel after it goes offline.

    Any attempted contact raises. Tests hand nodes no channel at all;
    this double exists so a test can state the offline condition
    explicitly and prove the serving path never needed the original.
    """

    def __getattr__(self, name):
        raise RuntimeError(f"registration channel is offline (tried to use .{name})")


class Gate2DecentralizedPolicyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.tmp = tmp

        # --- Setup phase: the central channel exists, alice registers. ---
        self.central_dir = tmp / "central"
        self.registry_log = self.central_dir / "registry.jsonl"
        self.audit_log = self.central_dir / "audit.jsonl"
        channel = RegistrationChannel(self.registry_log, self.audit_log)
        secret = channel.enroll_user("alice")
        signature = sign_library(secret, "alice", "member-access", "1.0.0", ALICE_CODE)
        self.record, created = channel.register(
            author="alice",
            name="member-access",
            version="1.0.0",
            code=ALICE_CODE,
            signature=signature,
        )
        assert created

        # --- Distribution phase: a second node gets its own copies. ---
        # The node's copy of the registration record (registry snapshot)
        # and its own payload store. After this point the central
        # channel plays no part.
        self.node_dir = tmp / "node-b"
        self.node_dir.mkdir()
        self.snapshot = self.node_dir / "registry-snapshot.jsonl"
        shutil.copyfile(self.registry_log, self.snapshot)
        self.payloads = {self.record.policy_hash: ALICE_CODE}

    def tearDown(self):
        self._tmp.cleanup()

    def _take_central_offline(self):
        """Make the central registration point genuinely unavailable:
        its files are moved away and any later contact attempt raises."""
        offline_dir = self.tmp / "central-offline"
        shutil.move(str(self.central_dir), str(offline_dir))
        assert not self.registry_log.exists()
        return OfflineChannel()

    def _node(self, node_id="node-b", serve=True, snapshot=None, payloads=None):
        return PolicyNode(
            node_id=node_id,
            registry_snapshot_path=snapshot or self.snapshot,
            payloads=self.payloads if payloads is None else payloads,
            serve_policies=serve,
        )

    # Outcome 1: a registered policy is served from a second,
    # independent node while the central registration point is offline.
    def test_policy_served_from_second_node_with_channel_offline(self):
        self._take_central_offline()

        node = self._node()
        served = node.serve_policy(self.record.policy_hash)

        self.assertEqual(served.code, ALICE_CODE)
        self.assertEqual(served.policy_hash, self.record.policy_hash)
        self.assertEqual(served.author, "alice")
        self.assertEqual(served.name, "member-access")
        self.assertEqual(served.code_hash, self.record.code_hash)
        self.assertEqual(served.validation_channel, self.record.validation_channel)
        self.assertEqual(served.record_hash, self.record.record_hash)

    # Outcome 1, verification half: the node trusts the registration
    # record, not the channel - a tampered snapshot is refused, and a
    # payload that does not match its record is refused.
    def test_node_verifies_from_registration_record_not_channel(self):
        self._take_central_offline()

        # Unregistered / unknown policy hash: no record, no serve.
        node = self._node()
        with self.assertRaises(PolicyNotFound):
            node.serve_policy("ab" * 32)

        # Tampered payload under a real policy hash: hash != code_hash.
        bad_payload_node = self._node(
            node_id="node-bad-payload",
            payloads={self.record.policy_hash: b"policy: allow everything\n"},
        )
        with self.assertRaises(TamperedRecord):
            bad_payload_node.serve_policy(self.record.policy_hash)

        # Tampered registration record: editing the snapshot entry
        # breaks the hash chain, and the node refuses at construction.
        tampered = self.node_dir / "registry-tampered.jsonl"
        entry = json.loads(self.snapshot.read_text(encoding="utf-8").splitlines()[0])
        entry["name"] = "forged-name"
        tampered.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaises(TamperedRecord):
            self._node(node_id="node-tampered", snapshot=tampered)

    # Outcome 2: a user's own node serves only by their choice.
    # Both nodes below are plain local constructions standing in for a
    # user's computer, configured from the user's own config file.
    def test_user_node_serves_only_by_opt_in(self):
        def write_config(name, node_id, serve):
            cfg = self.tmp / name
            cfg.write_text(
                json.dumps({"node_id": node_id, "serve_policies": serve}),
                encoding="utf-8",
            )
            return cfg

        opted_in = PolicyNode.from_config(
            config_path=write_config("user-pc.json", "alice-pc", True),
            registry_snapshot_path=self.snapshot,
            payloads=self.payloads,
        )
        not_opted_in = PolicyNode.from_config(
            config_path=write_config("other-pc.json", "bob-pc", False),
            registry_snapshot_path=self.snapshot,
            payloads=self.payloads,
        )
        default_node = self._node(node_id="default-pc", serve=False)

        # The user's own node, by their choice, is a policy source.
        served = opted_in.serve_policy(self.record.policy_hash)
        self.assertEqual(served.code, ALICE_CODE)
        self.assertEqual(opted_in.node_id, "alice-pc")

        # A node that did not opt in holds the same verified policy and
        # still does not serve it.
        self.assertTrue(not_opted_in.holds_policy(self.record.policy_hash))
        with self.assertRaises(NotServing):
            not_opted_in.serve_policy(self.record.policy_hash)
        with self.assertRaises(NotServing):
            default_node.serve_policy(self.record.policy_hash)


if __name__ == "__main__":
    unittest.main(verbosity=2)
