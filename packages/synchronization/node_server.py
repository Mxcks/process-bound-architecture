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
                     anchor_configured, anchor_status}
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
                    or payload -> 409.

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
  consulted here is the REGISTRY snapshot's; this process's mesh
  entries are still held by a plain SyncNode and networked sync
  does not consult a mesh anchor (the in-process AnchoredSyncNode
  of experiment 015 does, in-process).
- A fetched snapshot is verified by the receiver with Gate 2's
  verifier before any PolicyNode is built over it.

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
  quorum, local-only rollback, no revocation, and HMAC prototype
  signing in Gate 1. An external chain anchor exists (experiment
  012) and, since experiment 018, a server started with --anchor
  consults the registry anchor at startup (see above); since
  experiment 019 the anchor may be signed, and a server started
  with --anchor-signer plus --anchor-secret-file also verifies
  that signature at startup, refusing to start on
  anchor_signature_invalid. Without those arguments the
  consultation is experiment 018's head-only one. The mesh chain
  in this process consults no anchor.
"""

from __future__ import annotations

import argparse
import base64
import json
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
    NotServing,
    PolicyNode,
    PolicyNotFound,
    TamperedRecord,
)
import protected_link  # noqa: E402
from sync_mesh import StateEntry, SyncNode  # noqa: E402


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

    A SyncNode always; a PolicyNode only when the process was started
    with a registry snapshot (its own copy). All access is serialized
    with one lock because the HTTP server is threaded while the Gate
    3/2 prototypes are single-threaded objects.
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
    ):
        self.node_id = node_id
        # Experiment 017: when True (and the process runs over
        # mutual TLS), the handler checks every connecting peer's
        # certificate id against the id that peer claims, and this
        # node's own /sync checks a peer server's certificate id
        # against its claim before pulling entries.
        self.bind_identity = bool(bind_identity)
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
                with node.lock:
                    result = node.sync_node.receive_entries(incoming)
                self._send_json(200, result.to_dict())
                return
            if self.path == "/sync":
                peer = payload.get("peer")
                if not isinstance(peer, str) or not peer:
                    self._send_json(400, {"error": "sync needs a peer base URL"})
                    return
                self._sync_with_peer(peer)
                return
            self._send_json(404, {"error": f"unknown path: {self.path}"})

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
                result_self = node.sync_node.receive_entries(their_entries)
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
