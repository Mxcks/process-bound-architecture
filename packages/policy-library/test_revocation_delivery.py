"""
Automated tests for revocation delivery to a PLACED policy
(tests/test-cases/policy-availability.md, case 6), built in
experiment 021 over finding F1 of experiment 020.

Experiment 020 asserted the finding exactly: ReplicatedPolicy
exposed no reload/update method, and after the author revoked,
the grown registry was re-anchored by the same signer, and the
updated record was copied over the placement's own shared
snapshot file, the placed object STILL SERVED the revoked
policy - its holders were built once over the placement-time
snapshot and never reloaded.

This suite asserts the fixed behavior:

- ReplicatedPolicy.reload_snapshot() exists, in the same style
  as PolicyNode.reload_snapshot (experiments 008/015): it
  re-verifies the delivered snapshot and re-consults the anchor
  BEFORE any holder's view changes, then rebuilds every
  holder's view, and serve() on the placed policy raises
  PolicyRevoked.
- The anchor lifecycle is followed on the way (stage 8 of
  experiment 020): reloading the grown snapshot before
  re-anchoring is refused as AnchorStale and leaves the placed
  policy serving; after re-anchoring by the SAME signer, the
  reload stands (3 entries: two registrations, one revocation).
- A reload over a tampered snapshot raises TamperedRecord and
  leaves the previous serving state untouched.
- A policy that was NOT revoked keeps serving after the same
  reload.
- With NO reload called, the placed object still serves from
  its placement-time view. That remaining fact is asserted on
  purpose, not hidden: delivery here is an explicit reload
  call, not a push.

Run with either:

    python3 -m unittest discover -s packages/policy-library -v -p "test_revocation_delivery.py"
    python3 packages/policy-library/test_revocation_delivery.py

Registration (setup only) uses the Gate 1 package; placement
and serving never call the channel. Signing secrets are real
Gate 1 enrollments issued at runtime in the tests; no secret
is committed. Standard library only.
"""

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "registration"))

from policy_node import AnchorStale, PolicyRevoked, TamperedRecord  # noqa: E402
from registration import (  # noqa: E402
    RegistrationChannel,
    sign_library,
    sign_revocation,
)
from replication import NodeSpec, place_policy  # noqa: E402


def _load_registry_anchor_module():
    """Experiment 012's registry anchor, loaded by file path
    under a private name (the loader discipline of 015/020)."""
    anchor_file = HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_revocation_delivery_registry_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_registry_anchor = _load_registry_anchor_module()

ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
BOB_CODE = b"policy: allow if caller.tier == 'guest'\n"
HOLDERS = ("holder-a", "holder-b", "holder-c")


class RevocationDeliveryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.tmp = tmp

        # --- Setup: alice and bob each register through the
        # central channel (Gate 1), so the registry holds two
        # live policies over one shared snapshot. ---
        self.central_dir = tmp / "central"
        self.registry_log = self.central_dir / "registry.jsonl"
        self.channel = RegistrationChannel(
            self.registry_log, self.central_dir / "audit.jsonl")
        self.alice_secret = self.channel.enroll_user("alice")
        self.bob_secret = self.channel.enroll_user("bob")
        self.alice_record, created = self.channel.register(
            author="alice", name="member-access", version="1.0.0",
            code=ALICE_CODE,
            signature=sign_library(self.alice_secret, "alice",
                                   "member-access", "1.0.0", ALICE_CODE))
        assert created
        self.bob_record, created = self.channel.register(
            author="bob", name="guest-access", version="1.0.0",
            code=BOB_CODE,
            signature=sign_library(self.bob_secret, "bob",
                                   "guest-access", "1.0.0", BOB_CODE))
        assert created

        # --- The signed registry anchor (019), written by alice
        # outside the registry directory. ---
        self.anchor_path = tmp / "outside" / "registry-anchor.json"
        _registry_anchor.write_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret)

        # --- The placement's own shared snapshot copy. ---
        self.snapshot = tmp / "placement-snapshot.jsonl"
        shutil.copyfile(self.registry_log, self.snapshot)

    # -- helpers -----------------------------------------------------

    def _place(self, record, code):
        return place_policy(
            policy_hash=record.policy_hash,
            code=code,
            registry_snapshot_path=self.snapshot,
            candidates=[NodeSpec(n, serve_policies=True) for n in HOLDERS],
            floor=3,
            anchor_path=self.anchor_path,
            anchor_signer="alice",
            anchor_secret_hex=self.alice_secret,
        )

    def _revoke_alice(self):
        revocation, created = self.channel.revoke(
            author="alice",
            policy_hash=self.alice_record.policy_hash,
            signature=sign_revocation(self.alice_secret,
                                      self.alice_record.policy_hash))
        assert created
        return revocation

    def _reanchor(self):
        _registry_anchor.write_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret)

    # -- the fix -------------------------------------------------------

    def test_reload_delivers_revocation_to_every_holder(self):
        placed = self._place(self.alice_record, ALICE_CODE)
        self.assertTrue(hasattr(placed, "reload_snapshot"))
        self.assertEqual(placed.serve().code, ALICE_CODE)
        self.assertEqual(placed.holder_node_ids(), HOLDERS)

        self._revoke_alice()
        # Delivery over the placement's own snapshot file,
        # following the anchor lifecycle: before re-anchoring,
        # the honestly grown snapshot is refused as AnchorStale
        # and the placed policy's previous view still serves.
        shutil.copyfile(self.registry_log, self.snapshot)
        with self.assertRaises(AnchorStale):
            placed.reload_snapshot()
        self.assertEqual(placed.serve().code, ALICE_CODE)

        # After re-anchoring by the SAME signer, the reload
        # stands: 3 entries (alice + bob registrations, alice's
        # revocation), and every holder stops serving - serve()
        # walks holders in placement order and the refusal is
        # the revocation refusal, not unavailability.
        self._reanchor()
        loaded = placed.reload_snapshot()
        self.assertEqual(loaded, 3)
        self.assertEqual(placed.holder_node_ids(), HOLDERS)
        with self.assertRaises(PolicyRevoked):
            placed.serve()
        print("FIXED F1 placed-holders-reload: reload_snapshot "
              "delivered the revocation; serve() on the placed "
              "policy raises PolicyRevoked")

    def test_no_reload_keeps_placement_time_view(self):
        # The remaining fact, stated in the case and asserted
        # here on purpose: delivery is an explicit reload call.
        # Revocation + re-anchor + delivery over the snapshot
        # file, with NO reload called, leaves the placed object
        # serving from its placement-time view.
        placed = self._place(self.alice_record, ALICE_CODE)
        self._revoke_alice()
        self._reanchor()
        shutil.copyfile(self.registry_log, self.snapshot)
        self.assertEqual(placed.serve().code, ALICE_CODE)
        print("F1 REMAINING FACT: with no reload called, the "
              "placed object still serves from its "
              "placement-time view")

    def test_tampered_reload_refused_and_state_untouched(self):
        placed = self._place(self.alice_record, ALICE_CODE)
        self.assertEqual(placed.serve().code, ALICE_CODE)

        # A tampered delivery: one entry edited, so the chain no
        # longer verifies. The reload is refused and the placed
        # policy's previous serving state is untouched, exactly
        # as PolicyNode.reload_snapshot behaves (008/015).
        lines = self.snapshot.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[0])
        entry["name"] = "forged-name"
        tampered = self.tmp / "tampered-snapshot.jsonl"
        tampered.write_text(
            json.dumps(entry, sort_keys=True) + "\n" + lines[1] + "\n",
            encoding="utf-8")
        with self.assertRaises(TamperedRecord):
            placed.reload_snapshot(tampered)
        self.assertEqual(placed.serve().code, ALICE_CODE)
        self.assertEqual(placed.serve().record_hash,
                         self.alice_record.record_hash)

    def test_non_revoked_policy_keeps_serving_after_same_reload(self):
        alice_placed = self._place(self.alice_record, ALICE_CODE)
        bob_placed = self._place(self.bob_record, BOB_CODE)
        self._revoke_alice()
        self._reanchor()
        shutil.copyfile(self.registry_log, self.snapshot)

        self.assertEqual(alice_placed.reload_snapshot(), 3)
        self.assertEqual(bob_placed.reload_snapshot(), 3)
        with self.assertRaises(PolicyRevoked):
            alice_placed.serve()
        served = bob_placed.serve()
        self.assertEqual(served.code, BOB_CODE)
        self.assertEqual(served.author, "bob")
        self.assertEqual(served.record_hash, self.bob_record.record_hash)


if __name__ == "__main__":
    unittest.main(verbosity=2)
