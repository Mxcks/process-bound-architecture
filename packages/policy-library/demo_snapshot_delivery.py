"""
Runnable demo of automatic snapshot delivery to a placed policy
(experiment 022).

    python3 packages/policy-library/demo_snapshot_delivery.py

Walks through availability test case 7 in order and prints what
happened: a delivery watcher checks a placed policy's registry
snapshot on a modeled clock (explicit ticks, no sleeps); an
unchanged check does nothing; after the author revokes, the
grown registry is copied over the snapshot file and re-anchored
by the same signer, and one watcher tick delivers the revocation
automatically - the placed policy stops serving with no manual
reload call. Then, in a fresh setup: the same growth delivered
WITHOUT the re-anchor is refused as anchor_stale on every check
until the lifecycle is followed, and a tampered delivery is
refused as tampered_record with the previous view still
serving. All state is in a temporary directory; the path is
printed at the end. Nothing is deployed.
"""

import importlib.util
import json
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
from snapshot_delivery import SnapshotDeliveryWatcher  # noqa: E402


def _load_registry_anchor_module():
    anchor_file = HERE.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location(
        "pba_snapshot_delivery_demo_registry_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_registry_anchor = _load_registry_anchor_module()

CODE = b"policy: allow if caller.tier == 'member'\n"
HOLDERS = ("holder-a", "holder-b", "holder-c")


class _Setup:
    """One fresh registered, anchored, placed policy (the setup
    every earlier demo builds once; here each scenario gets its
    own so a revocation in one cannot leak into the next)."""

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
        self.watcher = SnapshotDeliveryWatcher(self.placed)

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


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-snapshot-delivery-demo-"))

    # 1. Unchanged check: nothing happens. Changed + lifecycle
    # followed: one tick delivers, serving stops.
    s = _Setup(tmp / "scenario-1")
    print("Setup. Policy registered:", s.record.policy_hash[:16] + "...",
          "- anchored snapshot entries: 1")
    report = s.watcher.tick()
    print("1. Unchanged check:", report.status,
          "- entries:", report.before_count, "->", report.after_count)
    s.revoke_and_deliver()
    s.reanchor()
    report = s.watcher.tick()
    print("   Revoked + delivered + re-anchored, one tick:", report.status,
          "- entries:", report.before_count, "->", report.after_count,
          "- head moved:", report.before_head != report.after_head)
    try:
        s.placed.serve()
        print("   Placed policy still serves: True (UNEXPECTED)")
    except PolicyRevoked:
        print("   Placed policy refuses as revoked: True")

    # 2. Growth without the re-anchor: refused as anchor_stale,
    # re-reported on every check, delivered once it is followed.
    s = _Setup(tmp / "scenario-2")
    s.revoke_and_deliver()
    report = s.watcher.tick()
    print("2. Delivered without re-anchor:", report.status,
          "- reason:", report.reason,
          "- still serves:", s.placed.serve().code == CODE)
    report = s.watcher.tick()
    print("   Next check re-reports:", report.status, "-", report.reason)
    s.reanchor()
    report = s.watcher.tick()
    print("   After the same-signer re-anchor:", report.status)

    # 3. Tampered delivery: refused, previous view untouched.
    s = _Setup(tmp / "scenario-3")
    lines = s.snapshot.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[0])
    entry["name"] = "forged-name"
    s.snapshot.write_text(json.dumps(entry, sort_keys=True) + "\n",
                          encoding="utf-8")
    report = s.watcher.tick()
    print("3. Tampered delivery:", report.status, "- reason:", report.reason,
          "- previous view still serves:", s.placed.serve().code == CODE)

    print("Demo state left in:", tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
