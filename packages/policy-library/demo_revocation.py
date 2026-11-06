"""
Runnable demo of revocation on the serving side (registration case
6, outcome 4: the stale window), experiment 008.

    python3 packages/policy-library/demo_revocation.py

A policy is registered and copied to a second node, which serves it.
Alice then revokes it centrally. The demo prints the stale window
honestly: the node, still holding only the pre-revocation record,
keeps serving the revoked policy. Then the updated record is
delivered (copied over the node's snapshot, the way Gate 3 sync
would deliver it), the node reloads, and it stops serving. All state
is in a temporary directory; the path is printed at the end.
"""

import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "registration"))

from policy_node import PolicyNode, PolicyRevoked  # noqa: E402
from registration import (  # noqa: E402
    RegistrationChannel,
    sign_library,
    sign_revocation,
)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-revocation-node-demo-"))
    code = b"policy: allow if caller.tier == 'member'\n"

    central = tmp / "central"
    channel = RegistrationChannel(central / "registry.jsonl", central / "audit.jsonl")
    alice_secret = channel.enroll_user("alice")
    record, _ = channel.register(
        author="alice", name="member-access", version="1.0.0",
        code=code,
        signature=sign_library(alice_secret, "alice", "member-access", "1.0.0", code),
    )
    node_dir = tmp / "node-b"
    node_dir.mkdir()
    snapshot = node_dir / "registry-snapshot.jsonl"
    shutil.copyfile(central / "registry.jsonl", snapshot)
    node = PolicyNode(
        node_id="node-b", registry_snapshot_path=snapshot,
        payloads={record.policy_hash: code}, serve_policies=True,
    )
    print("Setup. Node serves the policy:",
          node.serve_policy(record.policy_hash).code == code)

    channel.revoke(
        author="alice", policy_hash=record.policy_hash,
        signature=sign_revocation(alice_secret, record.policy_hash),
    )
    print("Alice revoked the policy centrally:",
          channel.is_revoked(record.policy_hash))

    # The stale window, shown not hidden: the node's snapshot has not
    # changed, so it cannot know - and it keeps serving.
    print("STALE WINDOW: node still serves the revoked policy (not yet synced):",
          node.serve_policy(record.policy_hash).code == code)

    shutil.copyfile(central / "registry.jsonl", snapshot)
    print("Updated record delivered; entries after reload:", node.reload_snapshot())
    try:
        node.serve_policy(record.policy_hash)
        print("UNEXPECTED: node served a revoked policy after sync")
        return 1
    except PolicyRevoked:
        print("STALE WINDOW CLOSED: node now refuses the revoked policy: True")
    print("Demo state left in:", tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
