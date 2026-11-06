"""
Automated tests for liveness detection with an automatic healing
trigger (experiment 016).

Each test maps to tests/test-cases/policy-availability.md case 5
(liveness detection with an automatic healing trigger, written
before this code). Run with either:

    python3 -m unittest discover -s packages/policy-library -v
    python3 packages/policy-library/test_liveness.py

Setup mirrors test_self_healing.py exactly: registration (setup
only) uses the Gate 1 package, placement uses Gate 4's
place_policy, and healing is experiment 010's heal() in
replication.py, reused by the watcher in liveness.py - never
duplicated. Time is deterministic: the liveness view advances in
explicit ticks the tests call, and a holder beats by having its id
passed to the watcher's tick(). No wall-clock sleeps anywhere.
Standard library only.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "registration"))

from registration import RegistrationChannel, sign_library  # noqa: E402
from replication import NodeSpec, place_policy  # noqa: E402
from liveness import (  # noqa: E402
    LOST_AFTER_MISSED_BEATS,
    LivenessWatcher,
)

ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"
ALL_THREE = ("original-host", "holder-b", "holder-c")


class LivenessTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.tmp = tmp

        central = tmp / "central"
        self.registry_log = central / "registry.jsonl"
        channel = RegistrationChannel(self.registry_log, central / "audit.jsonl")
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
        self.snapshot = tmp / "registry-snapshot.jsonl"
        shutil.copyfile(self.registry_log, self.snapshot)

    def tearDown(self):
        self._tmp.cleanup()

    def _place_three(self):
        return place_policy(
            policy_hash=self.record.policy_hash,
            code=ALICE_CODE,
            registry_snapshot_path=self.snapshot,
            candidates=[NodeSpec(n, serve_policies=True) for n in ALL_THREE],
            floor=3,
        )

    def _watcher(self, replicated, candidates):
        return LivenessWatcher(replicated, candidates=candidates)

    def _silent_ticks(self, watcher, silent_id, ticks, beating=ALL_THREE):
        """Advance `ticks` ticks with every holder except silent_id beating."""
        report = None
        for _ in range(ticks):
            report = watcher.tick(beating=[n for n in beating if n != silent_id])
        return report

    # Case 5, main outcome: a holder stops beating, stays
    # silent-short for threshold-1 ticks (no healing, still a
    # holder), then crosses the threshold on the next tick: the
    # watcher declares it lost, drops the count, and AUTOMATICALLY
    # triggers experiment 010's heal() - a replacement is recruited
    # under placement's eligibility rules, the floor is restored,
    # the policy still serves (and the replacement alone serves,
    # verified, once the survivors are gone), and the detection,
    # trigger, and outcome are all in the report / log.
    def test_lost_holder_triggers_automatic_healing_and_floor_restored(self):
        replicated = self._place_three()
        watcher = self._watcher(
            replicated,
            [
                NodeSpec("not-opted-in", serve_policies=False),
                NodeSpec("holder-b", serve_policies=True),  # already a holder
                NodeSpec("replacement-d", serve_policies=True),
                NodeSpec("spare-e", serve_policies=True),  # not needed
            ],
        )
        self.assertEqual(LOST_AFTER_MISSED_BEATS, 3)

        # Alive baseline: everyone beats, state alive, no action.
        report = watcher.tick(beating=list(ALL_THREE))
        self.assertEqual(report.status, "no_action")
        self.assertEqual(report.holder_states["original-host"], "alive")
        self.assertIsNone(report.healing)

        # Silent-short: threshold - 1 missed beats. Still a holder,
        # Gate 4 failover covers serving, NO healing triggered.
        report = self._silent_ticks(watcher, "original-host", LOST_AFTER_MISSED_BEATS - 1)
        self.assertEqual(report.holder_states["original-host"], "silent_short")
        self.assertEqual(report.silent_short_node_ids, ("original-host",))
        self.assertEqual(report.lost_node_ids, ())
        self.assertIsNone(report.healing)
        self.assertEqual(replicated.holder_node_ids(), ALL_THREE)
        self.assertFalse(replicated.needs_healing())
        self.assertEqual(replicated.serve().code, ALICE_CODE)

        # One more silent tick crosses the threshold: lost, and the
        # heal fires from the detection in the same check - nobody
        # called heal() or lose_holder() from the test.
        report = self._silent_ticks(watcher, "original-host", 1)
        self.assertEqual(report.lost_node_ids, ("original-host",))
        self.assertIsNotNone(report.healing)
        self.assertEqual(report.healing.recruited_node_ids, ("replacement-d",))
        self.assertEqual(report.healing.holder_count_before, 2)
        self.assertEqual(report.healing.holder_count_after, 3)
        self.assertTrue(report.healing.floor_restored)
        self.assertEqual(report.healing.status, "healed")
        self.assertEqual(report.status, "healed")
        self.assertTrue(report.floor_met)
        self.assertEqual(report.holder_count_after, 3)
        self.assertEqual(
            replicated.holder_node_ids(), ("holder-b", "holder-c", "replacement-d")
        )
        self.assertFalse(replicated.needs_healing())
        self.assertEqual(replicated.serve().code, ALICE_CODE)

        # The log records detection, trigger, and outcome as events.
        kinds = [e["event"] for e in watcher.events]
        self.assertIn("holder_lost", kinds)
        self.assertIn("healing_triggered", kinds)
        lost_event = next(e for e in watcher.events if e["event"] == "holder_lost")
        self.assertEqual(lost_event["node_id"], "original-host")
        self.assertEqual(lost_event["missed_beats"], LOST_AFTER_MISSED_BEATS)
        healed_event = next(
            e for e in watcher.events if e["event"] == "healing_triggered"
        )
        self.assertEqual(healed_event["status"], "healed")
        self.assertEqual(healed_event["recruited_node_ids"], ["replacement-d"])
        self.assertTrue(watcher.reports[-1].floor_met)

        # The recruited replacement is a real verified holder: it
        # alone serves after both survivors are lost (explicit
        # lose_holder here is the test proving the copy, as in 010).
        replicated.lose_holder("holder-b")
        replicated.lose_holder("holder-c")
        served = replicated.serve()
        self.assertEqual(served.code, ALICE_CODE)
        self.assertEqual(served.policy_hash, self.record.policy_hash)
        self.assertEqual(served.record_hash, self.record.record_hash)

    # Case 5, negative control 1: a holder goes silent briefly -
    # Gate 4's temporary-offline case, modeled with go_offline as
    # well as silence - and returns BEFORE the threshold. It is
    # never healed over and never loses its holder status, and when
    # it beats again it is simply alive, as the same holder.
    def test_silent_short_holder_returns_and_is_never_healed_over(self):
        replicated = self._place_three()
        watcher = self._watcher(
            replicated, [NodeSpec("replacement-d", serve_policies=True)]
        )
        replicated.go_offline("holder-c")

        report = self._silent_ticks(watcher, "holder-c", LOST_AFTER_MISSED_BEATS - 1)
        self.assertEqual(report.holder_states["holder-c"], "silent_short")
        self.assertEqual(report.status, "silent_short")
        self.assertIsNone(report.healing)
        self.assertEqual(replicated.holder_node_ids(), ALL_THREE)
        # Failover covers serving while it is offline (Gate 4).
        self.assertEqual(replicated.serve().code, ALICE_CODE)
        self.assertEqual(
            [e for e in watcher.events if e["event"] == "healing_triggered"], []
        )

        # It returns before the threshold: same holder, alive again.
        replicated.come_online("holder-c")
        report = watcher.tick(beating=list(ALL_THREE))
        self.assertEqual(report.holder_states["holder-c"], "alive")
        self.assertEqual(report.status, "no_action")
        self.assertIsNone(report.healing)
        self.assertEqual(replicated.holder_node_ids(), ALL_THREE)
        self.assertEqual(replicated.placement_report().status, "available")
        self.assertEqual(replicated.serve().code, ALICE_CODE)

    # Case 5, negative control 2: a holder is declared lost and NO
    # eligible replacement exists. The report says below floor
    # honestly, and later checks KEEP reporting the loss (a further
    # below_floor report and log event on each check) instead of
    # going silent. The survivors still serve while online.
    def test_lost_with_no_replacement_keeps_reporting_below_floor(self):
        replicated = self._place_three()
        watcher = self._watcher(
            replicated,
            [
                NodeSpec("not-opted-in", serve_policies=False),
                NodeSpec("holder-b", serve_policies=True),  # already a holder
                NodeSpec("holder-b", serve_policies=True),  # duplicate offer
            ],
        )
        report = self._silent_ticks(
            watcher, "holder-c", LOST_AFTER_MISSED_BEATS
        )
        self.assertEqual(report.lost_node_ids, ("holder-c",))
        self.assertIsNotNone(report.healing)
        self.assertEqual(report.healing.recruited_node_ids, ())
        self.assertEqual(report.healing.status, "below_floor")
        self.assertEqual(report.status, "below_floor")
        self.assertFalse(report.floor_met)
        self.assertEqual(report.holder_count_after, 2)
        self.assertEqual(replicated.placement_report().status, "below_floor")
        self.assertTrue(replicated.needs_healing())
        self.assertEqual(replicated.serve().code, ALICE_CODE)

        # Later checks keep reporting: not silent, not healed.
        for _ in range(2):
            report = watcher.tick(beating=["original-host", "holder-b"])
            self.assertEqual(report.status, "below_floor")
            self.assertFalse(report.floor_met)
            self.assertEqual(report.holder_count_after, 2)
            self.assertIsNotNone(report.healing)
            self.assertEqual(report.healing.status, "below_floor")
        below_events = [
            e for e in watcher.events if e["event"] == "below_floor"
        ]
        self.assertGreaterEqual(len(below_events), 3)
        self.assertEqual(replicated.holder_node_ids(), ("original-host", "holder-b"))

    # Case 5, returning-holder rule: a holder declared lost and
    # healed over later comes back beating. It rejoins as a
    # CANDIDATE only - not automatically as a holder, so the count
    # never double-counts (3, never 4) - and a later loss can
    # recruit it through the same automatic heal, as a candidate.
    def test_returned_lost_holder_rejoins_as_candidate_not_holder(self):
        replicated = self._place_three()
        watcher = self._watcher(
            replicated, [NodeSpec("replacement-d", serve_policies=True)]
        )
        report = self._silent_ticks(
            watcher, "original-host", LOST_AFTER_MISSED_BEATS
        )
        self.assertEqual(report.status, "healed")
        self.assertEqual(
            replicated.holder_node_ids(), ("holder-b", "holder-c", "replacement-d")
        )

        # The lost holder comes back, beating again.
        report = watcher.tick(
            beating=["original-host", "holder-b", "holder-c", "replacement-d"]
        )
        self.assertEqual(report.returned_candidate_node_ids, ("original-host",))
        self.assertIn("original-host", watcher.candidate_node_ids())
        self.assertNotIn("original-host", replicated.holder_node_ids())
        self.assertEqual(replicated.placement_report().holder_count, 3)
        self.assertEqual(report.holder_count_after, 3)
        self.assertEqual(replicated.serve().code, ALICE_CODE)
        returned = [
            e for e in watcher.events if e["event"] == "holder_returned_as_candidate"
        ]
        self.assertEqual(len(returned), 1)
        self.assertEqual(returned[0]["node_id"], "original-host")

        # A later loss recruits the returned holder, as a candidate,
        # through the same automatic trigger - count restored to
        # exactly the floor, never above it by double-counting.
        report = self._silent_ticks(
            watcher,
            "holder-b",
            LOST_AFTER_MISSED_BEATS,
            beating=("holder-c", "replacement-d", "original-host"),
        )
        self.assertEqual(report.lost_node_ids, ("holder-b",))
        self.assertEqual(report.status, "healed")
        self.assertEqual(report.healing.recruited_node_ids, ("original-host",))
        self.assertEqual(report.holder_count_after, 3)
        self.assertEqual(
            set(replicated.holder_node_ids()),
            {"holder-c", "replacement-d", "original-host"},
        )
        self.assertEqual(replicated.serve().code, ALICE_CODE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
