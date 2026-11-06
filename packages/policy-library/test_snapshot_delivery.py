"""
Automated tests for automatic snapshot delivery to a PLACED policy
(tests/test-cases/policy-availability.md, case 7), built in
experiment 022 over experiment 021's reload path.

Experiment 021 gave ReplicatedPolicy a reload_snapshot() and
stated its remaining fact in plain words: nothing calls it, so a
placed policy learns of a revocation only when someone reloads it
by hand. This suite asserts the automatic behavior:

- A delivery watcher in the style of experiment 016's liveness
  watcher (modeled tick counter, no wall-clock sleeps) checks, on
  each check, whether the registry snapshot a placed policy was
  built over has changed since the placed policy last loaded it.
- A clean change (snapshot verifies; with anchor consultation
  configured, the anchor lifecycle has been followed - the grown
  registry re-anchored by the SAME signer before the check) is
  delivered AUTOMATICALLY in that same check: the watcher calls
  reload_snapshot(), a revoked policy stops being served by the
  placed object, a non-revoked policy over the same snapshot
  keeps serving after its own delivery, and the delivery is
  recorded with before/after heads and entry counts.
- A change whose anchor lifecycle was NOT followed is refused in
  that check as anchor_stale, the placed policy keeps serving
  its previous view, and later checks keep re-reporting the
  refusal instead of going silent; once the lifecycle is
  followed, a later check delivers.
- A tampered delivery, and a rewritten delivery, are refused
  under their own names (tampered_record, anchor_mismatch), the
  previous view keeps serving, and the refusal re-reports.
- An unchanged snapshot produces no delivery and no reload:
  holder views are the same objects after the check as before.
- After an automatic delivery, a later heal recruits over the
  UPDATED record (case 6's stale-recruit window, closed on this
  path): the recruit refuses the revoked policy.

Run with either:

    python3 -m unittest discover -s packages/policy-library -v -p "test_snapshot_delivery.py"
    python3 packages/policy-library/test_snapshot_delivery.py

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

from policy_node import PolicyRevoked  # noqa: E402
from registration import (  # noqa: E402
    RegistrationChannel,
    sign_library,
    sign_revocation,
)
from replication import NodeSpec, place_policy  # noqa: E402
from snapshot_delivery import SnapshotDeliveryWatcher  # noqa: E402


def _load_registry_anchor_module():
    """Experiment 012's registry anchor, loaded by file path
    under a private name (the loader discipline of 015/020/021)."""
    anchor_file = HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_snapshot_delivery_registry_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_registry_anchor = _load_registry_anchor_module()

ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
BOB_CODE = b"policy: allow if caller.tier == 'guest'\n"
MALLORY_CODE = b"policy: allow if caller.tier == 'owner'\n"
HOLDERS = ("holder-a", "holder-b", "holder-c")


class SnapshotDeliveryTests(unittest.TestCase):
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
        # outside the registry directory, over the 2-entry chain.
        # The chain head at placement time is bob's entry. ---
        self.anchor_path = tmp / "outside" / "registry-anchor.json"
        _registry_anchor.write_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.alice_secret)
        self.placement_head = self.bob_record.record_hash

        # --- The placements' own shared snapshot copy. ---
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

    def _deliver_file(self):
        """The sync-layer delivery every earlier experiment makes
        by hand: the grown registry copied over the placements'
        shared snapshot file."""
        shutil.copyfile(self.registry_log, self.snapshot)

    @staticmethod
    def _view_ids(placed):
        return {
            node_id: id(node._entries)
            for node_id, node in placed._holders.items()
        }

    # Case 7, main outcome: an unchanged check does nothing (no
    # delivery, holder views not rebuilt); then the author
    # revokes, the grown registry is delivered over the snapshot
    # file, the anchor lifecycle is followed (same-signer
    # re-anchor), and ONE watcher tick delivers automatically -
    # nobody calls reload_snapshot() from the test. The revoked
    # placed policy stops serving; bob's placed policy, over the
    # same snapshot, is delivered by its own watcher and keeps
    # serving verified.
    def test_clean_change_delivered_automatically_revocation_takes_effect(self):
        alice_placed = self._place(self.alice_record, ALICE_CODE)
        bob_placed = self._place(self.bob_record, BOB_CODE)
        alice_watcher = SnapshotDeliveryWatcher(alice_placed)
        bob_watcher = SnapshotDeliveryWatcher(bob_placed)
        self.assertEqual(alice_placed.serve().code, ALICE_CODE)

        # Unchanged: no delivery, no reload churn.
        views_before = self._view_ids(alice_placed)
        report = alice_watcher.tick()
        self.assertEqual(report.status, "no_change")
        self.assertEqual(report.before_count, 2)
        self.assertEqual(report.after_count, 2)
        self.assertEqual(report.before_head, self.placement_head)
        self.assertEqual(report.after_head, self.placement_head)
        self.assertIsNone(report.loaded_entries)
        self.assertIsNone(report.reason)
        self.assertEqual(self._view_ids(alice_placed), views_before)
        self.assertEqual(
            [e for e in alice_watcher.events
             if e["event"] == "snapshot_delivered"], [])
        self.assertEqual(alice_placed.serve().code, ALICE_CODE)

        # Revocation lands centrally; the record is delivered
        # over the snapshot file and re-anchored by the same
        # signer - the delivery the watcher exists to notice.
        revocation = self._revoke_alice()
        self._deliver_file()
        self._reanchor()
        # Still the placement-time view until a check runs: the
        # watcher is driven by its modeled clock, not by magic.
        self.assertEqual(alice_placed.serve().code, ALICE_CODE)

        report = alice_watcher.tick()
        self.assertEqual(report.status, "delivered")
        self.assertEqual(report.before_count, 2)
        self.assertEqual(report.after_count, 3)
        self.assertEqual(report.before_head, self.placement_head)
        self.assertEqual(report.after_head, revocation.record_hash)
        self.assertEqual(report.loaded_entries, 3)
        self.assertIsNone(report.reason)
        delivered = [e for e in alice_watcher.events
                     if e["event"] == "snapshot_delivered"]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]["before_head"], self.placement_head)
        self.assertEqual(delivered[0]["after_head"], revocation.record_hash)
        self.assertEqual(delivered[0]["before_count"], 2)
        self.assertEqual(delivered[0]["after_count"], 3)
        # The reload really happened: holder views were rebuilt.
        self.assertNotEqual(self._view_ids(alice_placed), views_before)
        with self.assertRaises(PolicyRevoked):
            alice_placed.serve()
        self.assertEqual(alice_placed.holder_node_ids(), HOLDERS)

        # A later check over the now-current snapshot is quiet.
        report = alice_watcher.tick()
        self.assertEqual(report.status, "no_change")
        self.assertEqual(report.after_count, 3)

        # Bob's policy was not revoked: its own watcher delivers
        # the same change and it keeps serving, verified.
        report = bob_watcher.tick()
        self.assertEqual(report.status, "delivered")
        self.assertEqual(report.after_count, 3)
        served = bob_placed.serve()
        self.assertEqual(served.code, BOB_CODE)
        self.assertEqual(served.author, "bob")
        self.assertEqual(served.record_hash, self.bob_record.record_hash)
        print("AUTOMATIC DELIVERY: one watcher tick delivered the "
              "revocation; the placed policy raises PolicyRevoked "
              "and the non-revoked policy keeps serving")

    # Case 7, negative control 1: the snapshot grew honestly but
    # the anchor lifecycle was NOT followed (no re-anchor before
    # the check). The watcher refuses the delivery as
    # anchor_stale, the placed policy keeps serving its previous
    # view, and later checks KEEP re-reporting the refusal. Once
    # the lifecycle is followed, the next check delivers.
    def test_anchor_stale_delivery_refused_and_keeps_reporting(self):
        placed = self._place(self.alice_record, ALICE_CODE)
        watcher = SnapshotDeliveryWatcher(placed)
        self._revoke_alice()
        self._deliver_file()
        # Deliberately NO re-anchor: honest growth, stale anchor.

        report = watcher.tick()
        self.assertEqual(report.status, "delivery_refused")
        self.assertEqual(report.reason, "anchor_stale")
        self.assertEqual(report.before_count, 2)
        self.assertEqual(report.after_count, 3)
        self.assertIsNone(report.loaded_entries)
        self.assertEqual(placed.serve().code, ALICE_CODE)

        for _ in range(2):
            report = watcher.tick()
            self.assertEqual(report.status, "delivery_refused")
            self.assertEqual(report.reason, "anchor_stale")
            self.assertEqual(placed.serve().code, ALICE_CODE)
        refusals = [e for e in watcher.events
                    if e["event"] == "snapshot_delivery_refused"]
        self.assertEqual(len(refusals), 3)
        self.assertTrue(all(e["reason"] == "anchor_stale" for e in refusals))

        # The refusal is not sticky past its cause: follow the
        # lifecycle and the next check delivers the same change.
        self._reanchor()
        report = watcher.tick()
        self.assertEqual(report.status, "delivered")
        self.assertEqual(report.after_count, 3)
        with self.assertRaises(PolicyRevoked):
            placed.serve()

    # Case 7, negative control 2: the delivered snapshot is
    # tampered with (one entry edited in place, exactly the
    # tamper of experiment 021's refused reload). The watcher
    # notices the file changed, attempts the delivery, and
    # refuses it as tampered_record; the previous view keeps
    # serving and the refusal re-reports on the next check.
    def test_tampered_delivery_refused_and_keeps_reporting(self):
        placed = self._place(self.alice_record, ALICE_CODE)
        watcher = SnapshotDeliveryWatcher(placed)
        lines = self.snapshot.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[0])
        entry["name"] = "forged-name"
        self.snapshot.write_text(
            json.dumps(entry, sort_keys=True) + "\n" + lines[1] + "\n",
            encoding="utf-8")

        report = watcher.tick()
        self.assertEqual(report.status, "delivery_refused")
        self.assertEqual(report.reason, "tampered_record")
        served = placed.serve()
        self.assertEqual(served.code, ALICE_CODE)
        self.assertEqual(served.record_hash, self.alice_record.record_hash)

        report = watcher.tick()
        self.assertEqual(report.status, "delivery_refused")
        self.assertEqual(report.reason, "tampered_record")
        refusals = [e for e in watcher.events
                    if e["event"] == "snapshot_delivery_refused"]
        self.assertEqual(len(refusals), 2)

    # Case 7, negative control 3: the delivered snapshot is a
    # full rewrite from genesis (experiment 012's negative
    # control: internally consistent, different author and
    # code). With anchor consultation configured the delivery is
    # refused as anchor_mismatch and the genuine placed policy
    # keeps serving.
    def test_rewritten_delivery_refused_anchor_mismatch(self):
        placed = self._place(self.alice_record, ALICE_CODE)
        watcher = SnapshotDeliveryWatcher(placed)
        other = self.tmp / "rewritten-home"
        other_channel = RegistrationChannel(
            other / "registry.jsonl", other / "audit.jsonl")
        mallory_secret = other_channel.enroll_user("mallory")
        other_channel.register(
            author="mallory", name="member-access", version="1.0.0",
            code=MALLORY_CODE,
            signature=sign_library(mallory_secret, "mallory",
                                   "member-access", "1.0.0", MALLORY_CODE))
        shutil.copyfile(other / "registry.jsonl", self.snapshot)

        report = watcher.tick()
        self.assertEqual(report.status, "delivery_refused")
        self.assertEqual(report.reason, "anchor_mismatch")
        self.assertEqual(report.after_count, 1)
        self.assertEqual(placed.serve().code, ALICE_CODE)
        self.assertEqual(placed.serve().author, "alice")

    # Case 7, healing interaction: a holder is lost BEFORE the
    # delivery lands, so a shortfall is open while the change is
    # in flight (case 6's stale-recruit window). The automatic
    # delivery lands first; the heal that then runs recruits the
    # replacement over the UPDATED record - the recruit's own
    # view contains the revocation and refuses the policy, and
    # the placed policy refuses from every surviving holder.
    def test_heal_after_automatic_delivery_recruits_over_updated_record(self):
        placed = self._place(self.alice_record, ALICE_CODE)
        watcher = SnapshotDeliveryWatcher(placed)
        placed.lose_holder("holder-c")
        self.assertTrue(placed.needs_healing())
        self.assertEqual(placed.serve().code, ALICE_CODE)

        revocation = self._revoke_alice()
        self._deliver_file()
        self._reanchor()
        report = watcher.tick()
        self.assertEqual(report.status, "delivered")
        self.assertEqual(report.after_head, revocation.record_hash)
        with self.assertRaises(PolicyRevoked):
            placed.serve()

        healing = placed.heal(
            [NodeSpec("replacement-d", serve_policies=True)])
        self.assertEqual(healing.status, "healed")
        self.assertEqual(healing.recruited_node_ids, ("replacement-d",))
        recruit = placed._holders["replacement-d"]
        self.assertTrue(recruit.is_revoked(self.alice_record.policy_hash))

        # The recruit alone refuses: it was built over the
        # updated record, not over the stale placement-time one.
        placed.lose_holder("holder-a")
        placed.lose_holder("holder-b")
        with self.assertRaises(PolicyRevoked):
            placed.serve()
        print("AUTOMATIC DELIVERY + HEALING: the post-delivery "
              "recruit holds the updated record and refuses the "
              "revoked policy")


if __name__ == "__main__":
    unittest.main(verbosity=2)
