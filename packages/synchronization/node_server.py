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
                     mesh_state_configured, mesh_state_load,
                     snapshot_entries, last_snapshot_check,
                     last_snapshot_delivery, last_snapshot_push,
                     last_anchor_delivery, last_anchor_push,
                     last_mesh_anchor_delivery,
                     last_mesh_anchor_push}
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
POST /sync-all   -> {} (no payload): drive one sweep over
                    this process's configured peer list
                    (experiment 035): for each peer in the
                    --peers list, in list order, this process
                    runs exactly the per-peer sync POST /sync
                    performs - one code path, two callers - and
                    collects one result per peer, classified
                    as "converged", "refused" (with the
                    refusal's own name as "reason"),
                    "unreachable", "transport_failed", or
                    "self" (the entry naming this process's
                    own bound address, skipped, no contact).
                    One peer's outcome never stops the sweep.
                    The response is always 200 - per-peer
                    outcomes are the content, not transport
                    errors - and carries the sweep's "status"
                    ("no_peers" when the sweep has no peers to
                    attempt, "converged" when every attempted
                    peer converged, "partial" otherwise), the
                    per-peer "results" in list order, and this
                    process's state hash after the whole sweep.
                    /health carries the last sweep's response
                    body as last_sync_all, and reports the
                    configured list itself as peers beside
                    peers_configured.
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
POST /push-anchor
                 -> {peer: "http://127.0.0.1:PORT"}: this process
                    pushes its own standing registry record AND
                    its own registry anchor to a peer process
                    (experiment 030): it sends the entries its
                    PolicyNode stands on and the anchor object
                    its own anchor file holds to the peer's
                    POST /deliver-anchor (experiment 028) in its
                    paired form, and the peer judges the pair
                    together under exactly experiment 028's rules
                    - the receiving side is experiment 028's
                    surface, unmodified. The response is the
                    push's report: on 200, the peer's paired
                    delivery status as "status" and the peer's
                    paired delivery report itself under
                    "delivery" (on a process with no snapshot,
                    status "no_snapshot" and "delivery" null);
                    a peer that cannot be reached, a peer whose
                    transport refuses this process before the
                    paired endpoint runs, or this process's own
                    anchor unreadable on its own side is reported
                    on 502 in /sync's refusal shape. A process
                    with no anchor configured sends the record
                    alone to the peer's POST /deliver-snapshot -
                    experiment 027's push exactly. /health
                    carries the last paired push's outcome as
                    last_anchor_push, beside last_snapshot_push.
POST /deliver-anchor
                 -> {anchor: {...}} or {anchor: {...},
                    entries: [...]}: receive one registry
                    ANCHOR delivery over the wire (experiment
                    028): the anchor object's own JSON - the
                    content of an experiment 012/019 anchor
                    file, as data, never a file path - alone, or
                    paired with the snapshot entries it blesses.
                    The anchor is judged BEFORE anything
                    changes, in sync-mesh case 17's order:
                    configuration (no_anchor / no_snapshot), it
                    must read as an anchor, byte-identical is
                    no_change, succession judged anchor to
                    anchor (experiment 019's same-identity rule:
                    different_signer, audit-logged, the current
                    anchor untouched), the backward fence
                    (anchor_regression), signature and kept
                    history under this process's configured
                    identity (anchor_signature_invalid), and
                    pairing - an anchor alone must pair with
                    the record this process already stands on
                    (anchor_unpaired), and a pair is judged
                    together by a full PolicyNode construction
                    over the candidate record with the candidate
                    anchor, exactly as a file delivery is
                    judged, so neither half is applied unless
                    both stand. A stood anchor replaces the
                    anchor file and the running PolicyNode
                    reloads; a stood pair replaces both files
                    and reloads once from the pair. The response
                    is the delivery's report: status
                    "delivered", "delivery_refused" (with the
                    refusal's own name as "reason"),
                    "no_change", "no_anchor", or "no_snapshot",
                    plus the anchor's before/after heads,
                    counts, and history sequences, the delivered
                    signer, the loaded entry count when a paired
                    record was applied, and the anchor status
                    after the delivery. /health carries the last
                    anchor delivery's report as
                    last_anchor_delivery, beside
                    last_snapshot_delivery. The REGISTRY anchor
                    only: the mesh anchor of experiment 026
                    still arrives out of band. (Superseded in
                    part by experiment 031: the mesh anchor now
                    has its own delivery surface, POST
                    /deliver-mesh-anchor below.)
POST /deliver-mesh-anchor
                 -> {anchor: {...}}: receive one MESH anchor
                    delivery over the wire (experiment 031): the
                    mesh anchor object's own JSON - the content
                    of an experiment 012/019 mesh anchor file,
                    as data, never a file path - ALONE. There is
                    no paired form and no entries field, by
                    sync-mesh case 20's stated decision: mesh
                    entries keep their own consulted receive
                    path (experiment 026), so the anchor is
                    judged against the mesh state this process
                    HOLDS at delivery time. The anchor is
                    judged BEFORE anything changes, in case
                    20's order: configuration (no mesh anchor
                    configured -> "no_mesh_anchor"), it must
                    read as a mesh anchor, the current anchor
                    must read (anchor_unreadable), byte-
                    identical is no_change, succession judged
                    anchor to anchor (experiment 019's
                    same-identity rule, for this chain:
                    different_signer, audit-logged, the
                    current anchor untouched), the backward
                    fence (anchor_regression: an earlier
                    version of this anchor file, or an anchor
                    pinning an earlier prefix of the chain the
                    current anchor pins, proved against the
                    held entries), signature and kept history
                    under this process's configured mesh
                    identity (anchor_signature_invalid), the
                    witness countersignature where a mesh
                    witness is configured
                    (anchor_witness_invalid), and the head
                    judgement against the held mesh state with
                    experiment 026's check: exactly the held
                    state, or the current anchor's successor
                    in its own kept lineage, is accepted
                    ("delivered"); honest growth held past the
                    delivered anchor is anchor_stale; a chain
                    the held state is not and does not extend
                    is anchor_mismatch. A stood anchor replaces
                    the mesh anchor file and nothing else - the
                    mesh state itself is process memory and is
                    not the delivery's to change. The response
                    is the delivery's report: status
                    "delivered", "delivery_refused" (with the
                    refusal's own name as "reason"),
                    "no_change", or "no_mesh_anchor", plus the
                    anchor's before/after state hashes, entry
                    counts, and history sequences, the
                    delivered signer, and the mesh anchor
                    status after the delivery. /health carries
                    the last mesh anchor delivery's report as
                    last_mesh_anchor_delivery, beside
                    mesh_anchor_status. The MESH anchor only:
                    the registry anchor surfaces above are
                    untouched by it.
POST /push-mesh-anchor
                 -> {peer: "http://127.0.0.1:PORT"}: this process
                    pushes its own MESH anchor to a peer process
                    (experiment 032): it sends the anchor object
                    its own mesh anchor file holds to the peer's
                    POST /deliver-mesh-anchor (experiment 031),
                    and the peer judges it under exactly
                    experiment 031's rules - the receiving side
                    is experiment 031's surface, unmodified. The
                    response is the push's report: on 200, the
                    peer's mesh anchor delivery status as
                    "status" and the peer's mesh anchor delivery
                    report itself under "delivery" (on a process
                    with no mesh anchor configured, status
                    "no_mesh_anchor" and "delivery" null, and no
                    contact with the peer is made); a peer that
                    cannot be reached, a peer whose transport
                    refuses this process before the delivery
                    endpoint runs, or this process's own mesh
                    anchor unreadable on its own side is
                    reported on 502 in /sync's refusal shape.
                    /health carries the last mesh anchor push's
                    outcome as last_mesh_anchor_push, beside
                    last_mesh_anchor_delivery.
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
- A peer sweep (POST /sync-all, experiment 035) adds NO new
  verification path: each peer in the configured list is
  synced by the identical per-peer round POST /sync runs -
  the same initiating-side identity check where binding is
  on, the same verified pull, merge, and push-back, the same
  anchored consultation on the mesh receives where a mesh
  anchor is configured - so a sweep can refuse a peer under
  exactly the names a hand-driven /sync refuses it with, and
  the two surfaces cannot disagree about what one peer sync
  does. The peer list itself is a startup fact, never wire
  data: it is parsed and validated in main() before the
  process serves, and a file that cannot be read, or a
  content line that is not an http(s) base URL, refuses the
  start as peers_unreadable.
- Started with --tls-crl (experiment 034), the protected
  link ALSO enforces a certificate revocation list issued
  by the --tls-ca authority: the CRL is loaded into the
  TLS verify store at startup and the handshake itself
  checks each peer's leaf certificate against it
  (protected_link's crl support, the standard library's
  VERIFY_CRL_CHECK_LEAF). A revoked client certificate
  never completes the handshake - it is refused before
  any endpoint runs, under the name certificate revoked,
  a different door from experiment 014's rogue-CA refusal
  (an unverifiable issuer) and from experiment 017's
  identity_mismatch (an HTTP refusal after a completed
  handshake). The same list is enforced in the other
  direction by the client context this process initiates
  with, against the server certificates it connects out
  to. The list names serials, not node ids: a fresh
  certificate for the same node id under a new serial is
  not on it and still works. A --tls-crl path that cannot
  be read or carries no parseable CRL refuses the start
  as crl_unreadable. Without --tls-crl no revocation
  checking happens and behavior is exactly experiments
  014/017's.
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
- A record and anchor this process pushes to a peer as a
  pair (POST /push-anchor, experiment 030) are judged by
  the PEER's experiment 028 paired judgement, not by the
  sender: the sender contributes only what it stands on -
  the entries its PolicyNode stands on, and the anchor
  object its own anchor file holds (the file its PolicyNode
  itself consults, so the file and the standing agree) -
  and reports the peer's paired delivery report, refusals
  included, as the push's result. The identity checks and
  the 502 refusal shape are experiment 027's, unchanged; a
  sender with no anchor configured sends the record alone
  through experiment 025's surface, as experiment 027
  built. The push performs no lifecycle step for the peer:
  succession, freshness, and any witness countersignature
  (experiment 029) are the sender's completed facts,
  carried inside the two objects and judged at the peer.
- A registry anchor delivered over the wire (POST
  /deliver-anchor, experiment 028) is judged as strictly
  as the record, and - when it arrives paired with the
  record it blesses - judged TOGETHER with it: the
  delivered anchor is serialized to a candidate file and
  read with experiment 012's reader, its succession is
  judged anchor to anchor under experiment 019's
  same-identity rule (a different signer's anchor, or an
  unsigned one over a signed anchor, is refused as
  different_signer and the refusal is appended to the
  anchor audit log beside the current anchor, which is
  left byte-for-byte unchanged), a backward anchor is
  refused as anchor_regression under case 17's fence (an
  earlier version of this very anchor file, or an anchor
  pinning an earlier prefix of the chain this process
  stands on - never a foreign chain, which falls through
  to pairing), and its signature and kept history are
  verified with experiment 019's own verification where a
  signing identity is configured (anchor_signature_
  invalid, before any head judgement). Pairing is then
  experiment 015/019's consultation itself: an anchor
  alone must be exactly the standing record's anchor
  (checked against the snapshot file), and a pair is
  judged by a full PolicyNode construction over the
  candidate record with the CANDIDATE anchor. Only a
  delivery that stands replaces files: the anchor file
  from the verified candidate, and for a pair the snapshot
  file from the verified delivered record, the running
  PolicyNode reloading from what was delivered and
  verified. What this verification does NOT do is stated
  as plainly: an anchor over a REWRITTEN record, signed by
  the very identity that signed this process's current
  anchor with its history honestly extended, passes every
  judgement above - succession, the backward fence, the
  signature - because under experiment 019's symmetric
  model the secret that verifies is the secret that signs,
  and its holder is exactly who the same-identity rule
  trusts. Case 17's finding test proves the acceptance and
  the two refusals around it (the genuine anchor over the
  rewrite: anchor_mismatch; a different identity's:
  different_signer). The fence a blessed rewrite meets is
  the custody of the signer's secret, not the wire - as it
  already was for an anchor placed by hand. (Superseded in
  part by experiment 029: started with --anchor-witness
  plus --anchor-witness-secret-file, the process REQUIRES
  the anchor to be countersigned by that second enrolled
  identity: the same finding pair is then refused as
  anchor_witness_invalid unless the witness countersigned
  it, the wire delivery judgement included, in sync-mesh
  case 18's re-run of this finding. The fence moves from
  one secret's custody to two; the model stays symmetric.)
- A MESH anchor delivered over the wire (POST
  /deliver-mesh-anchor, experiment 031) is judged by the
  mesh side's own machinery, reusing experiment 012's
  reader, experiment 019's verification, experiment 029's
  witness verification, and experiment 026's check - loaded
  from this package's anchor module, never copied: the
  delivered anchor is serialized to a candidate file and
  read with experiment 012's mesh reader, an unreadable
  CURRENT anchor is anchor_unreadable, a byte-identical
  delivery is no_change, succession is judged anchor to
  anchor under experiment 019's same-identity rule for
  this chain (a different signer's anchor, or an unsigned
  one over a signed anchor, is refused as different_signer
  and the refusal is appended to the anchor audit log
  beside the current mesh anchor, which is left byte-for-
  byte unchanged), a backward anchor is refused as
  anchor_regression under case 20's fence (an earlier
  version of this very anchor file, or an anchor pinning
  an earlier prefix of the chain the current anchor pins,
  proved against the held entries - never a foreign chain,
  which falls through to the head judgement), its
  signature and kept history are verified with experiment
  019's own verification where a mesh signing identity is
  configured (anchor_signature_invalid, before any head
  judgement), and its countersignature against the
  configured mesh witness where one is set
  (anchor_witness_invalid, next in the order). The head
  judgement itself is experiment 026's check against the
  mesh state this process HOLDS - case 20's stated
  pairing decision: there is no paired form and no entries
  field, because mesh entries keep their own consulted
  receive path. Exactly the held state, or the current
  anchor's successor in its own kept lineage, is accepted
  and replaces the mesh anchor file (os.replace, from the
  verified candidate); honest growth held past the
  delivered anchor is anchor_stale, and a chain the held
  state is not and does not extend is anchor_mismatch.
  What this verification does NOT do is stated as plainly
  as case 17's twin: an anchor over a REWRITTEN mesh
  chain, written from the current anchor file with the
  current signer's secret and its history honestly
  extended, passes succession, the backward fence, and
  the signature alike and is delivered (case 20's
  finding) - the wire adds no weakness, since a
  hand-placed successor meets the same consultation, and
  the fence is the custody of the signer's secret, and at
  a witness-configured process the witness's too.
- A MESH anchor this process pushes to a peer (POST
  /push-mesh-anchor, experiment 032) is judged by the
  PEER's experiment 031 judgement, not by the sender: the
  sender contributes only the anchor object its own mesh
  anchor file holds (the file its AnchoredSyncNode itself
  consults on every mesh receive, so the file and the
  standing agree by construction) and reports the peer's
  mesh anchor delivery report, refusals included, as the
  push's result - it re-derives no verdict of its own.
  With identity binding on, the push first runs experiment
  017's initiating-side check on the peer, exactly as
  /push-snapshot does; a peer whose transport refuses the
  SENDER before the delivery endpoint runs (experiment
  017's accepting-side identity_mismatch, HTTP 403 naming
  claimed and certified ids) is reported by the push on
  502 in /sync's refusal shape. A sender with no mesh
  anchor configured reports no_mesh_anchor on 200 and
  contacts no peer; a sender whose own mesh anchor file
  cannot be read or parsed at push time reports its own
  anchor unreadable on 502 and contacts no peer. The push
  performs no lifecycle step for the peer: succession,
  freshness, and any witness countersignature (experiment
  029) are the sender's completed facts, carried inside
  the anchor object and judged at the peer.
- A mesh state file configured at startup (--mesh-state,
  experiment 033) is judged on load exactly as strictly
  as any state that arrives over the wire - by the
  mesh's own verification, never by the fact that it is
  this process's own file: the entries are deserialized
  with the wire's own decoding and the whole set is put
  through the node's restore_to_entries (Gate 3's
  whole-set judge: hash recompute, fork refusal,
  per-origin chain check) BEFORE the process serves
  anything, and a file that does not parse refuses the
  start as mesh_state_unreadable, a tampered entry as
  tampered_entry, a fork as forked_entry, a broken
  chain as broken_chain - MeshStateRefused out of the
  node's construction, the process exiting nonzero
  naming the refusal. With a mesh anchor ALSO
  configured, the recovered state is then consulted
  against the anchor (anchor_status()'s own
  classification) and a standing other than anchored
  refuses the start under the anchor's own name: the
  gate fires on a RECOVERED state, because persistence
  makes the state a durable claim the process stands on
  at startup, judged as the registry snapshot has been
  judged at startup since experiment 018; a fresh start
  over no file yet recovers nothing and gates on
  nothing. Writes are the same discipline on the way
  out (case 14's replace-from-verified-candidate): the
  accepted state is serialized in the wire's own entry
  format to a candidate file, the candidate is judged
  by the same whole-set verification, and only a
  candidate that stands replaces the state file - so
  the file on disk is always a file the mesh's own
  verification accepts, and a kill mid-write leaves the
  previous standing file or the new one, never a torn
  one.

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
  hold a certificate. (Superseded in part by experiment 034:
  started with --tls-crl, the process enforces a CA-issued
  revocation list at the handshake in both directions, loaded
  once at startup - a new list takes effect on restart, and a
  list that cannot be read refuses the start as crl_unreadable.
  Rotation and identity proofing remain unbuilt.) Plain HTTP is acceptable for a localhost
  experiment and for nothing else; mutual TLS at this scale is a
  prototype, not production transport security.
- No discovery: peer addresses are configured by whoever starts the
  processes and whoever calls /sync. No gossip schedule, no peer
  list, no automatic re-sync: sync happens when a client or a /sync
  call asks for it. (Superseded in part by experiment 035: started
  with --peers, the process holds a standing peer list - a
  plain-text file, one base URL per line, read once at startup -
  and a client can drive one sweep over it with POST /sync-all,
  each peer synced through the same per-peer round /sync runs
  and reported under its own outcome. The list is a startup fact,
  not a discovery: editing the file is a restart act, no surface
  delivers or receives a list, no membership is governed, and
  there is still no gossip schedule and no automatic re-sync -
  the sweep happens when a client drives it, as every surface in
  this stack is driven. What changed is only who holds the
  addresses: the node itself, from startup, instead of every
  caller, every time.)
- No persistence: mesh state lives in the server process's memory.
  Kill the process and its unreplicated entries are gone; what
  survives a kill is exactly what had already synced to another
  process (that is the availability claim the tests make, and the
  only one). (Superseded in part by experiment 033: started
  with --mesh-state, the process persists every accepted mesh
  change to that file - verified-candidate writes inside the
  mesh operations themselves - and a restart over the file
  recovers the state exactly, entries that never synced
  anywhere included, the load judged by the mesh's own
  verification and, with --mesh-anchor, gated on the anchor
  before the process serves. Without the flag this limit
  stands exactly as written. The file survives the PROCESS,
  not the machine: it is a local file under the operator's
  hand, tamper-evident on load, not tamper-proof - wholesale
  replacement with an older verified state recovers that
  older state, which the anchor gate refuses where an anchor
  is configured and a truncation on a line boundary recovers
  as the same kind of older state, exactly case 10/17's
  stated limit for every durable record in this stack.)
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
  Since experiment 028 the anchor such a delivery is
  consulted against can itself be delivered to the running
  server, when a client drives POST /deliver-anchor on it,
  alone or paired with the record it blesses. Since
  experiment 030 a server can also push its own record and
  anchor together to a peer, when a client drives
  POST /push-anchor on it. Since experiment 032 a server
  can also push its own MESH anchor to a peer, alone, when
  a client drives POST /push-mesh-anchor on it.
  No timer, scheduler,
  or thread inside the server fires any of these, nothing
  solicits a delivery, there is no broadcast or gossip of the
  record, and a server nobody drives or pushes to stays
  exactly as stale as experiment 007 left it (though since
  experiment 021 its refusal over the wire is a clean 410,
  not a dropped connection). The anchor a delivery is
  consulted against is the local stand-in file of
  experiments 012-019. Since experiment 028 it can also
  ARRIVE over the wire: a running server accepts an anchor
  delivery (POST /deliver-anchor), alone or paired with
  the record it blesses, judged in case 17's order before
  anything changes (see the VERIFICATION section above) -
  so the anchor lifecycle a case 14 delivery assumes can
  now be completed at a running server without a hand
  placing the file. What remains out of band is the
  anchor's WITNESS, not its transport: nothing here or in
  any earlier experiment holds the head where the record's
  writer cannot reach it, the anchor file remains a local
  file an operator can still replace or roll back by hand,
  and signing remains experiment 019's symmetric HMAC - a
  holder of the signer's secret can re-anchor anything,
  including a rewrite, and an anchor delivery by that
  holder is accepted as legitimately as one placed by
  hand (case 17's finding). A server can also SEND its
  anchor onward since experiment 030, but only paired with
  its record and only when a client drives the push
  (POST /push-anchor): POST /push-snapshot still transports
  the record only, no timer, scheduler, or solicitation
  moves an anchor unattended, and there is no anchor-alone
  server push - the anchor travels with the record it
  blesses, or not at all. An
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
  007-025 left it - and the MESH anchor still arrives out
  of band even now: experiment 028 delivers the registry
  anchor only. (Superseded in part by experiment 031: the
  mesh anchor now also arrives over the wire -
  POST /deliver-mesh-anchor, judged in sync-mesh case 20's
  order against the mesh state the server holds, before
  acceptance, so the 026 scenario can be driven forward
  by a delivery instead of a hand placing the file - but
  a server still cannot SEND its mesh anchor onward to a
  peer, and nothing solicits or schedules a mesh anchor
  delivery: it is driven by the pushing client, exactly
  as every delivery surface here is. Superseded in part
  by experiment 032: a server CAN now send its mesh
  anchor onward to a peer - POST /push-mesh-anchor
  carries the anchor object its own mesh anchor file
  holds to the peer's POST /deliver-mesh-anchor, judged
  there by experiment 031's judgement unmodified - but
  the push is still driven by a client call on the
  sender, and nothing solicits, schedules, or retries
  one.)
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
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


_MESH_ANCHOR_MODULE = None


def _mesh_anchor_module():
    """Experiment 012/019's MESH anchor module (this package's
    own anchor.py), imported lazily on first use (experiment
    031).

    The same resolution _anchored_mesh relies on: in the
    server process this file's own directory heads sys.path,
    so a plain `import anchor` here is this package's module,
    exactly as it is inside anchored_sync.py; importing
    lazily, only when a process actually receives a mesh
    anchor delivery, leaves every existing consumer's
    resolution exactly as it was. _anchored_mesh needs only
    the node class and the error; a mesh anchor DELIVERY
    (NetworkNode.deliver_mesh_anchor) needs the module's
    whole surface - its reader, its signature and witness
    verification, its audit log, its origin heads and its
    check - because the delivery is judged by that machinery
    reused, never by a copy of it.
    """
    global _MESH_ANCHOR_MODULE
    if _MESH_ANCHOR_MODULE is None:
        import anchor as mesh_anchor_module

        _MESH_ANCHOR_MODULE = mesh_anchor_module
    return _MESH_ANCHOR_MODULE


_REGISTRY_ANCHOR = None


def _registry_anchor():
    """Experiment 012/019's REGISTRY anchor module, loaded lazily
    by file path under a private name (experiment 028).

    The same collision as _anchored_mesh, one package over: this
    package's own anchor.py is the MESH anchor, and the registry
    anchor lives in packages/registration/anchor.py. Loading by
    path - the way policy_node.py loads the same module for its
    consultation - reuses experiment 019's signature and history
    verification, its audit log, and its anchor check exactly,
    with no copy in this package and no import of the
    registration channel. Lazy, like _anchored_mesh, so every
    existing consumer's import resolution is exactly as it was;
    only an anchor delivery ever loads it, and only a process
    that receives one has an anchor configured.
    """
    global _REGISTRY_ANCHOR
    if _REGISTRY_ANCHOR is None:
        anchor_file = _HERE.parent / "registration" / "anchor.py"
        spec = importlib.util.spec_from_file_location(
            "pba_node_server_registry_anchor", anchor_file)
        module = importlib.util.module_from_spec(spec)
        # Registered before exec: the anchor module declares
        # dataclasses, which resolve their module namespace
        # through sys.modules (as policy_node.py's loader notes).
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _REGISTRY_ANCHOR = module
    return _REGISTRY_ANCHOR


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


class MeshStateRefused(Exception):
    """A configured mesh state file could not be stood on at
    startup (experiment 033, sync-mesh case 22).

    Carries the refusal's own name in `.reason`:
    mesh_state_unreadable for a file that does not parse,
    Gate 3's divergence names (tampered_entry,
    forked_entry, broken_chain) for a file the mesh's own
    verification refuses, and the mesh anchor's names
    (anchor_stale, anchor_mismatch,
    anchor_signature_invalid, anchor_witness_invalid,
    anchor_unreadable) when a recovered state is refused
    by a configured mesh anchor's startup gate. Raised out
    of NetworkNode construction, so the process exits
    nonzero naming the refusal - exactly the refused-start
    mechanics of a tampered registry snapshot since
    experiment 007.
    """

    def __init__(self, reason: str, detail: str):
        super().__init__(
            f"mesh state recovery refused: {reason}: {detail}")
        self.reason = reason
        self.detail = detail


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
        anchor_witness: str | None = None,
        anchor_witness_secret_hex: str | None = None,
        mesh_anchor_path: str | Path | None = None,
        mesh_anchor_signer: str | None = None,
        mesh_anchor_secret_hex: str | None = None,
        mesh_anchor_witness: str | None = None,
        mesh_anchor_witness_secret_hex: str | None = None,
        mesh_state_path: str | Path | None = None,
        peers: list[str] | None = None,
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
        # Experiment 031: the mesh anchor's signing and
        # witness configuration, kept beside the path (the
        # AnchoredSyncNode below holds its own copies for the
        # consultation): a mesh anchor DELIVERY is judged
        # under the same configured identities, read from
        # here. Operator configuration only, in the registry
        # half's style - no delivery method reads a delivered
        # anchor's fields into these attributes.
        self.mesh_anchor_signer = mesh_anchor_signer
        self.mesh_anchor_secret_hex = mesh_anchor_secret_hex
        self.mesh_anchor_witness = mesh_anchor_witness
        self.mesh_anchor_witness_secret_hex = (
            mesh_anchor_witness_secret_hex)
        if self.mesh_anchor_path is not None:
            anchored_sync_node, _mesh_anchor_error = _anchored_mesh()
            self.sync_node = anchored_sync_node(
                node_id,
                self.mesh_anchor_path,
                anchor_signer=mesh_anchor_signer,
                anchor_secret_hex=mesh_anchor_secret_hex,
                # Experiment 029: the mesh anchor's witness pair,
                # passed straight through like the signer pair;
                # both None means no countersignature is required
                # of the mesh anchor.
                anchor_witness=mesh_anchor_witness,
                anchor_witness_secret_hex=mesh_anchor_witness_secret_hex,
            )
        else:
            self.sync_node = SyncNode(node_id)
        # Experiment 033: an optional durable record for
        # this process's mesh state (sync-mesh case 22).
        # With a state file configured, every accepted
        # change to the mesh state is persisted to it (see
        # _persist_mesh_state_if_changed) and an existing
        # file is recovered - judged, never trusted - at
        # construction (see _load_mesh_state); without one,
        # the mesh state is process memory exactly as
        # experiments 007-032 left it. Independent of the
        # registry snapshot and of the mesh anchor: the
        # mesh side exists on mesh-only processes too, and
        # persistence is its own opt-in.
        self.mesh_state_path = (
            Path(mesh_state_path) if mesh_state_path else None
        )
        # The load report for /health (case 22): a dict
        # with status "recovered" or "fresh" when a state
        # file is configured, None when none is.
        self.mesh_state_load: dict | None = None
        self._mesh_state_persisted_hash: str | None = None
        if self.mesh_state_path is not None:
            self._load_mesh_state()
        # Experiment 035: this process's standing peer list
        # (sync-mesh case 24) - the parsed --peers file, handed
        # in by main() already validated and normalized (see
        # read_peer_list). None (the default) is experiments
        # 007-034's world: the process knows no peers and /sync
        # names every address at call time. A configured list
        # is a startup fact: nothing here re-reads the file,
        # and no surface delivers or receives a list.
        self.peers: list[str] | None = (
            list(peers) if peers is not None else None
        )
        # This process's own base URL as main() bound it
        # (scheme from the TLS configuration, host and bound
        # port): set by main() after the server is built, used
        # by a sweep only to recognize a list entry naming this
        # process itself. None until main() sets it; in-process
        # constructions never set it and sweeps they drive do
        # no self detection.
        self.own_base_url: str | None = None
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
        # Experiment 029: the witness identity for the anchor,
        # passed straight to the PolicyNode alongside the signer
        # pair, in the same opt-in style. None (the default) is
        # experiment 019's judgement: no countersignature is
        # required. Operator configuration only - no delivery
        # method reads or writes these attributes.
        self.anchor_witness = anchor_witness
        self.anchor_witness_secret_hex = anchor_witness_secret_hex
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
                anchor_witness=self.anchor_witness,
                anchor_witness_secret_hex=self.anchor_witness_secret_hex,
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
        # Experiment 028: the most recent anchor delivery's
        # report, in the same field style as the last check's
        # and the last delivery's.
        self.last_anchor_delivery: dict | None = None
        # Experiment 030: the most recent PAIRED push this
        # process initiated toward a peer (POST /push-anchor)
        # - the push's response body, a 200 report or a 502
        # refusal alike - beside the last snapshot push's.
        self.last_anchor_push: dict | None = None
        # Experiment 031: the most recent MESH anchor
        # delivery's report, in the same field style as the
        # last anchor delivery's.
        self.last_mesh_anchor_delivery: dict | None = None
        # Experiment 032: the most recent MESH anchor push
        # this process initiated toward a peer (POST
        # /push-mesh-anchor) - the push's response body, a
        # 200 report or a 502 refusal alike - beside the
        # last mesh anchor delivery's.
        self.last_mesh_anchor_push: dict | None = None
        # Experiment 035: the most recent peer-list sweep's
        # response body (POST /sync-all) - the per-peer report,
        # beside the last pushes'. None until the first sweep
        # is driven.
        self.last_sync_all: dict | None = None
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
            result = self.sync_node.receive_entries(entries)
            self._persist_mesh_state_if_changed()
            return result
        _anchored_sync_node, mesh_anchor_error = _anchored_mesh()
        try:
            result = self.sync_node.receive_entries(entries)
        except mesh_anchor_error:
            result = SyncResult(
                accepted=0,
                divergence_detected=True,
                rolled_back=True,
                reason="anchor_unreadable",
                state_hash=self.sync_node.state_hash(),
            )
        self._persist_mesh_state_if_changed()
        return result

    def mutate_mesh(self, key: str, value: str) -> StateEntry:
        """Mutate this process's own mesh state (the
        /mutate surface's operation), experiment 033: the
        mutation itself is Gate 3's, unchanged; with a
        mesh state file configured the accepted change is
        persisted before the entry is returned, so the
        response the caller is about to receive is already
        durable. Callers hold self.lock, as for every
        other node access from the handler."""
        entry = self.sync_node.mutate(key, value)
        self._persist_mesh_state_if_changed()
        return entry

    def _load_mesh_state(self) -> None:
        """Recover the persisted mesh state at startup
        (experiment 033, sync-mesh case 22).

        No file at the configured path: a fresh start -
        nothing is loaded, the load is reported "fresh",
        and the file is created by the first accepted
        change, not at startup. A file that exists is
        judged, never trusted, before the process serves
        anything: its lines are deserialized with the
        wire's own entry decoding (a file that does not
        parse - malformed JSON, a bad entry encoding, a
        file cut mid-line - refuses the start as
        mesh_state_unreadable), and the whole set is
        judged by the mesh's own whole-set verification,
        this node's restore_to_entries (experiment 013's
        primitive: every hash recomputed, forks refused,
        every per-origin chain checked), a divergence
        refusing the start under Gate 3's own name
        (tampered_entry, forked_entry, broken_chain).
        The recovered set becomes the node's state and its
        checkpoint, exactly - same entries, same state
        hash.

        The anchor gate: when a mesh anchor is ALSO
        configured, the recovered state is then consulted
        against it by this node's own anchor_status()
        classification, and any standing other than
        "anchored" refuses the start under the anchor's
        own name. The gate fires only here, on a
        RECOVERED state: persistence makes the state a
        durable claim the process stands on at startup,
        judged the way the registry snapshot has been
        judged at startup since experiment 018. A fresh
        start recovers nothing and gates on nothing.
        """
        path = self.mesh_state_path
        self._mesh_state_persisted_hash = self.sync_node.state_hash()
        if not path.exists():
            self.mesh_state_load = {
                "status": "fresh",
                "entries": 0,
                "state_hash": self.sync_node.state_hash(),
            }
            return
        try:
            entries = [
                entry_from_dict(json.loads(line))
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise MeshStateRefused(
                "mesh_state_unreadable",
                f"mesh state file {path} does not parse as "
                f"mesh entries: {exc}",
            ) from exc
        verdict = self.sync_node.restore_to_entries(entries)
        if verdict.divergence_detected:
            raise MeshStateRefused(
                verdict.reason,
                f"mesh state file {path} failed the mesh's "
                f"own verification on load: {verdict.reason}",
            )
        self._mesh_state_persisted_hash = self.sync_node.state_hash()
        self.mesh_state_load = {
            "status": "recovered",
            "entries": len(entries),
            "state_hash": self.sync_node.state_hash(),
        }
        if self.mesh_anchor_path is not None:
            status = self.mesh_anchor_status
            if status != "anchored":
                raise MeshStateRefused(
                    status,
                    f"recovered mesh state in {path} is "
                    f"refused by the configured mesh anchor "
                    f"at startup: {status}",
                )

    def _persist_mesh_state_if_changed(self) -> None:
        """Persist this process's mesh state if it moved
        (experiment 033, sync-mesh case 22).

        Called at the end of the node's two mesh
        operations - its own mutation (mutate_mesh) and
        its one receive (receive_mesh_entries) - under
        the node's lock, before the driving response
        returns. A write happens only when the state hash
        differs from the hash this process last loaded or
        wrote, so a refused receive and a no-op sync
        rewrite nothing, byte for byte.

        The write is case 14's replace-from-verified-
        candidate discipline: the full entry set is
        serialized to a candidate file beside the state
        file, in the wire's own entry format (one JSON
        object per line, Gate 1's log format, in the
        node's deterministic total order), the candidate
        is flushed and fsynced, its bytes are read back
        and judged by Gate 3's own whole-set verification
        (a scratch node's restore_to_entries), and only
        a candidate that stands is os.replace'd over the
        state file. A kill at any point leaves either the
        previous standing file or the new one, never a
        torn one, and nothing unverified is ever the
        standing file.
        """
        if self.mesh_state_path is None:
            return
        current_hash = self.sync_node.state_hash()
        if current_hash == self._mesh_state_persisted_hash:
            return
        candidate_path = self.mesh_state_path.with_name(
            self.mesh_state_path.name + ".persist-candidate")
        candidate_bytes = "".join(
            json.dumps(entry_to_dict(entry), sort_keys=True) + "\n"
            for entry in self.sync_node.entries()
        ).encode("utf-8")
        with candidate_path.open("wb") as fh:
            fh.write(candidate_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        judged = [
            entry_from_dict(json.loads(line))
            for line in candidate_path.read_text(
                encoding="utf-8").splitlines()
            if line.strip()
        ]
        verdict = SyncNode(
            "mesh-state-candidate").restore_to_entries(judged)
        if verdict.divergence_detected:
            # The node's held state passed Gate 3's
            # verification to get in; a candidate built from
            # it that does not stand is an internal
            # inconsistency, refused loudly here rather than
            # written as the standing state.
            raise RuntimeError(
                f"mesh state candidate for "
                f"{self.mesh_state_path} failed verification "
                f"before replace: {verdict.reason}")
        os.replace(candidate_path, self.mesh_state_path)
        self._mesh_state_persisted_hash = current_hash

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
                    anchor_witness=self.anchor_witness,
                    anchor_witness_secret_hex=self.anchor_witness_secret_hex,
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

    def deliver_anchor(self, anchor_data: dict,
                       entries: list[dict] | None = None) -> dict:
        """One registry anchor delivery received over the wire
        (experiment 028, sync-mesh case 17).

        `anchor_data` is the anchor object's own JSON - the
        content of an experiment 012/019 anchor file, handed
        over as data, never a path. `entries`, when given, are
        the Gate 1 log entries of the record the delivered
        anchor blesses (case 14's delivery payload): the pair
        is judged TOGETHER and neither half is applied unless
        both stand. The REGISTRY anchor only; the mesh anchor
        of experiment 026 still arrives out of band.
        (Superseded in part by experiment 031, after this method
        was written: the mesh anchor now has its own delivery
        surface, deliver_mesh_anchor / POST /deliver-mesh-anchor
        below; this method remains the REGISTRY anchor's only.)

        The anchor is judged BEFORE anything changes, in case
        17's stated order, reusing experiment 019's verification
        and audit code (loaded by _registry_anchor(), never
        copied):

        - Configuration: no anchor path configured on this
          process: "no_anchor" - configuration is a startup
          choice and a delivery never turns consultation on.
          An anchor configured but no PolicyNode: "no_snapshot"
          - no record to pair an anchor against.
        - The delivered bytes are serialized to a candidate
          file beside the anchor (the anchor file's own format)
          and read with experiment 012's reader; a body that is
          not a registry anchor is refused under the anchor
          module's own error name (experiment 022's naming, as
          deliver_snapshot uses it). A configured anchor that
          cannot be read NOW is refused as "anchor_unreadable"
          (case 15's name for the same condition on the mesh
          side): the current anchor is the ground every
          judgement below stands on, and an unreadable one is a
          refusal, never a silent pass.
        - Paired pre-judgements (case 14's, on the delivered
          hashes): entries identical to the standing chain
          reduce the pair to the anchor-alone judgement below,
          with the snapshot half reported "no_change"; entries
          that are exactly an earlier prefix are refused as
          "snapshot_regression" with NEITHER half applied - the
          backward name for the record half is case 14's, and
          a shorter chain that is not that prefix never reaches
          it.
        - Anchor-alone only: a delivery byte-for-byte identical
          to the current anchor file is "no_change" - nothing
          written, nothing re-judged.
        - Succession, judged anchor to anchor (registration
          case 10's same-identity rule): a signed current
          anchor may be succeeded only by a delivered anchor
          signed by the SAME identity; a different identity -
          or an unsigned anchor over a signed one - is refused
          as "different_signer", the refusal appended to the
          anchor audit log beside the current anchor in case
          10's event shape, the current anchor file untouched.
          A signed delivery over an UNSIGNED current anchor
          establishes the signer (case 10's stated exception)
          and passes this judgement.
        - The backward fence, fenced as case 14 fences its
          own: a delivered anchor that is an earlier state -
          its kept history a proper prefix of the current
          anchor's kept history (an earlier version of this
          very anchor file), or its head pinning an earlier
          prefix of the chain this process stands on - is
          refused as "anchor_regression", audit-logged in the
          same shape. A foreign chain's head is an earlier
          state of neither and falls through to pairing, so
          the backward name cannot launder a rewrite.
        - Signature and history: with a signing identity
          configured on this process, experiment 019's own
          verification of the delivered record (signer match,
          history chain and signatures, top fields tied to the
          last history entry) must pass, or the refusal is
          "anchor_signature_invalid" - before any head
          judgement, as in every consultation since experiment
          019. With no identity configured, a signed delivered
          anchor's kept history must still verify as a chain
          (the part of the history verification that needs no
          secret); an unsigned one is judged by its head.
        - Witness (experiment 029, sync-mesh case 18): with a
          witness identity configured on this process, the
          delivered anchor's countersignature must verify for
          that witness, judged in the same step right after the
          signature's, or the refusal is
          "anchor_witness_invalid" - before pairing, as the
          signature's refusal is. The pairing consultations
          below run with the same witness configuration, so a
          pair that stands here stands there. The witness is
          operator configuration (constructor / CLI flags):
          no field of a delivery can install or change it, and
          a delivered anchor's witness field is data to be
          verified against the configuration, never adopted
          from it.
        - Pairing. Anchor alone: experiment 012's check of the
          server's snapshot file against the candidate anchor
          must be ok - the anchor pairs with the record this
          process already stands on - or the delivery is
          refused as "anchor_unpaired", the name case 17 sets:
          it pairs with nothing here. Paired: a full PolicyNode
          construction over a candidate record file with the
          CANDIDATE anchor as its anchor and this process's
          signing configuration - experiment 015/019's
          consultation - judges the pair together
          (anchor_stale / anchor_mismatch / tampered_record /
          anchor_signature_invalid, under experiment 022's
          naming). Only a pair that stands is applied.

        Application. Anchor alone: the anchor file is replaced
        from the verified candidate (os.replace) and the
        running PolicyNode reloads its standing record against
        it, so the anchor standing /health reports afterward is
        the new anchor's verdict, not a cached one. Paired: the
        snapshot file is replaced from the verified delivered
        record and the anchor file from the verified delivered
        anchor, and the PolicyNode reloads ONCE from the
        replaced pair (experiment 024's reload path); the
        record half is also recorded as this process's last
        snapshot delivery, in deliver_snapshot's report shape,
        because a record delivery did occur. A refused delivery
        at any judgement changes neither file, byte-for-byte,
        and the previous view keeps serving.

        The delivery is driven by the pushing peer (POST
        /deliver-anchor); no timer or thread in this process
        solicits one. Callers hold self.lock, as for every
        other node access from the handler.
        """
        anchor_mod = _registry_anchor()
        carried_entries = entries is not None

        def _report(status, reason, before, delivered, loaded_entries,
                    snapshot_status):
            return {
                "node_id": self.node_id,
                "status": status,
                "reason": reason,
                "paired": carried_entries,
                "before_head": before.chain_head if before else None,
                "after_head": delivered.chain_head if delivered else None,
                "before_count": before.entry_count if before else None,
                "after_count": delivered.entry_count if delivered else None,
                "before_seq": before.anchor_seq if before else None,
                "after_seq": delivered.anchor_seq if delivered else None,
                "signer": delivered.signer if delivered else None,
                "loaded_entries": loaded_entries,
                "snapshot_status": snapshot_status,
                "anchor_status": self.anchor_status,
            }

        # Configuration first (case 17).
        if self.anchor_path is None:
            report = _report("no_anchor", None, None, None, None, None)
            self.last_anchor_delivery = report
            return report
        if self.policy_node is None or self.snapshot_path is None:
            report = _report("no_snapshot", None, None, None, None, None)
            self.last_anchor_delivery = report
            return report

        candidate_anchor_path = self.anchor_path.with_name(
            self.anchor_path.name + ".wire-candidate")
        candidate_record_path = self.snapshot_path.with_name(
            self.snapshot_path.name + ".wire-candidate")
        try:
            candidate_anchor_path.write_bytes(
                (json.dumps(anchor_data, sort_keys=True, indent=2) + "\n"
                 ).encode("utf-8"))
            try:
                delivered = anchor_mod.read_anchor(candidate_anchor_path)
            except Exception as exc:
                report = _report("delivery_refused",
                                 _reload_refusal_reason(exc),
                                 None, None, None, None)
                self.last_anchor_delivery = report
                return report
            try:
                current = anchor_mod.read_anchor(self.anchor_path)
                current_bytes = self.anchor_path.read_bytes()
            except Exception:
                report = _report("delivery_refused", "anchor_unreadable",
                                 None, delivered, None, None)
                self.last_anchor_delivery = report
                return report

            # Case 14's hash-only pre-judgements on a paired
            # delivery's record half.
            judge_pair = carried_entries
            snapshot_status = None
            if carried_entries:
                loaded_hashes = [e["entry_hash"]
                                 for e in self.policy_node._entries]
                delivered_hashes = [e.get("entry_hash") for e in entries]
                if delivered_hashes == loaded_hashes:
                    judge_pair = False
                    snapshot_status = "no_change"
                elif (len(delivered_hashes) < len(loaded_hashes)
                        and delivered_hashes
                        == loaded_hashes[:len(delivered_hashes)]):
                    report = _report("delivery_refused",
                                     "snapshot_regression",
                                     current, delivered, None, None)
                    self.last_anchor_delivery = report
                    return report

            def _succession_or_regression_reason() -> str | None:
                """The anchor-to-anchor judgements (case 17):
                succession, then the backward fence - each
                refusal audit-logged beside the current anchor
                in registration case 10's event shape."""
                if (current.signer is not None
                        and delivered.signer != current.signer):
                    anchor_mod._append_anchor_audit(
                        self.anchor_path,
                        {
                            "event": "reanchor_rejected",
                            "reason": "different_signer",
                            "attempted_signer": delivered.signer,
                            "current_signer": current.signer,
                            "chain": "registry",
                            "chain_head": delivered.chain_head,
                            "entry_count": delivered.entry_count,
                        },
                    )
                    return "different_signer"
                standing = self.policy_node._entries
                pins_earlier_prefix = (
                    delivered.entry_count < len(standing)
                    and delivered.chain_head == (
                        standing[delivered.entry_count - 1]["entry_hash"]
                        if delivered.entry_count > 0 else GENESIS_HASH)
                )
                earlier_version = (
                    bool(delivered.history) and bool(current.history)
                    and len(delivered.history) < len(current.history)
                    and [dict(e) for e in delivered.history]
                    == [dict(e) for e in
                        current.history[:len(delivered.history)]]
                )
                if pins_earlier_prefix or earlier_version:
                    anchor_mod._append_anchor_audit(
                        self.anchor_path,
                        {
                            "event": "reanchor_rejected",
                            "reason": "anchor_regression",
                            "attempted_signer": delivered.signer,
                            "current_signer": current.signer,
                            "chain": "registry",
                            "chain_head": delivered.chain_head,
                            "entry_count": delivered.entry_count,
                        },
                    )
                    return "anchor_regression"
                return None

            def _signature_reason() -> str | None:
                """Experiment 019's verification of the delivered
                anchor, before any head judgement (case 17) - and,
                with a witness configured, experiment 029's
                countersignature judgement after it: an anchor the
                configured witness did not countersign is refused
                as "anchor_witness_invalid" here, so experiment
                028's finding pair - a rewritten record with an
                honestly extended anchor signed by the current
                signer - is refused at a witness-configured
                process unless the witness countersigned it."""
                if self.anchor_signer is not None:
                    if not anchor_mod._signature_verifies(
                            delivered, self.anchor_signer,
                            self.anchor_secret_hex):
                        return "anchor_signature_invalid"
                elif delivered.signer is not None:
                    if not anchor_mod.verify_anchor_history(
                            candidate_anchor_path,
                            signer=delivered.signer):
                        return "anchor_signature_invalid"
                if self.anchor_witness is not None:
                    if not anchor_mod._witness_verifies(
                            delivered, self.anchor_witness,
                            self.anchor_witness_secret_hex):
                        return "anchor_witness_invalid"
                return None

            if not judge_pair:
                # Anchor alone (or a pair whose record half is
                # the standing chain itself).
                if candidate_anchor_path.read_bytes() == current_bytes:
                    report = _report("no_change", None, current, delivered,
                                     None, snapshot_status)
                    self.last_anchor_delivery = report
                    return report
                reason = (_succession_or_regression_reason()
                          or _signature_reason())
                if reason is not None:
                    report = _report("delivery_refused", reason, current,
                                     delivered, None, snapshot_status)
                    self.last_anchor_delivery = report
                    return report
                # Pairing: experiment 012's check of the snapshot
                # file this process stands on against the
                # candidate anchor - the anchor must pair with
                # the record already held. The process's signing
                # and witness identities ride along when
                # configured (experiments 019/029): a witness
                # failure names itself anchor_witness_invalid in
                # the check's status and passes through under
                # that name.
                check_kwargs = {}
                if self.anchor_signer is not None:
                    check_kwargs["signer"] = self.anchor_signer
                    check_kwargs["secret_hex"] = self.anchor_secret_hex
                if self.anchor_witness is not None:
                    check_kwargs["witness"] = self.anchor_witness
                    check_kwargs["witness_secret_hex"] = (
                        self.anchor_witness_secret_hex)
                check = anchor_mod.check_registry_against_anchor(
                    self.snapshot_path, candidate_anchor_path,
                    **check_kwargs)
                if not check.ok:
                    reason = ("anchor_unpaired"
                              if check.status == "anchor_mismatch"
                              else check.status)
                    report = _report("delivery_refused", reason, current,
                                     delivered, None, snapshot_status)
                    self.last_anchor_delivery = report
                    return report
                os.replace(candidate_anchor_path, self.anchor_path)
                self.policy_node.reload_snapshot()
                report = _report("delivered", None, current, delivered,
                                 None, snapshot_status)
                self.last_anchor_delivery = report
                return report

            # The pair, judged together (case 17).
            reason = (_succession_or_regression_reason()
                      or _signature_reason())
            if reason is not None:
                report = _report("delivery_refused", reason, current,
                                 delivered, None, None)
                self.last_anchor_delivery = report
                return report
            candidate_record_path.write_bytes(
                "".join(json.dumps(entry, sort_keys=True) + "\n"
                        for entry in entries).encode("utf-8"))
            try:
                # The pair judges itself: a full PolicyNode
                # construction over the candidate record with
                # the candidate anchor as its anchor and this
                # process's signing configuration - experiment
                # 015/019's consultation - before anything
                # changes.
                PolicyNode(
                    node_id=self.node_id,
                    registry_snapshot_path=candidate_record_path,
                    payloads={},
                    serve_policies=False,
                    anchor_path=candidate_anchor_path,
                    anchor_signer=self.anchor_signer,
                    anchor_secret_hex=self.anchor_secret_hex,
                    anchor_witness=self.anchor_witness,
                    anchor_witness_secret_hex=self.anchor_witness_secret_hex,
                )
            except Exception as exc:
                report = _report("delivery_refused",
                                 _reload_refusal_reason(exc),
                                 current, delivered, None, None)
                self.last_anchor_delivery = report
                return report
            # The pair stands together. Both files are replaced
            # from the verified candidates and the running node
            # reloads once from the replaced pair, so what a
            # later restart or check sees is exactly what was
            # delivered and verified, under the anchor that was
            # delivered and verified.
            before = self._loaded_snapshot_facts
            os.replace(candidate_record_path, self.snapshot_path)
            os.replace(candidate_anchor_path, self.anchor_path)
            loaded = self.policy_node.reload_snapshot()
            presented = snapshot_facts(self.snapshot_path)
            self._loaded_snapshot_facts = presented
            self.last_snapshot_delivery = {
                "node_id": self.node_id,
                "status": "delivered",
                "reason": None,
                "before_head": before.head,
                "after_head": presented.head,
                "before_count": before.count,
                "after_count": presented.count,
                "loaded_entries": loaded,
                "anchor_status": self.anchor_status,
            }
            report = _report("delivered", None, current, delivered,
                             loaded, "delivered")
            self.last_anchor_delivery = report
            return report
        finally:
            for candidate in (candidate_anchor_path,
                              candidate_record_path):
                if candidate.exists():
                    candidate.unlink()

    def _mesh_anchor_subset_view(self, mesh_mod, record) -> SyncNode | None:
        """The held entries at or below a mesh anchor record's
        per-origin heads, as a throwaway view, when they are
        EXACTLY the record's entry set (experiment 031): the
        subset's count is the record's count, the subset's own
        per-origin heads are the record's heads, and the
        subset's recomputed state hash is the record's state
        hash. None when the held entries do not prove that
        exact subset. This is the proof the backward fence's
        second shape and the anchor_stale classification both
        stand on, judged with Gate 3's own restore primitive
        and state hash - in the style of
        AnchoredSyncNode._is_honest_extension, but against an
        arbitrary anchor record rather than this node's
        configured one, and exact where that check is
        existential."""
        held = self.sync_node.entries()
        subset = [
            entry for entry in held
            if entry.origin in record.origin_heads
            and entry.seq <= record.origin_heads[entry.origin]["seq"]
        ]
        if len(subset) != record.entry_count:
            return None
        view = SyncNode("mesh-anchor-delivery-view")
        if view.restore_to_entries(subset).divergence_detected:
            return None
        if view.state_hash() != record.state_hash:
            return None
        if mesh_mod.origin_heads(view) != record.origin_heads:
            return None
        return view

    def deliver_mesh_anchor(self, anchor_data: dict) -> dict:
        """One MESH anchor delivery received over the wire
        (experiment 031, sync-mesh case 20).

        `anchor_data` is the mesh anchor object's own JSON -
        the content of an experiment 012/019 mesh anchor
        file, handed over as data, never a path. There is NO
        paired form and no entries field, by case 20's stated
        decision: the anchor is judged against the mesh state
        this process HOLDS at delivery time and the anchor it
        already stands under, never against entries delivered
        in the same call, because mesh entries already have
        their own receive path with its own consultation
        (experiment 026). The registry surfaces
        (deliver_anchor above, experiments 028/030) are a
        different anchor and are untouched by this method.

        The anchor is judged BEFORE anything changes, in
        case 20's stated order, reusing experiment
        012/015/019/029's mesh anchor code (loaded by
        _mesh_anchor_module(), never copied):

        - Configuration: no mesh anchor path configured on
          this process: "no_mesh_anchor" - configuration is
          a startup choice and a delivery never turns
          consultation on.
        - The delivered bytes are serialized to a candidate
          file beside the mesh anchor (the mesh anchor
          file's own format) and read with experiment 012's
          mesh reader; a body that is not a mesh anchor is
          refused under the mesh anchor module's own error
          name (experiment 022's naming, as deliver_anchor
          uses it). A configured mesh anchor that cannot be
          read NOW is refused as "anchor_unreadable" (case
          15's name for the same condition): the current
          anchor is the ground every judgement below stands
          on, and an unreadable one is a refusal, never a
          silent pass.
        - A delivery byte-for-byte identical to the current
          mesh anchor file is "no_change" - nothing written,
          nothing re-judged.
        - Succession, judged anchor to anchor (case 11's
          same-identity rule): a signed current anchor may
          be succeeded only by a delivered anchor signed by
          the SAME identity; a different identity - or an
          unsigned anchor over a signed one - is refused as
          "different_signer", the refusal appended to the
          anchor audit log beside the current mesh anchor in
          case 11's event shape, the current anchor file
          untouched. A signed delivery over an UNSIGNED
          current anchor establishes the signer (case 11's
          stated exception).
        - The backward fence, fenced as case 17 fences its
          own and under the same name: a delivered anchor
          that is an earlier state - its kept history a
          proper prefix of the current anchor's kept history
          (an earlier version of this very anchor file), or
          its heads pinning an earlier prefix of the chain
          the current anchor pins, proved against the held
          entries by _mesh_anchor_subset_view - is refused
          as "anchor_regression", audit-logged in the same
          shape. The second shape fires only on what the
          held entries can prove; a foreign chain's head is
          an earlier state of neither and falls through to
          the head judgement, so the backward name cannot
          launder a rewrite.
        - Signature and history: with a mesh signing
          identity configured on this process, experiment
          019's own verification of the delivered record
          must pass, or the refusal is
          "anchor_signature_invalid" - before any head
          judgement, as in every consultation since
          experiment 019. With no identity configured, a
          signed delivered anchor's kept history must still
          verify as a chain; an unsigned one is judged by
          its heads.
        - Witness (experiment 029, case 18): with a mesh
          witness identity configured on this process, the
          delivered anchor's countersignature must verify
          for that witness, judged in the same step right
          after the signature's, or the refusal is
          "anchor_witness_invalid" - before the head
          judgement, as the signature's refusal is. The
          witness is operator configuration (constructor /
          CLI flags): no field of a delivery can install or
          change it.
        - The head judgement, against the mesh state this
          process HOLDS, with case 8's check (its signature
          and witness steps already judged above, not
          repeated). Accepted, in exactly two standings: the
          delivered anchor pins exactly the held state (the
          check is ok - the catch-up delivery), or it is
          the current anchor's SUCCESSOR IN ITS OWN KEPT
          LINEAGE (the delivered kept history has the
          current anchor's as a proper prefix - the anchor
          file case 11's write path produces when the
          current anchor is re-anchored forward) and the
          held state does not already stand honestly past
          it. A successor may pin a state this process does
          not hold yet: that is the re-anchor lifecycle
          arriving by wire, and every later receive is
          judged against the new anchor by case 8's
          consultation, exactly as against a hand-placed
          re-anchor. The lineage requirement is the proof
          an unsigned anchor cannot give, so an unsigned
          delivered anchor is accepted only in the first
          standing. Refused: "anchor_stale" when the held
          state grew honestly past the delivered anchor
          (the held entries contain everything it pins,
          exactly, plus more - _mesh_anchor_subset_view
          proves the pinned part), and "anchor_mismatch"
          when it pins a chain the held state is not and
          does not extend and it is not the lineage
          successor - a foreign chain's anchor in
          particular.

        Application is the one file replacement: the mesh
        anchor file is replaced from the verified candidate
        (os.replace). Nothing else moves - the mesh state
        is process memory and is not the delivery's to
        change; the standing /health reports afterward is
        the new anchor's verdict on the held state, read
        fresh by the AnchoredSyncNode's own consultation,
        not a cached one. A refused delivery at any
        judgement leaves the current mesh anchor file
        byte-for-byte unchanged, the held state untouched,
        and sync behavior exactly as it was. What acceptance
        does NOT fence is stated in case 20's finding, not
        hidden here: a successor anchor over a REWRITTEN
        chain, written from the current anchor file with
        the current signer's secret (which every verifier
        of a signed anchor holds, in experiment 019's
        symmetric model), passes succession, the backward
        fence, and the signature alike, and is accepted -
        the wire adds no weakness, since a hand-placed
        successor is accepted by the same consultation, and
        the fence is the custody of the signer's secret
        (and, at a witness-configured process, the
        witness's too).

        The delivery is driven by the pushing client (POST
        /deliver-mesh-anchor); no timer or thread in this
        process solicits one, and no server pushes its own
        mesh anchor onward in this experiment. Callers hold
        self.lock, as for every other node access from the
        handler. (Superseded in part by experiment 032: a
        server can now push its own mesh anchor onward -
        POST /push-mesh-anchor, sync-mesh case 21 - judged
        at the peer by this method, unmodified.)
        """
        mesh_mod = _mesh_anchor_module()

        def _report(status, reason, before, delivered):
            return {
                "node_id": self.node_id,
                "status": status,
                "reason": reason,
                "before_state_hash": (
                    before.state_hash if before else None),
                "after_state_hash": (
                    delivered.state_hash if delivered else None),
                "before_count": (
                    before.entry_count if before else None),
                "after_count": (
                    delivered.entry_count if delivered else None),
                "before_seq": before.anchor_seq if before else None,
                "after_seq": (
                    delivered.anchor_seq if delivered else None),
                "signer": delivered.signer if delivered else None,
                "mesh_anchor_status": self.mesh_anchor_status,
            }

        # Configuration first (case 20).
        if self.mesh_anchor_path is None:
            report = _report("no_mesh_anchor", None, None, None)
            self.last_mesh_anchor_delivery = report
            return report

        candidate_path = self.mesh_anchor_path.with_name(
            self.mesh_anchor_path.name + ".wire-candidate")
        try:
            candidate_path.write_bytes(
                (json.dumps(anchor_data, sort_keys=True, indent=2) + "\n"
                 ).encode("utf-8"))
            try:
                delivered = mesh_mod.read_mesh_anchor(candidate_path)
            except Exception as exc:
                report = _report("delivery_refused",
                                 _reload_refusal_reason(exc),
                                 None, None)
                self.last_mesh_anchor_delivery = report
                return report
            try:
                current = mesh_mod.read_mesh_anchor(
                    self.mesh_anchor_path)
                current_bytes = self.mesh_anchor_path.read_bytes()
            except Exception:
                report = _report("delivery_refused",
                                 "anchor_unreadable",
                                 None, delivered)
                self.last_mesh_anchor_delivery = report
                return report

            # Byte-identical is no_change (case 20): nothing
            # written, nothing re-judged.
            if candidate_path.read_bytes() == current_bytes:
                report = _report("no_change", None, current, delivered)
                self.last_mesh_anchor_delivery = report
                return report

            def _audit(reason: str) -> None:
                """Append a refusal to the anchor audit log
                beside the current mesh anchor, in the event
                shape experiment 019's own write path uses for
                this chain (chain "sync-mesh")."""
                mesh_mod._append_anchor_audit(
                    self.mesh_anchor_path,
                    {
                        "event": "reanchor_rejected",
                        "reason": reason,
                        "attempted_signer": delivered.signer,
                        "current_signer": current.signer,
                        "chain": "sync-mesh",
                        "state_hash": delivered.state_hash,
                        "entry_count": delivered.entry_count,
                    },
                )

            # Succession, judged anchor to anchor (case 20).
            if (current.signer is not None
                    and delivered.signer != current.signer):
                _audit("different_signer")
                report = _report("delivery_refused", "different_signer",
                                 current, delivered)
                self.last_mesh_anchor_delivery = report
                return report

            # The backward fence (case 20), both shapes:
            # (i) an earlier version of this very anchor file;
            # (ii) an anchor pinning an earlier prefix of the
            # chain the current anchor pins, proved against
            # the held entries.
            earlier_version = (
                bool(delivered.history) and bool(current.history)
                and len(delivered.history) < len(current.history)
                and [dict(e) for e in delivered.history]
                == [dict(e) for e in
                    current.history[:len(delivered.history)]]
            )
            pins_earlier_prefix = (
                delivered.entry_count < current.entry_count
                and self._mesh_anchor_subset_view(mesh_mod, delivered)
                is not None
            )
            if earlier_version or pins_earlier_prefix:
                _audit("anchor_regression")
                report = _report("delivery_refused",
                                 "anchor_regression",
                                 current, delivered)
                self.last_mesh_anchor_delivery = report
                return report

            # Signature and history (case 20), before any
            # head judgement.
            if self.mesh_anchor_signer is not None:
                if not mesh_mod._signature_verifies(
                        delivered, self.mesh_anchor_signer,
                        self.mesh_anchor_secret_hex):
                    report = _report("delivery_refused",
                                     "anchor_signature_invalid",
                                     current, delivered)
                    self.last_mesh_anchor_delivery = report
                    return report
            elif delivered.signer is not None:
                if not mesh_mod.verify_mesh_anchor_history(
                        candidate_path, signer=delivered.signer):
                    report = _report("delivery_refused",
                                     "anchor_signature_invalid",
                                     current, delivered)
                    self.last_mesh_anchor_delivery = report
                    return report

            # Witness (case 20 / case 18), in the same step,
            # right after the signature's.
            if self.mesh_anchor_witness is not None:
                if not mesh_mod._witness_verifies(
                        delivered, self.mesh_anchor_witness,
                        self.mesh_anchor_witness_secret_hex):
                    report = _report("delivery_refused",
                                     "anchor_witness_invalid",
                                     current, delivered)
                    self.last_mesh_anchor_delivery = report
                    return report

            # The head judgement (case 20), against the mesh
            # state this process holds, with case 8's check.
            check = mesh_mod.check_node_against_anchor(
                self.sync_node, candidate_path)
            if check.ok:
                os.replace(candidate_path, self.mesh_anchor_path)
                report = _report("delivered", None, current, delivered)
                self.last_mesh_anchor_delivery = report
                return report
            if self._mesh_anchor_subset_view(
                    mesh_mod, delivered) is not None:
                # The held entries contain everything the
                # delivered anchor pins, exactly, plus more:
                # the state grew honestly past it.
                report = _report("delivery_refused", "anchor_stale",
                                 current, delivered)
                self.last_mesh_anchor_delivery = report
                return report
            successor_in_lineage = (
                bool(current.history)
                and len(delivered.history) > len(current.history)
                and [dict(e) for e in
                     delivered.history[:len(current.history)]]
                == [dict(e) for e in current.history]
            )
            if successor_in_lineage:
                os.replace(candidate_path, self.mesh_anchor_path)
                report = _report("delivered", None, current, delivered)
                self.last_mesh_anchor_delivery = report
                return report
            report = _report("delivery_refused", "anchor_mismatch",
                             current, delivered)
            self.last_mesh_anchor_delivery = report
            return report
        finally:
            if candidate_path.exists():
                candidate_path.unlink()


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


def set_client_tls(cert, key, ca_cert, node_id: str | None = None,
                   crl=None) -> None:
    """Configure the credential these client helpers present on
    https:// connections, until clear_client_tls is called. The
    server process sets this from its own node certificate in main,
    so a server initiating /sync authenticates to its peer with the
    same credential it requires from clients. `node_id` is the id
    these helpers CLAIM on each request (sent as the X-PBA-Node-Id
    header); a binding-enabled peer checks it against the
    certificate's own id. `crl` (experiment 034) is the revocation
    list these helpers enforce against the SERVER certificates
    they connect to - the initiating half of the same list the
    server context enforces against clients."""
    global _CLIENT_CONTEXT, _CLIENT_NODE_ID
    _CLIENT_CONTEXT = protected_link.client_context(cert, key, ca_cert, crl=crl)
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


def request_sync_all(base_url: str) -> dict:
    """Drive one peer-list sweep on the node at base_url
    (experiment 035): the node syncs with each peer in its
    configured --peers list, in list order, through the same
    per-peer round request_sync drives for one peer, and this
    returns the sweep's report dict: "status" ("no_peers",
    "converged", or "partial"), one "results" entry per
    configured peer (its outcome - "converged", "refused"
    with the refusal's own name as "reason", "unreachable",
    "transport_failed", or "self" - beside the two
    SyncResults the round produced), and the node's state
    hash after the whole sweep. The endpoint answers 200
    always: per-peer outcomes are the content."""
    status, body = post_json(base_url, "/sync-all", {})
    assert status == 200, f"sync-all request failed: {status} {body}"
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


def deliver_anchor(base_url: str, anchor: dict,
                   entries: list[dict] | None = None) -> dict:
    """Push one registry anchor delivery to the RUNNING server
    at base_url (experiment 028): `anchor` is the anchor object's
    own JSON - the content of an experiment 012/019 anchor file,
    as data, never a path. `entries`, when given, are the
    snapshot's Gate 1 log entries for the record the anchor
    blesses: the pair is judged together by the receiving
    server and neither half is applied unless both stand (see
    NetworkNode.deliver_anchor). Returns the delivery's report
    dict: status "delivered", "delivery_refused" (with the
    refusal's own name as "reason"), "no_change", "no_anchor",
    or "no_snapshot"."""
    payload: dict = {"anchor": anchor}
    if entries is not None:
        payload["entries"] = entries
    status, body = post_json(base_url, "/deliver-anchor", payload)
    assert status == 200, f"anchor delivery failed: {status} {body}"
    return body


def deliver_mesh_anchor(base_url: str, anchor: dict) -> dict:
    """Push one MESH anchor delivery to the RUNNING server at
    base_url (experiment 031): `anchor` is the mesh anchor
    object's own JSON - the content of an experiment 012/019
    mesh anchor file, as data, never a path. There is no
    paired form (sync-mesh case 20's stated decision): the
    receiving server judges the anchor against the mesh
    state it holds and the anchor it already stands under,
    before anything changes, and answers with the delivery's
    own report dict: status "delivered" (with the report of
    what the anchor advanced from and to), "delivery_refused"
    (with the refusal's own name as "reason"), "no_change",
    or "no_mesh_anchor"."""
    status, body = post_json(
        base_url, "/deliver-mesh-anchor", {"anchor": anchor})
    assert status == 200, f"mesh anchor delivery failed: {status} {body}"
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


def push_anchor(base_url: str, peer_url: str) -> tuple[int, dict]:
    """Ask the node at base_url to push its standing registry
    record AND its registry anchor to the peer process at
    peer_url (server-to-server, experiment 030): the pushing
    server sends the entries its PolicyNode stands on and the
    anchor object its own anchor file holds to the peer's
    /deliver-anchor (experiment 028) in its paired form, which
    judges the pair together under exactly case 17's rules -
    neither half applied unless both stand. A pushing server
    with no anchor configured sends the record alone to the
    peer's /deliver-snapshot, exactly as push_snapshot does.
    Returns the (status, body) pair, like push_snapshot,
    because a push's refusal can ride a non-200 status and
    the caller must see its body. On 200 the body is the
    push's report: "status" equal to the peer's paired
    delivery status ("delivered", "delivery_refused",
    "no_change", or "no_snapshot" when the pushing server
    holds no snapshot) and the peer's paired delivery report
    under "delivery". On 502 the body names the refusal -
    "identity_mismatch" with the claimed and certified ids
    over a binding transport, in /sync's refusal shape, or
    the sender's own anchor unreadable on its own side."""
    return post_json(base_url, "/push-anchor", {"peer": peer_url})


def push_mesh_anchor(base_url: str, peer_url: str) -> tuple[int, dict]:
    """Ask the node at base_url to push its own MESH anchor
    to the peer process at peer_url (server-to-server,
    experiment 032): the pushing server sends the anchor
    object its own mesh anchor file holds to the peer's
    /deliver-mesh-anchor (experiment 031), which judges it
    against the mesh state the peer holds under exactly
    case 20's rules - the receiving side is experiment
    031's surface, unmodified. Returns the (status, body)
    pair, like push_snapshot and push_anchor, because a
    push's refusal can ride a non-200 status and the
    caller must see its body. On 200 the body is the
    push's report: "status" equal to the peer's mesh
    anchor delivery status ("delivered", "delivery_refused",
    "no_change", or "no_mesh_anchor" when the pushing
    server has no mesh anchor configured) and the peer's
    mesh anchor delivery report under "delivery". On 502
    the body names the refusal - "identity_mismatch" with
    the claimed and certified ids over a binding transport,
    in /sync's refusal shape, or the sender's own mesh
    anchor unreadable on its own side."""
    return post_json(base_url, "/push-mesh-anchor", {"peer": peer_url})


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
                        # Experiment 029: whether a witness is
                        # configured for this process's registry
                        # anchor (operator configuration only; a
                        # delivered anchor cannot change it).
                        "anchor_witness_configured": node.anchor_witness is not None,
                        # Experiment 026: the mesh side's anchor
                        # standing, beside the registry half's
                        # fields above - whether a mesh anchor
                        # is configured for this process's sync
                        # chain, and experiment 015's
                        # classification of the mesh state it
                        # currently holds.
                        "mesh_anchor_configured": node.mesh_anchor_path is not None,
                        "mesh_anchor_status": node.mesh_anchor_status,
                        # Experiment 033: the mesh side's
                        # persistence standing, beside the
                        # mesh anchor fields above - whether
                        # a mesh state file is configured for
                        # this process, and the load report
                        # from its startup (recovered / fresh,
                        # with the recovered entry count and
                        # state hash; null when no state file
                        # is configured).
                        "mesh_state_configured": node.mesh_state_path is not None,
                        "mesh_state_load": node.mesh_state_load,
                        # Experiment 035: this process's
                        # standing peer list (sync-mesh case
                        # 24) - whether a --peers file was
                        # configured at startup, and the
                        # parsed list itself (null when
                        # unconfigured). The list is reported
                        # whole: /health is the operator's
                        # standing surface and, at this
                        # prototype's localhost scale, a peer
                        # address is not a credential.
                        "peers_configured": node.peers is not None,
                        "peers": node.peers,
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
                        # Experiment 028: the last anchor
                        # delivery's report (None until the first
                        # anchor delivery arrives), beside the
                        # last snapshot delivery's.
                        "last_anchor_delivery": node.last_anchor_delivery,
                        # Experiment 030: the last PAIRED push
                        # this process initiated toward a peer
                        # (its response body; None until the
                        # first paired push is driven), beside
                        # the last snapshot push's.
                        "last_anchor_push": node.last_anchor_push,
                        # Experiment 031: the last MESH anchor
                        # delivery's report (None until the
                        # first mesh anchor delivery arrives),
                        # beside the mesh standing above.
                        "last_mesh_anchor_delivery": node.last_mesh_anchor_delivery,
                        # Experiment 032: the last MESH anchor
                        # push this process initiated toward a
                        # peer (its response body; None until
                        # the first mesh anchor push is
                        # driven), beside the last mesh anchor
                        # delivery's.
                        "last_mesh_anchor_push": node.last_mesh_anchor_push,
                        # Experiment 035: the last peer-list
                        # sweep's report (its full response
                        # body; None until the first sweep is
                        # driven), beside the last pushes'.
                        "last_sync_all": node.last_sync_all,
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
                    entry = node.mutate_mesh(key, value)
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
            if self.path == "/sync-all":
                # Experiment 035: one caller-driven sweep over
                # this process's configured peer list (see
                # _sync_all_peers). The report is the response,
                # per-peer outcomes included: a refused or
                # unreachable peer is a sweep outcome, not a
                # transport error, exactly as a divergence is a
                # /entries outcome.
                self._send_json(200, self._sync_all_peers())
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
            if self.path == "/push-anchor":
                # Experiment 030: this process pushes its own
                # standing registry record AND its registry
                # anchor to a peer process (see
                # _push_anchor_to_peer). The payload is the
                # peer's base URL, exactly /push-snapshot's
                # shape.
                peer = payload.get("peer")
                if not isinstance(peer, str) or not peer:
                    self._send_json(400, {
                        "error": "push-anchor needs a peer base URL"})
                    return
                self._push_anchor_to_peer(peer)
                return
            if self.path == "/push-mesh-anchor":
                # Experiment 032: this process pushes its own
                # MESH anchor to a peer process (see
                # _push_mesh_anchor_to_peer). The payload is
                # the peer's base URL, exactly /push-anchor's
                # shape.
                peer = payload.get("peer")
                if not isinstance(peer, str) or not peer:
                    self._send_json(400, {
                        "error": "push-mesh-anchor needs a peer base URL"})
                    return
                self._push_mesh_anchor_to_peer(peer)
                return
            if self.path == "/deliver-anchor":
                # Experiment 028: one registry anchor delivery
                # received over the wire (see NetworkNode.
                # deliver_anchor). The payload carries the
                # anchor object itself and, for the paired
                # delivery, the snapshot entries it blesses; the
                # report is the response, refusals included: a
                # refused anchor delivery is a delivery outcome,
                # not a transport error, exactly as above.
                raw_anchor = payload.get("anchor")
                if not isinstance(raw_anchor, dict):
                    self._send_json(400, {
                        "error": "deliver-anchor needs an anchor "
                                 "object"})
                    return
                raw_entries = payload.get("entries")
                if (raw_entries is not None
                        and (not isinstance(raw_entries, list)
                             or not all(isinstance(e, dict)
                                        for e in raw_entries))):
                    self._send_json(400, {
                        "error": "deliver-anchor entries must be "
                                 "a list of snapshot entry objects"})
                    return
                with node.lock:
                    report = node.deliver_anchor(raw_anchor, raw_entries)
                self._send_json(200, report)
                return
            if self.path == "/deliver-mesh-anchor":
                # Experiment 031: one MESH anchor delivery
                # received over the wire (see NetworkNode.
                # deliver_mesh_anchor / sync-mesh case 20).
                # The payload carries the mesh anchor object
                # itself, ALONE - no entries field, by case
                # 20's stated pairing decision, because mesh
                # entries keep their own consulted receive
                # path (case 15). The delivery's report is
                # the response, refusals included, exactly as
                # the anchor delivery above is answered.
                raw_anchor = payload.get("anchor")
                if not isinstance(raw_anchor, dict):
                    self._send_json(400, {
                        "error": "deliver-mesh-anchor needs an "
                                 "anchor object"})
                    return
                with node.lock:
                    report = node.deliver_mesh_anchor(raw_anchor)
                self._send_json(200, report)
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

        def _record_anchor_push(self, body: dict) -> dict:
            """Record a PAIRED push's outcome as this process's
            last anchor push (experiment 030) and return the
            body for sending: /health carries the response
            body of the last paired push driven on this
            process, a 200 report or a 502 refusal alike,
            beside the last snapshot push."""
            with node.lock:
                node.last_anchor_push = body
            return body

        def _push_anchor_to_peer(self, peer: str) -> None:
            # Initiating-side identity check (experiment 017),
            # exactly as _push_snapshot_to_peer: with binding
            # on, the peer server's certificate id is checked
            # against the node id the peer claims in its health
            # response BEFORE anything is pushed. A mismatch
            # refuses the push outright; neither half moves.
            if node.bind_identity:
                try:
                    verify_peer_identity(peer)
                except protected_link.IdentityMismatch as exc:
                    print(
                        f"identity_mismatch refused paired push to {peer}: "
                        f"claimed={exc.claimed} certified={exc.certified}",
                        flush=True,
                    )
                    self._send_json(502, self._record_anchor_push({
                        "error": "identity_mismatch",
                        "claimed": exc.claimed,
                        "certified": exc.certified,
                        "detail": str(exc),
                    }))
                    return
                except (AssertionError, urllib.error.URLError, OSError) as exc:
                    self._send_json(502, self._record_anchor_push(
                        {"error": f"peer unreachable: {exc}"}))
                    return
            # What this process stands on, both halves (case
            # 19): the record is the PolicyNode's loaded,
            # verified entries - what it serves from, exactly
            # as _push_snapshot_to_peer reads them; the anchor
            # is the object this process's own anchor file
            # holds, read from that file under the same lock.
            # For the anchor the file IS the standing (the
            # PolicyNode consults that very file at startup
            # and on every reload), so the file and the
            # standing agree by construction. A process with
            # no PolicyNode holds neither half to push.
            with node.lock:
                entries = (
                    [dict(e) for e in node.policy_node._entries]
                    if node.policy_node is not None else None
                )
                anchor = None
                anchor_error: Exception | None = None
                if node.anchor_path is not None:
                    try:
                        anchor = json.loads(
                            node.anchor_path.read_text(encoding="utf-8"))
                        if not isinstance(anchor, dict):
                            raise ValueError(
                                "anchor file does not hold an "
                                "anchor object")
                    except (OSError, ValueError) as exc:
                        anchor = None
                        anchor_error = exc
            if entries is None:
                self._send_json(200, self._record_anchor_push({
                    "node_id": node.node_id,
                    "peer": peer,
                    "status": "no_snapshot",
                    "delivery": None,
                }))
                return
            if anchor_error is not None:
                # This process's own anchor cannot be read: it
                # has nothing honest to send, and no contact
                # with the peer is made. A sender-side refusal,
                # reported on 502 in /sync's family rather than
                # as a push of nothing.
                self._send_json(502, self._record_anchor_push(
                    {"error": f"own anchor unreadable: {anchor_error}"}))
                return
            if anchor is None:
                # No anchor configured on this process: the
                # record alone, to the peer's /deliver-snapshot
                # - case 16's push exactly, its outcome
                # recorded here as this process's last anchor
                # push. The new surface adds nothing when there
                # is no anchor to carry.
                path = "/deliver-snapshot"
                payload = {"entries": entries}
            else:
                # The peer judges the pair together: its
                # /deliver-anchor in the paired form
                # (experiment 028) runs case 17's whole
                # judgement - succession, the backward fence,
                # signature and history, the witness judgement
                # where the peer is witness-configured, and
                # pairing - before anything changes.
                path = "/deliver-anchor"
                payload = {"anchor": anchor, "entries": entries}
            # post_json is used directly rather than the
            # deliver helpers (which assert a 200) because a
            # refusal can arrive as a non-200 and its body is
            # the outcome, exactly as in _push_snapshot_to_peer.
            try:
                status, delivery = post_json(peer, path, payload)
            except (urllib.error.URLError, OSError) as exc:
                self._send_json(502, self._record_anchor_push(
                    {"error": f"peer push failed: {exc}"}))
                return
            if status != 200:
                # The peer refused before its delivery endpoint
                # ran - over a binding transport, experiment
                # 017's accepting-side identity_mismatch (403
                # naming claimed and certified ids). The refusal
                # is the push's outcome and is reported in the
                # same shape /sync reports its refusals.
                body = {"error": delivery.get(
                    "error", f"peer refused the push: HTTP {status}")}
                for key in ("claimed", "certified", "detail"):
                    if key in delivery:
                        body[key] = delivery[key]
                self._send_json(502, self._record_anchor_push(body))
                return
            # The peer's paired delivery report IS the push's
            # result, refusals included: the peer judged the
            # pair as one thing, and its verdict is reported,
            # not re-derived here.
            self._send_json(200, self._record_anchor_push({
                "node_id": node.node_id,
                "peer": peer,
                "status": delivery.get("status"),
                "delivery": delivery,
            }))

        def _record_mesh_anchor_push(self, body: dict) -> dict:
            """Record a MESH anchor push's outcome as this
            process's last mesh anchor push (experiment 032)
            and return the body for sending: /health carries
            the response body of the last mesh anchor push
            driven on this process, a 200 report or a 502
            refusal alike, beside the last mesh anchor
            delivery."""
            with node.lock:
                node.last_mesh_anchor_push = body
            return body

        def _push_mesh_anchor_to_peer(self, peer: str) -> None:
            # Initiating-side identity check (experiment 017),
            # exactly as _push_anchor_to_peer: with binding
            # on, the peer server's certificate id is checked
            # against the node id the peer claims in its health
            # response BEFORE anything is pushed. A mismatch
            # refuses the push outright; nothing moves.
            if node.bind_identity:
                try:
                    verify_peer_identity(peer)
                except protected_link.IdentityMismatch as exc:
                    print(
                        f"identity_mismatch refused mesh anchor push to {peer}: "
                        f"claimed={exc.claimed} certified={exc.certified}",
                        flush=True,
                    )
                    self._send_json(502, self._record_mesh_anchor_push({
                        "error": "identity_mismatch",
                        "claimed": exc.claimed,
                        "certified": exc.certified,
                        "detail": str(exc),
                    }))
                    return
                except (AssertionError, urllib.error.URLError, OSError) as exc:
                    self._send_json(502, self._record_mesh_anchor_push(
                        {"error": f"peer unreachable: {exc}"}))
                    return
            # What this process stands on (case 21): the
            # anchor object this process's own MESH anchor
            # file holds, read from that file under the node
            # lock. For the mesh anchor the file IS the
            # standing, exactly as case 19 set it for the
            # registry anchor: the process's AnchoredSyncNode
            # consults that very file on every mesh receive,
            # so the file and the standing agree by
            # construction. The sender re-derives no verdict:
            # it reads its file and sends what the file holds.
            with node.lock:
                anchor = None
                anchor_error: Exception | None = None
                if node.mesh_anchor_path is not None:
                    try:
                        anchor = json.loads(
                            node.mesh_anchor_path.read_text(
                                encoding="utf-8"))
                        if not isinstance(anchor, dict):
                            raise ValueError(
                                "mesh anchor file does not hold "
                                "an anchor object")
                    except (OSError, ValueError) as exc:
                        anchor = None
                        anchor_error = exc
            if node.mesh_anchor_path is None:
                # No mesh anchor configured on this process:
                # it holds no mesh anchor to push (case 20's
                # own name for the mesh half's configuration
                # edge), and no contact with the peer is made.
                # The mesh push has no record-only fallback
                # and no second form: unlike _push_anchor_to_
                # peer, it carries the anchor alone or it does
                # not run, because case 20's surface has no
                # paired form for it to degrade into.
                self._send_json(200, self._record_mesh_anchor_push({
                    "node_id": node.node_id,
                    "peer": peer,
                    "status": "no_mesh_anchor",
                    "delivery": None,
                }))
                return
            if anchor_error is not None:
                # This process's own mesh anchor cannot be
                # read: it has nothing honest to send, and no
                # contact with the peer is made. A sender-side
                # refusal, reported on 502 in /sync's family
                # rather than as a push of nothing.
                self._send_json(502, self._record_mesh_anchor_push(
                    {"error": f"own anchor unreadable: {anchor_error}"}))
                return
            # The peer judges: its /deliver-mesh-anchor
            # (experiment 031) runs case 20's whole judgement
            # against the mesh state IT holds - configuration,
            # readability, no-change, succession, the backward
            # fence, signature and kept history, the witness
            # judgement where the peer is mesh-witness-
            # configured, and the head judgement - before
            # anything changes. post_json is used directly
            # rather than the deliver_mesh_anchor helper
            # (which asserts a 200) because a refusal can
            # arrive as a non-200 and its body is the outcome,
            # exactly as in _push_anchor_to_peer.
            try:
                status, delivery = post_json(
                    peer, "/deliver-mesh-anchor", {"anchor": anchor})
            except (urllib.error.URLError, OSError) as exc:
                self._send_json(502, self._record_mesh_anchor_push(
                    {"error": f"peer push failed: {exc}"}))
                return
            if status != 200:
                # The peer refused before its mesh anchor
                # delivery endpoint ran - over a binding
                # transport, experiment 017's accepting-side
                # identity_mismatch (403 naming claimed and
                # certified ids). The refusal is the push's
                # outcome and is reported in the same shape
                # /sync reports its refusals.
                body = {"error": delivery.get(
                    "error", f"peer refused the push: HTTP {status}")}
                for key in ("claimed", "certified", "detail"):
                    if key in delivery:
                        body[key] = delivery[key]
                self._send_json(502, self._record_mesh_anchor_push(body))
                return
            # The peer's mesh anchor delivery report IS the
            # push's result, refusals included: the peer
            # judged, and its verdict is reported, not
            # re-derived here.
            self._send_json(200, self._record_mesh_anchor_push({
                "node_id": node.node_id,
                "peer": peer,
                "status": delivery.get("status"),
                "delivery": delivery,
            }))

        def _sync_once(self, peer: str) -> tuple[int, dict]:
            # One peer sync round (experiments 007/017): the
            # initiating-side identity check where binding is on,
            # the verified pull, merge, and push-back - returning
            # (status, body) exactly as POST /sync answers them.
            # Experiment 035 extracted this from _sync_with_peer
            # unchanged so the peer-list sweep runs the identical
            # round per peer: one code path, two callers, and the
            # surfaces can never disagree about what one peer
            # sync does.
            #
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
                    return 502, {
                        "error": "identity_mismatch",
                        "claimed": exc.claimed,
                        "certified": exc.certified,
                        "detail": str(exc),
                    }
                except (AssertionError, urllib.error.URLError, OSError) as exc:
                    return 502, {"error": f"peer unreachable: {exc}"}
            # Pull: the peer's entries are untrusted data until this
            # node's receive_entries verifies them in the merge.
            try:
                their_entries = fetch_entries(peer)
            except (AssertionError, urllib.error.URLError, OSError) as exc:
                return 502, {"error": f"peer unreachable: {exc}"}
            with node.lock:
                result_self = node.receive_mesh_entries(their_entries)
                merged = node.sync_node.entries()
            # Push: the peer verifies the merged set again on its side.
            try:
                result_peer = push_entries(peer, merged)
            except (AssertionError, urllib.error.URLError, OSError) as exc:
                return 502, {
                    "error": f"peer push failed: {exc}",
                    "self": result_self.to_dict(),
                }
            with node.lock:
                state_hash = node.sync_node.state_hash()
            return 200, {
                "self": result_self.to_dict(),
                "peer": result_peer,
                "state_hash": state_hash,
            }

        def _sync_with_peer(self, peer: str) -> None:
            # POST /sync: one round, exactly experiments
            # 007-034's surface; the round itself is _sync_once.
            status, body = self._sync_once(peer)
            self._send_json(status, body)

        def _record_sync_all(self, body: dict) -> dict:
            # Experiment 035: the sweep's response body is what
            # /health carries as last_sync_all, beside the last
            # pushes' - recorded under the node lock, as every
            # last report is.
            with node.lock:
                node.last_sync_all = body
            return body

        @staticmethod
        def _classify_sync_outcome(peer: str, status: int,
                                   body: dict) -> dict:
            # Experiment 035: classify one _sync_once round into
            # the sweep's per-peer result vocabulary (sync-mesh
            # case 24), carrying the round's own evidence beside
            # the classification - both SyncResults on a completed
            # round, the refusal's own name on a refused one, the
            # transport error's own text on a failed one. Nothing
            # is renamed: a divergence keeps the SyncResult's
            # reason, an identity refusal keeps
            # "identity_mismatch".
            result = {
                "peer": peer,
                "outcome": None,
                "reason": None,
                "detail": None,
                "self": None,
                "peer_result": None,
                "state_hash": None,
            }
            if status == 200:
                result["self"] = body["self"]
                result["peer_result"] = body["peer"]
                result["state_hash"] = body["state_hash"]
                for side in (body["self"], body["peer"]):
                    if side["divergence_detected"]:
                        result["outcome"] = "refused"
                        result["reason"] = side["reason"]
                        return result
                result["outcome"] = "converged"
                return result
            error = str(body.get("error", ""))
            result["detail"] = error
            if error == "identity_mismatch":
                result["outcome"] = "refused"
                result["reason"] = "identity_mismatch"
                result["detail"] = body.get("detail")
                result["claimed"] = body.get("claimed")
                result["certified"] = body.get("certified")
            elif error.startswith("peer unreachable"):
                result["outcome"] = "unreachable"
            else:
                # "peer push failed: ...": the round began - the
                # pull stood, so the self SyncResult rides the
                # result - and the push-back could not be
                # delivered. A partial round, reported as one.
                result["outcome"] = "transport_failed"
                result["self"] = body.get("self")
            return result

        def _sync_all_peers(self) -> dict:
            # Experiment 035 (sync-mesh case 24): one driven
            # sweep over this process's configured peer list.
            # Each configured peer, in list order, gets exactly
            # the _sync_once round /sync performs; each outcome
            # is classified and collected, and NO outcome stops
            # the sweep - an unreachable or refusing peer is one
            # result among the rest, which is the entire point
            # of holding the list on the node. An entry naming
            # this process's own bound address is skipped and
            # reported as "self", with no contact made. The
            # response is the report: status "no_peers" when
            # the sweep has no peers to attempt (unconfigured,
            # an empty list, or a list naming only this
            # process), "converged" when every attempted peer
            # converged, "partial" otherwise, plus this
            # process's state hash after the whole sweep.
            peers = node.peers
            with node.lock:
                state_hash = node.sync_node.state_hash()
            if not peers:
                return self._record_sync_all({
                    "node_id": node.node_id,
                    "status": "no_peers",
                    "results": [],
                    "state_hash": state_hash,
                })
            results = []
            for peer in peers:
                if (node.own_base_url is not None
                        and peer == node.own_base_url):
                    results.append({
                        "peer": peer,
                        "outcome": "self",
                        "reason": None,
                        "detail": None,
                        "self": None,
                        "peer_result": None,
                        "state_hash": None,
                    })
                    continue
                status, body = self._sync_once(peer)
                results.append(
                    self._classify_sync_outcome(peer, status, body))
            attempted = [r for r in results if r["outcome"] != "self"]
            if not attempted:
                sweep_status = "no_peers"
            elif all(r["outcome"] == "converged" for r in attempted):
                sweep_status = "converged"
            else:
                sweep_status = "partial"
            with node.lock:
                state_hash = node.sync_node.state_hash()
            return self._record_sync_all({
                "node_id": node.node_id,
                "status": sweep_status,
                "results": results,
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


def read_peer_list(path: str | Path) -> list[str]:
    """Read and validate a --peers file (experiment 035,
    sync-mesh case 24): plain text, one peer base URL per
    line; blank lines and lines whose first non-space
    character is '#' are ignored; a trailing slash is
    stripped; an exact duplicate of an earlier line is
    dropped after its first occurrence. Every remaining
    line must be an http:// or https:// base URL with a
    host. Raises ValueError naming peers_unreadable on an
    unreadable file or a line that is not such a URL:
    the process must never run believing it holds a peer
    list it does not hold (the crl_unreadable /
    mesh_state_unreadable discipline). Returns the parsed
    list in file order."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(
            f"peers_unreadable: peer list {path} cannot be read: {exc}"
        ) from exc
    peers: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        url = stripped.rstrip("/")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(
                f"peers_unreadable: peer list {path} line {lineno} "
                f"is not an http(s) base URL: {stripped!r}"
            )
        if url not in peers:
            peers.append(url)
    return peers


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
    parser.add_argument("--anchor-witness", default=None,
                        help="witness identity for a witnessed anchor "
                             "(experiment 029); requires --anchor and "
                             "--anchor-witness-secret-file. With it, the "
                             "PolicyNode also requires the anchor's "
                             "witness countersignature at startup and "
                             "the process refuses to start on "
                             "anchor_witness_invalid; anchor deliveries "
                             "are judged against the same witness")
    parser.add_argument("--anchor-witness-secret-file", default=None,
                        help="file holding the anchor witness's "
                             "enrollment secret (hex), read once at "
                             "startup; requires --anchor-witness. A "
                             "file, not a value argument, so the secret "
                             "does not ride in the process command line")
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
    parser.add_argument("--mesh-anchor-witness", default=None,
                        help="witness identity for a witnessed mesh "
                             "anchor (experiment 029); requires "
                             "--mesh-anchor and "
                             "--mesh-anchor-witness-secret-file. With "
                             "it, every mesh consultation also requires "
                             "the mesh anchor's witness countersignature, "
                             "refusing as anchor_witness_invalid")
    parser.add_argument("--mesh-anchor-witness-secret-file", default=None,
                        help="file holding the mesh anchor witness's "
                             "enrollment secret (hex), read once at "
                             "startup; requires --mesh-anchor-witness. "
                             "A file, not a value argument, so the "
                             "secret does not ride in the process "
                             "command line")
    parser.add_argument("--mesh-state", default=None,
                        help="path to a file holding this process's "
                             "persisted mesh state (experiment 033); "
                             "independent of --snapshot and "
                             "--mesh-anchor. An existing file is "
                             "recovered at startup - judged by the "
                             "mesh's own verification, never trusted, "
                             "and, with --mesh-anchor, gated on the "
                             "anchor - and every accepted mesh "
                             "change is written back to it before "
                             "the driving response returns. A file "
                             "that does not exist yet starts the "
                             "process fresh and is created on the "
                             "first accepted change. Without it the "
                             "mesh state is process memory only, "
                             "exactly as experiments 007-032 left it")
    parser.add_argument("--peers", default=None,
                        help="path to a plain-text peer list for "
                             "this process (experiment 035): one "
                             "peer base URL per line, '#' comments "
                             "and blank lines ignored, read once at "
                             "startup. With it, /health reports the "
                             "list and a client can drive one sweep "
                             "over it with POST /sync-all, each peer "
                             "synced through the same round /sync "
                             "runs. The list is a startup fact: "
                             "changing the file is a restart act, "
                             "and no surface delivers one. A file "
                             "that cannot be read, or a content line "
                             "that is not an http(s) base URL, "
                             "refuses the start as peers_unreadable. "
                             "Without it the process knows no peers, "
                             "exactly as experiments 007-034 left it")
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
    parser.add_argument("--tls-crl", default=None,
                        help="path to a certificate revocation list "
                             "(PEM, issued by the --tls-ca authority) "
                             "to enforce at the TLS handshake "
                             "(experiment 034); requires the TLS "
                             "arguments. The list is loaded once at "
                             "startup and enforced in both directions: "
                             "against the client certificates this "
                             "process accepts, and against the server "
                             "certificates this process connects out "
                             "to. A new list takes effect on restart. "
                             "A path that cannot be read or carries "
                             "no parseable CRL refuses the start as "
                             "crl_unreadable")
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

    anchor_witness_identity = (args.anchor_witness,
                               args.anchor_witness_secret_file)
    if any(anchor_witness_identity) and not all(anchor_witness_identity):
        parser.error("--anchor-witness and --anchor-witness-secret-file "
                     "must be given together")
    if args.anchor_witness and not args.anchor:
        parser.error("--anchor-witness requires --anchor (the witness "
                     "identity is for that anchor)")
    anchor_witness_secret_hex = None
    if args.anchor_witness_secret_file:
        anchor_witness_secret_hex = Path(
            args.anchor_witness_secret_file).read_text(encoding="utf-8").strip()

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

    mesh_anchor_witness_identity = (args.mesh_anchor_witness,
                                    args.mesh_anchor_witness_secret_file)
    if (any(mesh_anchor_witness_identity)
            and not all(mesh_anchor_witness_identity)):
        parser.error("--mesh-anchor-witness and "
                     "--mesh-anchor-witness-secret-file must be given "
                     "together")
    if args.mesh_anchor_witness and not args.mesh_anchor:
        parser.error("--mesh-anchor-witness requires --mesh-anchor (the "
                     "witness identity is for that anchor)")
    mesh_anchor_witness_secret_hex = None
    if args.mesh_anchor_witness_secret_file:
        mesh_anchor_witness_secret_hex = Path(
            args.mesh_anchor_witness_secret_file
        ).read_text(encoding="utf-8").strip()

    tls_values = (args.tls_cert, args.tls_key, args.tls_ca)
    if any(tls_values) and not all(tls_values):
        parser.error("--tls-cert, --tls-key, and --tls-ca must be given together")
    if args.tls_bind_identity and not all(tls_values):
        parser.error("--tls-bind-identity requires --tls-cert, --tls-key, and --tls-ca")
    if args.tls_crl and not all(tls_values):
        parser.error("--tls-crl requires --tls-cert, --tls-key, and --tls-ca "
                     "(a revocation list is enforced by a protected link)")
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

    # Experiment 035: the peer list is parsed and validated
    # here, before the process serves anything; a bad list
    # refuses the start naming peers_unreadable, as a bad CRL
    # refuses it naming crl_unreadable.
    peer_list: list[str] | None = None
    if args.peers:
        try:
            peer_list = read_peer_list(args.peers)
        except ValueError as exc:
            parser.error(str(exc))

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
        anchor_witness=args.anchor_witness,
        anchor_witness_secret_hex=anchor_witness_secret_hex,
        mesh_anchor_path=args.mesh_anchor,
        mesh_anchor_signer=args.mesh_anchor_signer,
        mesh_anchor_secret_hex=mesh_anchor_secret_hex,
        mesh_anchor_witness=args.mesh_anchor_witness,
        mesh_anchor_witness_secret_hex=mesh_anchor_witness_secret_hex,
        mesh_state_path=args.mesh_state,
        peers=peer_list,
    )
    tls_context = None
    if all(tls_values):
        # A configured CRL that cannot be read or parsed refuses
        # the start here, naming crl_unreadable (experiment 034):
        # the process must never serve believing it enforces a
        # revocation list it does not hold.
        try:
            tls_context = protected_link.server_context(
                args.tls_cert, args.tls_key, args.tls_ca, crl=args.tls_crl
            )
        except protected_link.RevocationError as exc:
            parser.error(str(exc))
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
                       node_id=args.node_id, crl=args.tls_crl)
    server = build_server(node, args.host, args.port, tls_context=tls_context)
    bound_port = server.server_address[1]
    # Experiment 035: the process's own base URL as bound -
    # scheme from the TLS configuration - recorded on the
    # node so a peer-list sweep can recognize a list entry
    # naming this process itself. The URL spelling is the
    # one the operator was handed: the configured --host and
    # the bound port.
    node.own_base_url = (
        f"{'https' if tls_context is not None else 'http'}"
        f"://{args.host}:{bound_port}"
    )
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
