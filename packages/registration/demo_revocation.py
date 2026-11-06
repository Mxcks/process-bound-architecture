"""
Runnable demo of revocation (registration case 6, experiment 008).

    python3 packages/registration/demo_revocation.py

Walks through the registration-side outcomes in order: alice registers
a policy, a non-author tries to revoke it and is refused and logged,
alice revokes it herself, the revocation lands as a second entry in
the hash chain with the original entry untouched, and afterwards the
execution boundary refuses the policy and its validation channel is
no longer honored. All state is created in a temporary directory and
left there for inspection; the path is printed at the end.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from registration import (  # noqa: E402
    BlockedRevoked,
    ExecutionBoundary,
    RegistrationChannel,
    RevocationRejected,
    sign_library,
    sign_revocation,
)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-revocation-demo-"))
    channel = RegistrationChannel(tmp / "registry.jsonl", tmp / "audit.jsonl")
    boundary = ExecutionBoundary(channel)

    code = b"policy: allow if caller.tier == 'member'\n"
    alice_secret = channel.enroll_user("alice")
    record, _ = channel.register(
        author="alice", name="member-access", version="1.0.0",
        code=code,
        signature=sign_library(alice_secret, "alice", "member-access", "1.0.0", code),
    )
    print("Setup. Alice registered her policy:", record.policy_hash[:16] + "...")
    print("   Admitted before revocation:",
          boundary.admit(policy_hash=record.policy_hash, code=code).name)
    print("   Validation channel honored before revocation:",
          channel.validation_channel_for(record.policy_hash) == record.validation_channel)

    bob_secret = channel.enroll_user("bob")
    try:
        channel.revoke(
            author="bob", policy_hash=record.policy_hash,
            signature=sign_revocation(bob_secret, record.policy_hash),
        )
        print("1. UNEXPECTED: a non-author revoked alice's policy")
        return 1
    except RevocationRejected:
        rejected = [
            e for e in channel.read_audit_events()
            if e.get("event") == "revocation_rejected" and e.get("reason") == "not_author"
        ]
        print("1. Non-author revocation rejected and audit-logged:", len(rejected) == 1)

    lines_before = (tmp / "registry.jsonl").read_text(encoding="utf-8").splitlines()
    revocation, created = channel.revoke(
        author="alice", policy_hash=record.policy_hash,
        signature=sign_revocation(alice_secret, record.policy_hash),
    )
    lines_after = (tmp / "registry.jsonl").read_text(encoding="utf-8").splitlines()
    print("2. Author revoked her own policy:", created,
          "at log index", revocation.log_index)
    print("3. Revocation is a new chain entry, original entry untouched:",
          len(lines_after) == 2 and lines_after[0] == lines_before[0]
          and channel.verify_registry_chain())

    try:
        boundary.admit(policy_hash=record.policy_hash, code=code)
        print("4. UNEXPECTED: revoked code was admitted")
        return 1
    except BlockedRevoked:
        print("4. Execution boundary refuses the revoked policy: True")
    print("5. Validation channel no longer honored:",
          channel.validation_channel_for(record.policy_hash) is None)
    print("Demo state (registry + audit logs) left in:", tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
