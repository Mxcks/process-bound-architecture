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

SIGNED ANCHORS WITH A KEPT HISTORY (experiment 019, sync-mesh
case 11)
-------------------------------------------------------------------
write_mesh_anchor may be called with a signer and that signer's
Gate 1 enrollment secret. That writes a SIGNED anchor under
exactly the registry anchor's rules
(packages/registration/anchor.py states them in full): Gate 1's
HMAC scheme over a domain-separated canonical encoding covering
the per-origin heads, the state hash, the count, the signer,
the history sequence, and the previous history entry's hash;
re-anchoring only by the SAME identity that signed the current
anchor (a different identity's attempt is refused, logged in
the anchor audit log beside the anchor file, and leaves the
anchor unchanged; a first signed write over an unsigned anchor
establishes the signer); accepted re-anchors append signed,
hash-chained history entries inside the anchor file instead of
overwriting silently; and checks configured with a signing
identity treat a missing, wrong, or tampered signature, or a
history that does not verify, as anchor_signature_invalid -
distinct from anchor_mismatch and anchor_stale, never a valid
anchor. With no identity configured, mesh anchors are judged by
their heads exactly as experiments 012/015 built.

HONEST LIMITS (prototype, NOT production)
-----------------------------------------
- Same stand-in limit as the registry anchor
  (packages/registration/anchor.py, whose docstring states it in
  full): the anchor file here is a local file on the same machine.
  In its unsigned form it is unwitnessed and it proves the
  comparison logic only; an attacker who can also rewrite the
  anchor file defeats it. Signed (experiment 019), the attacker
  additionally needs the signer's secret to forge a signature or
  a history entry - but the file can still be deleted or rolled
  back to an older signed state, and signing inherits Gate 1's
  symmetric HMAC limit (verification requires the secret, and
  the channel that issued it could forge). Production needs the
  heads held somewhere a chain writer cannot reach, and
  cross-node head comparison at scale (Gate 3's named
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
  (Superseded in part by experiment 026: a server started with
  --mesh-anchor holds an AnchoredSyncNode for its mesh side and
  consults this anchor on every mesh receive over the wire; a
  server started without the flag still holds a plain SyncNode,
  as stated.)
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path

from sync_mesh import SyncNode

ANCHOR_FORMAT = "PBA-ANCHOR-v1"
GENESIS_HASH = "0" * 64


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


# -- anchor signing and kept history (experiment 019) -----------------------
#
# The mesh half of registration anchor.py's signing: the same
# Gate 1 HMAC scheme, the same history and audit discipline, with
# a canonical encoding for this chain (domain-separated from the
# registry anchor's by the chain name inside it).


def _canonical_heads(origin_heads: dict) -> str:
    return json.dumps(origin_heads, sort_keys=True, separators=(",", ":"))


def canonical_mesh_anchor_bytes(
    signer: str,
    origin_heads: dict,
    state_hash: str,
    entry_count: int,
    anchor_seq: int,
    prev_history_hash: str,
) -> bytes:
    """Deterministic encoding of one mesh anchor (re-)signing
    act: who signs, which per-origin heads and state, at which
    history sequence, chained to which previous history entry."""
    return (
        f"PBA-ANCHOR-SIGN-v1|sync-mesh|{signer}|{anchor_seq}|"
        f"{state_hash}|{entry_count}|{_canonical_heads(origin_heads)}|"
        f"{prev_history_hash}"
    ).encode("utf-8")


def sign_mesh_anchor(
    secret_hex: str,
    signer: str,
    origin_heads: dict,
    state_hash: str,
    entry_count: int,
    anchor_seq: int,
    prev_history_hash: str,
) -> str:
    """Prototype mesh anchor 'signature': HMAC-SHA256 over the
    canonical mesh anchor bytes, with the signer's Gate 1
    enrollment secret. See registration/anchor.py for why this
    is NOT production signing."""
    return hmac.new(
        bytes.fromhex(secret_hex),
        canonical_mesh_anchor_bytes(
            signer, origin_heads, state_hash, entry_count,
            anchor_seq, prev_history_hash,
        ),
        hashlib.sha256,
    ).hexdigest()


def _history_entry(
    signer: str,
    secret_hex: str,
    seq: int,
    heads: dict,
    state_hash: str,
    count: int,
    prev_hash: str,
) -> dict:
    """One kept-history entry, in the repo's chain style: the
    signature is part of the hashed body, so editing any field
    of an old entry breaks its entry hash."""
    signature = sign_mesh_anchor(
        secret_hex, signer, heads, state_hash, count, seq, prev_hash
    )
    body = {
        "seq": seq,
        "origin_heads": heads,
        "state_hash": state_hash,
        "entry_count": count,
        "signer": signer,
        "prev_hash": prev_hash,
        "signature": signature,
    }
    entry = dict(body)
    entry["entry_hash"] = _sha256_hex(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return entry


def _verify_history_entries(
    history: list[dict],
    signer: str | None = None,
    secret_hex: str | None = None,
) -> bool:
    """Recompute a kept history: sequences 0..n-1, prev-hash
    linkage from genesis, entry-hash recomputation, and with a
    secret every entry's signature; with a signer every entry
    names that signer. Same checks as the registry anchor's."""
    prev_hash = GENESIS_HASH
    for expected_seq, entry in enumerate(history):
        if entry.get("seq") != expected_seq:
            return False
        if entry.get("prev_hash") != prev_hash:
            return False
        if signer is not None and entry.get("signer") != signer:
            return False
        body = {k: v for k, v in entry.items() if k != "entry_hash"}
        recomputed = _sha256_hex(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        if not hmac.compare_digest(recomputed, entry.get("entry_hash", "")):
            return False
        if secret_hex is not None:
            expected_sig = sign_mesh_anchor(
                secret_hex,
                str(entry.get("signer")),
                entry.get("origin_heads", {}),
                str(entry.get("state_hash")),
                int(entry.get("entry_count", -1)),
                expected_seq,
                prev_hash,
            )
            if not hmac.compare_digest(expected_sig, entry.get("signature", "")):
                return False
        prev_hash = entry["entry_hash"]
    return True


def anchor_audit_path(anchor_path: str | Path) -> Path:
    """The anchor audit log beside a mesh anchor file (same
    naming and style as the registry anchor's)."""
    anchor_path = Path(anchor_path)
    return anchor_path.parent / (anchor_path.name + ".audit.jsonl")


def _append_anchor_audit(anchor_path: Path, event: dict) -> None:
    path = anchor_audit_path(anchor_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, sort_keys=True) + "\n")


def read_anchor_audit(anchor_path: str | Path) -> list[dict]:
    """The anchor audit events beside a mesh anchor ([] if none)."""
    path = anchor_audit_path(anchor_path)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def _signature_verifies(record: "MeshAnchor", signer: str, secret_hex: str) -> bool:
    """True only if a record is a genuine signed mesh anchor BY
    the configured signer: signer matches, kept history verifies
    (chain and signatures), top fields are exactly the history's
    last entry, and the top signature recomputes."""
    if record.signer is None or record.signer != signer:
        return False
    if record.signature is None or record.anchor_seq is None:
        return False
    history = [dict(e) for e in record.history]
    if not history or len(history) != record.anchor_seq + 1:
        return False
    if not _verify_history_entries(history, signer=signer, secret_hex=secret_hex):
        return False
    last = history[-1]
    if last["origin_heads"] != record.origin_heads:
        return False
    if last["state_hash"] != record.state_hash:
        return False
    if last["entry_count"] != record.entry_count:
        return False
    if not hmac.compare_digest(last["signature"], record.signature):
        return False
    expected = sign_mesh_anchor(
        secret_hex,
        signer,
        record.origin_heads,
        record.state_hash,
        record.entry_count,
        record.anchor_seq,
        last["prev_hash"],
    )
    return hmac.compare_digest(expected, record.signature)


@dataclass(frozen=True)
class MeshAnchor:
    origin_heads: dict[str, dict]
    state_hash: str
    entry_count: int
    # Experiment 019, signed anchors: all None / empty for an
    # unsigned anchor, exactly as experiments 012-018 wrote them.
    signer: str | None = None
    signature: str | None = None
    anchor_seq: int | None = None
    history: tuple = ()

    def to_dict(self) -> dict:
        data = {
            "format": ANCHOR_FORMAT,
            "chain": "sync-mesh",
            "origin_heads": self.origin_heads,
            "state_hash": self.state_hash,
            "entry_count": self.entry_count,
        }
        if self.signer is not None:
            data["signer"] = self.signer
            data["signature"] = self.signature
            data["anchor_seq"] = self.anchor_seq
            data["history"] = [dict(e) for e in self.history]
        return data


@dataclass(frozen=True)
class MeshAnchorCheck:
    heads_match: bool
    state_matches: bool
    anchored_state_hash: str
    presented_state_hash: str
    # Experiment 019: as on the registry AnchorCheck - unchecked
    # (False / None) with no identity configured; a configured
    # signature failure is its own status, ahead of mismatch.
    signature_checked: bool = False
    signature_valid: bool | None = None
    signer: str | None = None

    @property
    def ok(self) -> bool:
        base = self.heads_match and self.state_matches
        if self.signature_checked:
            return base and bool(self.signature_valid)
        return base

    @property
    def status(self) -> str:
        if self.signature_checked and not self.signature_valid:
            return "anchor_signature_invalid"
        return "anchored" if self.ok else "anchor_mismatch"

    def to_dict(self) -> dict:
        return {
            "heads_match": self.heads_match,
            "state_matches": self.state_matches,
            "anchored_state_hash": self.anchored_state_hash,
            "presented_state_hash": self.presented_state_hash,
            "signature_checked": self.signature_checked,
            "signature_valid": self.signature_valid,
            "signer": self.signer,
            "ok": self.ok,
            "status": self.status,
        }


def write_mesh_anchor(
    node: SyncNode,
    anchor_path: str | Path,
    *,
    signer: str | None = None,
    secret_hex: str | None = None,
) -> MeshAnchor:
    """Write the chain heads of a node's current view to an anchor
    file held outside the node. Returns the anchor written.

    Unsigned (no signer): exactly the experiment 012 behavior -
    the anchor file is overwritten. Signed (experiment 019):
    the same-identity re-anchor rule, refusal logging, and
    appended kept history as the registry anchor
    (packages/registration/anchor.py, write_anchor)."""
    heads = origin_heads(node)
    state_hash = node.state_hash()
    count = len(node.entries())
    anchor_path = Path(anchor_path)

    if signer is None:
        if secret_hex is not None:
            raise MeshAnchorError("secret_hex given without a signer")
        anchor = MeshAnchor(
            origin_heads=heads, state_hash=state_hash, entry_count=count
        )
        anchor_path.parent.mkdir(parents=True, exist_ok=True)
        anchor_path.write_text(
            json.dumps(anchor.to_dict(), sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return anchor

    if secret_hex is None:
        raise MeshAnchorError("a signed anchor needs the signer's secret_hex")

    history: list[dict] = []
    if anchor_path.exists():
        existing = read_mesh_anchor(anchor_path)
        if existing.signer is not None:
            if existing.signer != signer:
                _append_anchor_audit(
                    anchor_path,
                    {
                        "event": "reanchor_rejected",
                        "reason": "different_signer",
                        "attempted_signer": signer,
                        "current_signer": existing.signer,
                        "chain": "sync-mesh",
                        "state_hash": state_hash,
                        "entry_count": count,
                    },
                )
                raise MeshAnchorError(
                    f"re-anchor refused: anchor is signed by "
                    f"{existing.signer}, not by {signer} "
                    f"(same-identity rule, experiment 019)"
                )
            if not _signature_verifies(existing, signer, secret_hex):
                _append_anchor_audit(
                    anchor_path,
                    {
                        "event": "reanchor_rejected",
                        "reason": "existing_signature_invalid",
                        "attempted_signer": signer,
                        "current_signer": existing.signer,
                        "chain": "sync-mesh",
                        "state_hash": state_hash,
                        "entry_count": count,
                    },
                )
                raise MeshAnchorError(
                    "re-anchor refused: the existing signed anchor does "
                    "not verify under the presented secret"
                )
            history = [dict(e) for e in existing.history]

    seq = len(history)
    prev_hash = history[-1]["entry_hash"] if history else GENESIS_HASH
    entry = _history_entry(signer, secret_hex, seq, heads, state_hash, count, prev_hash)
    history.append(entry)
    anchor = MeshAnchor(
        origin_heads=heads,
        state_hash=state_hash,
        entry_count=count,
        signer=signer,
        signature=entry["signature"],
        anchor_seq=seq,
        history=tuple(history),
    )
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
        anchor = MeshAnchor(
            origin_heads={
                str(origin): {"seq": int(h["seq"]), "head": str(h["head"])}
                for origin, h in data["origin_heads"].items()
            },
            state_hash=str(data["state_hash"]),
            entry_count=int(data["entry_count"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise MeshAnchorError(f"anchor file is missing its heads: {anchor_path}") from exc
    if data.get("signer") is not None:
        try:
            anchor = MeshAnchor(
                origin_heads=anchor.origin_heads,
                state_hash=anchor.state_hash,
                entry_count=anchor.entry_count,
                signer=str(data["signer"]),
                signature=str(data["signature"]),
                anchor_seq=int(data["anchor_seq"]),
                history=tuple(dict(e) for e in data["history"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MeshAnchorError(
                f"signed anchor is missing its signature or history: {anchor_path}"
            ) from exc
    return anchor


def verify_mesh_anchor_history(
    anchor_path: str | Path,
    signer: str | None = None,
    secret_hex: str | None = None,
) -> bool:
    """Verify a mesh anchor file's kept history on its own (the
    registry anchor's verify_anchor_history, for this chain):
    chain recomputation, and with a secret every entry's
    signature. An unsigned anchor returns False."""
    record = read_mesh_anchor(anchor_path)
    if record.signer is None or not record.history:
        return False
    history = [dict(e) for e in record.history]
    if not _verify_history_entries(history, signer=signer, secret_hex=secret_hex):
        return False
    last = history[-1]
    return (
        last["origin_heads"] == record.origin_heads
        and last["state_hash"] == record.state_hash
        and last["entry_count"] == record.entry_count
        and last["seq"] == record.anchor_seq
        and hmac.compare_digest(last["signature"], record.signature or "")
    )


def check_node_against_anchor(
    node: SyncNode,
    anchor_path: str | Path,
    *,
    signer: str | None = None,
    secret_hex: str | None = None,
) -> MeshAnchorCheck:
    """Check a presented node against an outside mesh anchor: its
    per-origin heads and its state hash must match exactly.

    With a signing identity configured (experiment 019), the
    anchor's signature and kept history are ALSO verified, and a
    failure is reported in the check (status
    anchor_signature_invalid), never a silent pass."""
    anchor = read_mesh_anchor(anchor_path)
    presented_heads = origin_heads(node)
    heads_match = presented_heads == anchor.origin_heads
    presented_state = node.state_hash()
    signature_checked = signer is not None
    signature_valid: bool | None = None
    if signature_checked:
        if secret_hex is None:
            raise MeshAnchorError("checking a signed anchor needs secret_hex")
        signature_valid = _signature_verifies(anchor, signer, secret_hex)
    return MeshAnchorCheck(
        heads_match=heads_match,
        state_matches=hmac.compare_digest(presented_state, anchor.state_hash),
        anchored_state_hash=anchor.state_hash,
        presented_state_hash=presented_state,
        signature_checked=signature_checked,
        signature_valid=signature_valid,
        signer=anchor.signer,
    )


def verify_node_against_anchor(
    node: SyncNode,
    anchor_path: str | Path,
    *,
    signer: str | None = None,
    secret_hex: str | None = None,
) -> bool:
    """True only if the presented node holds exactly the anchored
    entry set: same origin heads, same state hash - and, when a
    signing identity is configured, a verifying signature."""
    return check_node_against_anchor(
        node, anchor_path, signer=signer, secret_hex=secret_hex
    ).ok
