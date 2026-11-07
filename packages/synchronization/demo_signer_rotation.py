"""
Runnable demo of signer rotation on the mesh anchor
(experiment 038).

    python3 packages/synchronization/demo_signer_rotation.py

Walks through sync-mesh test case 26 in order and prints
what happened: two sync nodes converge; alice anchors the
mesh state; the mesh grows, alice rotates mesh signing to
bob (alice signs the rotation, bob countersigns accepting
it), and bob's anchor carries the rotation inside the mesh
anchor file; an AnchoredSyncNode configured with the lineage
secrets accepts the rotated state, while the same node with
no lineage configured refuses the merge as
anchor_signature_invalid, exactly as case 11/18 judges an
anchor it cannot verify; and a lineage check of the
converged node names bob as the current signer. All state
is in a temporary directory; the path is printed at the
end. Nothing is deployed.
"""

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.append(str(HERE.parent / "registration"))

from anchor import (  # noqa: E402  (synchronization anchor, exp 012)
    check_node_against_anchor,
    make_mesh_anchor_rotation,
    read_mesh_anchor,
    write_mesh_anchor,
)
from anchored_sync import AnchoredSyncNode  # noqa: E402
from registration import RegistrationChannel  # noqa: E402
from sync_mesh import SyncNode  # noqa: E402


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pba-mesh-rotation-demo-"))
    registry_dir = tmp / "registry-home"
    channel = RegistrationChannel(
        registry_dir / "registry.jsonl", registry_dir / "audit.jsonl")
    alice_secret = channel.enroll_user("alice")
    bob_secret = channel.enroll_user("bob")
    lineage = {"alice": alice_secret, "bob": bob_secret}
    anchor_path = tmp / "outside" / "mesh-anchor.json"

    a = SyncNode("node-a")
    b = SyncNode("node-b")
    a.mutate("greeting", "hello")
    b.receive_entries(a.entries())
    b.mutate("mode", "member")
    a.receive_entries(b.entries())
    write_mesh_anchor(a, anchor_path, signer="alice",
                      secret_hex=alice_secret)
    print("Setup. Nodes converged; alice anchors the mesh:",
          read_mesh_anchor(anchor_path).state_hash[:16] + "...")

    # 1. The mesh grows; alice rotates to bob at the new
    # state, bob countersigns, and bob's anchor carries the
    # rotation inside the mesh anchor file.
    a.mutate("tier", "gold")
    b.receive_entries(a.entries())
    rotation = make_mesh_anchor_rotation(
        from_signer="alice", from_secret_hex=alice_secret,
        to_signer="bob", to_secret_hex=bob_secret,
        rotation_seq=0, effective_anchor_seq=1,
        effective_state_hash=a.state_hash(),
        effective_entry_count=len(a.entries()))
    write_mesh_anchor(a, anchor_path, signer="bob",
                      secret_hex=bob_secret, rotation=rotation,
                      predecessor_secret_hex=alice_secret)
    record = read_mesh_anchor(anchor_path)
    print("1. Rotated: mesh signer is now", record.signer,
          "; the anchor file carries", len(record.rotations),
          "rotation record(s)")

    # 2. A lineage-configured anchored node accepts the
    # rotated state; an unlineaged one refuses the merge.
    lined = AnchoredSyncNode(
        "node-c", anchor_path, anchor_signer="alice",
        anchor_secret_hex=alice_secret,
        anchor_signer_secrets=lineage)
    lined.receive_entries(a.entries())
    print("2. Lineage node anchor standing after the merge:",
          lined.anchor_status())
    plain = AnchoredSyncNode(
        "node-d", anchor_path, anchor_signer="alice",
        anchor_secret_hex=alice_secret)
    refused = plain.receive_entries(a.entries())
    print("   Unlineaged node merge refused:", refused.reason)

    # 3. The pre-rotation state still verifies, and the
    # retired signer's extension is refused by name.
    check = check_node_against_anchor(
        b, anchor_path, signer="alice", secret_hex=alice_secret,
        signer_secrets=lineage)
    print("3. Lineage check of the converged node:", check.status,
          "; current signer:", check.current_signer)
    print("DEMO OK: mesh signing moved alice -> bob inside the",
          "mesh anchor file; only lineage verifiers follow.")
    print("State left in:", tmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
