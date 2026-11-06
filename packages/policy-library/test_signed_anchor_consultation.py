"""
Automated tests for signed-anchor consultation in Gate 2
serving (experiment 019).

Each test maps to tests/test-cases/registration.md case 10
(signed anchors with a kept history, written before this code).
Run with either:

    python3 -m unittest discover -s packages/policy-library -v
    python3 packages/policy-library/test_signed_anchor_consultation.py

Setup mirrors test_anchor_consultation.py: a real Gate 1 channel
registers a real library and a second node holds a snapshot
copy, but the anchor outside the registry directory is written
SIGNED by alice (her enrollment secret, issued at runtime
inside the tests). A PolicyNode constructed with a signing
identity (anchor_signer + anchor_secret_hex) verifies the
anchor's signature at load, on top of experiment 015's head
consultation: a correctly signed anchor over the genuine
snapshot serves exactly as before; an anchor with a missing or
tampered signature is refused at load as anchor_signature_invalid
(a new AnchorSignatureInvalid refusal), never served, and never
reported as anchor_mismatch or anchor_stale. A node constructed
with an anchor but NO signing identity judges the anchor by its
head exactly as experiment 015 built - signing is opt-in.
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

from anchor import write_anchor  # noqa: E402  (registration anchor, exp 012)
from policy_node import (  # noqa: E402
    AnchorSignatureInvalid,
    PolicyNode,
)
from registration import RegistrationChannel, sign_library  # noqa: E402

ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"


class SignedAnchorConsultationServingTests(unittest.TestCase):
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

    def _tamper_anchor_file(self, mutate):
        data = json.loads(self.anchor_path.read_text(encoding="utf-8"))
        mutate(data)
        self.anchor_path.write_text(
            json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )

    # Case 10, Gate 2: a genuine snapshot with a correctly signed
    # anchor, consulted by a node configured with that signing
    # identity, is anchored and serves exactly as in case 8 -
    # with the signature now verified as part of the standing.
    def test_signed_anchor_with_identity_serves(self):
        node = self._node(
            anchor_signer="alice", anchor_secret_hex=self.alice_secret
        )
        self.assertEqual(node.anchor_status, "anchored")
        self.assertTrue(node.anchor_check.ok)
        self.assertTrue(node.anchor_check.signature_checked)
        self.assertTrue(node.anchor_check.signature_valid)
        served = node.serve_policy(self.record.policy_hash)
        self.assertEqual(served.code, ALICE_CODE)
        self.assertEqual(served.record_hash, self.record.record_hash)

    # Case 10, Gate 2: a tampered signature and a missing
    # signature are each refused at load as
    # anchor_signature_invalid, under that name - not a mismatch,
    # not stale - and nothing serves.
    def test_tampered_and_missing_signature_refused_at_load(self):
        def flip_signature(data):
            sig = data["signature"]
            data["signature"] = ("0" if sig[0] != "0" else "1") + sig[1:]

        self._tamper_anchor_file(flip_signature)
        with self.assertRaises(AnchorSignatureInvalid) as ctx:
            self._node(
                anchor_signer="alice", anchor_secret_hex=self.alice_secret
            )
        self.assertEqual(ctx.exception.status, "anchor_signature_invalid")
        self.assertTrue(ctx.exception.check.signature_checked)
        self.assertFalse(ctx.exception.check.signature_valid)
        self.assertNotEqual(ctx.exception.status, "anchor_mismatch")
        self.assertNotEqual(ctx.exception.status, "anchor_stale")

        # Missing: rewrite the anchor UNSIGNED, keep the node
        # configured with the signing identity.
        write_anchor(self.registry_log, self.anchor_path)
        with self.assertRaises(AnchorSignatureInvalid) as ctx:
            self._node(
                anchor_signer="alice", anchor_secret_hex=self.alice_secret
            )
        self.assertEqual(ctx.exception.status, "anchor_signature_invalid")

    # Case 10, Gate 2 backward compatibility: a node with the
    # anchor but NO signing identity configured judges the
    # signed anchor by its head exactly as experiment 015 built,
    # and serves; signing remains opt-in per node.
    def test_no_identity_configured_judges_signed_anchor_by_head(self):
        node = self._node()
        self.assertEqual(node.anchor_status, "anchored")
        self.assertFalse(node.anchor_check.signature_checked)
        self.assertEqual(node.serve_policy(self.record.policy_hash).code,
                         ALICE_CODE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
