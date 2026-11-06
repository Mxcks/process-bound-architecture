"""
Process-Bound Architecture - Gate 3 synchronization mesh prototype.

A small, runnable prototype of the synchronization layer
(packages/synchronization, layer contract: propagate state across
nodes continuously, with no central locks, through the Continuous
Synchronization Mesh). Python standard library only. No network, no
database, no dependencies.

Builds on the earlier gates without rewriting them: the hash-chain
discipline is the same idea as Gate 1's registry chain and Gate 2's
snapshot verification (each entry links to its predecessor and its
hash recomputes from its body alone), applied here to *state entries*
exchanged between nodes instead of registration records.

WHAT THIS PROTOTYPE DEMONSTRATES (the three Gate 3 outcomes)
------------------------------------------------------------
1. Concurrent mutations across multiple nodes converge to the same
   state with no central lock. Each node mutates locally and
   independently (there is no lock object, coordinator, or leader
   anywhere in this module). Nodes exchange hash-chained state
   entries in differing orders; because the materialized state is a
   deterministic function of the entry *set* (entries applied in the
   total order (lamport, origin, seq)), every node that has seen the
   same set ends with an identical state and an identical state hash.

2. A partitioned node rejoins and converges. A node cut off while the
   others keep mutating (and mutating locally itself) later exchanges
   entries with the rest and all nodes converge to the same state.
   No entries are lost: per-origin sequences are contiguous, so a
   rejoining node can receive the entries it missed in any order.

3. Split-brain / diverging state views are detected and trigger an
   automated rollback. Detection covers three observable cases:
   (a) a tampered entry, whose stored hash does not recompute from
       its body;
   (b) a forked entry, a second, individually well-hashed entry with
       the same (origin, seq) identity as an entry already held but a
       different hash - two views of the same origin's history;
   (c) a broken per-origin chain, an entry whose prev_hash does not
       link to its predecessor from the same origin.
   On any of these, receive/sync refuses the whole incoming batch,
   records a divergence event, and rolls the node back to its last
   locally verified consistent state (its checkpoint, taken after
   every successful verified merge and local mutation). The result
   object reports divergence_detected=True and rolled_back=True:
   that is the automated rollback signal Gate 3 asks for. The
   divergent entries are never kept.

HOW IT WORKS, IN PLAIN WORDS
----------------------------
- A mutation makes one immutable StateEntry on its origin node. The
  entry carries origin, a per-origin sequence number (0, 1, 2, ...),
  a Lamport clock value, the key/value written, an Execution-Attached
  Identity computed with the PBA-EAI-v1 schema, the hash of the
  origin's previous entry (its per-origin hash chain), and its own
  hash over all of the above.
- Anti-entropy is bulk and pairwise: sync_with exchanges full entry
  lists both ways, receive_entries merges a batch. Arrival order
  does not matter to the result, only the final set does.
- Conflict resolution for the same key written concurrently is
  deterministic last-writer-wins in the (lamport, origin, seq) total
  order. That rule is a prototype choice, stated here, not a claim
  that it is the right production semantics for every key.

HONEST LIMITS (prototype, NOT production)
-----------------------------------------
- Nodes are in-process objects on one machine exchanging Python
  objects by method call. There is no network transport, no peer
  discovery, no gossip schedule, no persistence, and no restart
  recovery. Nothing about scale or latency is demonstrated.
- There is NO real Byzantine consensus. Detection here is local hash
  and chain verification plus fork comparison when two views meet.
  There is no quorum, no voting, no BFT protocol, and a coordinated
  attacker is out of scope. Gate 5 is where a forced Byzantine fault
  must be observed against a canary harness; this prototype does not
  claim it.
- Rollback is LOCAL to the node that detected divergence: it restores
  that node's last verified checkpoint. A coordinated rollback
  prototype now exists on top of this module (experiment 013,
  packages/synchronization/coordinated_rollback.py, built on the
  restore_to_entries primitive below): a caller restores every
  participating node to one shared checkpoint. It is an explicit,
  caller-driven step in-process - there is still no network
  signalling, no consensus vote on the checkpoint, and no automatic
  trigger - and other nodes are not signalled beyond the SyncResult
  returned to the caller of the sync.
- Tamper evidence inherits the Gate 1/2 limit: one edited entry is
  detected, and a fork is detected when both versions are seen, but
  an attacker who rewrites one origin's entire chain from genesis
  AND keeps the victim from ever seeing the original entries would
  produce a chain that verifies on its own. Detecting that needs an
  external anchor / witnessed head hash: a prototype anchor for
  these per-origin heads now exists (experiment 012,
  packages/synchronization/anchor.py, a stand-in file held outside
  the node), and since experiment 015 an anchored variant of this
  node (packages/synchronization/anchored_sync.py) consults it on
  the merged state before accepting a peer's entries. This module
  itself still does not: a plain SyncNode's receive and sync judge
  entries by the checks above alone, exactly as before.
- A fork is only observable once a node (or a sync between nodes)
  sees both versions. A partitioned fork that never meets is not
  detected by anyone; that is a property of the design, stated, not
  hidden.
- receive_entries verifies the candidate set as a whole after a bulk
  merge (per-origin contiguity and chain linkage over the merged
  set). It is an anti-entropy round, not a streaming protocol that
  verifies entries one by one as they trickle in.
- No performance numbers were measured or are claimed. The
  sub-millisecond lineage benchmark in tests/test-cases/sync-mesh.md
  (case 4) is Gate 5 work and is NOT attempted here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, replace


GENESIS_HASH = "0" * 64


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_eai(origin: str, seq: int) -> str:
    """Execution-Attached Identity for a state entry, PBA-EAI-v1.

    Canonical form from schemas/eai-hashing-schema.md:
    "PBA-EAI-v1|" + ProcessID + "|" + PipelineStage + "|" +
    decimal(Epoch) + "|" + IngressVector, SHA-256, lowercase hex.
    Here ProcessID is the origin node, PipelineStage is "sync", the
    epoch is the entry's per-origin sequence number, and the ingress
    vector names the sync-mesh path the entry entered through.
    """
    canonical = f"PBA-EAI-v1|{origin}|sync|{seq}|node:{origin}/sync-mesh"
    return _sha256_hex(canonical.encode("utf-8"))


def _entry_body(
    *,
    origin: str,
    seq: int,
    lamport: int,
    key: str,
    value: str,
    prev_hash: str,
    eai: str,
) -> dict:
    return {
        "origin": origin,
        "seq": seq,
        "lamport": lamport,
        "key": key,
        "value": value,
        "prev_hash": prev_hash,
        "eai": eai,
    }


def compute_entry_hash(
    *,
    origin: str,
    seq: int,
    lamport: int,
    key: str,
    value: str,
    prev_hash: str,
    eai: str,
) -> str:
    body = _entry_body(
        origin=origin, seq=seq, lamport=lamport, key=key,
        value=value, prev_hash=prev_hash, eai=eai,
    )
    return _sha256_hex(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


@dataclass(frozen=True)
class StateEntry:
    """One immutable state mutation, hash-chained per origin."""

    origin: str
    seq: int
    lamport: int
    key: str
    value: str
    prev_hash: str
    eai: str
    entry_hash: str

    @property
    def entry_id(self) -> str:
        return f"{self.origin}:{self.seq}"

    def recomputed_hash(self) -> str:
        return compute_entry_hash(
            origin=self.origin, seq=self.seq, lamport=self.lamport,
            key=self.key, value=self.value, prev_hash=self.prev_hash,
            eai=self.eai,
        )

    def hash_verifies(self) -> bool:
        return hmac.compare_digest(self.recomputed_hash(), self.entry_hash)


def build_entry(
    *,
    origin: str,
    seq: int,
    lamport: int,
    key: str,
    value: str,
    prev_hash: str,
) -> StateEntry:
    """Build a correctly hashed entry. Used by SyncNode.mutate, and by
    tests to build a *forked* entry: a second entry with the same
    (origin, seq) as a real one but different content, whose own hash
    is individually valid. A fork is not a hash error; it is two valid
    hashes claiming the same place in one origin's history."""
    eai = compute_eai(origin, seq)
    entry_hash = compute_entry_hash(
        origin=origin, seq=seq, lamport=lamport, key=key,
        value=value, prev_hash=prev_hash, eai=eai,
    )
    return StateEntry(
        origin=origin, seq=seq, lamport=lamport, key=key, value=value,
        prev_hash=prev_hash, eai=eai, entry_hash=entry_hash,
    )


@dataclass(frozen=True)
class DivergenceEvent:
    reason: str  # "tampered_entry" | "forked_entry" | "broken_chain"
    detail: str
    entry_id: str | None

    def to_dict(self) -> dict:
        return {"reason": self.reason, "detail": self.detail, "entry_id": self.entry_id}


@dataclass(frozen=True)
class SyncResult:
    """The outcome of one receive/sync round.

    rolled_back is the automated rollback signal: True means a
    divergence was detected in this round and the node restored its
    last verified checkpoint instead of keeping anything incoming.
    The one other shape is restore_to_entries (the coordinated
    rollback primitive): rolled_back=True with
    divergence_detected=False means an explicit restore to a given
    verified entry set succeeded.
    """

    accepted: int
    divergence_detected: bool
    rolled_back: bool
    reason: str | None
    state_hash: str

    def to_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "divergence_detected": self.divergence_detected,
            "rolled_back": self.rolled_back,
            "reason": self.reason,
            "state_hash": self.state_hash,
        }


class SyncNode:
    """One node in the synchronization mesh.

    A node mutates locally with no lock and no coordinator, holds its
    entry set, and merges other nodes' entries by bulk anti-entropy.
    Its materialized state and state hash are pure functions of its
    entry set, so convergence is checkable by comparing hashes.
    """

    def __init__(self, node_id: str):
        self.node_id = node_id
        self._entries: dict[str, StateEntry] = {}
        self._own_seq = 0
        self._own_prev_hash = GENESIS_HASH
        self._lamport = 0
        # Checkpoint: last locally verified consistent entry set.
        self._checkpoint: dict[str, StateEntry] = {}
        self.divergence_log: list[DivergenceEvent] = []

    # -- local mutation (no central lock, no coordinator) ---------------

    def mutate(self, key: str, value: str) -> StateEntry:
        self._lamport += 1
        entry = build_entry(
            origin=self.node_id,
            seq=self._own_seq,
            lamport=self._lamport,
            key=key,
            value=value,
            prev_hash=self._own_prev_hash,
        )
        self._entries[entry.entry_id] = entry
        self._own_seq += 1
        self._own_prev_hash = entry.entry_hash
        self._take_checkpoint()
        return entry

    # -- views ------------------------------------------------------------

    def entries(self) -> list[StateEntry]:
        """All held entries, in the deterministic total order."""
        return sorted(
            self._entries.values(),
            key=lambda e: (e.lamport, e.origin, e.seq),
        )

    def entry_ids(self) -> set[str]:
        return set(self._entries)

    def materialized_state(self) -> dict[str, str]:
        """State derived from the entry set alone: apply entries in
        the (lamport, origin, seq) total order; later writes to a key
        overwrite earlier ones. Same set in, same state out, on any
        node, regardless of the order entries arrived in."""
        state: dict[str, str] = {}
        for entry in self.entries():
            state[entry.key] = entry.value
        return state

    def state_hash(self) -> str:
        payload = {
            "entry_hashes": [e.entry_hash for e in self.entries()],
            "state": self.materialized_state(),
        }
        return _sha256_hex(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )

    # -- anti-entropy -------------------------------------------------------

    def receive_entries(self, incoming: list[StateEntry]) -> SyncResult:
        """Merge a batch of entries from another node.

        On any divergence the whole batch is refused, a divergence
        event is logged, the node rolls back to its checkpoint, and
        the returned SyncResult carries the rollback signal. On
        success the merged set becomes the new checkpoint.
        """
        candidate: dict[str, StateEntry] = dict(self._entries)
        accepted = 0

        for entry in incoming:
            # (a) Tampered: the stored hash must recompute.
            if not entry.hash_verifies():
                return self._diverge(
                    DivergenceEvent(
                        reason="tampered_entry",
                        detail=f"entry {entry.entry_id} hash does not recompute",
                        entry_id=entry.entry_id,
                    )
                )
            existing = candidate.get(entry.entry_id)
            if existing is not None:
                # (b) Forked: same identity, different hash.
                if existing.entry_hash != entry.entry_hash:
                    return self._diverge(
                        DivergenceEvent(
                            reason="forked_entry",
                            detail=(
                                f"entry {entry.entry_id} already held with hash "
                                f"{existing.entry_hash}, incoming has {entry.entry_hash}"
                            ),
                            entry_id=entry.entry_id,
                        )
                    )
                continue  # already held, identical: not new
            candidate[entry.entry_id] = entry
            accepted += 1

        # (c) Broken chain: over the merged candidate set, every
        # origin's entries must form one contiguous chain from
        # genesis, each prev_hash linking to its predecessor.
        chain_error = self._verify_chains(candidate)
        if chain_error is not None:
            return self._diverge(chain_error)

        self._entries = candidate
        if candidate:
            self._lamport = max(self._lamport, max(e.lamport for e in candidate.values()))
        # Own-chain bookkeeping if own entries came back via a peer.
        own = [e for e in candidate.values() if e.origin == self.node_id]
        if own:
            top = max(own, key=lambda e: e.seq)
            self._own_seq = max(self._own_seq, top.seq + 1)
            self._own_prev_hash = top.entry_hash
        self._take_checkpoint()
        return SyncResult(
            accepted=accepted,
            divergence_detected=False,
            rolled_back=False,
            reason=None,
            state_hash=self.state_hash(),
        )

    def sync_with(self, other: "SyncNode") -> tuple[SyncResult, SyncResult]:
        """Bidirectional anti-entropy with one peer, from snapshots
        taken before either side merges, so neither side's merge can
        contaminate the other's input in the same round."""
        mine = self.entries()
        theirs = other.entries()
        result_other = other.receive_entries(mine)
        result_self = self.receive_entries(theirs)
        return result_self, result_other

    # -- explicit restore (coordinated rollback primitive) -------------------

    def restore_to_entries(self, entries: list[StateEntry]) -> SyncResult:
        """Restore this node to an explicitly given entry set.

        Local rollback (_diverge) can only return a node to its OWN
        last verified checkpoint, so a mesh whose nodes verified
        different states last has no common recovery point. This is
        the primitive coordinated rollback
        (packages/synchronization/coordinated_rollback.py,
        experiment 013) is built on: every participating node is
        restored to the SAME set, a shared checkpoint captured
        earlier from a converged mesh.

        The given set is judged exactly as a received batch is:
        every entry's hash must recompute, no second entry may claim
        an (origin, seq) identity with a different hash, and
        _verify_chains must pass over the whole set. On any failure
        this node diverges as usual - the event is logged, its own
        checkpoint is restored, and the returned SyncResult reports
        the divergence - and the given set is never kept. On success
        the set becomes both the current entries and the checkpoint,
        own-chain bookkeeping is rebuilt from the restored set (as
        in _diverge, so the node's next mutation extends the
        restored chain head), and the Lamport clock never moves
        backwards. accepted is 0: nothing was merged, the state was
        replaced.
        """
        candidate: dict[str, StateEntry] = {}
        for entry in entries:
            if not entry.hash_verifies():
                return self._diverge(
                    DivergenceEvent(
                        reason="tampered_entry",
                        detail=f"entry {entry.entry_id} hash does not recompute",
                        entry_id=entry.entry_id,
                    )
                )
            existing = candidate.get(entry.entry_id)
            if existing is not None and existing.entry_hash != entry.entry_hash:
                return self._diverge(
                    DivergenceEvent(
                        reason="forked_entry",
                        detail=(
                            f"entry {entry.entry_id} given twice with hashes "
                            f"{existing.entry_hash} and {entry.entry_hash}"
                        ),
                        entry_id=entry.entry_id,
                    )
                )
            candidate[entry.entry_id] = entry

        chain_error = self._verify_chains(candidate)
        if chain_error is not None:
            return self._diverge(chain_error)

        self._entries = candidate
        if candidate:
            self._lamport = max(self._lamport, max(e.lamport for e in candidate.values()))
        own = [e for e in candidate.values() if e.origin == self.node_id]
        if own:
            top = max(own, key=lambda e: e.seq)
            self._own_seq = top.seq + 1
            self._own_prev_hash = top.entry_hash
        else:
            self._own_seq = 0
            self._own_prev_hash = GENESIS_HASH
        self._take_checkpoint()
        return SyncResult(
            accepted=0,
            divergence_detected=False,
            rolled_back=True,
            reason=None,
            state_hash=self.state_hash(),
        )

    # -- divergence, checkpoint, rollback -----------------------------------

    @staticmethod
    def _verify_chains(entries: dict[str, StateEntry]) -> DivergenceEvent | None:
        by_origin: dict[str, list[StateEntry]] = {}
        for entry in entries.values():
            by_origin.setdefault(entry.origin, []).append(entry)
        for origin, chain in by_origin.items():
            chain.sort(key=lambda e: e.seq)
            prev_hash = GENESIS_HASH
            for expected_seq, entry in enumerate(chain):
                if entry.seq != expected_seq:
                    return DivergenceEvent(
                        reason="broken_chain",
                        detail=(
                            f"origin {origin}: expected seq {expected_seq}, "
                            f"found seq {entry.seq} (gap or duplicate in chain)"
                        ),
                        entry_id=entry.entry_id,
                    )
                if entry.prev_hash != prev_hash:
                    return DivergenceEvent(
                        reason="broken_chain",
                        detail=f"origin {origin} seq {entry.seq}: prev_hash link broken",
                        entry_id=entry.entry_id,
                    )
                prev_hash = entry.entry_hash
        return None

    def _take_checkpoint(self) -> None:
        self._checkpoint = dict(self._entries)

    def _diverge(self, event: DivergenceEvent) -> SyncResult:
        """Automated rollback: restore the last verified checkpoint,
        discarding the entire incoming batch, and signal it."""
        self.divergence_log.append(event)
        self._entries = dict(self._checkpoint)
        # Rebuild own-chain bookkeeping from the restored set.
        own = [e for e in self._entries.values() if e.origin == self.node_id]
        if own:
            top = max(own, key=lambda e: e.seq)
            self._own_seq = top.seq + 1
            self._own_prev_hash = top.entry_hash
        else:
            self._own_seq = 0
            self._own_prev_hash = GENESIS_HASH
        return SyncResult(
            accepted=0,
            divergence_detected=True,
            rolled_back=True,
            reason=event.reason,
            state_hash=self.state_hash(),
        )


def tamper_entry(entry: StateEntry, *, value: str) -> StateEntry:
    """Return a copy of an entry with its value changed but its hash
    kept: the textbook tampered entry, for tests and demos. Its stored
    hash no longer recomputes from its body."""
    return replace(entry, value=value)
