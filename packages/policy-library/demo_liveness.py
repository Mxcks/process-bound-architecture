"""
Runnable demo of liveness detection with an automatic healing
trigger (experiment 016).

    python3 packages/policy-library/demo_liveness.py

Walks through availability test case 5 in order and prints what
happened: holders beat into a shared liveness view on a modeled
clock (explicit ticks, no sleeps); a holder silent for fewer than
3 missed beats is silent-short - still a holder, no healing; on
the 3rd missed beat the watcher declares it lost and healing
triggers automatically, recruiting a replacement and restoring
the floor. Then: a brief silence that returns in time is never
healed over; a loss with no eligible replacement keeps reporting
below floor on later checks; and a lost holder that comes back
rejoins as a candidate only, never double-counted. All state is
in a temporary directory; the path is printed at the end.
Nothing is deployed.
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
from liveness import LivenessWatcher  # noqa: E402


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-liveness-demo-"))
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

    def place():
        return place_policy(
            policy_hash=record.policy_hash, code=code,
            registry_snapshot_path=snapshot,
            candidates=[
                NodeSpec("original-host", serve_policies=True),
                NodeSpec("holder-b", serve_policies=True),
                NodeSpec("holder-c", serve_policies=True),
            ],
            floor=3,
        )

    # 1. Silent-short, then lost: the heal fires on its own.
    replicated = place()
    watcher = LivenessWatcher(
        replicated, candidates=[NodeSpec("replacement-d", serve_policies=True)]
    )
    watcher.tick(beating=["original-host", "holder-b", "holder-c"])
    for _ in range(2):
        report = watcher.tick(beating=["holder-b", "holder-c"])
    print("1. Silent for 2 beats, state:", report.holder_states["original-host"],
          "- still a holder:", "original-host" in replicated.holder_node_ids(),
          "- healing triggered:", report.healing is not None)
    report = watcher.tick(beating=["holder-b", "holder-c"])
    print("   3rd missed beat, lost:", list(report.lost_node_ids),
          "- auto-healed:", report.status,
          "- recruited:", list(report.healing.recruited_node_ids),
          "- holders:", report.healing.holder_count_before, "->",
          report.healing.holder_count_after)
    print("   Policy still served after auto-healing:",
          replicated.serve().code == code)

    # 2. Brief silence, returns in time: never healed over.
    replicated = place()
    watcher = LivenessWatcher(
        replicated, candidates=[NodeSpec("replacement-d", serve_policies=True)]
    )
    for _ in range(2):
        report = watcher.tick(beating=["original-host", "holder-c"])
    print("2. Brief silence, state:", report.holder_states["holder-b"],
          "- healed over:", report.healing is not None)
    report = watcher.tick(beating=["original-host", "holder-b", "holder-c"])
    print("   Returned in time, state:", report.holder_states["holder-b"],
          "- holders unchanged:", replicated.holder_node_ids())

    # 3. Lost, no eligible replacement: keeps reporting below floor.
    replicated = place()
    watcher = LivenessWatcher(
        replicated, candidates=[NodeSpec("not-opted-in", serve_policies=False)]
    )
    for _ in range(3):
        report = watcher.tick(beating=["original-host", "holder-b"])
    print("3. Lost with no replacement, report:", report.status,
          "- holders:", report.holder_count_after, "of floor", report.floor)
    report = watcher.tick(beating=["original-host", "holder-b"])
    print("   Next check still reports:", report.status,
          "- survivors serve:", replicated.serve().code == code)

    # 4. The lost holder returns: candidate only, never double-counted.
    replicated = place()
    watcher = LivenessWatcher(
        replicated, candidates=[NodeSpec("replacement-d", serve_policies=True)]
    )
    for _ in range(3):
        watcher.tick(beating=["holder-b", "holder-c"])
    report = watcher.tick(
        beating=["original-host", "holder-b", "holder-c", "replacement-d"]
    )
    print("4. Lost holder returned as candidate:",
          list(report.returned_candidate_node_ids),
          "- is a holder again:", "original-host" in replicated.holder_node_ids(),
          "- holder count (never 4):", replicated.placement_report().holder_count)

    print("Demo state left in:", tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
