"""
Runnable demo of signed anchors with a kept history
(experiment 019).

    python3 packages/registration/demo_signed_anchor.py

Walks through registration test case 10 in order and prints
what happened: an enrolled identity (alice, enrolled at
runtime here) signs the anchor for the registry's head; the
genuine registry verifies with the signature checked; a
re-anchor by the same identity after honest growth appends to
the kept history inside the anchor file instead of
overwriting it; a re-anchor attempt by a different enrolled
identity (bob) is refused and logged in the anchor audit log
beside the anchor file, leaving the anchor unchanged; and an
anchor file whose signature is tampered with afterward fails
the signature check under its own name,
anchor_signature_invalid. All state is in a temporary
directory; the path is printed at the end. Nothing is
deployed.
"""

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from anchor import (  # noqa: E402
    AnchorError,
    check_registry_against_anchor,
    read_anchor,
    read_anchor_audit,
    verify_anchor_history,
    write_anchor,
)
from registration import RegistrationChannel, sign_library  # noqa: E402

CODE = b"policy: allow if caller.tier == 'member'\n"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-signed-anchor-demo-"))
    registry_dir = tmp / "registry-home"
    registry_log = registry_dir / "registry.jsonl"
    anchor_path = tmp / "outside" / "registry-anchor.json"

    channel = RegistrationChannel(registry_log, registry_dir / "audit.jsonl")
    alice_secret = channel.enroll_user("alice")
    bob_secret = channel.enroll_user("bob")
    record, _ = channel.register(
        author="alice", name="member-access", version="1.0.0", code=CODE,
        signature=sign_library(alice_secret, "alice", "member-access",
                               "1.0.0", CODE),
    )
    print("Setup. Policy registered:", record.policy_hash[:16] + "...")

    # 1. Alice signs the anchor for the genuine head.
    anchor = write_anchor(
        registry_log, anchor_path, signer="alice", secret_hex=alice_secret
    )
    print("1. Signed anchor: signer", anchor.signer,
          "- history entries:", len(anchor.history),
          "- signature:", anchor.signature[:16] + "...")

    # 2. The genuine registry verifies, signature checked.
    check = check_registry_against_anchor(
        registry_log, anchor_path, signer="alice", secret_hex=alice_secret
    )
    print("2. Genuine registry, signature checked:", check.ok,
          "- status:", check.status, "- signature valid:",
          check.signature_valid)

    # 3. Honest growth, then the SAME identity re-anchors: the
    # history grows by append, it does not overwrite.
    code_b = b"policy: billing\n"
    channel.register(
        author="bob", name="billing-export", version="0.2.0", code=code_b,
        signature=sign_library(bob_secret, "bob", "billing-export",
                               "0.2.0", code_b),
    )
    anchor = write_anchor(
        registry_log, anchor_path, signer="alice", secret_hex=alice_secret
    )
    print("3. Re-anchored by alice after growth: history entries:",
          len(anchor.history), "- sequences:",
          [e["seq"] for e in anchor.history],
          "- history verifies:",
          verify_anchor_history(
              anchor_path, signer="alice", secret_hex=alice_secret))

    # 4. A different enrolled identity tries to re-anchor: refused,
    # logged, anchor unchanged.
    try:
        write_anchor(
            registry_log, anchor_path, signer="bob", secret_hex=bob_secret
        )
        print("4. Bob's re-anchor: NOT REFUSED (this would be a bug)")
    except AnchorError as exc:
        print("4. Bob's re-anchor refused:", str(exc)[:80] + "...")
    events = read_anchor_audit(anchor_path)
    print("   Anchor audit log:", [
        (e["event"], e["reason"], e["attempted_signer"]) for e in events
    ])
    print("   Anchor still alice's, history entries:",
          len(read_anchor(anchor_path).history))

    # 5. The signature in the file is tampered with afterward:
    # the check names it anchor_signature_invalid.
    data = json.loads(anchor_path.read_text(encoding="utf-8"))
    sig = data["signature"]
    data["signature"] = ("0" if sig[0] != "0" else "1") + sig[1:]
    anchor_path.write_text(
        json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    check = check_registry_against_anchor(
        registry_log, anchor_path, signer="alice", secret_hex=alice_secret
    )
    print("5. Tampered signature:", check.ok, "- status:", check.status)

    print("Demo state left in:", tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
