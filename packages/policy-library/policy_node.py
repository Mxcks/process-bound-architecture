"""
Process-Bound Architecture - Gate 2 decentralized policy prototype.

A small, runnable prototype of Gate 2 (policy has no central home, in
practice), built on the Gate 1 registration package. Python standard
library only. No network, no database, no dependencies.

WHAT THIS PROTOTYPE DEMONSTRATES (the two Gate 2 outcomes)
----------------------------------------------------------
1. One registered policy is served from a second, independent node
   while the central registration channel is offline at runtime. The
   node holds a *copy* of the registration record (the hash-chained
   registry log written by Gate 1) plus the policy payload bytes. To
   serve, the node verifies, entirely on its own:

   - the hash chain over the copied registry entries recomputes
     exactly (same algorithm as Gate 1's verify_registry_chain, but
     implemented here against the file alone), and
   - the policy payload the node holds hashes to the code_hash in its
     registration entry, and the entry's policy_hash recomputes from
     (author, name, version, code).

   The node never receives, imports for serving, or calls the
   RegistrationChannel. Registration is how the record was *created*;
   it plays no part in serving. If the original channel and its files
   are gone, serving still works from the copy.

2. A user's own node can act as that policy source by their choice. A
   node serves only if it was constructed with serve_policies=True
   (opt-in, e.g. from the node's own config file). The default is
   False: a node that did not opt in holds the same verified data and
   still refuses to serve it.

HONEST LIMITS (prototype, NOT production)
-----------------------------------------
- "Second node" here is a second PolicyNode in the same process/machine
  over a *copied file*. There is no network transport, no discovery, no
  peer protocol, and no automatic replication: the copy is made by the
  demo/tests explicitly, the way a future sync layer (Gate 3) would
  have to move it. Nothing about availability at scale is claimed.
- Verification trusts the copied chain exactly as far as Gate 1 does:
  the chain is tamper-evident against edits to a single entry, but a
  whole-file rewrite from genesis would recompute cleanly. Detecting
  that needs an external anchor / witnessed head hash: a prototype
  anchor for this chain now exists (experiment 012,
  packages/registration/anchor.py, a stand-in file outside the
  registry directory). Since experiment 015 a node constructed with
  anchor_path consults that anchor at load and on reload (see ANCHOR
  CONSULTATION below) and refuses a rewritten snapshot; a node
  constructed WITHOUT an anchor path behaves exactly as before and
  still trusts the chain alone - that is kept, and stated, because
  the anchor is opt-in.
- The node serves policy *bytes* after verifying them. It does not
  sandbox or execute policy code (runtime package's job).

REVOCATION (test case 6, built in experiment 008)
----------------------------------------------------------
Gate 1 now appends revocations to the same hash-chained record
(event "revoked"; see packages/registration/registration.py). This
node honors them, with the stale window stated plainly:

- A snapshot that contains a policy's revocation entry makes
  serve_policy refuse that policy with PolicyRevoked, even when the
  payload verifies exactly. Registration entries and revocation
  entries are kept apart internally, so a revocation entry never
  overwrites the registration record it revokes.
- A node built over a *pre-revocation* snapshot does not know about
  the revocation and keeps serving: that is the stale window, and it
  is real. It closes when the node receives the updated record:
  reload_snapshot() re-reads (and re-verifies) a snapshot file - the
  node's own file after a sync copies the new record over it, or a
  new path - and rebuilds the node's view from it alone. After a
  successful reload the revoked policy is refused; a reload whose
  snapshot fails verification raises TamperedRecord and leaves the
  node's previous view untouched.
- There is no push or broadcast here: nothing in this prototype
  delivers the updated record to a stale node on its own (delivery
  is the synchronization layer's job, as it already is for the
  registration record itself), and a node that never receives it
  stays stale indefinitely. That is stated, not fixed over.
- Opt-in is a constructor flag / local config boolean. There is no
  authentication of the node operator and no consent record beyond the
  local config file.

ANCHOR CONSULTATION (test case 8, built in experiment 015)
------------------------------------------------------------
Experiment 012 built the external anchor and its check, and named
its own limit: nothing consulted it automatically, so a rewritten
registry would be served with the anchor never asked. A node
constructed with anchor_path now consults it, reusing experiment
012's check_registry_against_anchor (loaded from
packages/registration/anchor.py by file path, so this package gains
no import of the registration channel and no copy of the check):

- At construction, and again on reload_snapshot (the path by which
  an updated record arrives, experiment 008), the verified snapshot
  is checked against the anchor BEFORE the node's view changes.
- anchored: head and count match the anchor exactly. The node
  serves exactly as before, and node.anchor_status / node.anchor_
  check expose the consultation and its result.
- anchor_mismatch: the snapshot is not the anchored chain - in
  particular the full rewrite of experiment 012's negative control,
  which passes this module's own chain verification and is refused
  ONLY by the anchor. Construction (or the reload) raises
  AnchorMismatch and the snapshot is never served; a refused reload
  leaves the node's previous view untouched.
- anchor_stale: the snapshot GREW HONESTLY after anchoring. This is
  distinguished from a rewrite by the chain itself: the presented
  chain is longer than the anchor, and the entry at the anchored
  length still has the anchored head as its hash, so everything
  that was anchored is intact and entries were only appended.
  Honest growth is refused too - as AnchorStale, under its own
  name, never reported as a mismatch and never silently served as
  anchored - until the new head is re-anchored, which is exactly
  the lifecycle experiment 012 stated for its check (an extended
  presentation fails until re-anchored). After re-anchoring, the
  same snapshot loads and serves. A truncated snapshot is NOT
  growth (its prefix head is not the anchored head) and is
  anchor_mismatch.
- No anchor_path: no consultation happens, anchor_status is
  "no_anchor", and behavior is exactly the pre-015 behavior.
  Absence of an anchor is not an error.
- An anchor_path that was configured but cannot be read (missing
  or malformed anchor file) raises experiment 012's AnchorError at
  load: a caller who asked for an anchor is never silently served
  without one.
- The experiment 007/014 node server processes pass an anchor
  path only when started with --anchor (experiment 018), in which
  case this same consultation runs at server startup and a
  mismatch or a stale chain refuses the start; a server started
  without one consults nothing, exactly as before.

SIGNED ANCHOR CONSULTATION (test case 10, built in experiment 019)
-------------------------------------------------------------------
Experiment 019 lets the anchor itself be signed (see
packages/registration/anchor.py). A node constructed with
anchor_signer + anchor_secret_hex (the signer's enrolled
identity and its verification secret - both or neither)
verifies the anchor's signature and kept history as part of
the same consultation, before the head judgement:

- anchor_signature_invalid: the anchor is unsigned, was signed
  by a different identity, its signature was tampered with, or
  its kept history does not verify. Construction (or a reload)
  raises AnchorSignatureInvalid and the snapshot is never
  served; the name is distinct from anchor_mismatch and
  anchor_stale, and no head finding is reported for it.
- A correctly signed anchor over the genuine snapshot is
  anchored and serves exactly as before, with the check's
  signature fields recording that the signature was verified.
- No signing identity: the consultation is exactly experiment
  015's, judging a signed anchor by its head alone. The anchor
  file is still experiment 012's local stand-in, now optionally
  signed; its stated limits (a local file, symmetric HMAC
  signing where the verifier holds the secret, deletion and
  rollback to an older signed state not prevented) stand.

WITNESSED ANCHOR CONSULTATION (test case 11, experiment 029)
--------------------------------------------------------------
Experiment 029 lets the anchor be countersigned by a second
identity, a witness (see packages/registration/anchor.py). A
node constructed with anchor_witness +
anchor_witness_secret_hex (the witness's enrolled identity
and its verification secret - both or neither), in the
signer's opt-in style, verifies the anchor's countersignature
as part of the same consultation, after the signature
judgement and before the head judgement:

- anchor_witness_invalid: the anchor is not countersigned,
  was countersigned by a different identity, or its
  countersignature was tampered with. Construction (or a
  reload) raises AnchorWitnessInvalid and the snapshot is
  never served.
- A correctly countersigned anchor over the genuine snapshot
  is anchored and serves exactly as before, with the check's
  witness fields recording that the countersignature was
  verified.
- No witness identity: the consultation is exactly experiment
  019's. The countersignature inherits the symmetric HMAC
  limit in full: what it changes is custody (two enrolled
  secrets must now collude to bless a rewritten record), not
  the model.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path


GENESIS_HASH = "0" * 64


def _load_registry_anchor_module():
    """Load experiment 012's anchor check from its own file.

    Loaded by path under a private module name for two reasons: this
    node must not import the registration *channel* (its standalone
    discipline, above), and two packages each ship an anchor.py
    (registration, synchronization), so a plain `import anchor`
    could resolve to the wrong one depending on sys.path. The check
    functions themselves are experiment 012's, reused as-is:
    check_registry_against_anchor and its AnchorError.
    """
    anchor_file = Path(__file__).resolve().parent.parent / "registration" / "anchor.py"
    spec = importlib.util.spec_from_file_location("pba_registration_anchor", anchor_file)
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: the anchor module declares dataclasses,
    # which resolve their module namespace through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_library_bytes(author: str, name: str, version: str, code: bytes) -> bytes:
    """Same deterministic encoding as Gate 1 (registration.py).

    Duplicated on purpose: the node must be able to recompute a policy
    hash from a registration record + payload alone, without importing
    or calling the registration channel.
    """
    header = f"{author}\n{name}\n{version}\n{_sha256_hex(code)}\n".encode("utf-8")
    return header + code


def verify_registry_snapshot(registry_snapshot_path: str | Path) -> list[dict]:
    """Verify a copied registry log's hash chain, standalone.

    Reads the JSON-lines snapshot, checks log indices are 0..n-1 in
    order, each entry's prev_hash links to the previous entry_hash, and
    each entry_hash recomputes from the entry body. Returns the entries
    on success; raises TamperedRecord on any mismatch.

    This contacts nothing: no channel, no network, no secrets.
    """
    entries = []
    for line in Path(registry_snapshot_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))

    prev_hash = GENESIS_HASH
    for expected_index, entry in enumerate(entries):
        if entry.get("log_index") != expected_index:
            raise TamperedRecord(
                f"registry entry {expected_index}: log_index is {entry.get('log_index')}"
            )
        if entry.get("prev_hash") != prev_hash:
            raise TamperedRecord(f"registry entry {expected_index}: prev_hash link broken")
        body = {k: v for k, v in entry.items() if k != "entry_hash"}
        recomputed = _sha256_hex(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        if not hmac.compare_digest(recomputed, entry.get("entry_hash", "")):
            raise TamperedRecord(f"registry entry {expected_index}: entry_hash mismatch")
        prev_hash = entry["entry_hash"]
    return entries


class PolicyLibraryError(Exception):
    """Base class for policy-node refusals."""


class NotServing(PolicyLibraryError):
    """This node did not opt in to serving policies."""


class PolicyNotFound(PolicyLibraryError):
    """No registration entry or no payload held for this policy hash."""


class PolicyRevoked(PolicyLibraryError):
    """The policy's registration was revoked by its author (case 6).

    Raised only when this node's snapshot actually contains the
    revocation entry; a node holding a pre-revocation snapshot cannot
    raise it, which is exactly the stale window case 6 names.
    """


class TamperedRecord(PolicyLibraryError):
    """The registration record or the payload does not verify."""


class AnchorRefused(PolicyLibraryError):
    """Base for anchor-consultation refusals (experiment 015).

    Carries the experiment 012 AnchorCheck that produced the
    refusal, and a status naming which refusal this is, so a caller
    never has to parse the message to tell a rewrite from honest
    growth.
    """

    status = "anchor_refused"

    def __init__(self, message: str, check):
        super().__init__(message)
        self.check = check


class AnchorMismatch(AnchorRefused):
    """The snapshot is not the chain the anchor pins (case 8).

    Raised for a snapshot whose head/count differ from the anchor
    and which is not an honest extension of it - in particular a
    registry rewritten from genesis, which passes the chain's own
    verification and is refused only here.
    """

    status = "anchor_mismatch"


class AnchorStale(AnchorRefused):
    """The snapshot grew honestly past its anchor (case 8).

    The anchored entries are intact (the entry at the anchored
    length still carries the anchored head) and entries were only
    appended. Refused under its own name until the new head is
    re-anchored; not a tamper finding and not a silent pass.
    """

    status = "anchor_stale"


class AnchorSignatureInvalid(AnchorRefused):
    """The anchor's signature does not verify (case 10, experiment
    019).

    Raised only on a node configured with a signing identity
    (anchor_signer + anchor_secret_hex): the anchor is unsigned,
    was signed by a different identity, its signature was
    tampered with, or its kept history does not verify. Distinct
    from AnchorMismatch and AnchorStale by construction: the
    signature judgement runs before the head judgement, because
    a head edited after signing is a signature failure, not a
    head finding.
    """

    status = "anchor_signature_invalid"


class AnchorWitnessInvalid(AnchorRefused):
    """The anchor's witness countersignature does not verify
    (case 11, experiment 029).

    Raised only on a node configured with a witness identity
    (anchor_witness + anchor_witness_secret_hex): the anchor is
    not countersigned, was countersigned by a different
    identity, or its countersignature was tampered with.
    Distinct from AnchorSignatureInvalid and the head findings
    by construction: the witness judgement runs after the
    signature judgement (an anchor whose signature fails is a
    signature failure, countersigned or not) and before the
    head judgement.
    """

    status = "anchor_witness_invalid"


@dataclass(frozen=True)
class ServedPolicy:
    policy_hash: str
    author: str
    name: str
    version: str
    code: bytes
    code_hash: str
    validation_channel: str
    record_hash: str

    def to_dict(self) -> dict:
        return {
            "policy_hash": self.policy_hash,
            "author": self.author,
            "name": self.name,
            "version": self.version,
            "code_hash": self.code_hash,
            "validation_channel": self.validation_channel,
            "record_hash": self.record_hash,
        }


class PolicyNode:
    """An independent policy source: a node that can serve policies.

    Construction takes no RegistrationChannel and keeps no reference to
    one. The node is given:

    - registry_snapshot_path: its own copy of the Gate 1 registry log.
    - payloads: a mapping of policy_hash -> policy code bytes this node
      holds (in production this would be a local store; here a dict).
    - serve_policies: the operator's opt-in choice. Default False.
    - anchor_path: optional, experiment 015. A file holding an
      experiment 012 anchor for this snapshot's chain. When given,
      the snapshot is consulted against it at construction and on
      every reload, and a mismatch or a stale (honestly grown) chain
      is refused before it can serve. When omitted, no consultation
      happens and behavior is exactly the pre-015 behavior.

    The snapshot chain is verified at construction, so a node built
    over a tampered record fails before it can serve anything.
    """

    def __init__(
        self,
        *,
        node_id: str,
        registry_snapshot_path: str | Path,
        payloads: dict[str, bytes],
        serve_policies: bool = False,
        anchor_path: str | Path | None = None,
        anchor_signer: str | None = None,
        anchor_secret_hex: str | None = None,
        anchor_witness: str | None = None,
        anchor_witness_secret_hex: str | None = None,
    ):
        if (anchor_signer is None) != (anchor_secret_hex is None):
            raise ValueError(
                "anchor_signer and anchor_secret_hex must be given "
                "together (a signing identity is an id plus its "
                "verification secret)"
            )
        if (anchor_witness is None) != (anchor_witness_secret_hex is None):
            raise ValueError(
                "anchor_witness and anchor_witness_secret_hex must be "
                "given together (a witness identity is an id plus its "
                "verification secret)"
            )
        self.node_id = node_id
        self.registry_snapshot_path = Path(registry_snapshot_path)
        self._payloads = dict(payloads)
        self.serve_policies = bool(serve_policies)
        self.anchor_path = Path(anchor_path) if anchor_path is not None else None
        # Experiment 019: the signing identity this node verifies
        # its anchor's signature with, when configured. Both None
        # (the default) is experiment 015's head-only judgement.
        self.anchor_signer = anchor_signer
        self.anchor_secret_hex = anchor_secret_hex
        # Experiment 029: the witness identity this node verifies
        # its anchor's countersignature with, when configured, in
        # the signer's opt-in style. Both None (the default) is
        # experiment 019's judgement: no countersignature is
        # required.
        self.anchor_witness = anchor_witness
        self.anchor_witness_secret_hex = anchor_witness_secret_hex
        self.anchor_check = None
        self.anchor_status: str = "no_anchor"
        # Raises TamperedRecord if the copied chain does not verify.
        self._entries: list[dict] = []
        self._by_hash: dict[str, dict] = {}
        self._revoked: set[str] = set()
        entries = verify_registry_snapshot(self.registry_snapshot_path)
        if self.anchor_path is not None:
            self.anchor_check, self.anchor_status = self._consult_anchor(
                self.registry_snapshot_path, entries
            )
        self._load_entries(entries)

    def _consult_anchor(self, snapshot_path: Path, entries: list[dict]):
        """Consult experiment 012's anchor for a verified snapshot.

        Returns (check, "anchored") when the snapshot is exactly the
        anchored chain. Raises AnchorStale for an honest extension
        and AnchorMismatch for anything else that is not the
        anchored chain (a rewrite, a truncation). Raises experiment
        012's AnchorError when a configured anchor cannot be read:
        a caller who asked for an anchor is never silently served
        without one. The entries passed in have already passed this
        module's own chain verification, so internal_verifies in the
        check is expected True; it is still honored if it is not.
        """
        anchor_mod = _load_registry_anchor_module()
        kwargs = {}
        if self.anchor_signer is not None:
            kwargs["signer"] = self.anchor_signer
            kwargs["secret_hex"] = self.anchor_secret_hex
        if self.anchor_witness is not None:
            kwargs["witness"] = self.anchor_witness
            kwargs["witness_secret_hex"] = self.anchor_witness_secret_hex
        check = anchor_mod.check_registry_against_anchor(
            snapshot_path, self.anchor_path, **kwargs
        )
        if check.ok:
            return check, "anchored"
        if check.signature_checked and not check.signature_valid:
            # Experiment 019: the signature judgement precedes the
            # head judgement. A missing, wrong, or tampered
            # signature - or a kept history that does not verify -
            # is refused under its own name and never reaches the
            # mismatch / stale classification.
            raise AnchorSignatureInvalid(
                f"registry snapshot's anchor signature does not "
                f"verify for signer {self.anchor_signer}: refusing "
                f"as anchor_signature_invalid",
                check,
            )
        if check.witness_checked and not check.witness_valid:
            # Experiment 029: the witness judgement follows the
            # signature's and precedes the head judgement. A
            # missing, wrong-identity, or tampered
            # countersignature is refused under its own name and
            # never reaches the mismatch / stale classification.
            raise AnchorWitnessInvalid(
                f"registry snapshot's anchor is not countersigned "
                f"by witness {self.anchor_witness}: refusing as "
                f"anchor_witness_invalid",
                check,
            )
        if not check.internal_verifies:
            raise TamperedRecord(
                "registry snapshot failed internal verification in the anchor check"
            )
        # Honest growth: longer than the anchor, and the entry at
        # the anchored length still carries the anchored head, so
        # the anchored prefix is intact (that entry's hash binds
        # the whole prefix chain) and entries were only appended.
        # An empty anchor (count 0, genesis head) is extended by any
        # internally verified chain.
        if check.presented_count > check.anchored_count and (
            check.anchored_count == 0
            or entries[check.anchored_count - 1]["entry_hash"] == check.anchored_head
        ):
            raise AnchorStale(
                f"registry snapshot grew honestly past its anchor "
                f"({check.anchored_count} anchored entries, "
                f"{check.presented_count} presented): refusing as "
                f"anchor_stale until the new head is re-anchored",
                check,
            )
        raise AnchorMismatch(
            f"registry snapshot is not the anchored chain "
            f"(anchored head {check.anchored_head}, presented head "
            f"{check.presented_head}): refusing as anchor_mismatch",
            check,
        )

    def _load_entries(self, entries: list[dict]) -> None:
        """Rebuild this node's view from a verified entry list.

        Registration entries populate _by_hash; revocation entries
        (event "revoked", experiment 008) populate _revoked and never
        overwrite the registration entry for the same policy hash.
        """
        self._entries = entries
        self._by_hash = {}
        self._revoked = set()
        for entry in entries:
            if entry.get("event") == "revoked":
                self._revoked.add(entry["policy_hash"])
            else:
                self._by_hash[entry["policy_hash"]] = entry

    def reload_snapshot(self, snapshot_path: str | Path | None = None) -> int:
        """Receive an updated registry record and rebuild from it.

        This is how the stale window (case 6) closes: the updated
        snapshot - this node's own snapshot file after a sync has
        copied the new record over it, or an explicit new path - is
        re-verified standalone (hash chain recomputed from the file
        alone) and only then replaces the node's view. A snapshot
        that fails verification raises TamperedRecord and the node's
        previous view is left untouched. Returns the number of
        entries in the verified snapshot.

        Anchor consultation (experiment 015): on a node built with
        an anchor, the reloaded snapshot is ALSO consulted against
        the anchor before the view changes, with the same refusals
        as at construction (AnchorMismatch, AnchorStale); a refused
        reload likewise leaves the previous view - and its anchor
        state - untouched.
        """
        path = Path(snapshot_path) if snapshot_path is not None else self.registry_snapshot_path
        entries = verify_registry_snapshot(path)  # raises before any state changes
        check = status = None
        if self.anchor_path is not None:
            check, status = self._consult_anchor(path, entries)  # raises likewise
        self.registry_snapshot_path = path
        if self.anchor_path is not None:
            self.anchor_check, self.anchor_status = check, status
        self._load_entries(entries)
        return len(entries)

    # Backwards-readable alias: receiving the updated record *is* a sync
    # of this node's registry view, in the vocabulary of case 6.
    update_snapshot = reload_snapshot

    @classmethod
    def from_config(
        cls,
        *,
        config_path: str | Path,
        registry_snapshot_path: str | Path,
        payloads: dict[str, bytes],
        anchor_path: str | Path | None = None,
        anchor_signer: str | None = None,
        anchor_secret_hex: str | None = None,
        anchor_witness: str | None = None,
        anchor_witness_secret_hex: str | None = None,
    ) -> "PolicyNode":
        """Build a node from the operator's own JSON config file.

        The config is where the user's choice lives:
        {"node_id": "...", "serve_policies": true/false}
        A missing file or a missing flag means not opted in.
        """
        config_path = Path(config_path)
        config = (
            json.loads(config_path.read_text(encoding="utf-8"))
            if config_path.exists()
            else {}
        )
        return cls(
            node_id=config.get("node_id", "unnamed-node"),
            registry_snapshot_path=registry_snapshot_path,
            payloads=payloads,
            serve_policies=bool(config.get("serve_policies", False)),
            anchor_path=anchor_path,
            anchor_signer=anchor_signer,
            anchor_secret_hex=anchor_secret_hex,
            anchor_witness=anchor_witness,
            anchor_witness_secret_hex=anchor_witness_secret_hex,
        )

    # -- introspection (holding is not serving) ---------------------------

    def holds_policy(self, policy_hash: str) -> bool:
        """True if this node holds a verified record + payload.

        Says nothing about serving: a non-opted-in node can hold a
        policy and still refuse to serve it (outcome 2), and a node
        whose snapshot contains the policy's revocation holds it and
        refuses it as revoked (case 6).
        """
        return policy_hash in self._by_hash and policy_hash in self._payloads

    def is_revoked(self, policy_hash: str) -> bool:
        """True if this node's snapshot contains the policy's
        revocation entry. False both for live policies and for the
        stale case: a pre-revocation snapshot knows nothing."""
        return policy_hash in self._revoked

    # -- serving ------------------------------------------------------------

    def serve_policy(self, policy_hash: str) -> ServedPolicy:
        """Serve one registered policy, verified from the record alone.

        Order of checks matters and is deliberate:
        1. Opt-in first: a node that did not choose to serve refuses
           everything, before any lookup (outcome 2).
        2. The registration entry must exist in the verified snapshot.
        3. Revocation: if this node's snapshot contains the policy's
           revocation entry, the policy is refused as revoked (case
           6), whatever its payload looks like.
        4. The payload this node holds must hash to the entry's
           code_hash, and the entry's policy_hash must recompute from
           (author, name, version, payload). Serving bytes that do not
           match their registration record is a tamper refusal, not a
           serve (outcome 1's verification requirement).
        """
        if not self.serve_policies:
            raise NotServing(
                f"node {self.node_id} did not opt in to serving policies"
            )
        entry = self._by_hash.get(policy_hash)
        if entry is None:
            raise PolicyNotFound(f"no registration record for policy hash: {policy_hash}")
        if policy_hash in self._revoked:
            raise PolicyRevoked(
                f"policy revoked by its author ({entry['author']}): {policy_hash}"
            )
        code = self._payloads.get(policy_hash)
        if code is None:
            raise PolicyNotFound(f"node holds no payload for policy hash: {policy_hash}")
        if _sha256_hex(code) != entry["code_hash"]:
            raise TamperedRecord("held payload does not match the registered code_hash")
        recomputed_policy_hash = _sha256_hex(
            _canonical_library_bytes(entry["author"], entry["name"], entry["version"], code)
        )
        if not hmac.compare_digest(recomputed_policy_hash, policy_hash):
            raise TamperedRecord("held payload does not recompute to the registered policy_hash")
        return ServedPolicy(
            policy_hash=policy_hash,
            author=entry["author"],
            name=entry["name"],
            version=entry["version"],
            code=code,
            code_hash=entry["code_hash"],
            validation_channel=entry["validation_channel"],
            record_hash=entry["entry_hash"],
        )
