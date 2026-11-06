"""
Runnable demo of the external chain anchor (experiment 012).

    python3 packages/registration/demo_anchor.py

Walks through registration test case 7 in order and prints what
happened: a registry's chain head is anchored to a file outside the
registry's directory; the genuine registry verifies against the
anchor; then the negative control - a second registry rewritten
from genesis with different content, internally consistent so its
own chain verification passes - is presented, and only the anchor
check catches it. Also shows the anchor's lifecycle: a legitimate
new registration moves the head and fails the old anchor until it
is re-anchored. All state is in a temporary directory; the path is
printed at the end. Nothing is deployed.
"""

import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from anchor import (  # noqa: E402
    check_registry_against_anchor,
    verify_registry_against_anchor,
    write_anchor,
)
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
    tmp = Path(tempfile.mkdtemp(prefix="pba-anchor-demo-"))
    registry_dir = tmp / "registry-home"
    anchor_path = tmp / "outside" / "registry-anchor.json"

    channel = RegistrationChannel(registry_dir / "registry.jsonl", registry_dir / "audit.jsonl")
    record = _register(channel, "alice", "member-access", CODE)
    registry_log = registry_dir / "registry.jsonl"
    print("Setup. Policy registered:", record.policy_hash[:16] + "...")

    # 1. Anchor the head, outside the registry's directory.
    anchor = write_anchor(registry_log, anchor_path)
    print("1. Anchored head (outside the registry directory):",
          anchor.chain_head[:16] + "...", "- entries:", anchor.entry_count,
          "- anchor in registry dir:", anchor_path.parent == registry_dir)

    # 2. The genuine registry verifies against the outside anchor.
    print("2. Genuine registry verifies against the anchor:",
          verify_registry_against_anchor(registry_log, anchor_path))

    # 3. Negative control: a full rewrite from genesis that passes
    # its own internal verification - and fails the anchor check.
    other = tmp / "rewritten-home"
    evil_channel = RegistrationChannel(other / "registry.jsonl", other / "audit.jsonl")
    _register(evil_channel, "mallory", "member-access", EVIL)
    rewritten = tmp / "presented-rewritten.jsonl"
    shutil.copyfile(other / "registry.jsonl", rewritten)
    presented = RegistrationChannel(rewritten, tmp / "audit2.jsonl")
    check = check_registry_against_anchor(rewritten, anchor_path)
    print("3. Rewritten registry passes its OWN chain verification:",
          presented.verify_registry_chain())
    print("   Rewritten registry verifies against the anchor:",
          verify_registry_against_anchor(rewritten, anchor_path),
          "- status:", check.status)

    # 4. Lifecycle: a legitimate new registration moves the head,
    # the old anchor reports the mismatch, re-anchoring restores it.
    _register(channel, "bob", "billing-export", b"policy: billing\n")
    print("4. After a legitimate new registration, old anchor verifies:",
          verify_registry_against_anchor(registry_log, anchor_path))
    write_anchor(registry_log, anchor_path)
    print("   After re-anchoring, the registry verifies:",
          verify_registry_against_anchor(registry_log, anchor_path))

    print("Demo state left in:", tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
