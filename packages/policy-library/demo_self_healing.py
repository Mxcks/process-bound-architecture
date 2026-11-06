"""
Runnable demo of self-healing holder replacement (experiment 010).

    python3 packages/policy-library/demo_self_healing.py

Walks through availability test case 4 in order and prints what
happened: a policy placed with a replication floor of 3 loses a
holder permanently and drops below floor; the shortfall is detected;
a replacement is recruited from the other opted-in candidates and
the floor is restored; the policy is then served by the replacement
alone. Then the negative control: another policy loses a holder and
no eligible replacement exists, so healing reports below_floor
honestly instead of pretending. All state is in a temporary
directory; the path is printed at the end. Nothing is deployed.
"""

import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "registration"))

from registration import RegistrationChannel, sign_library  # noqa: E402
from replication import NodeSpec, place_policy  # noqa: E402


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-self-healing-demo-"))
    code = b"policy: allow if caller.tier == 'member'\n"

    central = tmp / "central"
    channel = RegistrationChannel(central / "registry.jsonl", central / "audit.jsonl")
    secret = channel.enroll_user("alice")
    signature = sign_library(secret, "alice", "member-access", "1.0.0", code)
    record, _ = channel.register(
        author="alice", name="member-access", version="1.0.0",
        code=code, signature=signature,
    )
    snapshot = tmp / "registry-snapshot.jsonl"
    shutil.copyfile(central / "registry.jsonl", snapshot)
    print("Setup. Policy registered:", record.policy_hash[:16] + "...")

    def place(candidates, floor):
        return place_policy(
            policy_hash=record.policy_hash, code=code,
            registry_snapshot_path=snapshot, candidates=candidates, floor=floor,
        )

    three = [
        NodeSpec("original-host", serve_policies=True),
        NodeSpec("holder-b", serve_policies=True),
        NodeSpec("holder-c", serve_policies=True),
    ]

    # 1. A holder is lost permanently: the count drops below floor.
    replicated = place(three, floor=3)
    replicated.lose_holder("original-host")
    report = replicated.placement_report()
    print("1. Holder lost, below floor reported honestly:",
          report.holder_count, "of floor", report.floor, "-", report.status)
    print("   Shortfall detected (needs_healing / shortfall):",
          replicated.needs_healing(), "/", replicated.shortfall())

    # 2. Healing recruits a replacement and restores the floor.
    healing = replicated.heal(
        [
            NodeSpec("not-opted-in", serve_policies=False),
            NodeSpec("replacement-d", serve_policies=True),
        ]
    )
    print("2. Healing recruited:", list(healing.recruited_node_ids),
          "- holders", healing.holder_count_before, "->",
          healing.holder_count_after, "- status:", healing.status)
    print("   Policy still served after healing:",
          replicated.serve().code == code)

    # 3. The replacement's copy is real: it alone serves, verified.
    replicated.lose_holder("holder-b")
    replicated.lose_holder("holder-c")
    served = replicated.serve()
    print("3. Replacement alone serves the verified policy:",
          served.code == code and served.record_hash == record.record_hash)

    # 4. Negative control: no eligible replacement, no pretending.
    stuck = place(three, floor=3)
    stuck.lose_holder("holder-c")
    healing = stuck.heal(
        [
            NodeSpec("not-opted-in", serve_policies=False),
            NodeSpec("holder-b", serve_policies=True),  # already a holder
        ]
    )
    report = stuck.placement_report()
    print("4. No eligible replacement, healing reports:", healing.status,
          "- placement still:", report.holder_count, "of floor",
          report.floor, "-", report.status)
    print("   Survivors still serve while online:", stuck.serve().code == code)

    print("Demo state left in:", tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
