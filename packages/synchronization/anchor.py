"""
Process-Bound Architecture - external anchor for sync-mesh chain
heads (experiment 012, test case 5 in tests/test-cases/sync-mesh.md;
detailed case: tests/test-cases/registration.md case 7).

Python standard library only. Builds on Gate 3's SyncNode without
modifying it: the heads anchored here are read from the node's own
public entry view (entries(), state_hash()).

THE GAP THIS CLOSES
-------------------
The registry chain is not the only chain in this architecture.
Every sync-mesh origin carries its own hash chain, and
sync_mesh.py's own docstring names the same limit the registry
has: an attacker who rewrites one origin's entire chain from
genesis, and keeps the victim from ever seeing the original
entries, produces a chain that verifies on its own. (Gate 3
does catch a fork when both versions MEET; a rewrite that
replaces the original before any meeting is the uncovered case.)

WHAT THIS PROTOTYPE DEMONSTRATES
---------------------------------
1. A mesh anchor is written from one node's view: for each origin,
   the head of its chain (its top sequence number and that entry's
   hash), plus the node's overall state hash and entry count,
   written to a JSON file the caller keeps outside the node's own
   state.
2. Any node presented later - the same node, or a peer that
   converged to exactly the anchored entry set - verifies against
   the anchor. A node with a different entry set does not.
3. NEGATIVE CONTROL: a node carrying a fully rewritten origin
   chain (same origin id, different mutations, every hash
   recomputing, acceptable to a fresh node with no divergence)
   FAILS the anchor check, because the rewritten origin's head
   does not match the anchored head.

HONEST LIMITS (prototype, NOT production)
-----------------------------------------
- Same stand-in limit as the registry anchor
  (packages/registration/anchor.py, whose docstring states it in
  full): the anchor file here is a local file on the same machine,
  unsigned and unwitnessed. It proves the comparison logic; an
  attacker who can also rewrite the anchor file defeats it.
  Production needs the heads held somewhere a chain writer cannot
  reach, and cross-node head comparison at scale (Gate 3's named
  direction) is not built here.
- The anchor pins one converged view at one moment. Nodes keep
  mutating honestly afterward and will then mismatch that anchor:
  a mismatch is "not the anchored state", never by itself a
  verdict of tampering rather than legitimate progress. Re-anchor
  when a new state is accepted, as with the registry anchor.
- SyncNode is in-process and keeps nothing on disk, so there is no
  mesh directory for an anchor to be outside OF; "outside" here
  means a file the node does not own or read, held by whoever runs
  the check. Consultation was wired in later: in-process sync
  consults it (experiment 015, anchored_sync.py), and the Gate 5
  verdict takes this mesh anchor as opt-in evidence (experiment
  018). The server processes' mesh side still holds a plain
  SyncNode and consults no mesh anchor; their --anchor (experiment
  018) consults the REGISTRY anchor for their snapshot.
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from pathlib import Path

from sync_mesh import SyncNode

ANCHOR_FORMAT = "PBA-ANCHOR-v1"


class MeshAnchorError(Exception):
    """The mesh anchor cannot be read or is not a mesh anchor."""


def origin_heads(node: SyncNode) -> dict[str, dict]:
    """Each origin's chain head from a node's view: the top
    sequence number held for that origin, and that entry's hash."""
    heads: dict[str, dict] = {}
    for entry in node.entries():
        current = heads.get(entry.origin)
        if current is None or entry.seq > current["seq"]:
            heads[entry.origin] = {"seq": entry.seq, "head": entry.entry_hash}
    return heads


@dataclass(frozen=True)
class MeshAnchor:
    origin_heads: dict[str, dict]
    state_hash: str
    entry_count: int

    def to_dict(self) -> dict:
        return {
            "format": ANCHOR_FORMAT,
            "chain": "sync-mesh",
            "origin_heads": self.origin_heads,
            "state_hash": self.state_hash,
            "entry_count": self.entry_count,
        }


@dataclass(frozen=True)
class MeshAnchorCheck:
    heads_match: bool
    state_matches: bool
    anchored_state_hash: str
    presented_state_hash: str

    @property
    def ok(self) -> bool:
        return self.heads_match and self.state_matches

    @property
    def status(self) -> str:
        return "anchored" if self.ok else "anchor_mismatch"

    def to_dict(self) -> dict:
        return {
            "heads_match": self.heads_match,
            "state_matches": self.state_matches,
            "anchored_state_hash": self.anchored_state_hash,
            "presented_state_hash": self.presented_state_hash,
            "ok": self.ok,
            "status": self.status,
        }


def write_mesh_anchor(node: SyncNode, anchor_path: str | Path) -> MeshAnchor:
    """Write the chain heads of a node's current view to an anchor
    file held outside the node. Returns the anchor written."""
    anchor = MeshAnchor(
        origin_heads=origin_heads(node),
        state_hash=node.state_hash(),
        entry_count=len(node.entries()),
    )
    anchor_path = Path(anchor_path)
    anchor_path.parent.mkdir(parents=True, exist_ok=True)
    anchor_path.write_text(
        json.dumps(anchor.to_dict(), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return anchor


def read_mesh_anchor(anchor_path: str | Path) -> MeshAnchor:
    """Read a mesh anchor file. Missing or malformed raises
    MeshAnchorError: no anchor is a finding, never a pass."""
    anchor_path = Path(anchor_path)
    if not anchor_path.exists():
        raise MeshAnchorError(f"no anchor file at {anchor_path}")
    try:
        data = json.loads(anchor_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MeshAnchorError(f"anchor file is not valid JSON: {anchor_path}") from exc
    if data.get("format") != ANCHOR_FORMAT or data.get("chain") != "sync-mesh":
        raise MeshAnchorError(f"not a {ANCHOR_FORMAT} sync-mesh anchor: {anchor_path}")
    try:
        return MeshAnchor(
            origin_heads={
                str(origin): {"seq": int(h["seq"]), "head": str(h["head"])}
                for origin, h in data["origin_heads"].items()
            },
            state_hash=str(data["state_hash"]),
            entry_count=int(data["entry_count"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise MeshAnchorError(f"anchor file is missing its heads: {anchor_path}") from exc


def check_node_against_anchor(node: SyncNode, anchor_path: str | Path) -> MeshAnchorCheck:
    """Check a presented node against an outside mesh anchor: its
    per-origin heads and its state hash must match exactly."""
    anchor = read_mesh_anchor(anchor_path)
    presented_heads = origin_heads(node)
    heads_match = presented_heads == anchor.origin_heads
    presented_state = node.state_hash()
    return MeshAnchorCheck(
        heads_match=heads_match,
        state_matches=hmac.compare_digest(presented_state, anchor.state_hash),
        anchored_state_hash=anchor.state_hash,
        presented_state_hash=presented_state,
    )


def verify_node_against_anchor(node: SyncNode, anchor_path: str | Path) -> bool:
    """True only if the presented node holds exactly the anchored
    entry set: same origin heads, same state hash."""
    return check_node_against_anchor(node, anchor_path).ok
