"""
Runnable demo of the Gate 1 registration prototype.

    python3 packages/registration/demo.py

Walks through all six Gate 1 outcomes in order and prints what happened.
All state is created in a temporary directory and left there for
inspection; the path is printed at the end. Nothing is deployed.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from registration import (  # noqa: E402
    BlockedUnregistered,
    ExecutionBoundary,
    RegistrationChannel,
    RegistrationRejected,
    policy_hash_for,
    sign_library,
)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-gate1-demo-"))
    channel = RegistrationChannel(tmp / "registry.jsonl", tmp / "audit.jsonl")
    boundary = ExecutionBoundary(channel)

    print("1. Channel exists before use:", channel.is_ready())

    code = b"policy: allow if caller.tier == 'member'\n"
    secret = channel.enroll_user("alice")
    signature = sign_library(secret, "alice", "member-access", "1.0.0", code)
    record, created = channel.register(
        author="alice", name="member-access", version="1.0.0",
        code=code, signature=signature,
    )
    print("2. Alice registered her own library:", created, record.policy_hash[:16] + "...")

    try:
        channel.register(
            author="alice", name="member-access", version="1.0.0",
            code=code, signature="ff" * 32,
        )
        print("3. UNEXPECTED: bad signature was accepted")
        return 1
    except RegistrationRejected:
        rejected = [e for e in channel.read_audit_events() if e.get("reason") == "bad_signature"]
        print("3. Bad signature rejected and audit-logged:", len(rejected) == 1)

    try:
        boundary.admit(
            policy_hash=policy_hash_for("eve", "never-registered", "0.0.1", b"x"),
            code=b"x",
        )
        print("4. UNEXPECTED: unregistered code was admitted")
        return 1
    except BlockedUnregistered:
        print("4. Unregistered code blocked at the execution boundary: True")

    again, created_again = channel.register(
        author="alice", name="member-access", version="1.0.0",
        code=code, signature=signature,
    )
    print(
        "5. Re-registering gives the same record, chain verifies:",
        again == record and not created_again and channel.verify_registry_chain(),
    )

    print("6. Validation channel bound on registration:", record.validation_channel)
    print("   Admitted at boundary:", boundary.admit(policy_hash=record.policy_hash, code=code).name)
    print("Demo state (registry + audit logs) left in:", tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
