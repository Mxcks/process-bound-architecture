"""
Automated tests for signer-rotation consultation in Gate 2
serving (experiment 038).

Each test maps to tests/test-cases/registration.md case 12
(anchor signer rotation, written before this code). Run with
either:

    python3 -m unittest discover -s packages/policy-library -v
    python3 packages/policy-library/test_signer_rotation_consultation.py

Setup mirrors test_witnessed_anchor_consultation.py: a real
Gate 1 channel registers real libraries, a node holds a
snapshot copy, and the anchor outside the registry directory
is written signed by alice, then ROTATED to bob (case 12's
rotation record: alice signs the handover, bob countersigns
accepting). A PolicyNode constructed with the lineage
secrets (anchor_signer_secrets, beside case 10's
anchor_signer pair, which names the lineage's root) verifies
the segmented history at load: the rotated anchor over the
grown snapshot serves exactly as in case 10, with the
rotation judgement recorded. An anchor whose post-rotation
entries are signed by the retired signer is refused at load
as anchor_signer_retired (a new AnchorSignerRetired
refusal), never served. A node with NO lineage configuration
judges exactly as experiments 019/029 built - rotation is
opt-in per node. Standard library only.
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

from anchor import (  # noqa: E402  (registration anchor, exp 012)
    make_anchor_rotation,
    write_anchor,
)
from policy_node import (  # noqa: E402
    AnchorSignatureInvalid,
    AnchorSignerRetired,
    PolicyNode,
)
from registration import RegistrationChannel, sign_library  # noqa: E402

ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
DAVE_CODE = b"policy: late addition\n"


class SignerRotationConsultationTests(unittest.TestCase):
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
        sig = sign_library(
            self.alice_secret, "alice", "member-access", "1.0.0",
            ALICE_CODE,
        )
        self.record, _ = channel.register(
            author="alice", name="member-access", version="1.0.0",
            code=ALICE_CODE, signature=sig,
        )
        assert channel.verify_registry_chain()
        self.anchor_before_bytes = None

        self.anchor_path = tmp / "outside" / "registry-anchor.json"
        write_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        self.anchor_before_bytes = self.anchor_path.read_bytes()

        # The registry grows and alice rotates the anchor to
        # bob over the grown head.
        dave_secret = channel.enroll_user("dave")
        sig_d = sign_library(
            dave_secret, "dave", "late-addition", "1.0.0", DAVE_CODE)
        self.record_d, _ = channel.register(
            author="dave", name="late-addition", version="1.0.0",
            code=DAVE_CODE, signature=sig_d,
        )
        assert channel.verify_registry_chain()

        self.node_dir = tmp / "node-b"
        self.node_dir.mkdir()
        self.snapshot = self.node_dir / "registry-snapshot.jsonl"
        shutil.copyfile(self.registry_log, self.snapshot)
        self.payloads = {
            self.record.policy_hash: ALICE_CODE,
            self.record_d.policy_hash: DAVE_CODE,
        }

        self.rotation = make_anchor_rotation(
            from_signer="alice", from_secret_hex=self.alice_secret,
            to_signer="bob", to_secret_hex=self.bob_secret,
            rotation_seq=0, effective_anchor_seq=1,
            effective_chain_head=self.record_d.record_hash,
            effective_entry_count=2,
        )
        write_anchor(
            self.registry_log, self.anchor_path,
            signer="bob", secret_hex=self.bob_secret,
            rotation=self.rotation,
            predecessor_secret_hex=self.alice_secret,
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

    def _lineage(self):
        return {"alice": self.alice_secret, "bob": self.bob_secret}

    # Case 12, Gate 2: a genuine grown snapshot with a
    # rotated anchor, consulted by a node configured with the
    # lineage root and the lineage secrets, is anchored and
    # serves - the rotation judgement recorded verified and
    # the successor named as the current signer.
    def test_rotated_anchor_with_lineage_serves(self):
        node = self._node(
            anchor_signer="alice",
            anchor_secret_hex=self.alice_secret,
            anchor_signer_secrets=self._lineage(),
        )
        self.assertEqual(node.anchor_status, "anchored")
        self.assertTrue(node.anchor_check.ok)
        self.assertTrue(node.anchor_check.rotation_checked)
        self.assertTrue(node.anchor_check.rotation_valid)
        self.assertEqual(node.anchor_check.current_signer, "bob")
        served = node.serve_policy(self.record.policy_hash)
        self.assertEqual(served.code, ALICE_CODE)
        served = node.serve_policy(self.record_d.policy_hash)
        self.assertEqual(served.code, DAVE_CODE)
        print("ROTATED CONSULTATION (Gate 2): rotated anchor "
              "anchored, both policies serve, the successor "
              "named current in the check")

    # Case 12, Gate 2: an anchor the retired signer extended
    # past the rotation point - built by honestly extending
    # the pre-rotation anchor as alice, then given the
    # rotation record it is judged against (the record a
    # delivery would carry) - is refused at load as
    # anchor_signer_retired, under that name and no other.
    def test_retired_signer_anchor_refused_at_load(self):
        retired_path = self.tmp / "outside" / "retired-anchor.json"
        retired_path.write_bytes(self.anchor_before_bytes)
        write_anchor(
            self.registry_log, retired_path,
            signer="alice", secret_hex=self.alice_secret,
        )
        data = json.loads(retired_path.read_text(encoding="utf-8"))
        data["rotations"] = [self.rotation]
        retired_path.write_text(
            json.dumps(data, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        with self.assertRaises(AnchorSignerRetired) as ctx:
            PolicyNode(
                node_id="node-b",
                registry_snapshot_path=self.snapshot,
                payloads=self.payloads,
                serve_policies=True,
                anchor_path=retired_path,
                anchor_signer="alice",
                anchor_secret_hex=self.alice_secret,
                anchor_signer_secrets=self._lineage(),
            )
        self.assertEqual(ctx.exception.status,
                         "anchor_signer_retired")
        self.assertEqual(ctx.exception.check.rotation_status,
                         "anchor_signer_retired")
        print("ROTATED CONSULTATION (Gate 2) refusal: the "
              "retired signer's anchor refused at load as "
              "anchor_signer_retired")

    # Case 12, Gate 2 backward compatibility: a node with no
    # lineage configuration judges the rotated anchor exactly
    # as experiment 019 judges a signed one - with no identity
    # it serves by head; with only the successor's identity it
    # cannot verify the segmented history and refuses as
    # anchor_signature_invalid, never silently.
    def test_no_lineage_configuration_judges_as_case_10(self):
        node = self._node()
        self.assertEqual(node.anchor_status, "anchored")
        served = node.serve_policy(self.record.policy_hash)
        self.assertEqual(served.code, ALICE_CODE)

        with self.assertRaises(AnchorSignatureInvalid):
            self._node(
                anchor_signer="bob",
                anchor_secret_hex=self.bob_secret,
            )
        print("ROTATED CONSULTATION (Gate 2) compatibility: "
              "no identity judges by head, as in case 10")


if __name__ == "__main__":
    unittest.main(verbosity=2)
