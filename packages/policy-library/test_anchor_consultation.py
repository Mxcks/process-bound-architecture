"""
Automated tests for anchor consultation in Gate 2 serving
(experiment 015).

Each test maps to tests/test-cases/registration.md case 8 (anchor
consultation in serving and sync, written before this code). Run
with either:

    python3 -m unittest discover -s packages/policy-library -v
    python3 packages/policy-library/test_anchor_consultation.py

Setup mirrors test_policy_node.py: a real Gate 1 channel registers
a real library, a second node holds a snapshot copy, and experiment
012's write_anchor anchors the genuine registry's head outside the
registry directory. The node under test is constructed WITH that
anchor path: loading the snapshot consults the anchor (experiment
012's check_registry_against_anchor, reused, not re-implemented).

The negative control is experiment 012's: a second channel's
registry, rewritten from genesis with different content,
internally consistent so Gate 2's own snapshot verification passes
on it. A node with NO anchor serves it exactly as before (that is
the pre-015 behavior, and this case claims it did not move); a
node WITH the genuine anchor must refuse it at load, named
anchor_mismatch.

Honest growth is asserted as the case states it, not hidden: one
more honest registration after anchoring makes the snapshot an
extension of the anchored chain (the anchored head is still the
prefix head), which is classified anchor_stale and refused until
re-anchored - at load, and again on reload (experiment 008's
snapshot path), where a refused reload leaves the node's previous
view serving untouched. Standard library only.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "registration"))

from anchor import write_anchor  # noqa: E402  (registration anchor, exp 012)
from policy_node import (  # noqa: E402
    AnchorMismatch,
    AnchorStale,
    PolicyNode,
    verify_registry_snapshot,
)
from registration import RegistrationChannel, sign_library  # noqa: E402

ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
BOB_CODE = b"policy: billing export for members\n"
MALLORY_CODE = b"policy: allow if caller.tier == 'attacker'\n"


class AnchorConsultationServingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.tmp = tmp

        # --- Setup: alice registers through the real channel. ---
        self.central_dir = tmp / "central"
        self.registry_log = self.central_dir / "registry.jsonl"
        channel = RegistrationChannel(
            self.registry_log, self.central_dir / "audit.jsonl"
        )
        self.channel = channel
        secret = channel.enroll_user("alice")
        sig = sign_library(secret, "alice", "member-access", "1.0.0", ALICE_CODE)
        self.record, _ = channel.register(
            author="alice", name="member-access", version="1.0.0",
            code=ALICE_CODE, signature=sig,
        )
        assert channel.verify_registry_chain()
        self.alice_secret = secret

        # --- Distribution: the node's own snapshot copy. ---
        self.node_dir = tmp / "node-b"
        self.node_dir.mkdir()
        self.snapshot = self.node_dir / "registry-snapshot.jsonl"
        shutil.copyfile(self.registry_log, self.snapshot)
        self.payloads = {self.record.policy_hash: ALICE_CODE}

        # --- Experiment 012: anchor the genuine head, outside. ---
        self.anchor_path = tmp / "outside" / "registry-anchor.json"
        write_anchor(self.registry_log, self.anchor_path)

    def tearDown(self):
        self._tmp.cleanup()

    def _anchored_node(self, snapshot=None, payloads=None):
        return PolicyNode(
            node_id="node-b",
            registry_snapshot_path=snapshot or self.snapshot,
            payloads=self.payloads if payloads is None else payloads,
            serve_policies=True,
            anchor_path=self.anchor_path,
        )

    def _rewrite_from_genesis(self):
        """Experiment 012's negative control, presented as a
        snapshot: a different channel's registry (different author,
        different code), internally consistent from genesis."""
        other = self.tmp / "rewritten-home"
        other_channel = RegistrationChannel(
            other / "registry.jsonl", other / "audit.jsonl"
        )
        secret = other_channel.enroll_user("mallory")
        sig = sign_library(secret, "mallory", "member-access", "1.0.0", MALLORY_CODE)
        record, _ = other_channel.register(
            author="mallory", name="member-access", version="1.0.0",
            code=MALLORY_CODE, signature=sig,
        )
        rewritten = self.node_dir / "registry-rewritten.jsonl"
        shutil.copyfile(other / "registry.jsonl", rewritten)
        return rewritten, record

    def _grow_honestly(self):
        """One more real registration on the genuine channel, then
        the node's snapshot is refreshed from the grown registry:
        the anchored entries are intact, one entry was appended."""
        bob_secret = self.channel.enroll_user("bob")
        sig_b = sign_library(bob_secret, "bob", "billing-export", "0.2.0", BOB_CODE)
        record_b, _ = self.channel.register(
            author="bob", name="billing-export", version="0.2.0",
            code=BOB_CODE, signature=sig_b,
        )
        assert self.channel.verify_registry_chain()
        shutil.copyfile(self.registry_log, self.snapshot)
        payloads = dict(self.payloads)
        payloads[record_b.policy_hash] = BOB_CODE
        return record_b, payloads

    # Case 8, main outcome: a genuine snapshot whose head matches
    # its anchor is consulted at load and serves exactly as before.
    def test_genuine_snapshot_consults_anchor_and_serves(self):
        node = self._anchored_node()
        self.assertEqual(node.anchor_status, "anchored")
        self.assertIsNotNone(node.anchor_check)
        self.assertTrue(node.anchor_check.ok)

        served = node.serve_policy(self.record.policy_hash)
        self.assertEqual(served.code, ALICE_CODE)
        self.assertEqual(served.record_hash, self.record.record_hash)
        self.assertEqual(served.author, "alice")

    # Case 8, negative control: the rewritten snapshot passes Gate
    # 2's own snapshot verification (that is what makes it the
    # uncovered gap), and the anchored node REFUSES it at load,
    # with the refusal named anchor_mismatch - it never serves.
    def test_rewritten_snapshot_refused_at_load_anchor_mismatch(self):
        rewritten, mallory_record = self._rewrite_from_genesis()
        # Internal verification alone says the rewrite is fine.
        entries = verify_registry_snapshot(rewritten)
        self.assertEqual(len(entries), 1)

        with self.assertRaises(AnchorMismatch) as ctx:
            self._anchored_node(
                snapshot=rewritten,
                payloads={mallory_record.policy_hash: MALLORY_CODE},
            )
        self.assertEqual(ctx.exception.status, "anchor_mismatch")
        self.assertFalse(ctx.exception.check.ok)
        self.assertTrue(ctx.exception.check.internal_verifies)
        self.assertFalse(ctx.exception.check.head_matches)

    # Case 8, no anchor: behavior is exactly the pre-015 behavior.
    # Absence is not an error - and, stated plainly, the rewritten
    # snapshot is served by an anchorless node exactly as it was
    # before this experiment, which is the hole 015 closes only
    # where an anchor is configured.
    def test_no_anchor_behavior_is_exactly_pre_015(self):
        node = PolicyNode(
            node_id="node-b",
            registry_snapshot_path=self.snapshot,
            payloads=self.payloads,
            serve_policies=True,
        )
        self.assertEqual(node.anchor_status, "no_anchor")
        self.assertIsNone(node.anchor_check)
        self.assertEqual(node.serve_policy(self.record.policy_hash).code, ALICE_CODE)

        rewritten, mallory_record = self._rewrite_from_genesis()
        anchorless = PolicyNode(
            node_id="node-c",
            registry_snapshot_path=rewritten,
            payloads={mallory_record.policy_hash: MALLORY_CODE},
            serve_policies=True,
        )
        self.assertEqual(anchorless.anchor_status, "no_anchor")
        served = anchorless.serve_policy(mallory_record.policy_hash)
        self.assertEqual(served.code, MALLORY_CODE)

    # Case 8, honest growth: the grown snapshot is an extension of
    # the anchored chain, NOT a rewrite. It is refused at load as
    # anchor_stale (never reported as a mismatch, never silently
    # served as anchored), and after re-anchoring the new head the
    # same snapshot loads and serves both policies.
    def test_honest_growth_is_anchor_stale_until_reanchored(self):
        record_b, payloads = self._grow_honestly()

        with self.assertRaises(AnchorStale) as ctx:
            self._anchored_node(payloads=payloads)
        self.assertEqual(ctx.exception.status, "anchor_stale")
        self.assertTrue(ctx.exception.check.internal_verifies)
        self.assertFalse(ctx.exception.check.ok)

        # The anchor lifecycle from experiment 012: anchor again
        # when the head legitimately moves. The anchor is written
        # from the channel's registry; the node's snapshot is a
        # copy of the same chain and verifies against it.
        write_anchor(self.registry_log, self.anchor_path)
        node = self._anchored_node(payloads=payloads)
        self.assertEqual(node.anchor_status, "anchored")
        self.assertEqual(node.serve_policy(self.record.policy_hash).code, ALICE_CODE)
        self.assertEqual(node.serve_policy(record_b.policy_hash).code, BOB_CODE)

    # Case 8, honest growth on the reload path (experiment 008):
    # a node serving the anchored snapshot refuses a reload to the
    # honestly grown snapshot as anchor_stale and keeps serving
    # its previous view untouched; after re-anchoring, the same
    # reload succeeds and the new policy serves.
    def test_reload_to_grown_snapshot_is_stale_refused_then_serves_after_reanchor(self):
        node = self._anchored_node()
        record_b, payloads = self._grow_honestly()

        with self.assertRaises(AnchorStale):
            node.reload_snapshot(self.snapshot)
        # Previous view untouched by the refused reload.
        self.assertEqual(node.anchor_status, "anchored")
        self.assertEqual(node.serve_policy(self.record.policy_hash).code, ALICE_CODE)

        write_anchor(self.registry_log, self.anchor_path)
        # The node's payload store is the operator's local store;
        # growing it is how the node would hold the new policy.
        node._payloads.update(payloads)
        count = node.reload_snapshot(self.snapshot)
        self.assertEqual(count, 2)
        self.assertEqual(node.anchor_status, "anchored")
        self.assertEqual(node.serve_policy(record_b.policy_hash).code, BOB_CODE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
