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
  that needs an external anchor / witnessed head hash, still unbuilt.
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
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path


GENESIS_HASH = "0" * 64


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
    ):
        self.node_id = node_id
        self.registry_snapshot_path = Path(registry_snapshot_path)
        self._payloads = dict(payloads)
        self.serve_policies = bool(serve_policies)
        # Raises TamperedRecord if the copied chain does not verify.
        self._entries: list[dict] = []
        self._by_hash: dict[str, dict] = {}
        self._revoked: set[str] = set()
        self._load_entries(verify_registry_snapshot(self.registry_snapshot_path))

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
        """
        path = Path(snapshot_path) if snapshot_path is not None else self.registry_snapshot_path
        entries = verify_registry_snapshot(path)  # raises before any state changes
        self.registry_snapshot_path = path
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
