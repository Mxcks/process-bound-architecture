"""
Runnable demo of anchor consultation in Gate 2 serving
(experiment 015).

    python3 packages/policy-library/demo_anchor_consultation.py

Walks through registration test case 8 on the serving side, in
order, and prints what happened: a policy node built WITH an
anchor serves the genuine snapshot; the negative control - a
registry rewritten from genesis, internally consistent - is
refused at load as anchor_mismatch while a node built WITHOUT an
anchor serves it exactly as it did before this experiment
(stated, not hidden: consultation is opt-in); and an honestly
grown snapshot is refused as anchor_stale until the new head is
re-anchored, after which it serves. All state is in a temporary
directory; the path is printed at the end. Nothing is deployed.
"""

import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "registration"))

from anchor import write_anchor  # noqa: E402  (registration anchor, exp 012)
from policy_node import AnchorMismatch, AnchorStale, PolicyNode  # noqa: E402
from registration import RegistrationChannel, sign_library  # noqa: E402

CODE = b"policy: allow if caller.tier == 'member'\n"
EVIL = b"policy: allow if caller.tier == 'attacker'\n"


def _register(channel, user, name, code):
    secret = channel.enroll_user(user)
    sig = sign_library(secret, user, name, "1.0.0", code)
    record, _ = channel.register(
        author=user, name=name, version="1.0.0", code=code, signature=sig
    )
    return record


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-anchor-consult-demo-"))
    central = tmp / "central"
    registry_log = central / "registry.jsonl"
    channel = RegistrationChannel(registry_log, central / "audit.jsonl")
    record = _register(channel, "alice", "member-access", CODE)

    node_dir = tmp / "node-b"
    node_dir.mkdir()
    snapshot = node_dir / "registry-snapshot.jsonl"
    shutil.copyfile(registry_log, snapshot)
    anchor_path = tmp / "outside" / "registry-anchor.json"
    write_anchor(registry_log, anchor_path)
    payloads = {record.policy_hash: CODE}

    # 1. With an anchor: the genuine snapshot is consulted at load
    # and serves.
    node = PolicyNode(
        node_id="node-b", registry_snapshot_path=snapshot,
        payloads=payloads, serve_policies=True, anchor_path=anchor_path,
    )
    served = node.serve_policy(record.policy_hash)
    print("1. Anchored node, genuine snapshot: anchor_status =",
          node.anchor_status, "- served policy by:", served.author)

    # 2. Negative control: a full rewrite from genesis. The
    # anchored node refuses it at load; the anchorless node serves
    # it, exactly the pre-015 behavior.
    other = tmp / "rewritten-home"
    evil_channel = RegistrationChannel(other / "registry.jsonl", other / "audit.jsonl")
    evil_record = _register(evil_channel, "mallory", "member-access", EVIL)
    rewritten = node_dir / "registry-rewritten.jsonl"
    shutil.copyfile(other / "registry.jsonl", rewritten)
    evil_payloads = {evil_record.policy_hash: EVIL}
    try:
        PolicyNode(
            node_id="node-b", registry_snapshot_path=rewritten,
            payloads=evil_payloads, serve_policies=True,
            anchor_path=anchor_path,
        )
        print("2. Anchored node REFUSED the rewrite: False (unexpected)")
    except AnchorMismatch as exc:
        print("2. Anchored node refused the rewrite at load:",
              exc.status, "- check ok:", exc.check.ok,
              "- internal verifies:", exc.check.internal_verifies)
    anchorless = PolicyNode(
        node_id="node-c", registry_snapshot_path=rewritten,
        payloads=evil_payloads, serve_policies=True,
    )
    print("   Anchorless node serves the rewrite (pre-015 behavior):",
          anchorless.serve_policy(evil_record.policy_hash).author,
          "- anchor_status:", anchorless.anchor_status)

    # 3. Honest growth: refused as anchor_stale, serves after
    # re-anchoring the new head.
    _register(channel, "bob", "billing-export", b"policy: billing\n")
    shutil.copyfile(registry_log, snapshot)
    try:
        PolicyNode(
            node_id="node-b", registry_snapshot_path=snapshot,
            payloads=payloads, serve_policies=True, anchor_path=anchor_path,
        )
        print("3. Anchored node refused honest growth: False (unexpected)")
    except AnchorStale as exc:
        print("3. Anchored node refused honest growth at load:", exc.status)
    write_anchor(registry_log, anchor_path)
    node = PolicyNode(
        node_id="node-b", registry_snapshot_path=snapshot,
        payloads=payloads, serve_policies=True, anchor_path=anchor_path,
    )
    print("   After re-anchoring: anchor_status =", node.anchor_status,
          "- serves:", node.serve_policy(record.policy_hash).author)

    print("Demo state left in:", tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
