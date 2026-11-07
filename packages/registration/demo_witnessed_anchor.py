"""
Runnable demo of witnessed anchors (experiment 029).

    python3 packages/registration/demo_witnessed_anchor.py

Walks through registration test case 11 in order and prints
what happened: an enrolled identity (alice) signs the anchor
for the registry's head and a second enrolled identity
(carol) countersigns it; a witness-configured check verifies
with both judgements recorded; the same anchor WITHOUT a
countersignature is refused under anchor_witness_invalid, as
is one countersigned by a different identity (bob); a
re-anchor by bob genuinely countersigned by carol is still
refused as a different signer and logged, because succession
is judged on the signer alone; and a tampered
countersignature fails under anchor_witness_invalid while a
tampered signer signature stays anchor_signature_invalid.
All state is in a temporary directory; the path is printed
at the end. Nothing is deployed.
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
    write_anchor,
)
from registration import RegistrationChannel, sign_library  # noqa: E402

CODE = b"policy: allow if caller.tier == 'member'\n"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-witnessed-anchor-demo-"))
    registry_dir = tmp / "registry-home"
    registry_log = registry_dir / "registry.jsonl"
    anchor_path = tmp / "outside" / "registry-anchor.json"

    channel = RegistrationChannel(registry_log, registry_dir / "audit.jsonl")
    alice_secret = channel.enroll_user("alice")
    bob_secret = channel.enroll_user("bob")
    carol_secret = channel.enroll_user("carol")
    record, _ = channel.register(
        author="alice", name="member-access", version="1.0.0", code=CODE,
        signature=sign_library(alice_secret, "alice", "member-access",
                               "1.0.0", CODE),
    )
    print("Setup. Policy registered:", record.policy_hash[:16] + "...")

    # 1. Alice signs, carol countersigns: the anchor records
    # both identities, and a witness-configured check verifies
    # with both judgements recorded.
    anchor = write_anchor(
        registry_log, anchor_path,
        signer="alice", secret_hex=alice_secret,
        witness="carol", witness_secret_hex=carol_secret,
    )
    print("1. Witnessed anchor: signer", anchor.signer,
          "- witness", anchor.witness,
          "- countersignature:", anchor.witness_signature[:16] + "...")
    check = check_registry_against_anchor(
        registry_log, anchor_path,
        signer="alice", secret_hex=alice_secret,
        witness="carol", witness_secret_hex=carol_secret,
    )
    print("   Witness-configured check:", check.ok,
          "- status:", check.status,
          "- signature valid:", check.signature_valid,
          "- witness valid:", check.witness_valid)

    # 2. The same write WITHOUT a countersignature: refused by
    # the witness-configured check under its own name.
    unsigned_path = tmp / "outside" / "signer-alone-anchor.json"
    write_anchor(
        registry_log, unsigned_path,
        signer="alice", secret_hex=alice_secret,
    )
    check = check_registry_against_anchor(
        registry_log, unsigned_path,
        signer="alice", secret_hex=alice_secret,
        witness="carol", witness_secret_hex=carol_secret,
    )
    print("2. Signer-alone anchor under a witness check:", check.ok,
          "- status:", check.status)

    # 3. Countersigned by the WRONG identity (bob): same name.
    wrong_path = tmp / "outside" / "wrong-witness-anchor.json"
    write_anchor(
        registry_log, wrong_path,
        signer="alice", secret_hex=alice_secret,
        witness="bob", witness_secret_hex=bob_secret,
    )
    check = check_registry_against_anchor(
        registry_log, wrong_path,
        signer="alice", secret_hex=alice_secret,
        witness="carol", witness_secret_hex=carol_secret,
    )
    print("3. Bob-countersigned anchor under carol's check:", check.ok,
          "- status:", check.status)

    # 4. Bob tries to succeed alice as signer, with carol's
    # genuine countersignature: succession is still refused,
    # and logged - the countersignature buys no succession.
    try:
        write_anchor(
            registry_log, anchor_path,
            signer="bob", secret_hex=bob_secret,
            witness="carol", witness_secret_hex=carol_secret,
        )
        print("4. Bob's countersigned re-anchor: NOT REFUSED (a bug)")
    except AnchorError as exc:
        print("4. Bob's countersigned re-anchor refused:",
              str(exc)[:80] + "...")
    events = read_anchor_audit(anchor_path)
    print("   Anchor audit log:", [
        (e["event"], e["reason"], e["attempted_signer"]) for e in events
    ])
    print("   Anchor still alice's, witnessed by:",
          read_anchor(anchor_path).witness)

    # 5. A tampered countersignature: anchor_witness_invalid.
    # A tampered signer signature stays anchor_signature_invalid.
    data = json.loads(anchor_path.read_text(encoding="utf-8"))
    wsig = data["witness_signature"]
    data["witness_signature"] = ("0" if wsig[0] != "0" else "1") + wsig[1:]
    anchor_path.write_text(
        json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    check = check_registry_against_anchor(
        registry_log, anchor_path,
        signer="alice", secret_hex=alice_secret,
        witness="carol", witness_secret_hex=carol_secret,
    )
    print("5. Tampered countersignature:", check.ok,
          "- status:", check.status)
    data = json.loads(anchor_path.read_text(encoding="utf-8"))
    data["witness_signature"] = wsig
    sig = data["signature"]
    data["signature"] = ("0" if sig[0] != "0" else "1") + sig[1:]
    anchor_path.write_text(
        json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    check = check_registry_against_anchor(
        registry_log, anchor_path,
        signer="alice", secret_hex=alice_secret,
        witness="carol", witness_secret_hex=carol_secret,
    )
    print("   Tampered signer signature:", check.ok,
          "- status:", check.status)

    print("Demo state left in:", tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
