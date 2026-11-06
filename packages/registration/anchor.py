"""
Process-Bound Architecture - external chain anchor prototype
(experiment 012, test case 7 in tests/test-cases/registration.md).

Python standard library only. No network, no database, no
dependencies. Builds on Gate 1's registry chain format without
modifying or re-implementing Gate 1's channel: the chain reading
and recomputation here is the same standalone algorithm Gate 2's
policy_node.verify_registry_snapshot uses against a file alone.

THE GAP THIS CLOSES
-------------------
Gate 1's hash chain, and every copy of it (Gate 2 snapshots, Gate 3
state, Gate 5's canary), is tamper-evident only within the record
itself: editing one entry breaks its hash, but a record rewritten
from genesis with different content, recomputed entry by entry,
verifies against itself. Experiments 002-010 all named the missing
piece the same way: an external anchor, a witnessed head hash,
something outside the record to compare the record against.

WHAT THIS PROTOTYPE DEMONSTRATES
---------------------------------
1. The chain head - the registry's latest entry hash, with the
   entry count - is written to an anchor file that lives OUTSIDE
   the registry's own directory. write_anchor refuses a path inside
   that directory: a file rewritten together with the registry
   would be no anchor at all.
2. A verifier presented with a registry (the channel's log, or a
   snapshot copy of it - the same chain) checks it in two steps:
   first the chain's own internal verification, then the anchor
   comparison. A genuine registry passes both.
3. NEGATIVE CONTROL: a fully rewritten registry that passes its own
   internal verification FAILS the anchor check, because its head
   does not match the anchored head. A truncated or extended
   presentation fails the same way: the anchor pins the head AND
   the length, and moving the head legitimately means anchoring
   again (that is the anchor's lifecycle, stated not hidden).

HONEST LIMITS (prototype, NOT production)
-----------------------------------------
- The anchor location is a STAND-IN, not a real external service:
  at prototype scale it is a separate local file outside the
  registry directory, on the same machine, writable by the same
  operator. It proves the comparison logic and nothing about the
  anchor itself being safe. An attacker who can rewrite the
  registry AND the anchor file defeats this completely. Production
  needs the head written somewhere the registry's writer cannot
  reach: a witnessed ledger, a second operator's countersignature,
  a public append-only log. None of that is built or claimed here.
- The anchor is not signed. Anyone who can write the anchor file
  can re-anchor a rewritten registry and it will verify. Signing /
  witnessing an anchor is production work, out of scope here.
- Anchoring is a point-in-time fact about one head. Checking a
  registry against an old anchor after the registry legitimately
  grew reports a mismatch, not a tamper verdict: the check object
  separates internal_verification_failed from anchor_mismatch, and
  neither says WHY a head moved. Deciding that is the operator's.
- This module anchors the registry chain. The sync mesh's
  per-origin chains are anchored the same way by
  packages/synchronization/anchor.py. Consultation of this check
  was wired in later, in stages: Gate 2 serving and Gate 3 sync
  consult it in-process (experiment 015), the networked server
  processes consult it at startup when started with --anchor
  (experiment 018), and the Gate 5 verdict takes it as opt-in
  evidence (experiment 018). None of that changes what this
  module's check itself is worth (see the stand-in limits above).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path


GENESIS_HASH = "0" * 64
ANCHOR_FORMAT = "PBA-ANCHOR-v1"


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class AnchorError(Exception):
    """The anchor cannot be written, read, or trusted as an anchor."""


def _read_entries(registry_log_path: str | Path) -> list[dict]:
    entries = []
    for line in Path(registry_log_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def _verify_entries(entries: list[dict]) -> bool:
    """The chain's own internal verification, standalone: indices
    0..n-1 in order, prev_hash linkage, entry_hash recomputation.
    Same algorithm as Gate 1's verify_registry_chain and Gate 2's
    snapshot verification, run here against a parsed entry list."""
    prev_hash = GENESIS_HASH
    for expected_index, entry in enumerate(entries):
        if entry.get("log_index") != expected_index:
            return False
        if entry.get("prev_hash") != prev_hash:
            return False
        body = {k: v for k, v in entry.items() if k != "entry_hash"}
        recomputed = _sha256_hex(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        if not hmac.compare_digest(recomputed, entry.get("entry_hash", "")):
            return False
        prev_hash = entry["entry_hash"]
    return True


def chain_head(entries: list[dict]) -> str:
    """The head of a registry chain: the latest entry's hash, or the
    genesis hash for an empty chain (anchoring an empty registry is
    a real fact: nothing registered yet, head is genesis)."""
    return entries[-1]["entry_hash"] if entries else GENESIS_HASH


@dataclass(frozen=True)
class AnchorRecord:
    chain_head: str
    entry_count: int

    def to_dict(self) -> dict:
        return {
            "format": ANCHOR_FORMAT,
            "chain": "registry",
            "chain_head": self.chain_head,
            "entry_count": self.entry_count,
        }


@dataclass(frozen=True)
class AnchorCheck:
    """The outcome of checking one presented registry against one anchor.

    internal_verifies is the chain judging itself; head_matches and
    count_matches are the anchor judging the chain. ok requires all
    three. status names which judgement failed, because "mismatch"
    and "does not even verify on its own" are different findings.
    """

    internal_verifies: bool
    head_matches: bool
    count_matches: bool
    anchored_head: str
    presented_head: str | None
    anchored_count: int
    presented_count: int

    @property
    def ok(self) -> bool:
        return self.internal_verifies and self.head_matches and self.count_matches

    @property
    def status(self) -> str:
        if not self.internal_verifies:
            return "internal_verification_failed"
        if self.ok:
            return "anchored"
        return "anchor_mismatch"

    def to_dict(self) -> dict:
        return {
            "internal_verifies": self.internal_verifies,
            "head_matches": self.head_matches,
            "count_matches": self.count_matches,
            "anchored_head": self.anchored_head,
            "presented_head": self.presented_head,
            "anchored_count": self.anchored_count,
            "presented_count": self.presented_count,
            "ok": self.ok,
            "status": self.status,
        }


def write_anchor(
    registry_log_path: str | Path, anchor_path: str | Path
) -> AnchorRecord:
    """Write the registry's current chain head to an anchor file
    outside the registry's own directory.

    The registry is verified internally before its head is anchored:
    anchoring a chain that does not verify on its own raises
    AnchorError and writes nothing. An anchor path inside the
    registry file's directory is refused for the same reason a
    self-witnessed record proves nothing.
    """
    registry_log_path = Path(registry_log_path)
    anchor_path = Path(anchor_path)
    if anchor_path.resolve().parent == registry_log_path.resolve().parent:
        raise AnchorError(
            "anchor must live outside the registry's directory: "
            f"{anchor_path} is beside {registry_log_path}"
        )
    entries = _read_entries(registry_log_path)
    if not _verify_entries(entries):
        raise AnchorError("registry does not verify internally; refusing to anchor it")
    record = AnchorRecord(chain_head=chain_head(entries), entry_count=len(entries))
    anchor_path.parent.mkdir(parents=True, exist_ok=True)
    anchor_path.write_text(
        json.dumps(record.to_dict(), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return record


def read_anchor(anchor_path: str | Path) -> AnchorRecord:
    """Read an anchor file. A missing or malformed anchor raises
    AnchorError: there is no anchor here is a finding, never a pass."""
    anchor_path = Path(anchor_path)
    if not anchor_path.exists():
        raise AnchorError(f"no anchor file at {anchor_path}")
    try:
        data = json.loads(anchor_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AnchorError(f"anchor file is not valid JSON: {anchor_path}") from exc
    if data.get("format") != ANCHOR_FORMAT or data.get("chain") != "registry":
        raise AnchorError(f"not a {ANCHOR_FORMAT} registry anchor: {anchor_path}")
    try:
        return AnchorRecord(
            chain_head=str(data["chain_head"]), entry_count=int(data["entry_count"])
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AnchorError(f"anchor file is missing its head or count: {anchor_path}") from exc


def check_registry_against_anchor(
    registry_log_path: str | Path, anchor_path: str | Path
) -> AnchorCheck:
    """Check a presented registry against an outside anchor.

    Never raises for a bad registry: a registry that fails internal
    verification, or whose head/count differ from the anchor, is
    reported in the AnchorCheck. Raises AnchorError only when the
    anchor itself cannot be read.
    """
    anchor = read_anchor(anchor_path)
    entries = _read_entries(registry_log_path)
    internal = _verify_entries(entries)
    presented_head = chain_head(entries) if entries else GENESIS_HASH
    return AnchorCheck(
        internal_verifies=internal,
        head_matches=hmac.compare_digest(presented_head, anchor.chain_head),
        count_matches=len(entries) == anchor.entry_count,
        anchored_head=anchor.chain_head,
        presented_head=presented_head,
        anchored_count=anchor.entry_count,
        presented_count=len(entries),
    )


def verify_registry_against_anchor(
    registry_log_path: str | Path, anchor_path: str | Path
) -> bool:
    """True only if the presented registry verifies internally AND
    its head and length match the outside anchor exactly."""
    return check_registry_against_anchor(registry_log_path, anchor_path).ok
