"""
Automated tests for revocation on the serving side
(tests/test-cases/registration.md, case 6, outcomes 2 and 4),
built in experiment 008 on top of the Gate 2 prototype.

- Outcome 2 (serving half): a PolicyNode whose snapshot contains the
  revocation entry stops serving the policy.
- Outcome 4: the stale window, named not hidden. A node holding only
  the pre-revocation snapshot keeps serving after the revocation
  happens elsewhere (the test asserts that it DOES, on purpose);
  once it receives the updated record (reload_snapshot, the same
  standalone verification as construction) it stops serving.

Registration revocation itself (author-only, chain entry, boundary,
audit log) is tested in packages/registration/test_revocation.py.

Run with either:

    python3 -m unittest discover -s packages/policy-library -v
    python3 packages/policy-library/test_revocation_serving.py

(The file is not named test_revocation.py on purpose: the
registration package has a test_revocation.py, and unittest
discovery imports test modules by bare module name, so two files
with the same name in different packages collide on sys.path.
Found by the experiment 008 regression run, recorded there.)

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
    PolicyNode,
    PolicyRevoked,
    TamperedRecord,
    verify_registry_snapshot,
)
from registration import (  # noqa: E402
    RegistrationChannel,
    sign_library,
    sign_revocation,
)

ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"


class RevocationServingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.tmp = tmp

        # --- Setup: alice registers through the central channel. ---
        self.central_dir = tmp / "central"
        self.registry_log = self.central_dir / "registry.jsonl"
        self.channel = RegistrationChannel(
            self.registry_log, self.central_dir / "audit.jsonl"
        )
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
        self.payloads = {self.policy_hash: ALICE_CODE}

        # --- The node's own pre-revocation snapshot copy. ---
        self.node_dir = tmp / "node-b"
        self.node_dir.mkdir()
        self.snapshot = self.node_dir / "registry-snapshot.jsonl"
        shutil.copyfile(self.registry_log, self.snapshot)

    def tearDown(self):
        self._tmp.cleanup()

    def _node(self, node_id="node-b", snapshot=None):
        return PolicyNode(
            node_id=node_id,
            registry_snapshot_path=snapshot or self.snapshot,
            payloads=self.payloads,
            serve_policies=True,
        )

    def _revoke_centrally(self):
        revocation, created = self.channel.revoke(
            author="alice",
            policy_hash=self.policy_hash,
            signature=sign_revocation(self.alice_secret, self.policy_hash),
        )
        assert created
        return revocation

    # Outcome 2 (serving half): a node whose snapshot contains the
    # revocation entry refuses the policy, even though the payload it
    # holds verifies exactly against the registration record.
    def test_node_with_updated_snapshot_stops_serving(self):
        self._revoke_centrally()
        # Distribution after revocation: the node receives the full,
        # updated record (registration entry + revocation entry).
        updated = self.node_dir / "registry-updated.jsonl"
        shutil.copyfile(self.registry_log, updated)

        entries = verify_registry_snapshot(updated)
        self.assertEqual([e["event"] for e in entries], ["registered", "revoked"])

        node = self._node(snapshot=updated)
        self.assertTrue(node.holds_policy(self.policy_hash))
        self.assertTrue(node.is_revoked(self.policy_hash))
        with self.assertRaises(PolicyRevoked):
            node.serve_policy(self.policy_hash)

    # Outcome 4: the stale window exists, and closes on sync.
    # Timeline, all asserted:
    #   1. Node is built over the pre-revocation snapshot and serves.
    #   2. Alice revokes centrally. The node's snapshot does not
    #      change (nobody delivered the new entry yet).
    #   3. STALE WINDOW, OPEN: the node still serves the revoked
    #      policy. This assertion is the honest part - it documents
    #      that revocation is not instantaneous everywhere.
    #   4. The updated record arrives (copied over the node's
    #      snapshot file, the way Gate 3 sync would deliver it) and
    #      the node reloads: re-verified, 2 entries.
    #   5. STALE WINDOW, CLOSED: the node now refuses the policy.
    def test_stale_node_serves_until_updated_record_arrives(self):
        node = self._node()
        served = node.serve_policy(self.policy_hash)
        self.assertEqual(served.code, ALICE_CODE)
        self.assertFalse(node.is_revoked(self.policy_hash))

        self._revoke_centrally()
        # The revocation is real in the central record...
        self.assertTrue(self.channel.is_revoked(self.policy_hash))
        self.assertTrue(self.channel.verify_registry_chain())
        # ...but this node's snapshot is still the 1-entry original.
        self.assertEqual(len(verify_registry_snapshot(self.snapshot)), 1)

        # STALE WINDOW (open): the node cannot know, and keeps serving.
        stale_served = node.serve_policy(self.policy_hash)
        self.assertEqual(stale_served.code, ALICE_CODE)
        self.assertEqual(stale_served.record_hash, self.record.record_hash)
        self.assertFalse(node.is_revoked(self.policy_hash))

        # Sync: the updated record is delivered over the node's own
        # snapshot file, and the node reloads from it.
        shutil.copyfile(self.registry_log, self.snapshot)
        loaded = node.reload_snapshot()
        self.assertEqual(loaded, 2)
        self.assertTrue(node.is_revoked(self.policy_hash))
        with self.assertRaises(PolicyRevoked):
            node.serve_policy(self.policy_hash)

    # Outcome 4, second delivery shape: the updated record arrives as
    # a new file and is loaded by path; and a tampered "update" is
    # refused by the same verification, leaving the node's previous
    # (stale) view untouched rather than bricking or corrupting it.
    def test_reload_by_path_and_tampered_reload_leaves_view_untouched(self):
        node = self._node(node_id="node-c")
        self.assertEqual(node.serve_policy(self.policy_hash).code, ALICE_CODE)

        self._revoke_centrally()
        delivered = self.tmp / "delivered-update.jsonl"
        shutil.copyfile(self.registry_log, delivered)

        # A tampered delivery fails verification; the node's view is
        # exactly what it was before the attempt (still stale, still
        # serving - the failure does not silently close the window
        # either; only a verified update does).
        tampered = self.tmp / "delivered-tampered.jsonl"
        lines = delivered.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[0])
        entry["name"] = "forged-name"
        tampered.write_text(
            json.dumps(entry, sort_keys=True) + "\n" + lines[1] + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(TamperedRecord):
            node.reload_snapshot(tampered)
        self.assertFalse(node.is_revoked(self.policy_hash))
        self.assertEqual(node.serve_policy(self.policy_hash).code, ALICE_CODE)

        # The honest delivery, by path, closes the window.
        loaded = node.reload_snapshot(delivered)
        self.assertEqual(loaded, 2)
        self.assertTrue(node.is_revoked(self.policy_hash))
        with self.assertRaises(PolicyRevoked):
            node.serve_policy(self.policy_hash)


if __name__ == "__main__":
    unittest.main(verbosity=2)
