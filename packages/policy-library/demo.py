"""
Runnable demo of the Gate 2 decentralized policy prototype.

    python3 packages/policy-library/demo.py

Walks through both Gate 2 outcomes in order and prints what happened:
a policy is registered centrally, copied to a second node, the central
channel is taken offline (its files are moved away), and the second
node still serves the policy after verifying the registration record
on its own. A second, non-opted-in node holding the same policy
refuses to serve it. All state is in a temporary directory; the path
is printed at the end. Nothing is deployed.
"""

import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "registration"))

from policy_node import NotServing, PolicyNode  # noqa: E402
from registration import RegistrationChannel, sign_library  # noqa: E402


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-gate2-demo-"))
    code = b"policy: allow if caller.tier == 'member'\n"

    # Setup: register centrally (Gate 1), then copy record + payload out.
    central = tmp / "central"
    channel = RegistrationChannel(central / "registry.jsonl", central / "audit.jsonl")
    secret = channel.enroll_user("alice")
    signature = sign_library(secret, "alice", "member-access", "1.0.0", code)
    record, _ = channel.register(
        author="alice", name="member-access", version="1.0.0",
        code=code, signature=signature,
    )
    node_dir = tmp / "node-b"
    node_dir.mkdir()
    snapshot = node_dir / "registry-snapshot.jsonl"
    shutil.copyfile(central / "registry.jsonl", snapshot)
    payloads = {record.policy_hash: code}
    print("Setup. Policy registered centrally:", record.policy_hash[:16] + "...")

    # The central registration point goes offline at runtime.
    shutil.move(str(central), str(tmp / "central-offline"))
    print("1a. Central registration point offline (files moved away):",
          not (central / "registry.jsonl").exists())

    node = PolicyNode(
        node_id="node-b", registry_snapshot_path=snapshot,
        payloads=payloads, serve_policies=True,
    )
    served = node.serve_policy(record.policy_hash)
    print("1b. Second node served the policy from the registration record alone:",
          served.code == code and served.record_hash == record.record_hash)

    guest = PolicyNode(
        node_id="alice-pc", registry_snapshot_path=snapshot,
        payloads=payloads, serve_policies=False,
    )
    try:
        guest.serve_policy(record.policy_hash)
        print("2. UNEXPECTED: a node that did not opt in served the policy")
        return 1
    except NotServing:
        print("2. Node that did not opt in holds the policy but does not serve it: True")

    opted_in_pc = PolicyNode(
        node_id="alice-pc", registry_snapshot_path=snapshot,
        payloads=payloads, serve_policies=True,
    )
    print("   The same user node, after opting in, serves it:",
          opted_in_pc.serve_policy(record.policy_hash).code == code)
    print("Demo state left in:", tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
