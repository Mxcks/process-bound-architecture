"""
Automated tests for witnessed-anchor consultation in Gate 2
serving (experiment 029).

Each test maps to tests/test-cases/registration.md case 11
(witnessed anchors, written before this code). Run with
either:

    python3 -m unittest discover -s packages/policy-library -v
    python3 packages/policy-library/test_witnessed_anchor_consultation.py

Setup mirrors test_signed_anchor_consultation.py: a real
Gate 1 channel registers a real library and a second node
holds a snapshot copy, but the anchor outside the registry
directory is written SIGNED by alice and COUNTERSIGNED by
carol, a second enrolled identity (experiment 029). A
PolicyNode constructed with a witness identity
(anchor_witness + anchor_witness_secret_hex, on top of case
10's anchor_signer pair) verifies the countersignature at
load as part of the same consultation: a correctly
countersigned anchor over the genuine snapshot serves
exactly as before; an anchor signed by the signer alone, or
countersigned by a different identity than the configured
witness, is refused at load as anchor_witness_invalid (a new
AnchorWitnessInvalid refusal), never served, and never
reported as anchor_signature_invalid, anchor_mismatch, or
anchor_stale. A node constructed with NO witness judges the
anchor exactly as experiment 019 built - the witness is
opt-in per node. Standard library only.
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
    AnchorWitnessInvalid,
    PolicyNode,
)
from registration import RegistrationChannel, sign_library  # noqa: E402

ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"


class WitnessedAnchorConsultationServingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.tmp = tmp

        self.central_dir = tmp / "central"
        self.registry_log = self.central_dir / "registry.jsonl"
        channel = RegistrationChannel(
            self.registry_log, self.central_dir / "audit.jsonl"
        )
        self.channel = channel
        self.alice_secret = channel.enroll_user("alice")
        self.bob_secret = channel.enroll_user("bob")
        self.carol_secret = channel.enroll_user("carol")
        sig = sign_library(
            self.alice_secret, "alice", "member-access", "1.0.0", ALICE_CODE
        )
        self.record, _ = channel.register(
            author="alice", name="member-access", version="1.0.0",
            code=ALICE_CODE, signature=sig,
        )
        assert channel.verify_registry_chain()

        self.node_dir = tmp / "node-b"
        self.node_dir.mkdir()
        self.snapshot = self.node_dir / "registry-snapshot.jsonl"
        shutil.copyfile(self.registry_log, self.snapshot)
        self.payloads = {self.record.policy_hash: ALICE_CODE}

        self.anchor_path = tmp / "outside" / "registry-anchor.json"
        write_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
            witness="carol", witness_secret_hex=self.carol_secret,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _node(self, **kwargs):
        return PolicyNode(
            node_id="node-b",
            registry_snapshot_path=self.snapshot,
            payloads=self.payloads,
            serve_policies=True,
            anchor_path=self.anchor_path,
            **kwargs,
        )

    def _witnessed_node(self):
        return self._node(
            anchor_signer="alice", anchor_secret_hex=self.alice_secret,
            anchor_witness="carol",
            anchor_witness_secret_hex=self.carol_secret,
        )

    # Case 11, Gate 2: a genuine snapshot with a correctly
    # countersigned anchor, consulted by a node configured
    # with the signer AND the witness, is anchored and serves
    # exactly as in case 10 - with both judgements now
    # recorded verified in the check.
    def test_countersigned_anchor_with_witness_serves(self):
        node = self._witnessed_node()
        self.assertEqual(node.anchor_status, "anchored")
        self.assertTrue(node.anchor_check.ok)
        self.assertTrue(node.anchor_check.signature_valid)
        self.assertTrue(node.anchor_check.witness_checked)
        self.assertTrue(node.anchor_check.witness_valid)
        served = node.serve_policy(self.record.policy_hash)
        self.assertEqual(served.code, ALICE_CODE)
        self.assertEqual(served.record_hash, self.record.record_hash)
        print("WITNESSED CONSULTATION (Gate 2): countersigned "
              "anchor anchored, policy serves, both judgements "
              "recorded in the check")

    # Case 11, Gate 2: an anchor signed by the signer alone
    # (a case 10 anchor) and an anchor countersigned by a
    # different identity than the configured witness are each
    # refused at load as anchor_witness_invalid, under that
    # name - not signature-invalid, not a mismatch, not
    # stale - and nothing serves.
    def test_signer_alone_and_wrong_witness_refused_at_load(self):
        # Signer alone: rewrite the anchor as case 10 wrote it.
        write_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        with self.assertRaises(AnchorWitnessInvalid) as ctx:
            self._witnessed_node()
        self.assertEqual(ctx.exception.status, "anchor_witness_invalid")
        self.assertTrue(ctx.exception.check.signature_valid)
        self.assertTrue(ctx.exception.check.witness_checked)
        self.assertFalse(ctx.exception.check.witness_valid)
        self.assertNotEqual(ctx.exception.status,
                            "anchor_signature_invalid")
        self.assertNotEqual(ctx.exception.status, "anchor_mismatch")
        self.assertNotEqual(ctx.exception.status, "anchor_stale")

        # Wrong witness: bob countersigns alice's anchor.
        self.anchor_path.unlink()
        write_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
            witness="bob", witness_secret_hex=self.bob_secret,
        )
        with self.assertRaises(AnchorWitnessInvalid) as ctx:
            self._witnessed_node()
        self.assertEqual(ctx.exception.status, "anchor_witness_invalid")
        self.assertEqual(ctx.exception.check.witness, "bob")
        print("WITNESSED CONSULTATION (Gate 2) refusals: "
              "signer-alone and wrong-witness anchors refused "
              "at load as anchor_witness_invalid")

    # Case 11, Gate 2 backward compatibility: a node with the
    # anchor and a signing identity but NO witness judges the
    # countersigned anchor exactly as experiment 019 built,
    # and serves; the witness remains opt-in per node.
    def test_no_witness_configured_judges_as_019(self):
        node = self._node(
            anchor_signer="alice", anchor_secret_hex=self.alice_secret
        )
        self.assertEqual(node.anchor_status, "anchored")
        self.assertFalse(node.anchor_check.witness_checked)
        self.assertEqual(node.serve_policy(self.record.policy_hash).code,
                         ALICE_CODE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
