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

SIGNED ANCHORS WITH A KEPT HISTORY (experiment 019, test case 10)
-------------------------------------------------------------------
write_anchor may be called with a signer and that signer's
enrollment secret (signer=..., secret_hex=...). That writes a
SIGNED anchor, reusing Gate 1's signing machinery exactly: the
same HMAC-SHA256 scheme as registration's sign_library /
sign_revocation, over a domain-separated canonical encoding
(canonical_anchor_bytes, prefix "PBA-ANCHOR-SIGN-v1"), so an
anchor signature can never be replayed as a library or
revocation signature. A signed anchor records its signer, and:

- Re-anchoring a signed anchor requires a valid signature from
  the SAME identity that signed the current anchor. An attempt
  by a different enrolled identity is refused, the anchor is
  left unchanged, and the refusal is appended to an anchor
  audit log beside the anchor file (anchor_audit_path). A first
  signed write over an unsigned anchor, or over no anchor,
  establishes the signer: an unsigned anchor has no signer to
  match, and that transition is the one stated exception.
- Each accepted signed (re-)anchor APPENDS to a history kept
  inside the anchor file instead of overwriting silently. A
  history entry records the anchored head and count, its
  sequence number, and its signer; entries are hash-chained
  exactly like the registry's own entries (sequence 0..n-1,
  prev-hash linkage, entry hash recomputed over the entry body,
  signature included in the body), and each entry is signed.
  verify_anchor_history recomputes that chain, and with a signer
  and secret also verifies every entry's signature.
- Checks take the same opt-in identity (signer=..., secret_hex=
  ...). With an identity configured, a signed anchor's signature
  AND history must verify, and the configured signer must be
  the anchor's signer; a missing, wrong, or tampered signature,
  or a history that does not verify, is the check status
  anchor_signature_invalid - distinct from anchor_mismatch and
  from honest-growth handling at the consultation points, and
  never a valid anchor. With NO identity configured, checks
  judge the anchor by its head exactly as experiment 012 built
  them, signed or unsigned: signing is opt-in, and the unsigned
  behavior of experiments 012-018 is unchanged.

WITNESSED ANCHORS (experiment 029, test case 11)
--------------------------------------------------
write_anchor may additionally be called with a witness and
that witness's enrollment secret (witness=...,
witness_secret_hex=..., both or neither, and only alongside a
signer). The witness is a SECOND enrolled identity, distinct
from the signer (a witness equal to the signer is refused at
write time: a countersignature by the signer's own identity
is not a second custody). The witness countersigns THIS
signed anchor - Gate 1's HMAC scheme again, over a
domain-separated canonical encoding of its own
(canonical_anchor_witness_bytes, prefix
"PBA-ANCHOR-WITNESS-v1") covering the signer's identity and
signature together with the head, the count, the history
sequence, and the previous history entry's hash. The
countersignature is part of the kept history entry for that
write (the entry records its witness and the
countersignature inside its hashed body, so editing either
breaks the entry hash the way editing the signer's fields
does) and the anchor's top level mirrors the last entry's
witness fields, exactly as it mirrors the signer's.

Checks take the same opt-in witness identity (witness=...,
witness_secret_hex=...). With a witness configured, the
anchor's countersignature must ALSO verify: the recorded
witness must be the configured witness, the history chain
must verify, the top countersignature must be the last
entry's, and it must recompute under the witness's secret.
A signer-alone anchor, an anchor countersigned by a
different identity, and a tampered top-level
countersignature are the check status
anchor_witness_invalid - distinct from
anchor_signature_invalid (which precedes it: the signature
judgement runs first, so a bad signature is a signature
failure even when the witness is also wrong), from
anchor_mismatch, and from anchor_stale, and never a valid
anchor. With NO witness configured, checks judge the anchor
exactly as experiment 019 built them: witnessing is opt-in,
per consultation point, like signing.

What this closes and what it does not: experiment 028's
finding was that a verifier holding the signer's secret
could sign a genuine anchor over a rewritten record and
have it accepted - the custody of ONE secret was the whole
fence. A witness-configured consultation moves that fence
to TWO secrets: blessing a rewrite now requires the
witness's countersignature as well, and the witness is a
different enrolled identity whose secret the signer does
not hold by construction of the check (each verifier
configured with a witness holds the witness's secret, not
the signer's - though nothing prevents one operator from
being configured with both, which is an operator choice,
stated not hidden). The model stays symmetric, and case 11
states the remaining limit in full: the witness requirement
binds the anchor's CURRENT write (older history entries'
countersignatures are tamper-evident record inside their
hashed bodies, not individually re-verified), and two
colluding custodians are exactly as able to bless a rewrite
as one was. This is a witnessed anchor at prototype scale,
not an asymmetric signature and not an external witness
service.

SIGNER ROTATION (experiment 038, test case 12)
--------------------------------------------------
Case 10's same-identity rule governs re-anchoring, not
succession: before this experiment there was no legitimate
way to move anchor authority from one signer to another,
so a signer's secret was effectively forever. A ROTATION
RECORD (make_anchor_rotation) is the succession instrument:
it names the current signer, the successor (a different
enrolled identity), a rotation sequence number, and the
point it takes effect at - the anchor history sequence of
the successor's first entry, with the chain head and entry
count that entry anchors. The CURRENT signer signs the
statement (canonical_anchor_rotation_bytes, prefix
"PBA-ANCHOR-ROTATE-v1") and the SUCCESSOR countersigns it
accepting (canonical_anchor_rotation_accept_bytes, prefix
"PBA-ANCHOR-ROTATE-ACCEPT-v1", covering the current
signer's signature); both signatures are required. The
successor's first write_anchor presents the record
(rotation=..., with predecessor_secret_hex - the outgoing
signer's secret, present at the handover so the write can
verify the statement it acts on); the write judges it under
its own names - a rotation from a signer who is not the
anchor's current signer is anchor_rotation_signer_invalid,
a rotation whose sequence is wrong or whose signatures do
not stand (a missing countersignature included) is
anchor_rotation_invalid, a rotation whose stated point is
not the write being made is anchor_rotation_mismatch -
refuses audit-logged as rotation_rejected in the anchor
audit log, and on acceptance appends the record to a
ROTATIONS list kept in the anchor file beside the history.
A second rotation chains from the successor the same way.
Writing over an anchor that already carries rotations
requires the lineage secrets (signer_secrets, an
identity-to-secret map for every signer in the lineage) so
the segmented history can be verified at the write.

Checks gain the same configuration (signer_secrets,
beside signer/secret_hex, which then names the lineage's
ROOT - the signer before the first rotation) and an
optional held rotations argument. The rotation judgement
runs after the chain's internal verification and before
the signature, witness, and head judgements: the rotation
chain is validated in full (sequence order, each
from_signer the signer current just before its point,
signatures under the lineage secrets, the history entry at
each effective point carrying exactly the stated head and
count), the kept history is then verified SEGMENT BY
SEGMENT (each entry under its own signer's secret), and an
anchor carrying entries at or after a rotation point
signed by the RETIRED signer is refused as
anchor_signer_retired. Entries before the point stay
valid: history signed before a rotation is judged exactly
as case 10 judges it. AnchorCheck records rotation_checked
/ rotation_valid / rotation_status / current_signer.
Rotation does not change the witness rule: where a witness
is configured, the successor's writes need its
countersignature exactly as the original signer's did. The
WITNESS itself is not rotated by this mechanism - it is
operator configuration (case 11), and case 12 states that
scope choice and its reason. The stated limits stand in
case 12 and the experiment README: a verifier a rotation
never reaches keeps judging the old signer; a compromised
old signer can sign a competing rotation and, before one
stands, this symmetric model cannot tell the two apart;
and lost-secret recovery is not built - rotation requires
the current signer's signature, exactly as re-anchoring
requires its secret.

HONEST LIMITS (prototype, NOT production)
-----------------------------------------
- The anchor location is a STAND-IN, not a real external service:
  at prototype scale it is a separate local file outside the
  registry directory, on the same machine, writable by the same
  operator. It proves the comparison logic and nothing about the
  anchor itself being safe. An attacker who can rewrite the
  registry AND the anchor file defeats an UNSIGNED anchor
  completely; against a SIGNED anchor (experiment 019) the same
  attacker still cannot forge a signature or a history entry
  without the signer's secret - but they can DELETE the anchor
  or roll the file back to an older signed state (the check
  pins a head, and an old signed anchor still verifies as a
  signature while failing as a head match), and whoever holds
  the signer's secret can re-anchor anything. Production needs
  the head written somewhere the registry's writer cannot
  reach: a witnessed ledger, a second operator's countersignature,
  a public append-only log. None of that is built or claimed here.
  (Superseded in part by experiment 029: a second operator's
  countersignature - the witness of case 11, above - IS built
  at prototype scale, and a witness-configured check refuses
  an anchor the witness did not countersign; the witnessed
  ledger and the public append-only log remain unbuilt, and
  the countersignature inherits this module's symmetric limit
  in full.)
- Anchor signing inherits Gate 1's signing limit in full: the
  "signature" is HMAC with a shared secret the channel itself
  issued, so verification REQUIRES the secret - a verifier
  holding it can also forge - and the channel could forge any
  signer's anchor. There is no witnessing, no asymmetric key,
  and no identity proofing. Production signing is still
  production work, out of scope here.
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


# -- anchor signing and kept history (experiment 019) -----------------------
#
# The signing scheme is Gate 1's, reused: HMAC-SHA256 with an
# enrolled identity's secret, over a domain-separated canonical
# encoding - the same construction as registration.py's
# sign_library / sign_revocation, with its own prefix so no
# signature can be replayed across purposes.


def canonical_anchor_bytes(
    signer: str,
    chain_head: str,
    entry_count: int,
    anchor_seq: int,
    prev_history_hash: str,
) -> bytes:
    """Deterministic encoding of one anchor (re-)signing act:
    who signs, what head, at which history sequence, chained to
    which previous history entry."""
    return (
        f"PBA-ANCHOR-SIGN-v1|registry|{signer}|{anchor_seq}|"
        f"{chain_head}|{entry_count}|{prev_history_hash}"
    ).encode("utf-8")


def sign_anchor(
    secret_hex: str,
    signer: str,
    chain_head: str,
    entry_count: int,
    anchor_seq: int,
    prev_history_hash: str,
) -> str:
    """Prototype anchor 'signature': HMAC-SHA256 over the
    canonical anchor bytes, with the signer's enrollment secret.

    See the module docstring for why this is NOT production
    signing (symmetric, channel-issued secrets)."""
    return hmac.new(
        bytes.fromhex(secret_hex),
        canonical_anchor_bytes(
            signer, chain_head, entry_count, anchor_seq, prev_history_hash
        ),
        hashlib.sha256,
    ).hexdigest()


# -- witness countersignatures (experiment 029) ------------------------------
#
# The witness countersigns the SIGNED anchor: the same Gate 1
# HMAC scheme, the signer's own enrollment-secret discipline,
# with its own prefix so a countersignature can never be
# replayed as an anchor signature or the other way round. The
# encoding covers the signer's signature itself, so the
# witness attests to this signed anchor, not merely to a head.


def canonical_anchor_witness_bytes(
    witness: str,
    signer: str,
    signature: str,
    chain_head: str,
    entry_count: int,
    anchor_seq: int,
    prev_history_hash: str,
) -> bytes:
    """Deterministic encoding of one anchor countersigning
    act: who witnesses, whose signed anchor (signer and that
    anchor's signature), what head, at which history
    sequence, chained to which previous history entry."""
    return (
        f"PBA-ANCHOR-WITNESS-v1|registry|{witness}|{signer}|{anchor_seq}|"
        f"{chain_head}|{entry_count}|{prev_history_hash}|{signature}"
    ).encode("utf-8")


def countersign_anchor(
    secret_hex: str,
    witness: str,
    signer: str,
    signature: str,
    chain_head: str,
    entry_count: int,
    anchor_seq: int,
    prev_history_hash: str,
) -> str:
    """Prototype anchor countersignature: HMAC-SHA256 over
    the canonical witness bytes, with the witness's
    enrollment secret. See the module docstring for why this
    is NOT production signing (symmetric, channel-issued
    secrets) - it moves the custody fence from one secret to
    two; it does not change the kind of secret."""
    return hmac.new(
        bytes.fromhex(secret_hex),
        canonical_anchor_witness_bytes(
            witness, signer, signature, chain_head, entry_count,
            anchor_seq, prev_history_hash,
        ),
        hashlib.sha256,
    ).hexdigest()


# -- signer rotation records (experiment 038) ------------------------------
#
# A rotation record is the succession instrument case 10 did
# not have: the current signer signs a statement handing
# anchor authority to a successor at a stated point, and the
# successor countersigns the same statement accepting it -
# the same two-signature discipline as the witness of
# experiment 029, with its own pair of domain-separated
# prefixes, so neither half can be replayed as an anchor
# signature, a witness countersignature, or the other half.

ROTATION_FORMAT = "PBA-ANCHOR-ROTATION-v1"


def canonical_anchor_rotation_bytes(
    from_signer: str,
    to_signer: str,
    rotation_seq: int,
    effective_anchor_seq: int,
    effective_chain_head: str,
    effective_entry_count: int,
) -> bytes:
    """Deterministic encoding of one rotation statement:
    who hands over, who accepts, which rotation this is, and
    the point it takes effect at (the history sequence of the
    successor's first entry, with the head and count that
    entry anchors)."""
    return (
        f"PBA-ANCHOR-ROTATE-v1|registry|{from_signer}|{to_signer}|"
        f"{rotation_seq}|{effective_anchor_seq}|"
        f"{effective_chain_head}|{effective_entry_count}"
    ).encode("utf-8")


def sign_anchor_rotation(
    secret_hex: str,
    from_signer: str,
    to_signer: str,
    rotation_seq: int,
    effective_anchor_seq: int,
    effective_chain_head: str,
    effective_entry_count: int,
) -> str:
    """The current signer's signature over a rotation
    statement: HMAC-SHA256 with the current signer's
    enrollment secret, Gate 1's scheme as everywhere here."""
    return hmac.new(
        bytes.fromhex(secret_hex),
        canonical_anchor_rotation_bytes(
            from_signer, to_signer, rotation_seq,
            effective_anchor_seq, effective_chain_head,
            effective_entry_count,
        ),
        hashlib.sha256,
    ).hexdigest()


def canonical_anchor_rotation_accept_bytes(
    to_signer: str,
    from_signer: str,
    rotation_seq: int,
    effective_anchor_seq: int,
    effective_chain_head: str,
    effective_entry_count: int,
    signature: str,
) -> bytes:
    """Deterministic encoding of the successor's acceptance:
    the same statement, plus the current signer's signature
    over it - the successor countersigns THIS signed
    statement, not merely the idea of one."""
    return (
        f"PBA-ANCHOR-ROTATE-ACCEPT-v1|registry|{to_signer}|"
        f"{from_signer}|{rotation_seq}|{effective_anchor_seq}|"
        f"{effective_chain_head}|{effective_entry_count}|{signature}"
    ).encode("utf-8")


def countersign_anchor_rotation(
    secret_hex: str,
    to_signer: str,
    from_signer: str,
    rotation_seq: int,
    effective_anchor_seq: int,
    effective_chain_head: str,
    effective_entry_count: int,
    signature: str,
) -> str:
    """The successor's countersignature over a rotation
    statement: HMAC-SHA256 with the successor's enrollment
    secret."""
    return hmac.new(
        bytes.fromhex(secret_hex),
        canonical_anchor_rotation_accept_bytes(
            to_signer, from_signer, rotation_seq,
            effective_anchor_seq, effective_chain_head,
            effective_entry_count, signature,
        ),
        hashlib.sha256,
    ).hexdigest()


def make_anchor_rotation(
    *,
    from_signer: str,
    from_secret_hex: str,
    to_signer: str,
    to_secret_hex: str,
    rotation_seq: int,
    effective_anchor_seq: int,
    effective_chain_head: str,
    effective_entry_count: int,
) -> dict:
    """Build a complete rotation record: the current signer
    signs the statement and the successor countersigns it
    accepting. Both secrets are required - a record with
    only one signature is not built here, and a record
    presented with one missing or not verifying is refused
    anchor_rotation_invalid wherever it is judged."""
    signature = sign_anchor_rotation(
        from_secret_hex, from_signer, to_signer, rotation_seq,
        effective_anchor_seq, effective_chain_head,
        effective_entry_count,
    )
    return {
        "format": ROTATION_FORMAT,
        "chain": "registry",
        "rotation_seq": rotation_seq,
        "from_signer": from_signer,
        "to_signer": to_signer,
        "effective_anchor_seq": effective_anchor_seq,
        "effective_chain_head": effective_chain_head,
        "effective_entry_count": effective_entry_count,
        "signature": signature,
        "countersignature": countersign_anchor_rotation(
            to_secret_hex, to_signer, from_signer, rotation_seq,
            effective_anchor_seq, effective_chain_head,
            effective_entry_count, signature,
        ),
    }


def _expected_signers(history: list[dict],
                      rotations: list[dict]) -> list:
    """The signer expected at each history sequence, under a
    rotation chain already judged for its signer links: the
    first entry's signer up to the first rotation point, then
    each rotation's successor from its point on."""
    base = history[0].get("signer") if history else None
    expected = []
    for seq in range(len(history)):
        signer = base
        for rot in rotations:
            eff = rot.get("effective_anchor_seq")
            if isinstance(eff, int) and seq >= eff:
                signer = rot.get("to_signer")
        expected.append(signer)
    return expected


def _validate_rotations(
    history: list[dict],
    rotations: list[dict],
    signer_secrets: dict | None,
) -> tuple[str | None, str | None]:
    """Judge a rotation chain against a kept history.

    Returns (refusal_status, current_signer): refusal_status
    is None when the chain stands, and current_signer is then
    the signer current at the history's tip. The judgements,
    in order: sequence order and effective points inside the
    history (anchor_rotation_invalid, or
    anchor_rotation_mismatch for a point past the record);
    each rotation's from_signer is the signer current just
    before its point (anchor_rotation_signer_invalid); its
    signature and the successor's countersignature verify
    under the lineage secrets (anchor_rotation_invalid - a
    lineage identity with no secret in the map included: in
    this model an unverifiable rotation is not a rotation);
    the history entry at its point carries exactly the
    stated head and count (anchor_rotation_mismatch); and
    every history entry is signed by the signer expected at
    its sequence - a superseded signer signing at or after
    its rotation point is anchor_signer_retired, and any
    other signer is a signature failure named in the
    signature judgement's own name."""
    if not rotations:
        return None, (history[-1].get("signer") if history else None)
    if not history:
        return "anchor_rotation_invalid", None
    for i, rot in enumerate(rotations):
        if rot.get("rotation_seq") != i:
            return "anchor_rotation_invalid", None
    effs = [rot.get("effective_anchor_seq") for rot in rotations]
    if any(not isinstance(e, int) or e < 1 for e in effs):
        return "anchor_rotation_invalid", None
    if any(e > len(history) for e in effs):
        return "anchor_rotation_mismatch", None
    if effs != sorted(effs) or len(set(effs)) != len(effs):
        return "anchor_rotation_invalid", None
    base = history[0].get("signer")
    for i, rot in enumerate(rotations):
        expected_from = base if i == 0 else rotations[i - 1].get("to_signer")
        if rot.get("from_signer") != expected_from:
            return "anchor_rotation_signer_invalid", None
        if not rot.get("to_signer") or rot.get("to_signer") == expected_from:
            return "anchor_rotation_invalid", None
        if signer_secrets is not None:
            from_secret = signer_secrets.get(str(rot.get("from_signer")))
            to_secret = signer_secrets.get(str(rot.get("to_signer")))
            if from_secret is None or to_secret is None:
                return "anchor_rotation_invalid", None
            expected_sig = sign_anchor_rotation(
                from_secret, str(rot["from_signer"]),
                str(rot["to_signer"]), i,
                int(rot["effective_anchor_seq"]),
                str(rot.get("effective_chain_head")),
                int(rot.get("effective_entry_count", -1)),
            )
            if not hmac.compare_digest(
                    expected_sig, rot.get("signature") or ""):
                return "anchor_rotation_invalid", None
            if not rot.get("countersignature"):
                return "anchor_rotation_invalid", None
            expected_counter = countersign_anchor_rotation(
                to_secret, str(rot["to_signer"]),
                str(rot["from_signer"]), i,
                int(rot["effective_anchor_seq"]),
                str(rot.get("effective_chain_head")),
                int(rot.get("effective_entry_count", -1)),
                expected_sig,
            )
            if not hmac.compare_digest(
                    expected_counter, rot.get("countersignature") or ""):
                return "anchor_rotation_invalid", None
        eff = int(rot["effective_anchor_seq"])
        if eff < len(history):
            entry = history[eff]
            if (entry.get("chain_head") != rot.get("effective_chain_head")
                    or entry.get("entry_count")
                    != rot.get("effective_entry_count")):
                return "anchor_rotation_mismatch", None
    expected = _expected_signers(history, rotations)
    lineage = {base}
    for rot in rotations:
        lineage.add(rot.get("from_signer"))
        lineage.add(rot.get("to_signer"))
    for seq, entry in enumerate(history):
        if entry.get("signer") != expected[seq]:
            if entry.get("signer") in lineage:
                return "anchor_signer_retired", None
            return "anchor_signature_invalid", None
    return None, expected[-1]


def rotation_refusal(
    record: "AnchorRecord",
    signer_secrets: dict | None,
    root_signer: str | None = None,
) -> str | None:
    """The rotation judgement over one anchor record, for
    callers that judge a delivered anchor before pairing it
    (experiment 038; node_server's wire judgement): None when
    the record carries no rotations, or when its rotation
    chain stands under the lineage secrets; otherwise the
    refusal's name. root_signer, when given, must be the
    rotation chain's first signer - the lineage's root the
    caller was configured with."""
    rotations = [dict(r) for r in record.rotations]
    if not rotations:
        return None
    if signer_secrets is None:
        return "anchor_rotation_invalid"
    history = [dict(e) for e in record.history]
    if root_signer is not None and history \
            and history[0].get("signer") != root_signer:
        return "anchor_rotation_signer_invalid"
    status, _current = _validate_rotations(
        history, rotations, signer_secrets)
    return status


def _history_verifies_rotated(
    history: list[dict],
    rotations: list[dict],
    signer_secrets: dict | None,
) -> bool:
    """Segment-by-segment verification of a rotated kept
    history: the hash chain recomputes as in case 10, and
    every entry's signature verifies under the secret of the
    signer expected at its sequence. A lineage identity with
    no secret in the map makes the history unverifiable, not
    partially verified."""
    if not history:
        return False
    if not _verify_history_entries(history):
        return False
    if signer_secrets is None:
        return False
    expected = _expected_signers(history, rotations)
    for seq, entry in enumerate(history):
        if entry.get("signer") != expected[seq]:
            return False
        secret = signer_secrets.get(str(expected[seq]))
        if secret is None:
            return False
        expected_sig = sign_anchor(
            secret, str(entry.get("signer")),
            str(entry.get("chain_head")),
            int(entry.get("entry_count", -1)), seq,
            str(entry.get("prev_hash")),
        )
        if not hmac.compare_digest(
                expected_sig, entry.get("signature") or ""):
            return False
    return True


def _signature_verifies_rotated(
    record: "AnchorRecord",
    signer_secrets: dict | None,
    root_signer: str | None = None,
) -> bool:
    """True only if a ROTATED record's segmented history
    verifies and its top fields are exactly the history's
    last entry, as _signature_verifies requires of a
    single-signer record. root_signer, when given, must be
    the lineage's root (the first history entry's signer):
    the configured signer names the root once rotations are
    in play, and the current signer is derived from the
    validated rotation chain, not asserted by the record."""
    if record.signer is None or record.signature is None \
            or record.anchor_seq is None:
        return False
    history = [dict(e) for e in record.history]
    if not history or len(history) != record.anchor_seq + 1:
        return False
    if root_signer is not None \
            and history[0].get("signer") != root_signer:
        return False
    rotations = [dict(r) for r in record.rotations]
    status, current = _validate_rotations(
        history, rotations, signer_secrets)
    if status is not None:
        return False
    if not _history_verifies_rotated(history, rotations, signer_secrets):
        return False
    last = history[-1]
    if last["chain_head"] != record.chain_head:
        return False
    if last["entry_count"] != record.entry_count:
        return False
    if record.signer != current or record.signer != last["signer"]:
        return False
    return hmac.compare_digest(last["signature"], record.signature)


def _history_entry(
    signer: str,
    secret_hex: str,
    seq: int,
    head: str,
    count: int,
    prev_hash: str,
    witness: str | None = None,
    witness_secret_hex: str | None = None,
) -> dict:
    """One kept-history entry, in the registry chain's own style:
    the signature is part of the hashed body, so editing any
    field of an old entry - head, count, signer, or the
    signature itself - breaks its entry hash. With a witness
    (experiment 029), the countersignature is part of the
    body the same way: the entry records its witness and the
    countersignature, and editing either breaks the hash
    exactly as editing the signer's fields does. An
    unwitnessed write produces exactly the experiment 019
    entry - no witness keys at all."""
    signature = sign_anchor(secret_hex, signer, head, count, seq, prev_hash)
    body = {
        "seq": seq,
        "chain_head": head,
        "entry_count": count,
        "signer": signer,
        "prev_hash": prev_hash,
        "signature": signature,
    }
    if witness is not None:
        body["witness"] = witness
        body["witness_signature"] = countersign_anchor(
            witness_secret_hex, witness, signer, signature,
            head, count, seq, prev_hash,
        )
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
    """Recompute a kept history: sequences 0..n-1 in order,
    prev-hash linkage from genesis, entry-hash recomputation.
    With a secret, every entry's signature must also verify
    under it; with a signer, every entry's signer must be that
    signer (the same-identity rule makes a genuine history
    single-signer)."""
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
            expected_sig = sign_anchor(
                secret_hex,
                str(entry.get("signer")),
                str(entry.get("chain_head")),
                int(entry.get("entry_count", -1)),
                expected_seq,
                prev_hash,
            )
            if not hmac.compare_digest(expected_sig, entry.get("signature", "")):
                return False
        prev_hash = entry["entry_hash"]
    return True


def anchor_audit_path(anchor_path: str | Path) -> Path:
    """The anchor audit log: a JSON-lines file beside the anchor,
    the same style as Gate 1's audit log. Refused re-anchor
    attempts are appended here; the anchor file itself is never
    touched by a refusal."""
    anchor_path = Path(anchor_path)
    return anchor_path.parent / (anchor_path.name + ".audit.jsonl")


def _append_anchor_audit(anchor_path: Path, event: dict) -> None:
    path = anchor_audit_path(anchor_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, sort_keys=True) + "\n")


def read_anchor_audit(anchor_path: str | Path) -> list[dict]:
    """The anchor audit events beside an anchor ([] if none)."""
    path = anchor_audit_path(anchor_path)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def _signature_verifies(record: "AnchorRecord", signer: str, secret_hex: str,
                        signer_secrets: dict | None = None) -> bool:
    """True only if a record is a genuine signed anchor BY the
    configured signer: the signer matches, a kept history is
    present and verifies (chain and signatures), the top fields
    are exactly the history's last entry, and the top signature
    is that entry's signature, recomputed.

    Experiment 038: a record carrying rotations, judged with
    the lineage secrets, verifies segment by segment instead
    (see _signature_verifies_rotated); `signer` then names
    the lineage's root, not the tip signer."""
    if signer_secrets is not None and record.rotations:
        return _signature_verifies_rotated(
            record, signer_secrets, root_signer=signer)
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
    if last["chain_head"] != record.chain_head:
        return False
    if last["entry_count"] != record.entry_count:
        return False
    if not hmac.compare_digest(last["signature"], record.signature):
        return False
    expected = sign_anchor(
        secret_hex,
        signer,
        record.chain_head,
        record.entry_count,
        record.anchor_seq,
        last["prev_hash"],
    )
    return hmac.compare_digest(expected, record.signature)


def _witness_verifies(record: "AnchorRecord", witness: str,
                      witness_secret_hex: str) -> bool:
    """True only if a record is a genuinely WITNESSED anchor
    BY the configured witness (experiment 029): the recorded
    witness is the configured one, a kept history is present
    and its hash chain verifies (sequences, linkage, entry
    hashes - the part of history verification that needs no
    secret), the top fields are the history's last entry in
    head, count, and witness fields, and the top
    countersignature recomputes under the witness's secret
    over the signer's signature and the anchor content.

    The requirement binds the anchor's CURRENT write: older
    entries' countersignatures ride inside their hashed
    bodies as tamper-evident record and are not individually
    re-verified here - case 11 states that limit plainly.
    This check is independent of the signer's secret: a
    witness-only configuration verifies the countersignature
    and the history chain without verifying the signer's
    per-entry signatures, exactly as a signer-only
    configuration verifies the signature without the witness.
    """
    if record.witness is None or record.witness != witness:
        return False
    if record.witness_signature is None or record.anchor_seq is None:
        return False
    if record.signer is None or record.signature is None:
        return False
    history = [dict(e) for e in record.history]
    if not history or len(history) != record.anchor_seq + 1:
        return False
    if not _verify_history_entries(history):
        return False
    last = history[-1]
    if last.get("witness") != witness:
        return False
    if last["chain_head"] != record.chain_head:
        return False
    if last["entry_count"] != record.entry_count:
        return False
    if not hmac.compare_digest(
            last.get("witness_signature") or "", record.witness_signature):
        return False
    expected = countersign_anchor(
        witness_secret_hex,
        witness,
        record.signer,
        record.signature,
        record.chain_head,
        record.entry_count,
        record.anchor_seq,
        last["prev_hash"],
    )
    return hmac.compare_digest(expected, record.witness_signature)


@dataclass(frozen=True)
class AnchorRecord:
    chain_head: str
    entry_count: int
    # Experiment 019, signed anchors: all None / empty for an
    # unsigned anchor, exactly as experiments 012-018 wrote them.
    signer: str | None = None
    signature: str | None = None
    anchor_seq: int | None = None
    history: tuple = ()
    # Experiment 029, witnessed anchors: both None for an
    # anchor nobody countersigned, exactly as experiment 019
    # wrote it.
    witness: str | None = None
    witness_signature: str | None = None
    # Experiment 038, signer rotation: the rotation records
    # this anchor's signer succession stands on, in rotation
    # sequence order. Empty for an anchor that never rotated,
    # exactly as experiments 019/029 wrote it - the key is
    # omitted from the file entirely, not written empty.
    rotations: tuple = ()

    def to_dict(self) -> dict:
        data = {
            "format": ANCHOR_FORMAT,
            "chain": "registry",
            "chain_head": self.chain_head,
            "entry_count": self.entry_count,
        }
        if self.signer is not None:
            data["signer"] = self.signer
            data["signature"] = self.signature
            data["anchor_seq"] = self.anchor_seq
            data["history"] = [dict(e) for e in self.history]
            if self.witness is not None:
                data["witness"] = self.witness
                data["witness_signature"] = self.witness_signature
            if self.rotations:
                data["rotations"] = [dict(r) for r in self.rotations]
        return data


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
    # Experiment 019: signature_checked is False (and
    # signature_valid None) when no signing identity was
    # configured for the check - the pre-019 judgement, by head
    # alone. When an identity IS configured, signature_valid must
    # also be True for ok, and its failure is its own status,
    # anchor_signature_invalid, ahead of anchor_mismatch: a head
    # edited after signing is a signature failure, not a head
    # finding.
    signature_checked: bool = False
    signature_valid: bool | None = None
    signer: str | None = None
    # Experiment 029: witness_checked is False (and
    # witness_valid None) when no witness identity was
    # configured for the check - the experiment 019 judgement.
    # When a witness IS configured, witness_valid must also be
    # True for ok, and its failure is its own status,
    # anchor_witness_invalid, AFTER anchor_signature_invalid
    # (the signature judgement precedes the witness judgement)
    # and ahead of anchor_mismatch.
    witness_checked: bool = False
    witness_valid: bool | None = None
    witness: str | None = None
    # Experiment 038: rotation_checked is False (and
    # rotation_valid / rotation_status / current_signer None)
    # when no lineage configuration was given for the check -
    # the pre-038 judgement. When a lineage IS configured,
    # rotation_valid must also be True for ok, and its failure
    # is reported under rotation_status (one of
    # anchor_signer_retired, anchor_rotation_signer_invalid,
    # anchor_rotation_invalid, anchor_rotation_mismatch, or
    # anchor_signature_invalid for a history entry signed by
    # an identity outside the lineage), AFTER
    # internal_verification_failed and BEFORE
    # anchor_signature_invalid: retired is the fact about
    # such an anchor, ahead of how its signatures verify.
    rotation_checked: bool = False
    rotation_valid: bool | None = None
    rotation_status: str | None = None
    current_signer: str | None = None

    @property
    def ok(self) -> bool:
        base = self.internal_verifies and self.head_matches and self.count_matches
        if self.rotation_checked and not self.rotation_valid:
            return False
        if self.signature_checked and not self.signature_valid:
            return False
        if self.witness_checked and not self.witness_valid:
            return False
        return base

    @property
    def status(self) -> str:
        if not self.internal_verifies:
            return "internal_verification_failed"
        if self.rotation_checked and not self.rotation_valid:
            return self.rotation_status or "anchor_rotation_invalid"
        if self.signature_checked and not self.signature_valid:
            return "anchor_signature_invalid"
        if self.witness_checked and not self.witness_valid:
            return "anchor_witness_invalid"
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
            "signature_checked": self.signature_checked,
            "signature_valid": self.signature_valid,
            "signer": self.signer,
            "witness_checked": self.witness_checked,
            "witness_valid": self.witness_valid,
            "witness": self.witness,
            "rotation_checked": self.rotation_checked,
            "rotation_valid": self.rotation_valid,
            "rotation_status": self.rotation_status,
            "current_signer": self.current_signer,
            "ok": self.ok,
            "status": self.status,
        }


def _judge_rotation_write(
    existing: "AnchorRecord",
    rotation,
    signer: str,
    secret_hex: str,
    predecessor_secret_hex: str | None,
    signer_secrets: dict | None,
    head: str,
    count: int,
) -> str | None:
    """Judge a rotation presented at the successor's first
    write (experiment 038). Returns the refusal's name, or
    None when the rotation stands for THIS write. The order:
    the signer link first (the rotation's from_signer is the
    anchor's current signer and its to_signer is the
    presenting writer - anchor_rotation_signer_invalid); the
    existing anchor itself verifies (segment by segment when
    it already carries rotations - anchor_rotation_invalid);
    the rotation's sequence is the next one
    (anchor_rotation_invalid); its stated point is this write
    (anchor_rotation_mismatch); and both of its signatures
    verify - the current signer's under
    predecessor_secret_hex, the successor's countersignature
    under the writer's own secret (anchor_rotation_invalid, a
    missing countersignature included)."""
    if not isinstance(rotation, dict):
        return "anchor_rotation_invalid"
    if rotation.get("from_signer") != existing.signer \
            or rotation.get("to_signer") != signer:
        return "anchor_rotation_signer_invalid"
    if existing.rotations:
        if signer_secrets is None:
            return "anchor_rotation_invalid"
        root = existing.history[0].get("signer") \
            if existing.history else None
        if not _signature_verifies_rotated(
                existing, signer_secrets, root_signer=root):
            return "anchor_rotation_invalid"
    else:
        if predecessor_secret_hex is None or not _signature_verifies(
                existing, str(existing.signer),
                predecessor_secret_hex):
            return "anchor_rotation_invalid"
    if rotation.get("rotation_seq") != len(existing.rotations):
        return "anchor_rotation_invalid"
    if rotation.get("effective_anchor_seq") != len(existing.history):
        return "anchor_rotation_mismatch"
    if rotation.get("effective_chain_head") != head \
            or rotation.get("effective_entry_count") != count:
        return "anchor_rotation_mismatch"
    if predecessor_secret_hex is None:
        return "anchor_rotation_invalid"
    expected_sig = sign_anchor_rotation(
        predecessor_secret_hex, str(existing.signer), signer,
        int(rotation["rotation_seq"]),
        int(rotation["effective_anchor_seq"]), head, count,
    )
    if not hmac.compare_digest(
            expected_sig, rotation.get("signature") or ""):
        return "anchor_rotation_invalid"
    if not rotation.get("countersignature"):
        return "anchor_rotation_invalid"
    expected_counter = countersign_anchor_rotation(
        secret_hex, signer, str(existing.signer),
        int(rotation["rotation_seq"]),
        int(rotation["effective_anchor_seq"]), head, count,
        expected_sig,
    )
    if not hmac.compare_digest(
            expected_counter, rotation.get("countersignature") or ""):
        return "anchor_rotation_invalid"
    return None


def write_anchor(
    registry_log_path: str | Path,
    anchor_path: str | Path,
    *,
    signer: str | None = None,
    secret_hex: str | None = None,
    witness: str | None = None,
    witness_secret_hex: str | None = None,
    rotation: dict | None = None,
    predecessor_secret_hex: str | None = None,
    signer_secrets: dict | None = None,
) -> AnchorRecord:
    """Write the registry's current chain head to an anchor file
    outside the registry's own directory.

    The registry is verified internally before its head is anchored:
    anchoring a chain that does not verify on its own raises
    AnchorError and writes nothing. An anchor path inside the
    registry file's directory is refused for the same reason a
    self-witnessed record proves nothing.

    Unsigned (no signer): exactly the experiment 012 behavior -
    the anchor file is overwritten with the current head.

    Signed (experiment 019, signer + secret_hex both given): the
    anchor is signed by that enrolled identity, under the
    re-anchor rule stated in the module docstring. Re-anchoring
    a signed anchor by a DIFFERENT identity, or over a signed
    anchor whose existing signature/history does not verify
    under the presented secret, is refused (AnchorError), the
    anchor file is left unchanged, and the refusal is appended
    to the anchor audit log. An accepted signed write appends
    one entry to the kept history inside the anchor file.

    Witnessed (experiment 029, witness + witness_secret_hex
    both given, alongside a signer): the new history entry is
    countersigned by that second enrolled identity. A witness
    equal to the signer is refused (AnchorError): a
    countersignature by the signer's own identity is not a
    second custody. The witness arguments change nothing about
    the re-anchor rule - succession is judged on the signer
    alone, exactly as in experiment 019, so a different signer
    is refused however genuine the countersignature is.

    Rotated (experiment 038, rotation given): this write is
    the successor's first write over an anchor signed by the
    rotation's from_signer, and the rotation record - judged
    by _judge_rotation_write under its own refusal names,
    refusals audit-logged as rotation_rejected - is what
    makes a different signer legitimate here, the one
    succession instrument case 10 did not have. On acceptance
    the record is appended to the anchor's rotations list.
    predecessor_secret_hex is the outgoing signer's secret,
    present at the handover so the write can verify the
    existing anchor and the rotation statement it acts on.
    Writing over an anchor that ALREADY carries rotations
    (with or without a new rotation) requires signer_secrets,
    the lineage map, so the segmented history is verified.
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
    head = chain_head(entries)
    count = len(entries)

    if signer is None:
        if secret_hex is not None:
            raise AnchorError("secret_hex given without a signer")
        if witness is not None or witness_secret_hex is not None:
            raise AnchorError(
                "a witness countersigns a SIGNED anchor: witness "
                "given without a signer (experiment 029)"
            )
        record = AnchorRecord(chain_head=head, entry_count=count)
        anchor_path.parent.mkdir(parents=True, exist_ok=True)
        anchor_path.write_text(
            json.dumps(record.to_dict(), sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return record

    if secret_hex is None:
        raise AnchorError("a signed anchor needs the signer's secret_hex")

    if (witness is None) != (witness_secret_hex is None):
        raise AnchorError(
            "witness and witness_secret_hex must be given together "
            "(a witness is an id plus its verification secret)"
        )
    if witness is not None and witness == signer:
        raise AnchorError(
            "a witness must be a second identity, distinct from "
            "the signer: a countersignature by the signer's own "
            "identity is not a second custody (experiment 029)"
        )

    history: list[dict] = []
    rotations_kept: list[dict] = []
    if anchor_path.exists():
        existing = read_anchor(anchor_path)
        if existing.signer is not None:
            if rotation is not None:
                # Experiment 038: the successor's first write,
                # presenting the rotation record. Judged under
                # its own names, refused audit-logged as
                # rotation_rejected; the module docstring's
                # rotation section states the order.
                status = _judge_rotation_write(
                    existing, rotation, signer, secret_hex,
                    predecessor_secret_hex, signer_secrets,
                    head, count,
                )
                if status is not None:
                    _append_anchor_audit(
                        anchor_path,
                        {
                            "event": "rotation_rejected",
                            "reason": status,
                            "attempted_signer": signer,
                            "current_signer": existing.signer,
                            "chain": "registry",
                            "chain_head": head,
                            "entry_count": count,
                        },
                    )
                    raise AnchorError(
                        f"rotation refused: {status} - the "
                        f"presented rotation does not stand for "
                        f"this write (experiment 038, case 12)"
                    )
                history = [dict(e) for e in existing.history]
                rotations_kept = [dict(r) for r in existing.rotations]
                rotations_kept.append(dict(rotation))
            elif existing.rotations:
                # Experiment 038: writing over a ROTATED anchor
                # requires the lineage secrets, so the segmented
                # history is verified segment by segment rather
                # than partially. Succession is still the tip
                # signer's identity alone.
                if existing.signer != signer:
                    _append_anchor_audit(
                        anchor_path,
                        {
                            "event": "reanchor_rejected",
                            "reason": "different_signer",
                            "attempted_signer": signer,
                            "current_signer": existing.signer,
                            "chain": "registry",
                            "chain_head": head,
                            "entry_count": count,
                        },
                    )
                    raise AnchorError(
                        f"re-anchor refused: anchor is signed by "
                        f"{existing.signer}, not by {signer} "
                        f"(same-identity rule, experiment 019)"
                    )
                if signer_secrets is None:
                    raise AnchorError(
                        "anchor_rotation_invalid: re-anchoring a "
                        "rotated anchor needs the lineage secrets "
                        "(signer_secrets), so its segmented history "
                        "can be verified (experiment 038)"
                    )
                root = existing.history[0].get("signer") \
                    if existing.history else None
                if not _signature_verifies_rotated(
                        existing, signer_secrets, root_signer=root):
                    _append_anchor_audit(
                        anchor_path,
                        {
                            "event": "reanchor_rejected",
                            "reason": "existing_signature_invalid",
                            "attempted_signer": signer,
                            "current_signer": existing.signer,
                            "chain": "registry",
                            "chain_head": head,
                            "entry_count": count,
                        },
                    )
                    raise AnchorError(
                        "re-anchor refused: the existing rotated "
                        "anchor does not verify under the "
                        "presented lineage secrets"
                    )
                history = [dict(e) for e in existing.history]
                rotations_kept = [dict(r) for r in existing.rotations]
            else:
                if existing.signer != signer:
                    _append_anchor_audit(
                        anchor_path,
                        {
                            "event": "reanchor_rejected",
                            "reason": "different_signer",
                            "attempted_signer": signer,
                            "current_signer": existing.signer,
                            "chain": "registry",
                            "chain_head": head,
                            "entry_count": count,
                        },
                    )
                    raise AnchorError(
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
                            "chain": "registry",
                            "chain_head": head,
                            "entry_count": count,
                        },
                    )
                    raise AnchorError(
                        "re-anchor refused: the existing signed anchor does "
                        "not verify under the presented secret"
                    )
                history = [dict(e) for e in existing.history]
        elif rotation is not None:
            raise AnchorError(
                "anchor_rotation_invalid: the existing anchor is "
                "unsigned - there is no signer to rotate from "
                "(experiment 038)"
            )
        # An existing UNSIGNED anchor has no signer to match: a
        # first signed write establishes the signer and starts
        # the history (the stated exception in case 10).
    elif rotation is not None:
        raise AnchorError(
            "anchor_rotation_invalid: no anchor exists here - "
            "there is no signer to rotate from (experiment 038)"
        )

    seq = len(history)
    prev_hash = history[-1]["entry_hash"] if history else GENESIS_HASH
    entry = _history_entry(
        signer, secret_hex, seq, head, count, prev_hash,
        witness=witness, witness_secret_hex=witness_secret_hex,
    )
    history.append(entry)
    record = AnchorRecord(
        chain_head=head,
        entry_count=count,
        signer=signer,
        signature=entry["signature"],
        anchor_seq=seq,
        history=tuple(history),
        witness=witness,
        witness_signature=entry.get("witness_signature"),
        rotations=tuple(rotations_kept),
    )
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
        record = AnchorRecord(
            chain_head=str(data["chain_head"]), entry_count=int(data["entry_count"])
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AnchorError(f"anchor file is missing its head or count: {anchor_path}") from exc
    if data.get("signer") is not None:
        # A signed anchor (experiment 019): its signer, signature,
        # sequence, and kept history ride in the same file.
        # Reading parses them; VERIFYING them is the check's job,
        # with a configured identity.
        try:
            history = tuple(dict(e) for e in data["history"])
            record = AnchorRecord(
                chain_head=record.chain_head,
                entry_count=record.entry_count,
                signer=str(data["signer"]),
                signature=str(data["signature"]),
                anchor_seq=int(data["anchor_seq"]),
                history=history,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AnchorError(
                f"signed anchor is missing its signature or history: {anchor_path}"
            ) from exc
        if data.get("witness") is not None:
            # A witnessed anchor (experiment 029): its witness
            # and countersignature ride in the same file, parsed
            # here and verified by the check, with a configured
            # witness identity.
            try:
                record = AnchorRecord(
                    chain_head=record.chain_head,
                    entry_count=record.entry_count,
                    signer=record.signer,
                    signature=record.signature,
                    anchor_seq=record.anchor_seq,
                    history=record.history,
                    witness=str(data["witness"]),
                    witness_signature=str(data["witness_signature"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise AnchorError(
                    f"witnessed anchor is missing its countersignature: "
                    f"{anchor_path}"
                ) from exc
        if data.get("rotations") is not None:
            # A rotated anchor (experiment 038): its rotation
            # records ride in the same file, parsed here and
            # verified by the check, with a configured lineage.
            try:
                rotations = tuple(dict(r) for r in data["rotations"])
            except (TypeError, ValueError) as exc:
                raise AnchorError(
                    f"rotated anchor has malformed rotation records: "
                    f"{anchor_path}"
                ) from exc
            record = AnchorRecord(
                chain_head=record.chain_head,
                entry_count=record.entry_count,
                signer=record.signer,
                signature=record.signature,
                anchor_seq=record.anchor_seq,
                history=record.history,
                witness=record.witness,
                witness_signature=record.witness_signature,
                rotations=rotations,
            )
    return record


def verify_anchor_history(
    anchor_path: str | Path,
    signer: str | None = None,
    secret_hex: str | None = None,
    witness: str | None = None,
    witness_secret_hex: str | None = None,
    signer_secrets: dict | None = None,
    rotations: list | None = None,
) -> bool:
    """Verify an anchor file's kept history on its own: the
    hash chain recomputes (sequences, linkage, entry hashes),
    and with a secret every entry's signature verifies under
    it; with a signer every entry names that signer. An
    unsigned anchor has no history and returns False: there is
    nothing to verify is a finding, not a pass. With a witness
    (experiment 029), the anchor's countersignature must also
    verify for that witness - the current write's fence, as
    _witness_verifies states it. With rotations in play
    (experiment 038: the record's own, or a held rotations
    argument) and the lineage secrets given, the history
    verifies segment by segment under the validated rotation
    chain instead - signer then names the lineage's root."""
    record = read_anchor(anchor_path)
    if record.signer is None or not record.history:
        return False
    history = [dict(e) for e in record.history]
    effective_rotations = (
        [dict(r) for r in rotations] if rotations is not None
        else [dict(r) for r in record.rotations]
    )
    if effective_rotations:
        if signer_secrets is None:
            return False
        status, _current = _validate_rotations(
            history, effective_rotations, signer_secrets)
        if status is not None:
            return False
        if signer is not None and history[0].get("signer") != signer:
            return False
        if not _history_verifies_rotated(
                history, effective_rotations, signer_secrets):
            return False
        last = history[-1]
        tied = (
            last["chain_head"] == record.chain_head
            and last["entry_count"] == record.entry_count
            and last["seq"] == record.anchor_seq
            and hmac.compare_digest(last["signature"],
                                    record.signature or "")
        )
        if not tied:
            return False
        if witness is not None:
            if witness_secret_hex is None:
                raise AnchorError("verifying a witnessed anchor needs "
                                  "witness_secret_hex")
            return _witness_verifies(record, witness, witness_secret_hex)
        return True
    if not _verify_history_entries(history, signer=signer, secret_hex=secret_hex):
        return False
    # The history must also BE the anchor: its last entry is the
    # anchor's own head, count, sequence, and signature.
    last = history[-1]
    tied = (
        last["chain_head"] == record.chain_head
        and last["entry_count"] == record.entry_count
        and last["seq"] == record.anchor_seq
        and hmac.compare_digest(last["signature"], record.signature or "")
    )
    if not tied:
        return False
    if witness is not None:
        if witness_secret_hex is None:
            raise AnchorError("verifying a witnessed anchor needs "
                              "witness_secret_hex")
        return _witness_verifies(record, witness, witness_secret_hex)
    return True


def check_registry_against_anchor(
    registry_log_path: str | Path,
    anchor_path: str | Path,
    *,
    signer: str | None = None,
    secret_hex: str | None = None,
    witness: str | None = None,
    witness_secret_hex: str | None = None,
    signer_secrets: dict | None = None,
    rotations: list | None = None,
) -> AnchorCheck:
    """Check a presented registry against an outside anchor.

    Never raises for a bad registry: a registry that fails internal
    verification, or whose head/count differ from the anchor, is
    reported in the AnchorCheck. Raises AnchorError only when the
    anchor itself cannot be read.

    With a signing identity configured (experiment 019, signer +
    secret_hex), the anchor's signature and kept history are ALSO
    verified, and a failure there is reported in the check
    (signature_checked / signature_valid / status
    anchor_signature_invalid), never a silent pass and never a
    raise: a consultation point must be able to name it. With no
    identity, the check is experiment 012's, by head alone.

    With a witness identity configured (experiment 029, witness +
    witness_secret_hex), the anchor's countersignature is ALSO
    verified, and a failure there is reported in the check
    (witness_checked / witness_valid / status
    anchor_witness_invalid), under the same discipline: named,
    never a silent pass, never a raise.

    With a lineage configured (experiment 038, signer_secrets,
    and optionally rotations the verifier HOLDS), the anchor's
    rotation chain is ALSO judged - before the signature,
    witness, and head judgements - and the signature judgement
    runs segment by segment. signer then names the lineage's
    root; secret_hex may be omitted when signer_secrets carries
    the root's secret. A held rotations list that disagrees
    with the anchor's own records is anchor_rotation_invalid.
    """
    anchor = read_anchor(anchor_path)
    entries = _read_entries(registry_log_path)
    internal = _verify_entries(entries)
    presented_head = chain_head(entries) if entries else GENESIS_HASH

    # Experiment 038: the rotation judgement, engaged by a
    # lineage configuration or by held rotation records.
    held_rotations = (
        [dict(r) for r in rotations] if rotations is not None else None
    )
    own_rotations = [dict(r) for r in anchor.rotations]
    rotation_checked = signer_secrets is not None or held_rotations is not None
    rotation_valid: bool | None = None
    rotation_status: str | None = None
    current_signer: str | None = None
    if rotation_checked:
        rotation_valid = True
        if held_rotations is not None and own_rotations \
                and held_rotations != own_rotations:
            rotation_valid = False
            rotation_status = "anchor_rotation_invalid"
        else:
            effective = own_rotations if own_rotations \
                else (held_rotations or [])
            if effective:
                if signer_secrets is None:
                    rotation_valid = False
                    rotation_status = "anchor_rotation_invalid"
                else:
                    status, current_signer = _validate_rotations(
                        [dict(e) for e in anchor.history],
                        effective, signer_secrets,
                    )
                    if status is not None:
                        rotation_valid = False
                        rotation_status = status
            else:
                current_signer = anchor.signer

    signature_checked = signer is not None or signer_secrets is not None
    signature_valid: bool | None = None
    if signature_checked:
        if secret_hex is None and signer_secrets is None:
            raise AnchorError("checking a signed anchor needs secret_hex")
        if own_rotations and signer_secrets is not None:
            # The segmented judgement: meaningful only when the
            # rotation chain itself stands.
            signature_valid = bool(
                rotation_valid
                and _signature_verifies_rotated(
                    anchor, signer_secrets, root_signer=signer)
            )
        elif signer is not None and secret_hex is not None:
            signature_valid = _signature_verifies(
                anchor, signer, secret_hex,
                signer_secrets=signer_secrets)
        elif signer_secrets is not None and anchor.signer is not None \
                and anchor.signer in signer_secrets:
            signature_valid = _signature_verifies(
                anchor, anchor.signer, signer_secrets[anchor.signer])
        else:
            signature_valid = False
    witness_checked = witness is not None
    witness_valid: bool | None = None
    if witness_checked:
        if witness_secret_hex is None:
            raise AnchorError("checking a witnessed anchor needs "
                              "witness_secret_hex")
        witness_valid = _witness_verifies(anchor, witness, witness_secret_hex)
    return AnchorCheck(
        internal_verifies=internal,
        head_matches=hmac.compare_digest(presented_head, anchor.chain_head),
        count_matches=len(entries) == anchor.entry_count,
        anchored_head=anchor.chain_head,
        presented_head=presented_head,
        anchored_count=anchor.entry_count,
        presented_count=len(entries),
        signature_checked=signature_checked,
        signature_valid=signature_valid,
        signer=anchor.signer,
        witness_checked=witness_checked,
        witness_valid=witness_valid,
        witness=anchor.witness,
        rotation_checked=rotation_checked,
        rotation_valid=rotation_valid,
        rotation_status=rotation_status,
        current_signer=current_signer,
    )


def verify_registry_against_anchor(
    registry_log_path: str | Path,
    anchor_path: str | Path,
    *,
    signer: str | None = None,
    secret_hex: str | None = None,
    witness: str | None = None,
    witness_secret_hex: str | None = None,
    signer_secrets: dict | None = None,
    rotations: list | None = None,
) -> bool:
    """True only if the presented registry verifies internally AND
    its head and length match the outside anchor exactly - and,
    when a signing identity is configured, the anchor's signature
    and history verify for that signer, and when a witness
    identity is configured, its countersignature verifies for
    that witness, and when a lineage is configured, its
    rotation chain stands and its segmented history verifies."""
    return check_registry_against_anchor(
        registry_log_path, anchor_path, signer=signer, secret_hex=secret_hex,
        witness=witness, witness_secret_hex=witness_secret_hex,
        signer_secrets=signer_secrets, rotations=rotations,
    ).ok
