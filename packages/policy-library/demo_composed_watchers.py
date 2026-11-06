"""
Runnable demo of the composed liveness + snapshot-delivery
watchers over one placed policy (experiment 023).

    python3 packages/policy-library/demo_composed_watchers.py

Walks through availability test case 8 in order and prints
what happened: one coordinator ticks a placed policy's
delivery watcher and liveness watcher in one cycle, delivery
before liveness. Quiet cycles do nothing. Then the hard case:
a revocation is delivered over the snapshot file and
re-anchored while a holder is two beats into silence, and the
next composed cycle delivers the revocation, declares the
holder lost, and heals over the updated record - the recruit
refuses the revoked policy. Then, in a fresh setup: the same
growth delivered WITHOUT the re-anchor in a loss window is
refused as anchor_stale, the heal still fires over the last
good record, and the delivery lands only after the re-anchor.
All state is in a temporary directory; the path is printed at
the end. Nothing is deployed.
"""

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "registration"))

from policy_node import PolicyRevoked  # noqa: E402
from registration import RegistrationChannel, sign_library, sign_revocation  # noqa: E402
from replication import NodeSpec, place_policy  # noqa: E402
from composed_watchers import ComposedWatcher  # noqa: E402


def _load_registry_anchor_module():
    anchor_file = HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_composed_demo_registry_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_registry_anchor = _load_registry_anchor_module()

CODE = b"policy: allow if caller.tier == 'member'\n"
HOLDERS = ("holder-a", "holder-b", "holder-c")
CANDIDATES = [NodeSpec("replacement-d", serve_policies=True)]


class _Setup:
    """One fresh registered, anchored, placed policy with a
    composed watcher over it (each scenario gets its own, so a
    revocation in one cannot leak into the next)."""

    def __init__(self, root: Path):
        central = root / "central"
        self.registry_log = central / "registry.jsonl"
        self.channel = RegistrationChannel(
            self.registry_log, central / "audit.jsonl")
        self.secret = self.channel.enroll_user("alice")
        self.record, _ = self.channel.register(
            author="alice", name="member-access", version="1.0.0",
            code=CODE,
            signature=sign_library(self.secret, "alice",
                                   "member-access", "1.0.0", CODE))
        self.anchor_path = root / "outside" / "registry-anchor.json"
        self.reanchor()
        self.snapshot = root / "placement-snapshot.jsonl"
        shutil.copyfile(self.registry_log, self.snapshot)
        self.placed = place_policy(
            policy_hash=self.record.policy_hash, code=CODE,
            registry_snapshot_path=self.snapshot,
            candidates=[NodeSpec(n, serve_policies=True) for n in HOLDERS],
            floor=3,
            anchor_path=self.anchor_path, anchor_signer="alice",
            anchor_secret_hex=self.secret)
        self.composed = ComposedWatcher(self.placed, candidates=list(CANDIDATES))

    def reanchor(self):
        _registry_anchor.write_anchor(
            self.registry_log, self.anchor_path,
            signer="alice", secret_hex=self.secret)

    def revoke_and_deliver(self):
        self.channel.revoke(
            author="alice", policy_hash=self.record.policy_hash,
            signature=sign_revocation(self.secret,
                                      self.record.policy_hash))
        shutil.copyfile(self.registry_log, self.snapshot)

    def silent_cycles(self, silent_id, ticks):
        report = None
        for _ in range(ticks):
            report = self.composed.tick(
                beating=[n for n in HOLDERS if n != silent_id])
        return report


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-composed-watchers-demo-"))

    # 1. Quiet composed cycles: no reloads, no heals.
    s = _Setup(tmp / "scenario-1")
    print("Setup. Policy registered:", s.record.policy_hash[:16] + "...",
          "- cycle order:", list(s.composed.order))
    for _ in range(2):
        report = s.composed.tick(beating=list(HOLDERS))
    print("1. Two quiet composed cycles:", report.delivery.status, "/",
          report.liveness.status, "- holders:",
          report.holder_count_before, "->", report.holder_count_after)

    # 2. The hard case in one window: holder-c two beats silent,
    # then revocation delivered + re-anchored; one composed
    # cycle delivers, declares the loss, and heals over the
    # updated record.
    s.silent_cycles("holder-c", 2)
    s.revoke_and_deliver()
    s.reanchor()
    report = s.composed.tick(beating=["holder-a", "holder-b"])
    print("2. Revoked + loss in one window, one composed cycle:",
          report.delivery.status, "- lost:",
          list(report.liveness.lost_node_ids), "- healed:",
          report.liveness.healing.status, "- recruited:",
          list(report.liveness.healing.recruited_node_ids))
    recruit = s.placed._holders["replacement-d"]
    print("   Recruit holds the updated record (is_revoked):",
          recruit.is_revoked(s.record.policy_hash))
    try:
        s.placed.serve()
        print("   Placed policy still serves: True (UNEXPECTED)")
    except PolicyRevoked:
        print("   Placed policy refuses as revoked: True")

    # 3. Refusal composition: growth without the re-anchor in a
    # loss window - refused as anchor_stale, heal still fires
    # over the last good record, delivery lands after re-anchor.
    s = _Setup(tmp / "scenario-3")
    s.silent_cycles("holder-c", 2)
    s.revoke_and_deliver()
    report = s.composed.tick(beating=["holder-a", "holder-b"])
    print("3. Delivered without re-anchor + loss, one cycle:",
          report.delivery.status, "-", report.delivery.reason,
          "- healed:", report.liveness.healing.status,
          "- recruited:", list(report.liveness.healing.recruited_node_ids))
    recruit = s.placed._holders["replacement-d"]
    print("   Recruit holds the last good record (is_revoked):",
          recruit.is_revoked(s.record.policy_hash),
          "- previous view still serves:", s.placed.serve().code == CODE)
    s.reanchor()
    report = s.composed.tick(
        beating=["holder-a", "holder-b", "replacement-d"])
    print("   After the same-signer re-anchor:", report.delivery.status)

    print("Demo state left in:", tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
