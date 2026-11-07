"""
Process-Bound Architecture - networked node server (experiment 007).

One node as its own OS process, serving and requesting sync over
HTTP on localhost. Python standard library only (http.server for the
server, urllib for the client helpers). No dependencies.

This is the transport step the earlier gates named as unbuilt:
Gates 1-5 ran nodes as in-process objects exchanging Python objects
by method call. A node here is a separate process: it owns one
SyncNode (Gate 3, packages/synchronization/sync_mesh.py) and,
optionally, one PolicyNode (Gate 2, packages/policy-library/
policy_node.py) built from its own copy of the Gate 1 registry
snapshot plus the policy payloads it holds. Other processes talk to
it only over HTTP; nothing is shared by memory or by file at runtime.

ENDPOINTS
---------
GET  /health     -> {node_id, ok, entries, state_hash,
                     serve_policies, policy_configured,
                     anchor_configured, anchor_status,
                     mesh_anchor_configured, mesh_anchor_status,
                     snapshot_entries, last_snapshot_check,
                     last_snapshot_delivery}
POST /mutate     -> {key, value}: mutate this process's own
                    SyncNode; returns the entry created and the new
                    state hash. Mutations happen inside the server
                    process, so tests drive state through the wire,
                    not around it.
GET  /entries    -> {entries: [...]}: this node's full mesh entry
                    list, serialized (anti-entropy pull).
POST /entries    -> {entries: [...]}: merge a batch into this node.
                    Deserialized entries go through SyncNode.
                    receive_entries unchanged, so receipt re-runs
                    Gate 3's verification: hash recompute, fork
                    detection, per-origin chain check, and rollback
                    on divergence. The SyncResult is the response.
POST /sync       -> {peer: "http://127.0.0.1:PORT"}: this process
                    initiates sync with a peer process: it pulls the
                    peer's entries (GET /entries), merges them
                    locally (verified, as above), then pushes its
                    merged entry list to the peer (POST /entries),
                    whose merge verifies them again on that side.
                    Both SyncResults are returned.
POST /check-snapshot
                 -> drive one snapshot check on this RUNNING
                    process (experiment 024): compare this
                    process's own snapshot file's presented facts
                    (entry count, chain head, file fingerprint -
                    experiment 022's change fact) with the facts
                    its PolicyNode last loaded; on a clean change,
                    reload the PolicyNode in the same check
                    (experiment 015/021 reload semantics). The
                    response is the check's report: status
                    "reloaded", "reload_refused" (with the
                    refusal's own name as "reason"), "no_change",
                    or "no_snapshot", plus the before/after entry
                    counts and chain heads, the loaded entry count
                    when a reload stood, and the anchor status
                    after the check. /health carries the last
                    check's report and the loaded snapshot entry
                    count, so the process's current standing is
                    readable without driving a check.
POST /deliver-snapshot
                 -> {entries: [...]}: receive one registry
                    snapshot delivery over the wire
                    (experiment 025): the snapshot ENTRIES
                    themselves, as JSON dicts, never a file
                    path. The delivered entries are verified
                    as a chain and consulted against this
                    process's configured anchor (and its
                    signature, where a signer is configured)
                    BEFORE anything changes, exactly as a file
                    delivery is judged; only a delivery that
                    stands is applied, through the same reload
                    as /check-snapshot: this process's own
                    snapshot file is rewritten from the
                    verified delivery (Gate 1's log format)
                    and the PolicyNode reloads from it, so the
                    file and the serving view move together.
                    The response is the delivery's report:
                    status "delivered", "delivery_refused"
                    (with the refusal's own name as "reason"),
                    "no_change", or "no_snapshot", plus the
                    before/after entry counts and chain heads,
                    the loaded entry count when a delivery
                    stood, and the anchor status after the
                    delivery. /health carries the last
                    delivery's report as last_snapshot_delivery,
                    beside last_snapshot_check.
POST /push-snapshot
                 -> {peer: "http://127.0.0.1:PORT"}: this process
                    pushes its own standing registry record to a
                    peer process (experiment 027): it sends the
                    entries its PolicyNode stands on to the peer's
                    POST /deliver-snapshot (experiment 025), and
                    the peer judges and applies them under exactly
                    experiment 025's rules - the receiving side is
                    experiment 025's surface, unmodified. The
                    response is the push's report: on 200, the
                    peer's delivery status as "status" and the
                    peer's delivery report itself under "delivery"
                    (on a process with no snapshot, status
                    "no_snapshot" and "delivery" null); a peer that
                    cannot be reached, or whose transport refuses
                    this process before the delivery endpoint runs,
                    is reported on 502 in /sync's refusal shape.
                    /health carries the last push's outcome as
                    last_snapshot_push, beside last_snapshot_check
                    and last_snapshot_delivery.
GET  /snapshot   -> the raw bytes of this node's registry snapshot
                    file (JSON-lines, exactly the Gate 1 log format).
                    A receiver writes the bytes to its own file and
                    verifies them with Gate 2's standalone
                    verify_registry_snapshot before trusting them;
                    the transport itself grants no trust.
GET  /policy/<policy_hash>
                 -> serve one registered policy through the node's
                    PolicyNode: Gate 2 verification runs inside
                    serve_policy on every request (snapshot chain was
                    verified at node construction; payload hash and
                    policy-hash recompute run per serve). Response
                    carries the metadata plus the code as base64.
                    Refusals map to HTTP: not opted in -> 403,
                    unknown/unheld policy -> 404, tampered record
                    or payload -> 409, policy revoked by its
                    author -> 410 with error "policy_revoked"
                    (experiment 021; before it, PolicyRevoked was
                    not caught here and the request dropped the
                    connection with no response - finding F2 of
                    experiment 020).
Started with --mesh-anchor (experiment 026), the process's
mesh side is experiment 015's AnchoredSyncNode instead of a
plain SyncNode, and /health also reports that side's standing
(mesh_anchor_configured, mesh_anchor_status); see the
VERIFICATION section below.

VERIFICATION IS NEVER BYPASSED ON THIS TRANSPORT
------------------------------------------------
- Entries arriving over HTTP are plain data until receive_entries
  verifies them; a tampered or forked entry gets the same divergence
  + rollback over the wire as in-process (covered by a test).
- The PolicyNode verifies the snapshot's hash chain at server
  startup: a server pointed at a tampered snapshot refuses to start
  (also covered by a test).
- Started with --anchor (experiment 018), the PolicyNode ALSO
  consults experiment 012's registry anchor for its snapshot at
  startup, exactly as experiment 015 built for the in-process
  node: a snapshot rewritten from genesis (internally consistent,
  passing the chain check above) refuses to start as
  anchor_mismatch, and a snapshot that grew honestly past its
  anchor refuses to start as anchor_stale, until the new head is
  re-anchored. Without --anchor no consultation happens and
  startup behavior is exactly experiments 007/014's. The anchor
  consulted here is the REGISTRY snapshot's.
- Started with --mesh-anchor (experiment 026), this process's
  mesh side is experiment 015's AnchoredSyncNode, and every
  mesh receive (POST /entries, and both sides of POST /sync,
  which already call receive_entries) consults experiment
  012's mesh anchor on the merged state before accepting a
  peer's entries as converged: a peer whose origin chain was
  rewritten from genesis is flagged divergent over the wire
  as anchor_mismatch, honest growth past the anchor as
  anchor_stale until the grown state is re-anchored, and -
  with --mesh-anchor-signer plus --mesh-anchor-secret-file -
  a mesh anchor whose signature does not verify as
  anchor_signature_invalid (experiment 019). /health reports
  the mesh side's standing as mesh_anchor_status (experiment
  015's classification of the state the process currently
  holds) beside the registry anchor_status. Without
  --mesh-anchor the mesh side is a plain SyncNode and
  consults nothing, exactly as experiments 007-025 left it.
  One wire adaptation, stated in sync-mesh case 15: where
  the in-process consultation raises on a configured anchor
  that cannot be read (after rolling the merge back), the
  wire receive returns that refusal in the SyncResult shape
  under reason anchor_unreadable, and /health reports the
  same name - a refusal is a sync outcome, never a dropped
  connection.
- A fetched snapshot is verified by the receiver with Gate 2's
  verifier before any PolicyNode is built over it.
- A running server's live reload (POST /check-snapshot,
  experiment 024) is the PolicyNode's own reload, not a new
  mechanism: the presented snapshot is verified (chain
  recomputed) and consulted against the anchor BEFORE the
  serving view changes, and a refused reload leaves the running
  view untouched and is re-reported by every later check until
  its cause is fixed - experiment 022's discipline at the
  process surface.
- A registry snapshot delivered over the wire
  (POST /deliver-snapshot, experiment 025) is judged by the
  same verification, not by the transport: the delivered
  entries are first written to a candidate file and put
  through a full PolicyNode construction (chain verification
  plus the anchor consultation above, signature included
  where configured) BEFORE the server's snapshot file or
  serving view changes. A refused delivery changes neither,
  byte-for-byte. Only a candidate that stands replaces the
  snapshot file (rewritten from the verified delivery, in
  Gate 1's log format) and is then loaded by the running
  PolicyNode's own reload from that file, so what a later
  restart or /check-snapshot sees is exactly what was
  delivered and verified. Two judgements run before the
  candidate verification, on the delivered hashes alone: a
  delivery identical to the chain this process already
  stands on is "no_change", and a delivery that is exactly
  an earlier prefix of that chain is refused as
  snapshot_regression (sync-mesh case 14 sets the name) -
  a shorter chain that is NOT that prefix falls through to
  the candidate verification and is judged there (a rewrite
  is anchor_mismatch), so the backward name cannot launder
  a rewrite.
- A record this process pushes to a peer (POST
  /push-snapshot, experiment 027) is judged by the PEER's
  experiment 025 verification, not by the sender: the
  sender contributes only the entries its PolicyNode
  stands on (its loaded, verified entries, which cases
  13/14 keep in step with its snapshot file) and reports
  the peer's delivery report, refusals included, as the
  push's result - it re-derives no verdict of its own.
  With identity binding on, the push first runs experiment
  017's initiating-side check on the peer (its certificate
  id against the id it claims), exactly as /sync does; a
  peer whose transport refuses the SENDER before the
  delivery endpoint runs (experiment 017's accepting-side
  identity_mismatch, HTTP 403 naming claimed and certified
  ids) is reported by the push on 502 in /sync's refusal
  shape, so the refusal is named in the push result, never
  hidden behind a generic transport failure.

HONEST LIMITS (prototype, NOT production)
-----------------------------------------
- Localhost only. Started without TLS arguments (the experiment
  007 default, unchanged), the transport is plain HTTP: no TLS, no
  authentication, no authorization on any endpoint, and anyone who
  can reach the port can mutate, sync, and (on an opted-in node)
  fetch policies. Started with --tls-cert/--tls-key/--tls-ca, the
  SAME endpoints run over mutual TLS (experiment 014,
  protected_link.py): the wire is encrypted and a client must
  present a certificate chaining to the given CA or the handshake
  is refused before any endpoint is reached. That authenticates
  possession of a CA-issued credential. Started additionally with
  --tls-bind-identity (experiment 017), the certificate is also
  bound to the node id a process claims: the process refuses to
  start if its own certificate was issued for a different id, an
  accepting node refuses a peer whose claimed id (the
  X-PBA-Node-Id header) differs from the id in its certificate,
  and an initiating node checks a peer server's certificate id
  against that peer's claimed id before pulling any entries.
  Without the flag, no such check runs and behavior is exactly
  experiment 014's. There is still no authorization beyond the
  identity check and opt-in policy serving, no certificate
  revocation or rotation, and no identity proofing of who SHOULD
  hold a certificate. Plain HTTP is acceptable for a localhost
  experiment and for nothing else; mutual TLS at this scale is a
  prototype, not production transport security.
- No discovery: peer addresses are configured by whoever starts the
  processes and whoever calls /sync. No gossip schedule, no peer
  list, no automatic re-sync: sync happens when a client or a /sync
  call asks for it.
- No persistence: mesh state lives in the server process's memory.
  Kill the process and its unreplicated entries are gone; what
  survives a kill is exactly what had already synced to another
  process (that is the availability claim the tests make, and the
  only one).
- Sync is still Gate 3's bulk anti-entropy (full entry lists both
  ways), now serialized as JSON. No streaming, no deltas, and no
  performance, scale, or latency numbers are measured or claimed.
- Inherited limits stand unchanged: no Byzantine consensus or
  quorum, local-only rollback, and HMAC prototype signing in
  Gate 1. Revocation delivery to a running server is driven,
  never unattended: the process reloads its PolicyNode when a
  client drives POST /check-snapshot after the updated record
  has been copied over its snapshot file (experiment 024), or
  when a peer pushes the record itself to
  POST /deliver-snapshot (experiment 025). Since experiment
  027 a server can also push its own standing record to a
  peer, when a client drives POST /push-snapshot on it.
  No timer, scheduler,
  or thread inside the server fires any of these, nothing
  solicits a delivery, there is no broadcast or gossip of the
  record, and a server nobody drives or pushes to stays
  exactly as stale as experiment 007 left it (though since
  experiment 021 its refusal over the wire is a clean 410,
  not a dropped connection). The anchor a delivery is
  consulted against still arrives out of band: the anchor
  file is the local stand-in of experiments 012-019, and
  experiment 025 transports the record, not the anchor. An
  external chain anchor exists (experiment 012) and, since
  experiment 018, a server started with --anchor consults the
  registry anchor at startup (see above); since experiment
  019 the anchor may be signed, and a server started with
  --anchor-signer plus --anchor-secret-file also verifies
  that signature at startup, refusing to start on
  anchor_signature_invalid. Without those arguments the
  consultation is experiment 018's head-only one. Since
  experiment 024 the same consultation also runs on every
  live reload, and since experiment 025 on every wire
  delivery, not only at startup. The mesh chain in this
  process consults an anchor only when the process is
  started with --mesh-anchor (experiment 026, in the
  VERIFICATION section below); a process started without
  one consults no mesh anchor, exactly as experiments
  007-025 left it.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _pkg in ("policy-library",):
    _path = str(_HERE.parent / _pkg)
    if _path not in sys.path:
        sys.path.insert(0, _path)
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from policy_node import (  # noqa: E402
    GENESIS_HASH,
    NotServing,
    PolicyNode,
    PolicyNotFound,
    PolicyRevoked,
    TamperedRecord,
)
# Experiment 024: experiment 022's change facts and refusal
# naming, reused exactly - the server's snapshot check invents
# no change fact and no refusal vocabulary of its own.
from snapshot_delivery import _refusal_reason as _reload_refusal_reason  # noqa: E402
from snapshot_delivery import snapshot_facts  # noqa: E402
import protected_link  # noqa: E402
from sync_mesh import StateEntry, SyncNode, SyncResult  # noqa: E402


_ANCHORED_MESH = None


def _anchored_mesh():
    """Experiment 015's AnchoredSyncNode and experiment 012's
    MeshAnchorError, imported lazily on first use (experiment
    026).

    Two packages ship an anchor.py (registration,
    synchronization), so a plain `import anchor` resolves by
    sys.path order - the collision policy_node.py's docstring
    describes - and this module is imported by consumers whose
    path order is theirs, not this module's. A top-level import
    here could bind the wrong anchor module in a shared
    process, or freeze the right one where a consumer expects
    the other package's. Importing lazily, only when a process
    actually configures a mesh anchor, leaves every existing
    consumer's resolution exactly as it was. In the server
    process itself, this file's own directory heads sys.path,
    so the plain intra-package imports below resolve to this
    package's modules, exactly as they do inside
    anchored_sync.py.
    """
    global _ANCHORED_MESH
    if _ANCHORED_MESH is None:
        from anchor import MeshAnchorError
        from anchored_sync import AnchoredSyncNode

        _ANCHORED_MESH = (AnchoredSyncNode, MeshAnchorError)
    return _ANCHORED_MESH


# -- entry serialization ---------------------------------------------------
# Entries cross the wire as plain JSON dicts of their dataclass
# fields. Deserialization constructs the StateEntry as-is, stored
# hash included: nothing is recomputed or "fixed up" in transit, so
# the receiving node's receive_entries sees exactly what was sent
# and its verification (hash recompute, fork, chain) decides.


def entry_to_dict(entry: StateEntry) -> dict:
    return asdict(entry)


def entry_from_dict(data: dict) -> StateEntry:
    return StateEntry(
        origin=data["origin"],
        seq=int(data["seq"]),
        lamport=int(data["lamport"]),
        key=data["key"],
        value=data["value"],
        prev_hash=data["prev_hash"],
        eai=data["eai"],
        entry_hash=data["entry_hash"],
    )


# -- the node one process owns ----------------------------------------------


class NetworkNode:
    """One node's worth of state for one server process.

    A SyncNode always (experiment 015's AnchoredSyncNode when the
    process was started with a mesh anchor, experiment 026); a
    PolicyNode only when the process was started with a registry
    snapshot (its own copy). All access is serialized with one lock
    because the HTTP server is threaded while the Gate 3/2
    prototypes are single-threaded objects.
    """

    def __init__(
        self,
        *,
        node_id: str,
        registry_snapshot_path: str | Path | None = None,
        payloads: dict[str, bytes] | None = None,
        serve_policies: bool = False,
        bind_identity: bool = False,
        anchor_path: str | Path | None = None,
        anchor_signer: str | None = None,
        anchor_secret_hex: str | None = None,
        mesh_anchor_path: str | Path | None = None,
        mesh_anchor_signer: str | None = None,
        mesh_anchor_secret_hex: str | None = None,
    ):
        self.node_id = node_id
        # Experiment 017: when True (and the process runs over
        # mutual TLS), the handler checks every connecting peer's
        # certificate id against the id that peer claims, and this
        # node's own /sync checks a peer server's certificate id
        # against its claim before pulling entries.
        self.bind_identity = bool(bind_identity)
        # Experiment 026: an optional experiment 012 MESH anchor
        # for this process's sync chain. With one configured the
        # mesh side is experiment 015's AnchoredSyncNode, which
        # consults the anchor on the merged state before
        # accepting a peer's entries (the signer pair is passed
        # straight through, both or neither, as AnchoredSyncNode
        # requires); without one it is the plain SyncNode of
        # experiments 007-025 and no mesh consultation happens.
        # The mesh anchor is independent of the registry anchor
        # below and of any snapshot: the mesh side exists on
        # mesh-only processes too.
        self.mesh_anchor_path = (
            Path(mesh_anchor_path) if mesh_anchor_path else None
        )
        if self.mesh_anchor_path is not None:
            anchored_sync_node, _mesh_anchor_error = _anchored_mesh()
            self.sync_node = anchored_sync_node(
                node_id,
                self.mesh_anchor_path,
                anchor_signer=mesh_anchor_signer,
                anchor_secret_hex=mesh_anchor_secret_hex,
            )
        else:
            self.sync_node = SyncNode(node_id)
        self.snapshot_path = (
            Path(registry_snapshot_path) if registry_snapshot_path else None
        )
        # Experiment 018: an optional experiment 012 registry
        # anchor for the snapshot's chain. Passed straight to the
        # PolicyNode, whose experiment 015 consultation runs at
        # construction; None (the default) means no consultation
        # and behavior exactly as experiments 007/014 left it.
        self.anchor_path = Path(anchor_path) if anchor_path else None
        # Experiment 019: the signing identity for the anchor,
        # passed straight to the PolicyNode alongside the anchor
        # path (both or neither, as PolicyNode requires). None
        # (the default) is experiment 018's head-only consultation.
        self.anchor_signer = anchor_signer
        self.anchor_secret_hex = anchor_secret_hex
        self.policy_node: PolicyNode | None = None
        if self.snapshot_path is not None:
            # PolicyNode construction verifies the snapshot hash
            # chain; a tampered snapshot raises here and the server
            # refuses to start. That refusal is deliberate. With an
            # anchor configured, a rewritten snapshot raises
            # AnchorMismatch and an honestly grown one AnchorStale
            # at the same place, with the same effect.
            self.policy_node = PolicyNode(
                node_id=node_id,
                registry_snapshot_path=self.snapshot_path,
                payloads=payloads or {},
                serve_policies=serve_policies,
                anchor_path=self.anchor_path,
                anchor_signer=self.anchor_signer,
                anchor_secret_hex=self.anchor_secret_hex,
            )
        # Experiment 024: the presented facts of the snapshot this
        # process's PolicyNode last loaded (at startup, the
        # snapshot it was just built over), and the most recent
        # snapshot check's report. The facts move only when a live
        # reload stands - experiment 022's state discipline - so a
        # refused delivery is re-attempted and re-reported by
        # every later check until its cause is fixed.
        self._loaded_snapshot_facts = (
            snapshot_facts(self.snapshot_path)
            if self.policy_node is not None
            else None
        )
        self.last_snapshot_check: dict | None = None
        # Experiment 025: the most recent wire delivery's
        # report, in the same field shape as the last check's.
        self.last_snapshot_delivery: dict | None = None
        # Experiment 027: the most recent push THIS process
        # initiated toward a peer - the push's response body,
        # a 200 report or a 502 refusal alike - beside the
        # last check's and the last delivery's.
        self.last_snapshot_push: dict | None = None
        self.serve_policies = bool(serve_policies)
        self.lock = threading.Lock()

    @property
    def anchor_status(self) -> str:
        """This process's anchor standing, for /health: the
        PolicyNode's experiment 015 status when a policy node
        exists, otherwise "no_anchor" (no snapshot, so nothing to
        consult an anchor about)."""
        if self.policy_node is not None:
            return self.policy_node.anchor_status
        return "no_anchor"

    @property
    def mesh_anchor_status(self) -> str:
        """This process's MESH anchor standing, for /health
        (experiment 026): experiment 015's own classification of
        the mesh state this process currently holds ("anchored",
        "anchor_stale", "anchor_mismatch", or, with a signing
        identity configured, "anchor_signature_invalid") when a
        mesh anchor is configured, otherwise "no_anchor" (no
        mesh anchor configured, so nothing to consult). A
        configured anchor that cannot be read reports
        "anchor_unreadable" - experiment 015's divergence reason
        for the same condition - instead of raising out of
        /health: health is where an operator looks when sync is
        already refusing."""
        if self.mesh_anchor_path is None:
            return "no_anchor"
        _anchored_sync_node, mesh_anchor_error = _anchored_mesh()
        try:
            return self.sync_node.anchor_status()
        except mesh_anchor_error:
            return "anchor_unreadable"

    def receive_mesh_entries(self, entries: list[StateEntry]) -> SyncResult:
        """Merge a batch of mesh entries into this process's sync
        node: the one receive every mesh surface uses (POST
        /entries and this node's side of POST /sync).

        With no mesh anchor configured this is exactly
        SyncNode.receive_entries (experiments 007-025). With one
        configured it is AnchoredSyncNode's consultation
        (experiment 015), which returns its refusals in the
        SyncResult shape - except for a configured anchor that
        cannot be read, where the in-process node rolls the
        merge back, logs the divergence, and RAISES
        MeshAnchorError. Over the wire that raise is translated
        here into the same shape - accepted 0,
        divergence_detected True, rolled_back True, reason
        "anchor_unreadable" (sync-mesh case 15's stated
        adaptation) - so a refused receive is a sync outcome in
        the response, never a dropped connection (the lesson of
        experiment 020's finding F2). Callers hold self.lock, as
        for every other node access from the handler.
        """
        if self.mesh_anchor_path is None:
            return self.sync_node.receive_entries(entries)
        _anchored_sync_node, mesh_anchor_error = _anchored_mesh()
        try:
            return self.sync_node.receive_entries(entries)
        except mesh_anchor_error:
            return SyncResult(
                accepted=0,
                divergence_detected=True,
                rolled_back=True,
                reason="anchor_unreadable",
                state_hash=self.sync_node.state_hash(),
            )

    @property
    def snapshot_loaded_count(self) -> int | None:
        """Entries in the registry snapshot this process's
        PolicyNode currently serves from (experiment 024), for
        /health; None when no policy node is configured."""
        if self._loaded_snapshot_facts is None:
            return None
        return self._loaded_snapshot_facts.count

    def check_snapshot(self) -> dict:
        """One snapshot check on this RUNNING process (experiment
        024, sync-mesh case 13).

        Compare the snapshot file's presented facts (entry count,
        chain head, file fingerprint - experiment 022's change
        fact, computed by snapshot_delivery.snapshot_facts) with
        the facts this process's PolicyNode last loaded. Equal:
        report "no_change" and call nothing, so serving is
        undisturbed. Different: call the PolicyNode's own
        reload_snapshot() (experiments 015/021: chain verification
        and anchor consultation run before the view changes). Its
        success is a "reloaded" report and moves the last-loaded
        facts to the presented ones; any refusal (TamperedRecord,
        AnchorStale, AnchorMismatch, AnchorSignatureInvalid,
        ...) becomes a "reload_refused" report under the refusal's
        own name - experiment 022's naming, reused - with the
        last-loaded facts unmoved, so the next check re-attempts
        and re-reports the same refusal until the cause is fixed.
        With no PolicyNode configured there is nothing to reload:
        "no_snapshot".

        The check is driven by the caller (POST /check-snapshot);
        no timer or thread in this process fires it. Callers hold
        self.lock, as for every other node access from the
        handler.
        """
        if self.policy_node is None or self.snapshot_path is None:
            report = {
                "node_id": self.node_id,
                "status": "no_snapshot",
                "reason": None,
                "before_head": None,
                "after_head": None,
                "before_count": None,
                "after_count": None,
                "loaded_entries": None,
                "anchor_status": self.anchor_status,
            }
            self.last_snapshot_check = report
            return report
        before = self._loaded_snapshot_facts
        try:
            presented = snapshot_facts(self.snapshot_path)
        except Exception as exc:  # unreadable or malformed file
            report = {
                "node_id": self.node_id,
                "status": "reload_refused",
                "reason": _reload_refusal_reason(exc),
                "before_head": before.head,
                "after_head": before.head,
                "before_count": before.count,
                "after_count": before.count,
                "loaded_entries": None,
                "anchor_status": self.anchor_status,
            }
            self.last_snapshot_check = report
            return report
        if presented == before:
            report = {
                "node_id": self.node_id,
                "status": "no_change",
                "reason": None,
                "before_head": before.head,
                "after_head": presented.head,
                "before_count": before.count,
                "after_count": presented.count,
                "loaded_entries": None,
                "anchor_status": self.anchor_status,
            }
            self.last_snapshot_check = report
            return report
        try:
            loaded = self.policy_node.reload_snapshot()
        except Exception as exc:
            report = {
                "node_id": self.node_id,
                "status": "reload_refused",
                "reason": _reload_refusal_reason(exc),
                "before_head": before.head,
                "after_head": presented.head,
                "before_count": before.count,
                "after_count": presented.count,
                "loaded_entries": None,
                "anchor_status": self.anchor_status,
            }
            self.last_snapshot_check = report
            return report
        self._loaded_snapshot_facts = presented
        report = {
            "node_id": self.node_id,
            "status": "reloaded",
            "reason": None,
            "before_head": before.head,
            "after_head": presented.head,
            "before_count": before.count,
            "after_count": presented.count,
            "loaded_entries": loaded,
            "anchor_status": self.anchor_status,
        }
        self.last_snapshot_check = report
        return report

    def deliver_snapshot(self, entries: list[dict]) -> dict:
        """One registry snapshot delivery received over the wire
        (experiment 025, sync-mesh case 14).

        `entries` are the delivered snapshot's Gate 1 log
        entries as plain JSON dicts - the record itself, handed
        over as data. The delivery is judged exactly as
        strictly as a file delivery:

        - Identical to the chain this process already stands
          on (same entry hashes, in order): "no_change" -
          nothing is written and nothing reloads.
        - Exactly an earlier prefix of that chain (fewer
          entries, every delivered hash the loaded hash at its
          index): refused as "snapshot_regression", the name
          case 14 sets. The judgement runs on the hashes
          alone, before any verification: it says only "this
          is our own chain, only older", and a shorter chain
          that is NOT this prefix never reaches it.
        - Anything else is written to a candidate file beside
          the snapshot and put through a full PolicyNode
          construction with this process's own anchor
          configuration - chain verification plus the
          experiment 015/019 anchor consultation (signature
          included where a signer is configured) - BEFORE
          anything changes. Any refusal the construction
          raises (TamperedRecord, AnchorStale, AnchorMismatch,
          AnchorSignatureInvalid, ...) becomes a
          "delivery_refused" report under the refusal's own
          name (experiment 022's naming, reused through
          check_snapshot's import), and neither the snapshot
          file nor the serving view changes, byte-for-byte.
        - A candidate that stands replaces the snapshot file
          (os.replace of the verified candidate, serialized in
          Gate 1's own log format - json.dumps(entry,
          sort_keys=True), one entry per line, exactly as
          registration.py writes its log) and the running
          PolicyNode reloads from that file, experiment 024's
          reload path: the file and the serving view move
          together, so a later restart or check sees the same
          record.

        With no PolicyNode configured there is nothing to
        deliver to: "no_snapshot". The delivery is driven by
        the pushing peer (POST /deliver-snapshot); no timer or
        thread in this process solicits one. Callers hold
        self.lock, as for every other node access from the
        handler.
        """
        if self.policy_node is None or self.snapshot_path is None:
            report = {
                "node_id": self.node_id,
                "status": "no_snapshot",
                "reason": None,
                "before_head": None,
                "after_head": None,
                "before_count": None,
                "after_count": None,
                "loaded_entries": None,
                "anchor_status": self.anchor_status,
            }
            self.last_snapshot_delivery = report
            return report
        before = self._loaded_snapshot_facts
        # The loaded view's hashes: PolicyNode keeps its
        # verified entries in _entries (the policy-library's
        # own modules reach into placement internals the same
        # way - see snapshot_delivery.py). "Stands on" means
        # this view, not the file: the file is only the view's
        # durable form, kept in step by 024's checks and by
        # this method's stood deliveries.
        loaded_hashes = [e["entry_hash"] for e in self.policy_node._entries]
        delivered_hashes = [e.get("entry_hash") for e in entries]
        after_head = delivered_hashes[-1] if delivered_hashes else GENESIS_HASH

        def _report(status, reason, presented_count, presented_head,
                    loaded_entries):
            return {
                "node_id": self.node_id,
                "status": status,
                "reason": reason,
                "before_head": before.head,
                "after_head": presented_head,
                "before_count": before.count,
                "after_count": presented_count,
                "loaded_entries": loaded_entries,
                "anchor_status": self.anchor_status,
            }

        if delivered_hashes == loaded_hashes:
            report = _report("no_change", None, before.count, before.head,
                             None)
            self.last_snapshot_delivery = report
            return report
        if (len(delivered_hashes) < len(loaded_hashes)
                and delivered_hashes == loaded_hashes[:len(delivered_hashes)]):
            report = _report("delivery_refused", "snapshot_regression",
                             len(entries), after_head, None)
            self.last_snapshot_delivery = report
            return report

        candidate_path = self.snapshot_path.with_name(
            self.snapshot_path.name + ".wire-candidate")
        candidate_bytes = "".join(
            json.dumps(entry, sort_keys=True) + "\n" for entry in entries
        ).encode("utf-8")
        try:
            candidate_path.write_bytes(candidate_bytes)
            try:
                # The candidate judges itself: a full PolicyNode
                # construction over the candidate file, with this
                # process's anchor configuration and no payloads
                # (construction verifies; it serves nothing).
                PolicyNode(
                    node_id=self.node_id,
                    registry_snapshot_path=candidate_path,
                    payloads={},
                    serve_policies=False,
                    anchor_path=self.anchor_path,
                    anchor_signer=self.anchor_signer,
                    anchor_secret_hex=self.anchor_secret_hex,
                )
            except Exception as exc:
                report = _report("delivery_refused",
                                 _reload_refusal_reason(exc),
                                 len(entries), after_head, None)
                self.last_snapshot_delivery = report
                return report
            # The delivery stands. The file is replaced by the
            # verified candidate and the running node reloads
            # from it (the reload re-verifies the same bytes
            # from the real path, as every 024 reload does).
            os.replace(candidate_path, self.snapshot_path)
            loaded = self.policy_node.reload_snapshot()
        finally:
            if candidate_path.exists():
                candidate_path.unlink()
        presented = snapshot_facts(self.snapshot_path)
        self._loaded_snapshot_facts = presented
        report = _report("delivered", None, presented.count, presented.head,
                         loaded)
        self.last_snapshot_delivery = report
        return report


# -- HTTP client helpers (used by /sync, the tests, and the demo) ------------
#
# Protected links (experiment 014): when set_client_tls has been
# called, every https:// request through these helpers presents the
# configured client certificate and verifies the server against the
# configured CA, via protected_link.client_context. Plain http://
# requests keep experiment 007's original behavior exactly, so the
# unmodified network tests exercise the same code path as before.

_CLIENT_CONTEXT = None
_CLIENT_NODE_ID = None

# The header a client uses to CLAIM its node id on a protected
# link (experiment 017). A binding-enabled server compares this
# claim against the id inside the certificate the client actually
# presented; the header alone grants nothing.
CLAIM_HEADER = "X-PBA-Node-Id"


def set_client_tls(cert, key, ca_cert, node_id: str | None = None) -> None:
    """Configure the credential these client helpers present on
    https:// connections, until clear_client_tls is called. The
    server process sets this from its own node certificate in main,
    so a server initiating /sync authenticates to its peer with the
    same credential it requires from clients. `node_id` is the id
    these helpers CLAIM on each request (sent as the X-PBA-Node-Id
    header); a binding-enabled peer checks it against the
    certificate's own id."""
    global _CLIENT_CONTEXT, _CLIENT_NODE_ID
    _CLIENT_CONTEXT = protected_link.client_context(cert, key, ca_cert)
    _CLIENT_NODE_ID = node_id


def clear_client_tls() -> None:
    global _CLIENT_CONTEXT, _CLIENT_NODE_ID
    _CLIENT_CONTEXT = None
    _CLIENT_NODE_ID = None


def probe_peer_node_id(base_url: str) -> str | None:
    """The node id inside the SERVER certificate at base_url, read
    from a fresh TLS connection's peer certificate. The handshake
    is the same mutual TLS as any other link, so this succeeds for
    any CA-issued server certificate, matching claim or not - the
    comparison against the claim is the caller's check."""
    if _CLIENT_CONTEXT is None:
        raise RuntimeError("probe_peer_node_id needs set_client_tls first")
    parts = urllib.parse.urlparse(base_url)
    host = parts.hostname or "127.0.0.1"
    port = parts.port
    raw = socket.create_connection((host, port), timeout=10)
    try:
        with _CLIENT_CONTEXT.wrap_socket(raw, server_hostname=host) as tls:
            return protected_link.peercert_node_id(tls.getpeercert())
    except Exception:
        raw.close()
        raise


def verify_peer_identity(base_url: str, expected_node_id: str | None = None) -> str:
    """Initiating-side identity check (experiment 017): the peer
    server's certificate id must equal the node id the peer claims
    in its health response (and `expected_node_id`, when given).
    Returns the verified node id; raises
    protected_link.IdentityMismatch, naming claimed and certified
    ids, on any disagreement."""
    claimed = fetch_health(base_url)["node_id"]
    certified = probe_peer_node_id(base_url)
    if certified != claimed:
        raise protected_link.IdentityMismatch(claimed, certified)
    if expected_node_id is not None and certified != expected_node_id:
        raise protected_link.IdentityMismatch(expected_node_id, certified)
    return certified


def _urlopen(req: urllib.request.Request, timeout: float = 10):
    if req.full_url.startswith("https://") and _CLIENT_CONTEXT is not None:
        return urllib.request.urlopen(req, timeout=timeout, context=_CLIENT_CONTEXT)
    return urllib.request.urlopen(req, timeout=timeout)


def _request_json(method: str, url: str, payload: dict | None = None) -> tuple[int, dict]:
    data = None
    headers = {}
    if _CLIENT_NODE_ID is not None:
        headers[CLAIM_HEADER] = _CLIENT_NODE_ID
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"error": body}


def get_json(base_url: str, path: str) -> tuple[int, dict]:
    return _request_json("GET", base_url.rstrip("/") + path)


def post_json(base_url: str, path: str, payload: dict) -> tuple[int, dict]:
    return _request_json("POST", base_url.rstrip("/") + path, payload)


def fetch_health(base_url: str) -> dict:
    status, body = get_json(base_url, "/health")
    assert status == 200, f"health check failed: {status} {body}"
    return body


def fetch_state(base_url: str) -> dict:
    status, body = get_json(base_url, "/state")
    assert status == 200, f"state fetch failed: {status} {body}"
    return body


def mutate(base_url: str, key: str, value: str) -> dict:
    status, body = post_json(base_url, "/mutate", {"key": key, "value": value})
    assert status == 200, f"mutate failed: {status} {body}"
    return body


def fetch_entries(base_url: str) -> list[StateEntry]:
    """Pull a peer's entries and deserialize them. Callers merge
    them through SyncNode.receive_entries, which is where they are
    verified; this function deliberately verifies nothing itself."""
    status, body = get_json(base_url, "/entries")
    assert status == 200, f"entries fetch failed: {status} {body}"
    return [entry_from_dict(d) for d in body["entries"]]


def push_entries(base_url: str, entries: list[StateEntry]) -> dict:
    """Push entries to a peer; returns the peer's SyncResult dict
    (its verification verdict on what it received)."""
    status, body = post_json(
        base_url, "/entries", {"entries": [entry_to_dict(e) for e in entries]}
    )
    assert status == 200, f"entries push failed: {status} {body}"
    return body


def request_sync(base_url: str, peer_url: str) -> dict:
    """Ask the node at base_url to sync with the peer process at
    peer_url (server-to-server: pull, verified merge, push back)."""
    status, body = post_json(base_url, "/sync", {"peer": peer_url})
    assert status == 200, f"sync request failed: {status} {body}"
    return body


def check_snapshot(base_url: str) -> dict:
    """Drive one snapshot check on the RUNNING server at base_url
    (experiment 024): the server compares its own snapshot file's
    presented facts with the facts its PolicyNode last loaded and,
    on a clean change, reloads in the same check. Returns the
    check's report dict: status "reloaded", "reload_refused"
    (with the refusal's own name as "reason"), "no_change", or
    "no_snapshot"."""
    status, body = post_json(base_url, "/check-snapshot", {})
    assert status == 200, f"snapshot check failed: {status} {body}"
    return body


def deliver_snapshot(base_url: str, entries: list[dict]) -> dict:
    """Push one registry snapshot delivery to the RUNNING server
    at base_url (experiment 025): `entries` are the snapshot's
    Gate 1 log entries as JSON dicts - the record itself, not a
    path. The receiving server verifies them as a chain and
    consults its configured anchor (and signature) before
    anything changes; only a delivery that stands rewrites its
    snapshot file and reloads its PolicyNode. Returns the
    delivery's report dict: status "delivered",
    "delivery_refused" (with the refusal's own name as
    "reason"), "no_change", or "no_snapshot"."""
    status, body = post_json(
        base_url, "/deliver-snapshot", {"entries": entries})
    assert status == 200, f"snapshot delivery failed: {status} {body}"
    return body


def push_snapshot(base_url: str, peer_url: str) -> tuple[int, dict]:
    """Ask the node at base_url to push its standing registry
    snapshot to the peer process at peer_url (server-to-server,
    experiment 027): the pushing server sends the entries its
    PolicyNode stands on to the peer's /deliver-snapshot
    (experiment 025), which judges and applies them under
    exactly case 14's rules. Returns the (status, body) pair -
    fetch_policy's shape, not request_sync's, because a push's
    refusal can ride a non-200 status and the caller must see
    its body. On 200 the body is the push's report: "status"
    equal to the peer's delivery status ("delivered",
    "delivery_refused", "no_change", or "no_snapshot" when
    the pushing server holds no snapshot) and the peer's
    delivery report under "delivery". On 502 the body names
    the transport refusal - "identity_mismatch" with the
    claimed and certified ids over a binding transport, in
    /sync's refusal shape."""
    return post_json(base_url, "/push-snapshot", {"peer": peer_url})


def fetch_policy(base_url: str, policy_hash: str) -> tuple[int, dict]:
    """Fetch a served policy. Returns (status, body); on 200 the
    body carries the code as base64 in "code_base64"."""
    return get_json(base_url, f"/policy/{policy_hash}")


def fetch_snapshot_bytes(base_url: str) -> bytes:
    """Fetch a peer's raw registry snapshot bytes. The receiver is
    responsible for verifying them (Gate 2's verify_registry_
    snapshot over a file written from these bytes) before use."""
    req = urllib.request.Request(base_url.rstrip("/") + "/snapshot", method="GET")
    if _CLIENT_NODE_ID is not None:
        req.add_header(CLAIM_HEADER, _CLIENT_NODE_ID)
    with _urlopen(req, timeout=10) as resp:
        assert resp.status == 200
        return resp.read()


# -- HTTP server ---------------------------------------------------------------


def make_handler(node: NetworkNode):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # -- plumbing ---------------------------------------------------

        def log_message(self, fmt, *args):  # keep test output clean
            pass

        def _send_json(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_bytes(self, status: int, data: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_json_body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8"))

        def _check_peer_identity(self) -> bool:
            """Accepting-side identity check (experiment 017).
            With binding off this always passes, exactly as in
            experiment 014. With binding on, the connecting peer's
            certificate id (read from THIS connection's peer
            certificate) must equal the node id the peer claims in
            the X-PBA-Node-Id header. A mismatch is refused here,
            before any endpoint runs, with a plainly stated
            identity_mismatch naming both ids - over HTTP, because
            the handshake itself already succeeded on possession
            of a valid CA-issued certificate."""
            if not node.bind_identity:
                return True
            try:
                peercert = self.connection.getpeercert()
            except (ValueError, OSError):
                peercert = None
            certified = (
                protected_link.peercert_node_id(peercert) if peercert else None
            )
            claimed = self.headers.get(CLAIM_HEADER)
            if certified is not None and claimed == certified:
                return True
            mismatch = protected_link.IdentityMismatch(claimed, certified)
            print(
                f"identity_mismatch refused: claimed={claimed} "
                f"certified={certified} path={self.path}",
                flush=True,
            )
            self._send_json(403, {
                "error": "identity_mismatch",
                "claimed": claimed,
                "certified": certified,
                "detail": str(mismatch),
            })
            return False

        # -- GET ----------------------------------------------------------

        def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
            if not self._check_peer_identity():
                return
            if self.path == "/health":
                with node.lock:
                    self._send_json(200, {
                        "node_id": node.node_id,
                        "ok": True,
                        "entries": len(node.sync_node.entry_ids()),
                        "state_hash": node.sync_node.state_hash(),
                        "serve_policies": node.serve_policies,
                        "policy_configured": node.policy_node is not None,
                        "anchor_configured": node.anchor_path is not None,
                        "anchor_status": node.anchor_status,
                        # Experiment 026: the mesh side's anchor
                        # standing, beside the registry half's
                        # fields above - whether a mesh anchor
                        # is configured for this process's sync
                        # chain, and experiment 015's
                        # classification of the mesh state it
                        # currently holds.
                        "mesh_anchor_configured": node.mesh_anchor_path is not None,
                        "mesh_anchor_status": node.mesh_anchor_status,
                        # Experiment 024: the running process's
                        # current registry standing - how many
                        # entries its PolicyNode serves from, and
                        # the last live-reload check's report
                        # (None until the first check is driven).
                        "snapshot_entries": node.snapshot_loaded_count,
                        "last_snapshot_check": node.last_snapshot_check,
                        # Experiment 025: the last wire delivery's
                        # report (None until the first delivery is
                        # pushed), beside the last check's.
                        "last_snapshot_delivery": node.last_snapshot_delivery,
                        # Experiment 027: the last push this
                        # process initiated toward a peer (its
                        # response body; None until the first
                        # push is driven), beside the last
                        # delivery's.
                        "last_snapshot_push": node.last_snapshot_push,
                    })
                return
            if self.path == "/state":
                with node.lock:
                    self._send_json(200, {
                        "node_id": node.node_id,
                        "state": node.sync_node.materialized_state(),
                        "state_hash": node.sync_node.state_hash(),
                        "entry_ids": sorted(node.sync_node.entry_ids()),
                    })
                return
            if self.path == "/entries":
                with node.lock:
                    entries = [entry_to_dict(e) for e in node.sync_node.entries()]
                self._send_json(200, {"node_id": node.node_id, "entries": entries})
                return
            if self.path == "/snapshot":
                if node.snapshot_path is None or not node.snapshot_path.exists():
                    self._send_json(404, {"error": "no registry snapshot on this node"})
                    return
                self._send_bytes(
                    200,
                    node.snapshot_path.read_bytes(),
                    "application/x-ndjson",
                )
                return
            if self.path.startswith("/policy/"):
                self._serve_policy(self.path[len("/policy/"):])
                return
            self._send_json(404, {"error": f"unknown path: {self.path}"})

        def _serve_policy(self, policy_hash: str) -> None:
            if node.policy_node is None:
                self._send_json(404, {"error": "policy serving not configured on this node"})
                return
            try:
                with node.lock:
                    served = node.policy_node.serve_policy(policy_hash)
            except NotServing as exc:
                self._send_json(403, {"error": "not_serving", "detail": str(exc)})
                return
            except PolicyNotFound as exc:
                self._send_json(404, {"error": "policy_not_found", "detail": str(exc)})
                return
            except PolicyRevoked as exc:
                # Experiment 021 (finding F2 of experiment 020):
                # PolicyRevoked is a sibling PolicyLibraryError,
                # not a subclass of the refusals above; uncaught,
                # it propagated out of this handler and dropped
                # the connection with no HTTP response at all.
                # It now maps to a clean refusal in the same
                # shape: 410 Gone, error naming the revocation.
                self._send_json(410, {"error": "policy_revoked", "detail": str(exc)})
                return
            except TamperedRecord as exc:
                self._send_json(409, {"error": "tampered_record", "detail": str(exc)})
                return
            body = served.to_dict()
            body["code_base64"] = base64.b64encode(served.code).decode("ascii")
            body["served_by"] = node.node_id
            self._send_json(200, body)

        # -- POST -----------------------------------------------------------

        def do_POST(self):  # noqa: N802 (BaseHTTPRequestHandler API)
            try:
                payload = self._read_json_body()
            except (ValueError, UnicodeDecodeError) as exc:
                self._send_json(400, {"error": f"bad JSON body: {exc}"})
                return
            if not self._check_peer_identity():
                return
            if self.path == "/mutate":
                key, value = payload.get("key"), payload.get("value")
                if not isinstance(key, str) or not isinstance(value, str):
                    self._send_json(400, {"error": "mutate needs string key and value"})
                    return
                with node.lock:
                    entry = node.sync_node.mutate(key, value)
                    state_hash = node.sync_node.state_hash()
                self._send_json(200, {
                    "entry": entry_to_dict(entry),
                    "state_hash": state_hash,
                })
                return
            if self.path == "/entries":
                try:
                    incoming = [entry_from_dict(d) for d in payload.get("entries", [])]
                except (KeyError, TypeError, ValueError) as exc:
                    self._send_json(400, {"error": f"bad entry encoding: {exc}"})
                    return
                # Gate 3 verification runs here, inside receive_entries:
                # hash recompute, fork detection, chain check, rollback.
                # Since experiment 026 the receive is the node's one
                # mesh receive, so a configured mesh anchor is also
                # consulted on the merged state before acceptance.
                with node.lock:
                    result = node.receive_mesh_entries(incoming)
                self._send_json(200, result.to_dict())
                return
            if self.path == "/sync":
                peer = payload.get("peer")
                if not isinstance(peer, str) or not peer:
                    self._send_json(400, {"error": "sync needs a peer base URL"})
                    return
                self._sync_with_peer(peer)
                return
            if self.path == "/check-snapshot":
                # Experiment 024: one caller-driven snapshot check
                # on this running process (see NetworkNode.
                # check_snapshot). The report is the response,
                # refusals included: a refused reload is a check
                # outcome, not a transport error, exactly as a
                # divergence is a /entries outcome.
                with node.lock:
                    report = node.check_snapshot()
                self._send_json(200, report)
                return
            if self.path == "/deliver-snapshot":
                # Experiment 025: one registry snapshot delivery
                # pushed to this running process (see NetworkNode.
                # deliver_snapshot). The payload carries the
                # snapshot entries themselves; a bare list of
                # entry objects is the only accepted shape. The
                # report is the response, refusals included: a
                # refused delivery is a delivery outcome, not a
                # transport error, exactly as above.
                raw_entries = payload.get("entries")
                if (not isinstance(raw_entries, list)
                        or not all(isinstance(e, dict)
                                   for e in raw_entries)):
                    self._send_json(400, {
                        "error": "deliver-snapshot needs an entries "
                                 "list of snapshot entry objects"})
                    return
                with node.lock:
                    report = node.deliver_snapshot(raw_entries)
                self._send_json(200, report)
                return
            if self.path == "/push-snapshot":
                # Experiment 027: this process pushes its own
                # standing registry record to a peer process
                # (see _push_snapshot_to_peer). The payload is
                # the peer's base URL, exactly /sync's shape.
                peer = payload.get("peer")
                if not isinstance(peer, str) or not peer:
                    self._send_json(400, {
                        "error": "push-snapshot needs a peer base URL"})
                    return
                self._push_snapshot_to_peer(peer)
                return
            self._send_json(404, {"error": f"unknown path: {self.path}"})

        def _record_push(self, body: dict) -> dict:
            """Record a push's outcome as this process's last
            push (experiment 027) and return the body for
            sending: /health carries the response body of the
            last push driven on this process, a 200 report or
            a 502 refusal alike, beside the last check and the
            last delivery."""
            with node.lock:
                node.last_snapshot_push = body
            return body

        def _push_snapshot_to_peer(self, peer: str) -> None:
            # Initiating-side identity check (experiment 017),
            # exactly as _sync_with_peer: with binding on, the
            # peer server's certificate id is checked against
            # the node id the peer claims in its health response
            # BEFORE anything is pushed. A mismatch refuses the
            # push outright; no record moves.
            if node.bind_identity:
                try:
                    verify_peer_identity(peer)
                except protected_link.IdentityMismatch as exc:
                    print(
                        f"identity_mismatch refused push to {peer}: "
                        f"claimed={exc.claimed} certified={exc.certified}",
                        flush=True,
                    )
                    self._send_json(502, self._record_push({
                        "error": "identity_mismatch",
                        "claimed": exc.claimed,
                        "certified": exc.certified,
                        "detail": str(exc),
                    }))
                    return
                except (AssertionError, urllib.error.URLError, OSError) as exc:
                    self._send_json(502, self._record_push(
                        {"error": f"peer unreachable: {exc}"}))
                    return
            # The record this process STANDS ON: the PolicyNode's
            # loaded, verified entries - what it serves from,
            # which experiments 024/025 keep in step with its
            # snapshot file - never the file read around the
            # view. A process with no PolicyNode holds no record
            # to push.
            with node.lock:
                entries = (
                    [dict(e) for e in node.policy_node._entries]
                    if node.policy_node is not None else None
                )
            if entries is None:
                self._send_json(200, self._record_push({
                    "node_id": node.node_id,
                    "peer": peer,
                    "status": "no_snapshot",
                    "delivery": None,
                }))
                return
            # The peer judges: its /deliver-snapshot (experiment
            # 025) verifies the entries as a chain and consults
            # its anchor before anything changes. post_json is
            # used directly rather than the deliver_snapshot
            # helper (which asserts a 200) because a refusal can
            # arrive as a non-200 and its body is the outcome.
            try:
                status, delivery = post_json(
                    peer, "/deliver-snapshot", {"entries": entries})
            except (urllib.error.URLError, OSError) as exc:
                self._send_json(502, self._record_push(
                    {"error": f"peer push failed: {exc}"}))
                return
            if status != 200:
                # The peer refused before its delivery endpoint
                # ran - over a binding transport, experiment
                # 017's accepting-side identity_mismatch (403
                # naming claimed and certified ids). The refusal
                # is the push's outcome and is reported in the
                # same shape /sync reports its refusals, not
                # hidden behind a generic transport error.
                body = {"error": delivery.get(
                    "error", f"peer refused the push: HTTP {status}")}
                for key in ("claimed", "certified", "detail"):
                    if key in delivery:
                        body[key] = delivery[key]
                self._send_json(502, self._record_push(body))
                return
            # The peer's delivery report IS the push's result,
            # refusals included: the peer judged, and its
            # verdict is reported, not re-derived here.
            self._send_json(200, self._record_push({
                "node_id": node.node_id,
                "peer": peer,
                "status": delivery.get("status"),
                "delivery": delivery,
            }))

        def _sync_with_peer(self, peer: str) -> None:
            # Initiating-side identity check (experiment 017): with
            # binding on, the peer server's certificate id is
            # checked against the node id the peer claims in its
            # health response BEFORE any entries are pulled. A
            # mismatch refuses the sync outright; no state moves.
            if node.bind_identity:
                try:
                    verify_peer_identity(peer)
                except protected_link.IdentityMismatch as exc:
                    print(
                        f"identity_mismatch refused sync with {peer}: "
                        f"claimed={exc.claimed} certified={exc.certified}",
                        flush=True,
                    )
                    self._send_json(502, {
                        "error": "identity_mismatch",
                        "claimed": exc.claimed,
                        "certified": exc.certified,
                        "detail": str(exc),
                    })
                    return
                except (AssertionError, urllib.error.URLError, OSError) as exc:
                    self._send_json(502, {"error": f"peer unreachable: {exc}"})
                    return
            # Pull: the peer's entries are untrusted data until this
            # node's receive_entries verifies them in the merge.
            try:
                their_entries = fetch_entries(peer)
            except (AssertionError, urllib.error.URLError, OSError) as exc:
                self._send_json(502, {"error": f"peer unreachable: {exc}"})
                return
            with node.lock:
                result_self = node.receive_mesh_entries(their_entries)
                merged = node.sync_node.entries()
            # Push: the peer verifies the merged set again on its side.
            try:
                result_peer = push_entries(peer, merged)
            except (AssertionError, urllib.error.URLError, OSError) as exc:
                self._send_json(502, {
                    "error": f"peer push failed: {exc}",
                    "self": result_self.to_dict(),
                })
                return
            with node.lock:
                state_hash = node.sync_node.state_hash()
            self._send_json(200, {
                "self": result_self.to_dict(),
                "peer": result_peer,
                "state_hash": state_hash,
            })

    return Handler


def build_server(node: NetworkNode, host: str, port: int,
                 tls_context=None) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(node))
    server.daemon_threads = True
    if tls_context is not None:
        # Protected link (experiment 014): the listening socket
        # itself speaks TLS and the context requires a client
        # certificate, so an unauthenticated peer cannot reach any
        # endpoint - the handshake is refused before HTTP begins,
        # and there is no plaintext mode on this port.
        server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PBA networked node server (prototype)")
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0,
                        help="0 (default) picks an ephemeral port")
    parser.add_argument("--snapshot", default=None,
                        help="path to this node's copy of the registry snapshot")
    parser.add_argument("--payloads", default=None,
                        help="JSON file mapping policy_hash -> code hex")
    parser.add_argument("--serve-policies", action="store_true",
                        help="opt in to serving policies (Gate 2 choice)")
    parser.add_argument("--anchor", default=None,
                        help="path to an experiment 012 registry anchor for "
                             "--snapshot's chain (experiment 018); requires "
                             "--snapshot. The PolicyNode consults it at "
                             "startup and the process refuses to start on "
                             "anchor_mismatch or anchor_stale")
    parser.add_argument("--anchor-signer", default=None,
                        help="signing identity for a signed anchor "
                             "(experiment 019); requires --anchor and "
                             "--anchor-secret-file. With it, the PolicyNode "
                             "also verifies the anchor's signature at "
                             "startup and the process refuses to start on "
                             "anchor_signature_invalid")
    parser.add_argument("--anchor-secret-file", default=None,
                        help="file holding the anchor signer's enrollment "
                             "secret (hex), read once at startup; "
                             "requires --anchor-signer. A file, not a "
                             "value argument, so the secret does not ride "
                             "in the process command line")
    parser.add_argument("--mesh-anchor", default=None,
                        help="path to an experiment 012 mesh anchor for "
                             "this process's sync chain (experiment 026); "
                             "independent of --snapshot. The mesh side "
                             "then runs as experiment 015's "
                             "AnchoredSyncNode and consults the anchor on "
                             "every mesh receive, flagging a rewritten "
                             "peer anchor_mismatch and honest growth "
                             "anchor_stale. Without it the mesh side is "
                             "a plain SyncNode and consults nothing")
    parser.add_argument("--mesh-anchor-signer", default=None,
                        help="signing identity for a signed mesh anchor "
                             "(experiment 019); requires --mesh-anchor "
                             "and --mesh-anchor-secret-file. With it, "
                             "every mesh consultation also verifies the "
                             "anchor's signature, refusing as "
                             "anchor_signature_invalid")
    parser.add_argument("--mesh-anchor-secret-file", default=None,
                        help="file holding the mesh anchor signer's "
                             "enrollment secret (hex), read once at "
                             "startup; requires --mesh-anchor-signer. "
                             "A file, not a value argument, so the "
                             "secret does not ride in the process "
                             "command line")
    parser.add_argument("--port-file", default=None,
                        help="write the bound port to this file once listening")
    parser.add_argument("--tls-cert", default=None,
                        help="certificate file for a protected link (experiment 014); "
                             "requires --tls-key and --tls-ca")
    parser.add_argument("--tls-key", default=None,
                        help="private key file matching --tls-cert")
    parser.add_argument("--tls-ca", default=None,
                        help="CA that must have issued every peer's certificate")
    parser.add_argument("--tls-bind-identity", action="store_true",
                        help="bind certificates to claimed node ids "
                             "(experiment 017); requires the TLS arguments")
    args = parser.parse_args(argv)

    if args.anchor and not args.snapshot:
        parser.error("--anchor requires --snapshot (the anchor is for "
                     "the snapshot's registry chain)")
    anchor_identity = (args.anchor_signer, args.anchor_secret_file)
    if any(anchor_identity) and not all(anchor_identity):
        parser.error("--anchor-signer and --anchor-secret-file must be "
                     "given together")
    if args.anchor_signer and not args.anchor:
        parser.error("--anchor-signer requires --anchor (the signing "
                     "identity is for that anchor)")
    anchor_secret_hex = None
    if args.anchor_secret_file:
        anchor_secret_hex = Path(
            args.anchor_secret_file).read_text(encoding="utf-8").strip()

    mesh_anchor_identity = (args.mesh_anchor_signer,
                            args.mesh_anchor_secret_file)
    if any(mesh_anchor_identity) and not all(mesh_anchor_identity):
        parser.error("--mesh-anchor-signer and --mesh-anchor-secret-file "
                     "must be given together")
    if args.mesh_anchor_signer and not args.mesh_anchor:
        parser.error("--mesh-anchor-signer requires --mesh-anchor (the "
                     "signing identity is for that anchor)")
    mesh_anchor_secret_hex = None
    if args.mesh_anchor_secret_file:
        mesh_anchor_secret_hex = Path(
            args.mesh_anchor_secret_file).read_text(encoding="utf-8").strip()

    tls_values = (args.tls_cert, args.tls_key, args.tls_ca)
    if any(tls_values) and not all(tls_values):
        parser.error("--tls-cert, --tls-key, and --tls-ca must be given together")
    if args.tls_bind_identity and not all(tls_values):
        parser.error("--tls-bind-identity requires --tls-cert, --tls-key, and --tls-ca")
    if args.tls_bind_identity:
        # Startup self-check: this process may not even claim a
        # node id its own certificate was not issued for. Refusing
        # to start states the mismatch plainly on stderr.
        certified = protected_link.cert_file_node_id(args.tls_cert)
        if certified != args.node_id:
            parser.error(
                f"identity_mismatch: this process claims node id "
                f"{args.node_id!r} but its certificate was issued for "
                f"{certified!r}; refusing to start"
            )

    payloads: dict[str, bytes] = {}
    if args.payloads:
        raw = json.loads(Path(args.payloads).read_text(encoding="utf-8"))
        payloads = {h: bytes.fromhex(hex_code) for h, hex_code in raw.items()}

    # A tampered snapshot raises here (PolicyNode verifies the chain
    # at construction) and the process exits nonzero: refused start.
    # With --anchor, a rewritten snapshot (AnchorMismatch) or an
    # honestly grown one (AnchorStale) raises at the same place with
    # the same effect (experiment 018).
    node = NetworkNode(
        node_id=args.node_id,
        registry_snapshot_path=args.snapshot,
        payloads=payloads,
        serve_policies=args.serve_policies,
        bind_identity=args.tls_bind_identity,
        anchor_path=args.anchor,
        anchor_signer=args.anchor_signer,
        anchor_secret_hex=anchor_secret_hex,
        mesh_anchor_path=args.mesh_anchor,
        mesh_anchor_signer=args.mesh_anchor_signer,
        mesh_anchor_secret_hex=mesh_anchor_secret_hex,
    )
    tls_context = None
    if all(tls_values):
        tls_context = protected_link.server_context(
            args.tls_cert, args.tls_key, args.tls_ca
        )
        # Outgoing connections from this process (server-to-server
        # /sync) present this node's own certificate, so the peer
        # authenticates this process exactly as it authenticates
        # any other client. The claim sent with them is this
        # process's node id, always: with binding on, a peer checks
        # it against the certificate; with binding off (experiment
        # 014 mode), a binding peer still checks it - which is what
        # lets a false-claiming 014-mode process be refused by a
        # binding node it initiates toward.
        set_client_tls(args.tls_cert, args.tls_key, args.tls_ca,
                       node_id=args.node_id)
    server = build_server(node, args.host, args.port, tls_context=tls_context)
    bound_port = server.server_address[1]
    if args.port_file:
        Path(args.port_file).write_text(str(bound_port), encoding="utf-8")
    print(f"LISTENING {bound_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
