"""
Automated tests for self-healing holder replacement (experiment 010).

Each test maps to tests/test-cases/policy-availability.md case 4
(self-healing holder replacement, written before this code). Run
with either:

    python3 -m unittest discover -s packages/policy-library -v
    python3 packages/policy-library/test_self_healing.py

Setup mirrors test_availability.py exactly: registration (setup
only) uses the Gate 1 package, placement uses Gate 4's place_policy,
and healing extends Gate 4's ReplicatedPolicy in replication.py
without changing Gate 4's behavior. Holders are in-process
PolicyNodes; "lost permanently" is modeled by lose_holder(), which
removes the holder outright - distinct from go_offline(), Gate 4's
temporary-liveness flag, which never changes the holder count and
never triggers healing. Standard library only.
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
from replication import (  # noqa: E402
    NodeSpec,
    PolicyUnavailable,
    place_policy,
)

ALICE_CODE = b"policy: allow if caller.tier == 'member'\n"


class SelfHealingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.tmp = tmp

        # --- Registration time: alice registers (Gate 1), and the
        # registry snapshot every holder will verify from is copied.
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

    def _place(self, candidates, floor):
        return place_policy(
            policy_hash=self.record.policy_hash,
            code=ALICE_CODE,
            registry_snapshot_path=self.snapshot,
            candidates=candidates,
            floor=floor,
        )

    def _place_three(self):
        return self._place(
            [
                NodeSpec("original-host", serve_policies=True),
                NodeSpec("holder-b", serve_policies=True),
                NodeSpec("holder-c", serve_policies=True),
            ],
            floor=3,
        )

    # Case 4, main outcome: a holder is lost permanently, the holder
    # count drops below the floor, the shortfall is detected, a
    # replacement is recruited from other opted-in nodes (skipping a
    # non-opted-in candidate and an existing holder offered again),
    # the policy is copied to the replacement, the floor is restored,
    # and the policy is still served - by the replacement itself once
    # it is the only holder left, which proves the copy is a real,
    # verified Gate 2 holder and not a name in a list.
    def test_healing_restores_floor_after_holder_is_lost(self):
        replicated = self._place_three()
        self.assertFalse(replicated.needs_healing())
        self.assertEqual(replicated.shortfall(), 0)

        # The original host is lost permanently: not a temporary
        # offline, the holder is gone and the count drops.
        replicated.lose_holder("original-host")
        self.assertEqual(replicated.holder_node_ids(), ("holder-b", "holder-c"))
        report = replicated.placement_report()
        self.assertEqual(report.holder_count, 2)
        self.assertFalse(report.floor_met)
        self.assertEqual(report.status, "below_floor")

        # Detection: the shortfall is a fact the object states.
        self.assertTrue(replicated.needs_healing())
        self.assertEqual(replicated.shortfall(), 1)

        # The policy still serves from the surviving holders while
        # it is below floor (below floor is a risk report, Gate 4's
        # rule, not a refusal).
        self.assertEqual(replicated.serve().code, ALICE_CODE)

        healing = replicated.heal(
            [
                NodeSpec("not-opted-in", serve_policies=False),
                NodeSpec("holder-b", serve_policies=True),  # already a holder
                NodeSpec("replacement-d", serve_policies=True),
                NodeSpec("spare-e", serve_policies=True),  # not needed
            ]
        )
        self.assertEqual(healing.holder_count_before, 2)
        self.assertEqual(healing.floor, 3)
        self.assertEqual(healing.recruited_node_ids, ("replacement-d",))
        self.assertEqual(healing.holder_count_after, 3)
        self.assertTrue(healing.floor_restored)
        self.assertEqual(healing.status, "healed")
        as_dict = healing.to_dict()
        self.assertEqual(as_dict["recruited_node_ids"], ["replacement-d"])
        self.assertEqual(as_dict["status"], "healed")

        self.assertFalse(replicated.needs_healing())
        report = replicated.placement_report()
        self.assertEqual(
            report.holder_node_ids, ("holder-b", "holder-c", "replacement-d")
        )
        self.assertTrue(report.floor_met)
        self.assertEqual(report.status, "available")

        # Served afterward - and when both survivors are lost too,
        # the replacement alone serves the verified bytes, proving
        # the copy it received is real.
        self.assertEqual(replicated.serve().code, ALICE_CODE)
        replicated.lose_holder("holder-b")
        replicated.lose_holder("holder-c")
        served = replicated.serve()
        self.assertEqual(served.code, ALICE_CODE)
        self.assertEqual(served.policy_hash, self.record.policy_hash)
        self.assertEqual(served.record_hash, self.record.record_hash)

    # Case 4, negative control (Gate 4 style): a holder is lost and
    # NO eligible replacement exists - the only candidates are not
    # opted in, already holders, or repeats of one another. Healing
    # must report below floor honestly, recruit nobody, and never
    # claim it healed. The remaining holders still serve while
    # online; losing them is reported as unservable, not hidden.
    def test_negative_control_no_eligible_replacement_reports_below_floor(self):
        replicated = self._place_three()
        replicated.lose_holder("holder-c")
        self.assertTrue(replicated.needs_healing())

        healing = replicated.heal(
            [
                NodeSpec("not-opted-in", serve_policies=False),
                NodeSpec("also-not-opted-in", serve_policies=False),
                NodeSpec("holder-b", serve_policies=True),  # already a holder
                NodeSpec("holder-b", serve_policies=True),  # duplicate offer
            ]
        )
        self.assertEqual(healing.recruited_node_ids, ())
        self.assertEqual(healing.holder_count_before, 2)
        self.assertEqual(healing.holder_count_after, 2)
        self.assertFalse(healing.floor_restored)
        self.assertEqual(healing.status, "below_floor")

        report = replicated.placement_report()
        self.assertEqual(report.holder_count, 2)
        self.assertFalse(report.floor_met)
        self.assertEqual(report.status, "below_floor")
        self.assertTrue(replicated.needs_healing())
        self.assertEqual(replicated.shortfall(), 1)

        # Below floor, not gone: the survivors serve while online.
        self.assertEqual(replicated.serve().code, ALICE_CODE)
        replicated.go_offline("original-host")
        replicated.go_offline("holder-b")
        with self.assertRaises(PolicyUnavailable):
            replicated.serve()
        self.assertFalse(replicated.availability_report().currently_servable)

    # Case 4, boundaries: healing at the floor is a no-op that
    # recruits nobody even when spares are offered (a temporary
    # go_offline is Gate 4 failover, not a loss, and does not change
    # this); and a double loss is healed by recruiting exactly the
    # shortfall, in candidate order, no more.
    def test_healing_noop_at_floor_and_recruits_exact_shortfall(self):
        replicated = self._place_three()

        # At floor: no-op, even with an eligible spare on offer.
        healing = replicated.heal([NodeSpec("spare-d", serve_policies=True)])
        self.assertEqual(healing.status, "at_floor")
        self.assertEqual(healing.recruited_node_ids, ())
        self.assertEqual(healing.holder_count_after, 3)
        self.assertTrue(healing.floor_restored)

        # Temporary offline is not a permanent loss: the holder
        # count is unchanged and healing is still a no-op.
        replicated.go_offline("holder-c")
        self.assertFalse(replicated.needs_healing())
        healing = replicated.heal([NodeSpec("spare-d", serve_policies=True)])
        self.assertEqual(healing.status, "at_floor")
        self.assertEqual(healing.recruited_node_ids, ())
        replicated.come_online("holder-c")

        # Two holders lost: shortfall is 2, exactly two recruited.
        replicated.lose_holder("original-host")
        replicated.lose_holder("holder-b")
        self.assertEqual(replicated.shortfall(), 2)
        healing = replicated.heal(
            [
                NodeSpec("replacement-d", serve_policies=True),
                NodeSpec("replacement-e", serve_policies=True),
                NodeSpec("replacement-f", serve_policies=True),  # not needed
            ]
        )
        self.assertEqual(
            healing.recruited_node_ids, ("replacement-d", "replacement-e")
        )
        self.assertEqual(healing.holder_count_before, 1)
        self.assertEqual(healing.holder_count_after, 3)
        self.assertEqual(healing.status, "healed")
        self.assertEqual(
            replicated.holder_node_ids(),
            ("holder-c", "replacement-d", "replacement-e"),
        )
        self.assertEqual(replicated.serve().code, ALICE_CODE)

        # Losing a node that is not (or no longer) a holder is an
        # error, not a silent no-op.
        with self.assertRaises(KeyError):
            replicated.lose_holder("original-host")


if __name__ == "__main__":
    unittest.main(verbosity=2)
