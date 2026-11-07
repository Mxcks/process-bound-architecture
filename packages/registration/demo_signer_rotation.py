"""
Runnable demo of anchor signer rotation (experiment 038).

    python3 packages/registration/demo_signer_rotation.py

Walks through registration test case 12 in order and prints
what happened: alice signs the registry anchor; alice rotates
signing to bob (alice signs the rotation, bob countersigns
accepting it) and bob's next anchor carries the rotation
inside the anchor file; a lineage-configured check accepts
the rotated anchor, the pre-rotation anchor file still
verifies on its own history, and an honestly extended
anchor signed by the retired alice is refused as
anchor_signer_retired by a verifier holding the rotation;
bob then rotates to carol and the chain continues; and a
rotation signed by a non-current signer is refused at the
write path under its own name. All state is in a
temporary directory; the path is printed at the end.
Nothing is deployed.
"""

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from anchor import (  # noqa: E402
    AnchorError,
    check_registry_against_anchor,
    make_anchor_rotation,
    read_anchor,
    verify_anchor_history,
    write_anchor,
)
from registration import RegistrationChannel, sign_library  # noqa: E402

CODE = b"policy: allow if caller.tier == 'member'\n"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-signer-rotation-demo-"))
    registry_dir = tmp / "registry-home"
    registry_log = registry_dir / "registry.jsonl"
    anchor_path = tmp / "outside" / "registry-anchor.json"

    channel = RegistrationChannel(registry_log, registry_dir / "audit.jsonl")
    alice_secret = channel.enroll_user("alice")
    bob_secret = channel.enroll_user("bob")
    carol_secret = channel.enroll_user("carol")
    lineage = {"alice": alice_secret, "bob": bob_secret,
               "carol": carol_secret}

    count = 0

    def register(author, secret, name):
        nonlocal count
        record, _ = channel.register(
            author=author, name=name, version="1.0.0", code=CODE,
            signature=sign_library(secret, author, name, "1.0.0", CODE))
        count += 1
        return record

    register("alice", alice_secret, "member-access")
    write_anchor(registry_log, anchor_path, signer="alice",
                 secret_hex=alice_secret)
    print("Setup. Alice anchors the registry:",
          read_anchor(anchor_path).chain_head[:16] + "...")
    # Keep a copy of alice's pre-rotation anchor file: step 2
    # judges this very file after the rotation lands.
    import shutil
    pre_path = tmp / "outside" / "pre-rotation-anchor.json"
    shutil.copyfile(anchor_path, pre_path)

    # 1. The registry grows; alice rotates to bob at the new
    # head, bob countersigns, and bob's anchor carries the
    # rotation inside the anchor file.
    record = register("alice", alice_secret, "billing-access")
    rotation = make_anchor_rotation(
        from_signer="alice", from_secret_hex=alice_secret,
        to_signer="bob", to_secret_hex=bob_secret,
        rotation_seq=0, effective_anchor_seq=1,
        effective_chain_head=record.record_hash,
        effective_entry_count=count)
    write_anchor(registry_log, anchor_path, signer="bob",
                 secret_hex=bob_secret, rotation=rotation,
                 predecessor_secret_hex=alice_secret)
    record = read_anchor(anchor_path)
    print("1. Rotated: signer is now", record.signer,
          "; the anchor file carries", len(record.rotations),
          "rotation record(s); a lineage check says:",
          check_registry_against_anchor(
              registry_log, anchor_path, signer="alice",
              secret_hex=alice_secret,
              signer_secrets=lineage).status)

    # 2. History before the rotation point stays valid: the
    # copied pre-rotation anchor file still verifies on its
    # own kept history after the rotation has landed.
    print("2. The pre-rotation anchor file still verifies on",
          "its own history:",
          verify_anchor_history(pre_path, signer="alice",
                                secret_hex=alice_secret))

    # 3. The retired signer cannot extend: alice honestly
    # extends her own pre-rotation anchor copy over the grown
    # registry - every signature genuine - and a verifier
    # holding the rotation refuses it under the new name.
    retired_path = tmp / "outside" / "anchor-retired.json"
    retired_path.write_bytes(pre_path.read_bytes())
    write_anchor(registry_log, retired_path, signer="alice",
                 secret_hex=alice_secret)
    print("3. Alice's honestly extended anchor, judged by a",
          "verifier holding the rotation:",
          check_registry_against_anchor(
              registry_log, retired_path, signer="alice",
              secret_hex=alice_secret, signer_secrets=lineage,
              rotations=[rotation]).status)

    # 4. Succession continues: bob rotates to carol.
    register("bob", bob_secret, "audit-access")
    record = register("bob", bob_secret, "partner-access")
    rotation2 = make_anchor_rotation(
        from_signer="bob", from_secret_hex=bob_secret,
        to_signer="carol", to_secret_hex=carol_secret,
        rotation_seq=1, effective_anchor_seq=2,
        effective_chain_head=record.record_hash,
        effective_entry_count=count)
    write_anchor(registry_log, anchor_path, signer="carol",
                 secret_hex=carol_secret, rotation=rotation2,
                 predecessor_secret_hex=bob_secret,
                 signer_secrets=lineage)
    record = read_anchor(anchor_path)
    print("4. Second rotation: signer is now", record.signer,
          "; rotations carried:", len(record.rotations),
          "; lineage check:",
          check_registry_against_anchor(
              registry_log, anchor_path, signer="alice",
              secret_hex=alice_secret,
              signer_secrets=lineage).status)

    # 5. A rotation by a non-current signer is refused: the
    # current signer is carol, and this record is alice's.
    record_now = read_anchor(anchor_path)
    try:
        bad = make_anchor_rotation(
            from_signer="alice", from_secret_hex=alice_secret,
            to_signer="bob", to_secret_hex=bob_secret,
            rotation_seq=2,
            effective_anchor_seq=record_now.anchor_seq + 1,
            effective_chain_head=record_now.chain_head,
            effective_entry_count=record_now.entry_count)
        write_anchor(registry_log, anchor_path, signer="bob",
                     secret_hex=bob_secret, rotation=bad,
                     predecessor_secret_hex=alice_secret,
                     signer_secrets=lineage)
        print("5. UNEXPECTED: a non-current signer's rotation stood")
        return 1
    except AnchorError as exc:
        print("5. Non-current signer's rotation refused:", exc)

    print("DEMO OK: trust moved alice -> bob -> carol inside the",
          "anchor file; the retired signer is refused by name.")
    print("State left in:", tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
