"""
Runnable demo of the Gate 4 policy availability prototype.

    python3 packages/policy-library/demo_availability.py

Walks through the Gate 4 outcomes in order and prints what happened:
first the negative control (one holder, it goes offline, the policy
is gone), then a policy placed at registration time with a
replication floor of 3 surviving two of its three holders going
offline, then a placement whose floor cannot be met reporting its
shortfall honestly (2 holders vs a floor of 3, status below_floor).
All state is in a temporary directory; the path is printed at the
end. Nothing is deployed.
"""

import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "registration"))

from registration import RegistrationChannel, sign_library  # noqa: E402
from replication import NodeSpec, PolicyUnavailable, place_policy  # noqa: E402


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-gate4-demo-"))
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

    # 1. Negative control: exactly one holder, and it goes offline.
    solo = place([NodeSpec("only-host", serve_policies=True)], floor=1)
    print("1a. Sole holder serves while online:", solo.serve().code == code)
    solo.go_offline("only-host")
    try:
        solo.serve()
        print("1b. UNEXPECTED: sole-holder policy served after its host went offline")
        return 1
    except PolicyUnavailable:
        print("1b. Sole holder offline, policy unavailable (the honest failure): True")

    # 2. Floor of 3 at registration time: survives losing two holders.
    replicated = place(
        [
            NodeSpec("original-host", serve_policies=True),
            NodeSpec("holder-b", serve_policies=True),
            NodeSpec("holder-c", serve_policies=True),
        ],
        floor=3,
    )
    report = replicated.placement_report()
    print("2a. Placement: holders", report.holder_count, "of floor", report.floor,
          "- status:", report.status)
    replicated.go_offline("original-host")
    print("2b. Original host offline, another holder serves:",
          replicated.serve().code == code)
    replicated.go_offline("holder-b")
    print("2c. Two of three holders offline, the last one serves:",
          replicated.serve().code == code)

    # 3. Floor cannot be met: only 2 of the candidates opted in.
    short = place(
        [
            NodeSpec("holder-a", serve_policies=True),
            NodeSpec("not-opted-in", serve_policies=False),
            NodeSpec("holder-b", serve_policies=True),
        ],
        floor=3,
    )
    report = short.placement_report()
    print("3. Floor unmet, reported honestly: holders", report.holder_count,
          "vs floor", report.floor, "- status:", report.status)

    print("Demo state left in:", tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
