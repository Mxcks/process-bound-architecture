"""
Automated tests for the Gate 4 policy availability prototype.

Each test maps to a Gate 4 outcome in
docs/architecture/08-outcomes-that-unlock-the-next-build.md and to
tests/test-cases/policy-availability.md (cases 1 and 2; case 3,
deliberate removal with notification, is not built in this gate and
is not claimed here). Run with either:

    python3 -m unittest discover -s packages/policy-library -v
    python3 packages/policy-library/test_availability.py

Registration (setup only) uses the Gate 1 package; placement and
serving never contact it. Standard library only. Holders are
in-process PolicyNodes; "offline" is a flag on the ReplicatedPolicy,
which is stated honestly in replication.py's module docstring.
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


class Gate4AvailabilityTests(unittest.TestCase):
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

    # Outcome 1 (availability case 1, negative control): a policy
    # held by exactly one node is unavailable after that node goes
    # offline. This is the honest failure from layer 07, shown as a
    # test: before the host goes offline it serves; after, serve()
    # raises PolicyUnavailable and the live report says so plainly.
    def test_negative_control_sole_holder_offline_is_unavailable(self):
        replicated = self._place([NodeSpec("only-host", serve_policies=True)], floor=1)

        report = replicated.placement_report()
        self.assertEqual(report.holder_count, 1)
        self.assertTrue(report.floor_met)
        self.assertEqual(report.status, "available")

        served = replicated.serve()
        self.assertEqual(served.code, ALICE_CODE)

        replicated.go_offline("only-host")

        with self.assertRaises(PolicyUnavailable):
            replicated.serve()
        live = replicated.availability_report()
        self.assertEqual(live.online_count, 0)
        self.assertFalse(live.currently_servable)

    # Outcome 2 (availability case 2): with a replication floor of 3
    # set at registration time, the policy survives its hosts going
    # offline one by one: the original host drops and another holder
    # serves; a second holder drops and the last one still serves.
    def test_replicated_policy_survives_holders_going_offline(self):
        candidates = [
            NodeSpec("original-host", serve_policies=True),
            NodeSpec("holder-b", serve_policies=True),
            NodeSpec("holder-c", serve_policies=True),
        ]
        replicated = self._place(candidates, floor=3)

        report = replicated.placement_report()
        self.assertEqual(report.floor, 3)
        self.assertEqual(report.holder_count, 3)
        self.assertEqual(
            report.holder_node_ids, ("original-host", "holder-b", "holder-c")
        )
        self.assertTrue(report.floor_met)
        self.assertEqual(report.status, "available")

        # Served before anything goes offline, verified bytes.
        served = replicated.serve()
        self.assertEqual(served.code, ALICE_CODE)
        self.assertEqual(served.record_hash, self.record.record_hash)

        # Original host goes offline: still served, by another holder.
        replicated.go_offline("original-host")
        served = replicated.serve()
        self.assertEqual(served.code, ALICE_CODE)
        self.assertEqual(served.policy_hash, self.record.policy_hash)
        live = replicated.availability_report()
        self.assertEqual(live.online_count, 2)
        self.assertTrue(live.currently_servable)

        # Second of three holders goes offline: the last one serves.
        replicated.go_offline("holder-b")
        served = replicated.serve()
        self.assertEqual(served.code, ALICE_CODE)
        live = replicated.availability_report()
        self.assertEqual(live.online_holder_node_ids, ("holder-c",))
        self.assertTrue(live.currently_servable)

        # All three offline: unavailable, and the report says why.
        replicated.go_offline("holder-c")
        with self.assertRaises(PolicyUnavailable):
            replicated.serve()

    # Outcome 3: the floor is reported honestly when it cannot be
    # met. Floor is 3, but of four candidates only two opted in (and
    # one eligible id is offered twice): placement lands on the two
    # real holders and reports 2 vs 3, floor_met=False, status
    # below_floor - never "available".
    def test_floor_reported_honestly_when_it_cannot_be_met(self):
        candidates = [
            NodeSpec("holder-a", serve_policies=True),
            NodeSpec("holder-a", serve_policies=True),  # duplicate: not independent
            NodeSpec("not-opted-in", serve_policies=False),
            NodeSpec("also-not-opted-in", serve_policies=False),
            NodeSpec("holder-b", serve_policies=True),
        ]
        replicated = self._place(candidates, floor=3)

        report = replicated.placement_report()
        self.assertEqual(report.floor, 3)
        self.assertEqual(report.holder_count, 2)
        self.assertEqual(report.holder_node_ids, ("holder-a", "holder-b"))
        self.assertFalse(report.floor_met)
        self.assertEqual(report.status, "below_floor")
        as_dict = report.to_dict()
        self.assertEqual(as_dict["holder_count"], 2)
        self.assertEqual(as_dict["floor"], 3)
        self.assertEqual(as_dict["status"], "below_floor")

        # Below floor does not mean unplaced or unservable right now;
        # it means not guaranteed. The two holders do serve while
        # they are online, and losing both is reported, not hidden.
        self.assertEqual(replicated.serve().code, ALICE_CODE)
        replicated.go_offline("holder-a")
        replicated.go_offline("holder-b")
        with self.assertRaises(PolicyUnavailable):
            replicated.serve()
        self.assertFalse(replicated.availability_report().currently_servable)


if __name__ == "__main__":
    unittest.main(verbosity=2)
