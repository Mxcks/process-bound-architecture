"""
Automated tests for the composed liveness + snapshot-delivery
watchers over ONE placed policy
(tests/test-cases/policy-availability.md, case 8), built in
experiment 023 over experiments 016 and 022.

Experiments 016 and 022 each built a watcher over a placed
policy - liveness (automatic healing on a lost declaration) and
snapshot delivery (automatic reload on a snapshot change) - and
each proved itself against the placement separately. 022's
heal-after-delivery row used explicit lose_holder()/heal()
calls, not 016's automatic trigger, so the two watchers were
never run composed. This suite asserts the composed behavior
of composed_watchers.ComposedWatcher, a thin coordinator that
drives both existing watchers over the same ReplicatedPolicy
in one cycle, in a stated order:

- The cycle order is delivery BEFORE liveness, so any healing
  in the same cycle recruits over the newest record.
- The hard case in one window: a revocation (delivered over
  the snapshot file, anchor lifecycle followed) and a holder
  crossing the lost threshold land in the SAME composed cycle;
  the delivery is applied, the holder is declared lost, the
  automatic heal recruits over the UPDATED record, the recruit
  refuses the revoked policy, and a non-revoked policy over the
  same snapshot keeps serving.
- No double action: quiet composed cycles reload nothing and
  heal nothing (holder views keep their identity, counts
  stable); a delivery-only cycle delivers and does not heal;
  a loss-only cycle heals and does not deliver.
- Refusal composition: a delivery refused as anchor_stale (or
  tampered_record) in a cycle does not block that cycle's
  automatic heal for a genuinely lost holder; the heal
  recruits over the LAST GOOD record, the refused delivery is
  not forced through, and the composed report records both
  the refusal (with its reason) and the heal (with its result).

Run with either:

    python3 -m unittest discover -s packages/policy-library -v -p "test_composed_watchers.py"
    python3 packages/policy-library/test_composed_watchers.py

Setup mirrors test_snapshot_delivery.py exactly (registration
through Gate 1, a signed anchor, placements over one shared
snapshot file); the liveness side mirrors test_liveness.py
(modeled beats, no wall-clock sleeps). Signing secrets are
real Gate 1 enrollments issued at runtime; no secret is
committed. Standard library only.
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
from composed_watchers import ComposedWatcher  # noqa: E402


def _load_registry_anchor_module():
    """Experiment 012's registry anchor, loaded by file path
    under a private name (the loader discipline of 015/020/021)."""
    anchor_file = HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_composed_registry_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_registry_anchor = _load_registry_anchor_module()

ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
BOB_CODE = b"policy: allow if caller.tier == 'guest'\n"
HOLDERS = ("holder-a", "holder-b", "holder-c")
CANDIDATES = [
    NodeSpec("replacement-d", serve_policies=True),
    NodeSpec("spare-e", serve_policies=True),
]


class ComposedWatcherTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.tmp = tmp

        # --- Setup, exactly as test_snapshot_delivery.py: alice
        # and bob register through Gate 1; a signed anchor pins
        # the 2-entry chain; placements share one snapshot copy.
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
        self.anchor_path = tmp / "outside" / "registry-anchor.json"
        self._reanchor()
        self.placement_head = self.bob_record.record_hash
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

    def _composed(self, placed):
        return ComposedWatcher(placed, candidates=list(CANDIDATES))

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
        shutil.copyfile(self.registry_log, self.snapshot)

    @staticmethod
    def _view_ids(placed):
        return {
            node_id: id(node._entries)
            for node_id, node in placed._holders.items()
        }

    def _prime_silence(self, composed, silent_id, ticks=2):
        """Run `ticks` composed cycles with every holder except
        silent_id beating (silent_short territory, no actions)."""
        report = None
        for _ in range(ticks):
            report = composed.tick(
                beating=[n for n in HOLDERS if n != silent_id])
            self.assertEqual(report.delivery.status, "no_change")
            self.assertEqual(report.liveness.lost_node_ids, ())
            self.assertIsNone(report.liveness.healing)
        return report

    # Case 8, no double action: several composed cycles with no
    # snapshot change and everyone beating produce NO reloads
    # and NO heals - holder views are the same objects after the
    # cycles as before, counts are stable, neither watcher's log
    # holds an action event, and every composed report says so.
    def test_quiet_cycles_no_double_action(self):
        placed = self._place(self.alice_record, ALICE_CODE)
        composed = self._composed(placed)
        self.assertEqual(composed.order, ("delivery", "liveness"))
        views_before = self._view_ids(placed)

        for _ in range(3):
            report = composed.tick(beating=list(HOLDERS))
            self.assertEqual(report.order, ("delivery", "liveness"))
            self.assertEqual(report.delivery.status, "no_change")
            self.assertIsNone(report.delivery.reason)
            self.assertEqual(report.liveness.status, "no_action")
            self.assertEqual(report.liveness.lost_node_ids, ())
            self.assertIsNone(report.liveness.healing)
            self.assertEqual(report.holder_count_before, 3)
            self.assertEqual(report.holder_count_after, 3)

        self.assertEqual(self._view_ids(placed), views_before)
        self.assertEqual(placed.holder_node_ids(), HOLDERS)
        self.assertEqual(
            [e for e in composed.delivery.events
             if e["event"] == "snapshot_delivered"], [])
        self.assertEqual(
            [e for e in composed.liveness.events
             if e["event"] in ("holder_lost", "healing_triggered")], [])
        self.assertEqual(len(composed.reports), 3)
        self.assertEqual(
            [e["event"] for e in composed.events],
            ["composed_cycle"] * 3)
        self.assertEqual(placed.serve().code, ALICE_CODE)
        print("COMPOSED QUIET: 3 composed cycles, no reloads, "
              "no heals, holder views untouched")

    # Case 8, exactly one action (delivery only): a revocation
    # lands while every holder beats. The composed cycle
    # delivers it and does nothing else - no holder is lost, no
    # heal fires, the holder set is unchanged.
    def test_delivery_only_cycle_exactly_one_action(self):
        placed = self._place(self.alice_record, ALICE_CODE)
        composed = self._composed(placed)
        revocation = self._revoke_alice()
        self._deliver_file()
        self._reanchor()

        report = composed.tick(beating=list(HOLDERS))
        self.assertEqual(report.delivery.status, "delivered")
        self.assertEqual(report.delivery.before_count, 2)
        self.assertEqual(report.delivery.after_count, 3)
        self.assertEqual(report.delivery.after_head,
                         revocation.record_hash)
        self.assertEqual(report.delivery.loaded_entries, 3)
        self.assertEqual(report.liveness.lost_node_ids, ())
        self.assertIsNone(report.liveness.healing)
        self.assertEqual(report.liveness.status, "no_action")
        self.assertEqual(placed.holder_node_ids(), HOLDERS)
        with self.assertRaises(PolicyRevoked):
            placed.serve()

    # Case 8, exactly one action (loss only): no snapshot change;
    # a holder crosses the lost threshold. The composed cycle
    # that declares it lost heals automatically and delivers
    # nothing - delivery reports no_change on every cycle and
    # holder views are never rebuilt by a reload.
    def test_loss_only_cycle_exactly_one_action(self):
        placed = self._place(self.alice_record, ALICE_CODE)
        composed = self._composed(placed)
        views_before = self._view_ids(placed)
        self._prime_silence(composed, "holder-c", ticks=2)

        report = composed.tick(beating=["holder-a", "holder-b"])
        self.assertEqual(report.delivery.status, "no_change")
        self.assertEqual(report.liveness.lost_node_ids, ("holder-c",))
        self.assertIsNotNone(report.liveness.healing)
        self.assertEqual(report.liveness.healing.recruited_node_ids,
                         ("replacement-d",))
        self.assertEqual(report.liveness.healing.status, "healed")
        self.assertEqual(report.liveness.status, "healed")
        self.assertEqual(
            placed.holder_node_ids(),
            ("holder-a", "holder-b", "replacement-d"))
        # The surviving holders' views were never reloaded: the
        # only new view is the recruit's own construction.
        after = self._view_ids(placed)
        self.assertEqual(after["holder-a"], views_before["holder-a"])
        self.assertEqual(after["holder-b"], views_before["holder-b"])
        self.assertEqual(
            [e for e in composed.delivery.events
             if e["event"] == "snapshot_delivered"], [])
        self.assertEqual(placed.serve().code, ALICE_CODE)

    # Case 8, the hard case in one window: holder-c is two beats
    # into silence; THEN the revocation is delivered over the
    # snapshot file and re-anchored; in the next composed cycle
    # holder-c stays silent (crossing the threshold), so that
    # ONE cycle must deliver the revocation AND declare the loss
    # AND heal - in that order - recruiting replacement-d over
    # the UPDATED record. The recruit refuses the revoked
    # policy; bob's non-revoked policy over the same snapshot
    # keeps serving after its own delivery.
    def test_revocation_and_loss_same_window_heals_over_updated_record(self):
        alice_placed = self._place(self.alice_record, ALICE_CODE)
        bob_placed = self._place(self.bob_record, BOB_CODE)
        composed = self._composed(alice_placed)
        bob_watcher = SnapshotDeliveryWatcher(bob_placed)
        self._prime_silence(composed, "holder-c", ticks=2)

        revocation = self._revoke_alice()
        self._deliver_file()
        self._reanchor()
        # Still the placement-time view until the composed cycle.
        self.assertEqual(alice_placed.serve().code, ALICE_CODE)

        report = composed.tick(beating=["holder-a", "holder-b"])
        # One composed report names all of it, in order.
        self.assertEqual(report.order, ("delivery", "liveness"))
        self.assertEqual(report.delivery.status, "delivered")
        self.assertEqual(report.delivery.before_count, 2)
        self.assertEqual(report.delivery.after_count, 3)
        self.assertEqual(report.delivery.after_head,
                         revocation.record_hash)
        self.assertEqual(report.liveness.lost_node_ids, ("holder-c",))
        self.assertIsNotNone(report.liveness.healing)
        self.assertEqual(report.liveness.healing.recruited_node_ids,
                         ("replacement-d",))
        self.assertEqual(report.liveness.healing.holder_count_before, 2)
        self.assertEqual(report.liveness.healing.holder_count_after, 3)
        self.assertTrue(report.liveness.healing.floor_restored)
        self.assertEqual(report.holder_count_before, 3)
        self.assertEqual(report.holder_count_after, 3)
        cycle_events = [e for e in composed.events
                        if e["event"] == "composed_cycle"]
        self.assertEqual(cycle_events[-1]["delivery_status"], "delivered")
        self.assertEqual(cycle_events[-1]["lost_node_ids"], ["holder-c"])
        self.assertEqual(cycle_events[-1]["recruited_node_ids"],
                         ["replacement-d"])

        # The recruit was built over the UPDATED record: its own
        # view contains the revocation, and it alone refuses.
        recruit = alice_placed._holders["replacement-d"]
        self.assertTrue(recruit.is_revoked(self.alice_record.policy_hash))
        with self.assertRaises(PolicyRevoked):
            alice_placed.serve()
        alice_placed.lose_holder("holder-a")
        alice_placed.lose_holder("holder-b")
        with self.assertRaises(PolicyRevoked):
            alice_placed.serve()

        # The non-revoked policy over the same snapshot keeps
        # serving after its own delivery of the same change.
        bob_report = bob_watcher.tick()
        self.assertEqual(bob_report.status, "delivered")
        served = bob_placed.serve()
        self.assertEqual(served.code, BOB_CODE)
        self.assertEqual(served.author, "bob")
        self.assertEqual(served.record_hash, self.bob_record.record_hash)
        print("COMPOSED HARD CASE: one cycle delivered the "
              "revocation, declared holder-c lost, and healed "
              "over the updated record; the recruit refuses the "
              "revoked policy and bob's policy keeps serving")

    # Case 8, refusal composition (anchor_stale): the grown
    # snapshot is delivered WITHOUT the re-anchor in the same
    # window where holder-c crosses the lost threshold. The
    # composed cycle refuses the delivery as anchor_stale AND
    # heals - over the LAST GOOD record, so the recruit serves
    # the pre-revocation view honestly - and the composed
    # report records both. The heal does not force the refused
    # delivery through: the next cycle still refuses, until the
    # lifecycle is followed and a later cycle delivers.
    def test_anchor_stale_refusal_does_not_block_healing_over_last_good(self):
        placed = self._place(self.alice_record, ALICE_CODE)
        composed = self._composed(placed)
        self._prime_silence(composed, "holder-c", ticks=2)

        self._revoke_alice()
        self._deliver_file()
        # Deliberately NO re-anchor: honest growth, stale anchor.

        report = composed.tick(
            beating=["holder-a", "holder-b"])
        self.assertEqual(report.delivery.status, "delivery_refused")
        self.assertEqual(report.delivery.reason, "anchor_stale")
        self.assertEqual(report.delivery.after_count, 3)
        self.assertEqual(report.liveness.lost_node_ids, ("holder-c",))
        self.assertIsNotNone(report.liveness.healing)
        self.assertEqual(report.liveness.healing.recruited_node_ids,
                         ("replacement-d",))
        self.assertEqual(report.liveness.healing.status, "healed")
        cycle = composed.events[-1]
        self.assertEqual(cycle["delivery_status"], "delivery_refused")
        self.assertEqual(cycle["delivery_reason"], "anchor_stale")
        self.assertEqual(cycle["healing_status"], "healed")
        self.assertEqual(cycle["recruited_node_ids"], ["replacement-d"])

        # The recruit holds the LAST GOOD record: no revocation
        # in its view, and it serves the policy exactly as the
        # placement did before the refused delivery.
        recruit = placed._holders["replacement-d"]
        self.assertFalse(recruit.is_revoked(self.alice_record.policy_hash))
        served = placed.serve()
        self.assertEqual(served.code, ALICE_CODE)
        self.assertEqual(served.record_hash, self.alice_record.record_hash)

        # Not forced through: the next composed cycle (everyone
        # beating, including the recruit) refuses again, and the
        # placement path is back on the delivery snapshot.
        report = composed.tick(
            beating=["holder-a", "holder-b", "replacement-d"])
        self.assertEqual(report.delivery.status, "delivery_refused")
        self.assertEqual(report.delivery.reason, "anchor_stale")
        self.assertIsNone(report.liveness.healing)
        self.assertEqual(placed.serve().code, ALICE_CODE)

        # Once the lifecycle is followed, a later cycle delivers
        # to every holder, the recruit included.
        self._reanchor()
        report = composed.tick(
            beating=["holder-a", "holder-b", "replacement-d"])
        self.assertEqual(report.delivery.status, "delivered")
        with self.assertRaises(PolicyRevoked):
            placed.serve()
        print("COMPOSED REFUSAL (anchor_stale): the refusal and "
              "the heal over the last good record are both in "
              "the composed report; delivery lands only after "
              "the re-anchor")

    # Case 8, refusal composition (tampered): the snapshot file
    # is edited in place (021's tamper) in the same window where
    # holder-c crosses the lost threshold. Same composition:
    # refused as tampered_record, heal still fires over the
    # last good record, previous view keeps serving.
    def test_tampered_refusal_does_not_block_healing_over_last_good(self):
        placed = self._place(self.alice_record, ALICE_CODE)
        composed = self._composed(placed)
        self._prime_silence(composed, "holder-c", ticks=2)

        lines = self.snapshot.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[0])
        entry["name"] = "forged-name"
        self.snapshot.write_text(
            json.dumps(entry, sort_keys=True) + "\n" + lines[1] + "\n",
            encoding="utf-8")

        report = composed.tick(beating=["holder-a", "holder-b"])
        self.assertEqual(report.delivery.status, "delivery_refused")
        self.assertEqual(report.delivery.reason, "tampered_record")
        self.assertEqual(report.liveness.lost_node_ids, ("holder-c",))
        self.assertIsNotNone(report.liveness.healing)
        self.assertEqual(report.liveness.healing.recruited_node_ids,
                         ("replacement-d",))
        recruit = placed._holders["replacement-d"]
        self.assertFalse(recruit.is_revoked(self.alice_record.policy_hash))
        served = placed.serve()
        self.assertEqual(served.code, ALICE_CODE)
        self.assertEqual(served.record_hash, self.alice_record.record_hash)

        # The refusal keeps being reported; nothing was forced.
        report = composed.tick(
            beating=["holder-a", "holder-b", "replacement-d"])
        self.assertEqual(report.delivery.status, "delivery_refused")
        self.assertEqual(report.delivery.reason, "tampered_record")
        self.assertEqual(placed.serve().code, ALICE_CODE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
