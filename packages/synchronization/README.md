# packages/synchronization - Synchronization Layer

Separate layer 2 of the operational pipeline. Synchronization does
exactly one job: propagate state across nodes continuously, with no
central locks, through the Continuous Synchronization Mesh
(`packages/sync-mesh/` is the mesh mechanism; this package is the layer
contract every node implements).

Inputs: state transitions from the runtime, each carrying its EAI.
Outputs: converged state, or a divergence signal that triggers rollback.
Failure mode: split-brain or diverging state views. Detection is this
layer's job; hiding divergence is a hard failure (see Gate 3 and Gate 5).

---

Status: **Gate 3 prototype built and passing** (2026-11-06, experiment
`experiments/004-gate3-sync-mesh/`), **networked node processes
built and passing** on top of it (2026-11-06, experiment
`experiments/007-networked-nodes/`): nodes now also run as separate
OS processes exchanging entries and registry snapshots over HTTP on
localhost, and an **external anchor for mesh chain heads built and
passing** (2026-11-06, experiment
`experiments/012-external-chain-anchor/`; `tests/test-cases/sync-mesh.md`
case 5), and **coordinated rollback across nodes built and
passing** (2026-11-06, experiment
`experiments/013-coordinated-rollback/`; `tests/test-cases/sync-mesh.md`
case 6), and **protected node links built and passing**
(2026-11-06, experiment `experiments/014-protected-node-links/`;
`tests/test-cases/sync-mesh.md` case 7): the networked processes
can now speak mutual TLS - encrypted in transit, with a client
certificate required - while discovery by set addresses remains
open, and **anchor consultation in sync built and passing**
(2026-11-06, experiment `experiments/015-anchor-consultation/`;
`tests/test-cases/sync-mesh.md` case 8): an `AnchoredSyncNode`
built with a mesh anchor path consults experiment 012's anchor on
the merged state before accepting a peer's entries, flagging a
rewritten peer `anchor_mismatch` and honest growth
`anchor_stale` until re-anchored; a node built without one
behaves exactly as before, and **anchor consultation in the
networked server processes built and passing** (2026-11-06,
experiment `experiments/018-anchor-in-servers-and-verdict/`;
`tests/test-cases/registration.md` case 9): a server started
with `--anchor` passes experiment 012's registry anchor to the
PolicyNode it builds, so this consultation also runs at server
startup over both transports (plaintext and mutual TLS), while
a server without the flag consults nothing. Prototype only, not production. The module docstrings in
`sync_mesh.py` and `node_server.py` are the authoritative lists of
honest limits. The layer contract above is unchanged; what follows
documents the prototypes that now implement it at prototype scale.

## What is here

| File | What it is |
|---|---|
| `sync_mesh.py` | The Gate 3 prototype: `SyncNode`, `StateEntry`, `SyncResult`, per-origin hash chains, EAI per entry (PBA-EAI-v1), deterministic convergence, divergence detection with automated local rollback. Python standard library only. Builds on the Gate 1/2 hash-chain discipline; does not modify or re-implement those packages. |
| `test_sync_mesh.py` | Automated tests, one per Gate 3 outcome (the divergence test exercises all three divergence shapes). |
| `demo.py` | Runnable walkthrough of all three Gate 3 outcomes. |
| `node_server.py` | The networked node: one `SyncNode` plus one optional Gate 2 `PolicyNode` per OS process, served over HTTP on localhost (stdlib `http.server` / `urllib` only). Endpoints for health, state, mutate, entry exchange, server-initiated `/sync`, registry snapshot, verified policy serving, - since experiment 024 - `POST /check-snapshot`, a caller-driven check that reloads the running process's `PolicyNode` from its snapshot file when the file changed cleanly (experiment 022's change facts and refusal discipline, experiment 015/021's reload), and - since experiment 025 - `POST /deliver-snapshot`, a peer-pushed registry snapshot delivery carrying the entries themselves, verified and anchor-consulted before anything changes and applied through the same reload, with the server's snapshot file rewritten from the verified delivery, and - since experiment 026 - an optional `--mesh-anchor` (plus `--mesh-anchor-signer` / `--mesh-anchor-secret-file` for a signed mesh anchor), independent of `--snapshot`, under which the process's mesh side is experiment 015's `AnchoredSyncNode` and every mesh receive consults the mesh anchor before acceptance, with `/health` reporting `mesh_anchor_configured` / `mesh_anchor_status`, and - since experiment 027 - `POST /push-snapshot`, a driven endpoint in `/sync`'s style under which this process pushes the entries its `PolicyNode` stands on to a peer's `/deliver-snapshot` and reports the peer's delivery report as the push's result (transport refusals on 502 in `/sync`'s shape), with `/health` reporting `last_snapshot_push`, and - since experiment 028 - `POST /deliver-anchor`, a driven endpoint under which this process receives its registry anchor over the wire, alone or paired with the record it blesses, judged in sync-mesh case 17's order (succession `different_signer` audit-logged, the backward fence `anchor_regression`, signature and history `anchor_signature_invalid`, pairing `anchor_unpaired` / the pair judged together) before anything changes, with `/health` reporting `last_anchor_delivery`, and - since experiment 030 - `POST /push-anchor`, a driven endpoint in `/push-snapshot`'s style under which this process pushes the entries its `PolicyNode` stands on AND the anchor object its own anchor file holds to a peer's `/deliver-anchor` in its paired form (a process with no anchor configured sends the record alone to the peer's `/deliver-snapshot`, exactly experiment 027's push) and reports the peer's paired delivery report as the push's result (transport refusals on 502 in `/sync`'s shape, and a sender whose own anchor file cannot be read reports 502 `own anchor unreadable` without contacting the peer), with `/health` reporting `last_anchor_push`. Receipt always runs the existing Gate 3/2 verification; the transport grants no trust. Also carries the client helpers the tests and demo use. |
| `test_network_nodes.py` | Automated tests that spawn real server processes on ephemeral ports: two processes converge over HTTP, snapshot + policy cross the boundary verified (tampered entry rejected on receipt, tampered snapshot refuses startup), and a killed process's survivors still serve and converge. Processes are cleaned up by the tests. |
| `demo_network_nodes.py` | Runnable walkthrough of the networked outcomes: two processes sync and converge, the second serves the policy, it is killed, and the first still serves. |
| `anchor.py` | The external anchor for mesh chain heads (experiment 012): writes each origin's head (top seq + entry hash) plus the state hash from a node's view to a file outside the node, and checks a presented node against it. Builds on `SyncNode` without modifying it. |
| `test_anchor.py` | Automated tests for sync-mesh case 5: converged nodes verify against the anchor, and the negative control (a fully rewritten origin chain that passes internal checks fails the anchor check). |
| `coordinated_rollback.py` | Coordinated rollback (experiment 013): a shared checkpoint captured from a verified-converged set of nodes (entry set, state, state hash, per-origin chain heads), a coordinator that restores every participating node to that one checkpoint through `sync_mesh.py`'s `restore_to_entries`, and a checkpoint check that names a node that skipped the rollback divergent. Builds on `SyncNode` and reuses `anchor.py`'s `origin_heads`. |
| `test_coordinated_rollback.py` | Automated tests for sync-mesh case 6: coordinated rollback restores the shared checkpoint on all nodes after a fork fault (whose local rollback observably lands elsewhere), the mesh converges on new changes afterward, and the negative control (a node that skips the rollback is detected divergent and rejoins only by restoring the checkpoint itself). |
| `demo_coordinated_rollback.py` | Runnable walkthrough of case 6: checkpoint, fault, local rollback missing the shared checkpoint, coordinated rollback, skipped node detected, rejoin. |
| `protected_link.py` | Protected node links (experiment 014): mutual-TLS contexts for the networked transport (`server_context` requires a client certificate chaining to the given CA; `client_context` verifies the server and presents the peer's own certificate), plus runtime generation of throwaway test credentials (openssl subprocess; test CA, per-node certificates, and a rogue CA for refusal tests). No credential file is committed. |
| `test_protected_nodes.py` | Automated tests for sync-mesh case 7: two protected processes converge and a policy crosses while a byte-capturing proxy proves the wire carries no plaintext, unauthenticated and rogue-credential peers are refused at the handshake with no plaintext fallback, Gate 3/2 verification still runs over the protected link (tampered entry diverges, opt-in still enforced, snapshot verifies on receipt), and a killed protected process's survivors still serve and converge. Credentials are generated per test into its temporary directory. |
| `demo_protected_nodes.py` | Runnable walkthrough of case 7: two protected processes converge, the capture holds no plaintext, a credential-less client is refused, the second process is killed, and the first still serves. |
| `anchored_sync.py` | Anchor consultation for sync (experiment 015): `AnchoredSyncNode`, a `SyncNode` subclass that reuses `anchor.py`'s experiment 012 check on the merged state before accepting a peer's entries; refusals roll back through `sync_mesh.py`'s `restore_to_entries` and are logged as divergences under the reasons `anchor_mismatch` / `anchor_stale`. Builds on `SyncNode` without modifying its receive logic. |
| `test_anchor_consultation.py` | Automated tests for sync-mesh case 8: genuine peer accepted and converged, rewritten peer flagged `anchor_mismatch` and not accepted, no-anchor sync accepting the rewrite exactly as before (stated plainly), honest growth flagged `anchor_stale` until re-anchored. |
| `demo_anchor_consultation.py` | Runnable walkthrough of case 8: genuine accept, rewrite flagged while a plain node accepts it, growth stale until re-anchored. |
| `test_anchor_in_servers.py` | Automated tests for registration case 9's server half (experiment 018): an anchored server refuses the rewritten snapshot at startup (`anchor_mismatch`) and honest growth (`anchor_stale`) and serves the genuine snapshot over plaintext and over mutual TLS; an anchorless server serves the rewrite exactly as before. |
| `demo_anchor_in_servers.py` | Runnable walkthrough of case 9's server half: genuine serves anchored, rewrite served anchorless and refused anchored, growth stale until re-anchored. |
| `test_signed_mesh_anchor.py` | Automated tests for sync-mesh case 11 (experiment 019): a signed mesh anchor verifies with its kept history, a missing/tampered signature is `anchor_signature_invalid`, a same-identity re-anchor appends history while a different identity is refused and logged, a tampered history entry is caught, an identity-configured `AnchoredSyncNode` refuses a tampered-signature anchor under that divergence reason, and without an identity a signed anchor is judged by its heads as in cases 5/8. |
| `test_signed_anchor_in_servers.py` | Automated tests for registration case 10's server half (experiment 019): a server started with `--anchor-signer` and `--anchor-secret-file` serves the genuine signed anchor and refuses to start over a tampered signature (`AnchorSignatureInvalid`); without a signing identity the same signed anchor behaves exactly as experiment 018. |
| `test_revoked_policy_network.py` | Automated tests for sync-mesh case 12 (experiment 021, fixing experiment 020's finding F2): a revoked policy requested from a restarted server returns a clean HTTP 410 refusal (error `policy_revoked`) instead of dropping the connection, over plaintext and over mutual TLS, and the server keeps serving a non-revoked policy afterward. (`node_server.py`'s `_serve_policy` catches `PolicyRevoked` since experiment 021.) |
| `test_server_live_reload.py` | Automated tests for sync-mesh case 13 (experiment 024): a running server learns of a revocation without a restart - a caller-driven `POST /check-snapshot` reloads its `PolicyNode` in the check that notices the delivered change, over plaintext and over mutual TLS with a signed anchor; `anchor_stale` / `tampered_record` / `anchor_mismatch` deliveries are refused under their own names with the previous view serving and the refusal re-reported until its cause is fixed; a no-change check reloads nothing; restart over a rewritten snapshot still refuses at startup; a mesh-only server reports `no_snapshot`. |
| `demo_server_live_reload.py` | Runnable walkthrough of case 13: one anchored server process, never restarted - no-change check, revocation delivered without the re-anchor refused as `anchor_stale` with the stale view serving, re-anchor, one check reloads, the revoked policy refuses 410 over the wire and `/health` reports the new standing. |
| `test_snapshot_wire_transport.py` | Automated tests for sync-mesh case 14 (experiment 025): a running server receives the updated registry record over the wire - `POST /deliver-snapshot` carries the snapshot entries themselves, verified as a chain and consulted against the server's anchor (and signature) before anything changes; a stood delivery rewrites the server's snapshot file from the verified entries and reloads through case 13's path, so a check right after reports `no_change`; refusals `anchor_stale` / `tampered_record` / `anchor_mismatch` / `anchor_signature_invalid` and the backward `snapshot_regression` each leave file and view byte-for-byte unchanged, over plaintext and over mutual TLS with identity binding and a signed anchor; a mesh-only server reports `no_snapshot`; the startup gate is re-asserted. |
| `demo_snapshot_wire_transport.py` | Runnable walkthrough of case 14: one anchored server process, never restarted and its file never copied by hand - no-change push, revocation pushed without the re-anchor refused as `anchor_stale`, re-anchor, one push delivers and the file is the delivered record, the revoked policy refuses 410, a check after reports `no_change`, and the old record pushed again is refused as `snapshot_regression`. |
| `test_mesh_anchor_wire.py` | Automated tests for sync-mesh case 15 (experiment 026): a server started with `--mesh-anchor` runs its mesh side as experiment 015's `AnchoredSyncNode`, so mesh receives over the wire consult the mesh anchor - honest convergence onto exactly the anchored state with `/health` reporting `mesh_anchor_status`; a rewritten peer PROCESS flagged divergent as `anchor_mismatch` over `/sync` with the anchored server's state unchanged (and accepted by a mesh-anchorless server, stated plainly); honest growth refused as `anchor_stale` until re-anchored; an entry edited in transit refused as `tampered_entry`; a differently-signed mesh anchor refused as `anchor_signature_invalid` until the genuine signed anchor is restored and the same-signer re-anchor lands the growth; an unreadable mesh anchor refuses in the SyncResult shape as `anchor_unreadable` with `/health` still answering; the consultation also proven over mutual TLS with a signed mesh anchor. |
| `demo_mesh_anchor_wire.py` | Runnable walkthrough of case 15: two plain servers converge, the mesh anchor is written over their state, one restarts with `--mesh-anchor` and converges onto the anchored state through the consultation, honest growth is refused as `anchor_stale` until re-anchored, and a fresh anchored server refuses a rewritten chain pushed over `/entries` as `anchor_mismatch`, adopting nothing. |
| `test_server_to_server_delivery.py` | Automated tests for sync-mesh case 16 (experiment 027): one server pushes its standing record to another - a driven `POST /push-snapshot` on the sender sends the entries its `PolicyNode` stands on to the peer's unmodified `/deliver-snapshot`, and the peer's delivery report is the push's result; the end-to-end story over two running servers (revocation reaches A by case 14 delivery, one push delivers it to B, 410 over the wire, B's file byte-for-byte the delivered record, the pair's mesh sync undisturbed, a second push `no_change`); refusals reported in the push result (`snapshot_regression` to an ahead peer, `anchor_stale` while the peer's anchor stands over the old head then delivered once its lifecycle completes, `anchor_mismatch` when an anchorless sender tries to spread a rewrite to an anchored peer); the full flow over mutual TLS with identity binding and a signed anchor; a false-claiming pusher refused at the binding peer's transport as `identity_mismatch` (502 naming claimed and certified, exactly experiment 017's refusal); a mesh-only sender reports `no_snapshot`. |
| `demo_server_to_server_delivery.py` | Runnable walkthrough of case 16: two anchored servers on the same record, neither restarted and B's file never copied by hand - the revocation is delivered to A (case 14), one driven push A -> B delivers it (B's file is the delivered record, alice refuses 410 from B, bob serves, `/health` on both sides reports the standing), and the same push again reports `no_change` and rewrites nothing. |
| `test_anchor_wire_delivery.py` | Automated tests for sync-mesh case 17 (experiment 028): a running server receives its registry anchor over the wire - `POST /deliver-anchor` carries the anchor object's own JSON, alone or paired with the entries it blesses, judged in case 17's order before anything changes (configuration `no_anchor` / `no_snapshot`; byte-identical `no_change`; succession `different_signer`, audit-logged in case 10's shape, on a signer-configured server and on a head-only one; the backward fence `anchor_regression` in both its shapes; a tampered kept history `anchor_signature_invalid`; pairing - an anchor alone accepted only for the standing record, `anchor_unpaired` for a head that pairs with nothing held; a pair judged together through case 14's discipline with the delivered anchor, `anchor_stale` applying neither half, then `delivered` with both files byte-for-byte the delivered pair and the revocation live at 410); the case's finding in three legs (the genuine anchor over a rewrite `anchor_mismatch`, a different identity's `different_signer`, the current signer's own honestly-extended anchor over the rewrite ACCEPTED - custody of the signer's secret is the whole fence); the paired flow over mutual TLS with identity binding and a signed anchor. |
| `demo_anchor_wire_delivery.py` | Runnable walkthrough of case 17: one signer-configured server process, never restarted and its anchor file never written by hand after startup - identical anchor `no_change`, the new anchor alone refused as `anchor_unpaired`, the pair delivered together (410 over the wire), the original anchor refused as `anchor_regression` and audit-logged, and the finding printed as it happens: mallory's rewritten record paired with an alice-signed anchor over it is accepted and served. |
| `test_witnessed_mesh_anchor.py` | Automated tests for sync-mesh case 18's mesh half (experiment 029): a countersigned mesh anchor verifies with the witness checked, a signer-alone mesh anchor is `anchor_witness_invalid` at the check and as an `AnchoredSyncNode` divergence (rolled back, state unchanged), and the write discipline holds on this chain too - a different signer countersigned by the witness is still refused at write, a witness equal to the signer is refused. |
| `test_witnessed_anchor_in_servers.py` | Automated tests for sync-mesh case 18's server half (experiment 029): a server started with `--anchor-witness` refuses to start over a signer-alone anchor naming `AnchorWitnessInvalid` and serves over a countersigned one; case 17's finding re-run at that server in its three new legs (the pair experiment 028 accepted refused with no countersignature, refused countersigned by a non-witness, accepted countersigned by the configured witness - the fence is two secrets' custody now, the model unchanged); honest paired delivery and live reload under the witness, each refused signer-alone first; succession over the wire (a pair signed by a different signer, genuinely countersigned, still `different_signer`); the witness as configuration only (a delivery countersigned by a new identity installs nothing and the configured witness's pair still lands); the mesh side's `/health` standing under a mesh witness. |
| `test_paired_push.py` | Automated tests for sync-mesh case 19 (experiment 030): one server pushes its record AND its anchor to a peer - a driven `POST /push-anchor` on the sender sends the entries its `PolicyNode` stands on and the anchor object its own anchor file holds to the peer's unmodified paired `/deliver-anchor`, and the peer's paired delivery report is the push's result; the end-to-end story over two running servers with signed anchors (the revocation reaches A by case 17 paired delivery, the record-only push still refuses `anchor_stale` at B as the control, ONE paired push brings B fully current - B's snapshot byte-for-byte the delivered record and B's anchor byte-for-byte the anchor A sent - 410 over the wire, and a second paired push is the pair's `no_change` rewriting neither file); refusals reported in the push result (`different_signer` for a sender whose anchor names another signer, audit-logged at the peer; `anchor_regression` for an earlier version of the peer's own anchor lineage; `anchor_witness_invalid` at a witness-configured peer for a signer-alone sender anchor, delivered once the sender's anchor is countersigned by the configured witness); the sender's own shapes (no anchor configured: the record alone through case 14's surface; mesh-only: `no_snapshot`; own anchor file unreadable at push time: 502 `own anchor unreadable`, the peer never contacted); the full paired flow over mutual TLS with identity binding and a signed anchor; a false-claiming paired pusher refused at the binding peer's transport as `identity_mismatch` (502 naming claimed and certified, exactly experiment 017's refusal), the peer recording no anchor delivery. |
| `demo_paired_push.py` | Runnable walkthrough of case 19: two servers on the same record under their own alice-signed anchors, neither restarted and no hand touching B's files - the revocation is delivered to A as a pair (case 17), the record-only push refuses `anchor_stale` at B as the control, ONE driven paired push A -> B delivers both halves (B's snapshot is the delivered record, B's anchor is the anchor A sent, alice refuses 410 from B, bob serves, `/health` on both sides reports the standing), and the same paired push again reports `no_change` and rewrites neither file. |

## How to run it

From the repo root:

```sh
# Tests (either command works)
python3 -m unittest discover -s packages/synchronization -v
python3 packages/synchronization/test_sync_mesh.py

# Demo (concurrent mutations converge, a partition heals, a fork is
# detected and rolled back)
python3 packages/synchronization/demo.py

# Networked nodes: tests spawn and clean up their own server
# processes on ephemeral localhost ports; the demo does the same
# visibly
python3 packages/synchronization/test_network_nodes.py
python3 packages/synchronization/demo_network_nodes.py

# External anchor for mesh chain heads (sync-mesh case 5)
python3 packages/synchronization/test_anchor.py

# Coordinated rollback (sync-mesh case 6)
python3 packages/synchronization/test_coordinated_rollback.py
python3 packages/synchronization/demo_coordinated_rollback.py

# Protected node links (sync-mesh case 7)
python3 packages/synchronization/test_protected_nodes.py
python3 packages/synchronization/demo_protected_nodes.py

# Anchor consultation in sync (sync-mesh case 8)
python3 packages/synchronization/test_anchor_consultation.py
python3 packages/synchronization/demo_anchor_consultation.py

# One node server by hand (ephemeral port; prints LISTENING <port>)
python3 packages/synchronization/node_server.py --node-id my-node \
  --snapshot <my-registry-snapshot.jsonl> --payloads <payloads.json> \
  --serve-policies --port-file /tmp/my-node.port
```

Requires Python 3.10+. No dependencies. In-process prototypes use no
network; the networked prototype uses localhost HTTP only, and
nothing is deployed anywhere else.

## How it works, in plain words

1. A node mutates locally, with no lock and no coordinator: each
   mutation becomes one immutable entry carrying its origin, a
   per-origin sequence number, a Lamport clock value, the key/value,
   an Execution-Attached Identity (PBA-EAI-v1), the hash of the
   origin's previous entry (a per-origin hash chain, the same
   discipline as Gate 1's registry chain), and its own hash.
2. Nodes reconcile by bulk pairwise anti-entropy: `sync_with` swaps
   full entry lists from pre-merge snapshots, `receive_entries`
   merges a batch. Arrival order does not matter, because the
   materialized state is a pure function of the entry set: entries
   applied in the total order (lamport, origin, seq), later writes
   to a key winning. Same set in, same state and same state hash
   out, on every node. That is what "converged" means here, and the
   tests check it three ways (entry sets, materialized state, hash).
3. A partitioned node is just a node nobody syncs with. Because
   per-origin sequences are contiguous, rejoining is the same merge:
   it receives the entries it missed, in any order, and converges.
4. Divergence is never merged. A batch containing a tampered entry
   (hash does not recompute), a forked entry (a second valid hash
   claiming an (origin, seq) slot already held: split-brain), or a
   broken per-origin chain link is refused whole. The node logs a
   divergence event and rolls back to its checkpoint, the last
   locally verified consistent entry set, and the returned
   `SyncResult` reports `divergence_detected=True, rolled_back=True`:
   the automated rollback signal. The node keeps working afterwards:
   an honest sync still converges.

## Networked nodes, in plain words

`node_server.py` is the step experiments 002-006 all named as
unbuilt: a node as a real process. Each server process owns its own
`SyncNode` and, when started with a snapshot copy, its own Gate 2
`PolicyNode`; processes share nothing but HTTP calls. Mutations are
driven into a process over HTTP (`POST /mutate`), anti-entropy is
the same full-list exchange as Gate 3, just serialized (`GET`/`POST
/entries`), and a process can initiate sync with a peer itself
(`POST /sync`: pull, verified merge, push back, peer verifies
again). Registry snapshots cross as raw bytes (`GET /snapshot`) and
are verified by the receiver with Gate 2's standalone verifier
before use; policies are served over HTTP (`GET /policy/<hash>`)
with Gate 2 verification running inside the serving process on
every request. What survives killing a process is exactly what had
already synced to another process, no more: the network tests prove
that shape (a killed node's entries live on in its peers, its
unsynced ones would not).

## Protected node links, in plain words

`protected_link.py` (experiment 014) wraps the networked
transport's socket in mutual TLS when a server is started with
`--tls-cert/--tls-key/--tls-ca`. The server requires a client
certificate chaining to its CA, so an unauthenticated peer - no
certificate, or one from a different authority - is refused at
the handshake and never reaches an endpoint, and there is no
plaintext mode on a protected port. Clients (including a server
process initiating `/sync`, which presents its own node
certificate) verify the server against the same CA with the
address check on. The wire carries ciphertext only: the case 7
tests drive a successful exchange through a byte-capturing
forwarding proxy and require the capture to contain none of the
plaintext it carried. TLS decides who may connect and keeps the
wire unreadable; it grants the payload no trust, so every Gate 3
and Gate 2 check runs unchanged inside the receiving process -
a tampered entry over the protected link diverges and rolls back
exactly as over plaintext. Discovery is untouched: addresses are
still configured by whoever starts the processes.

## Coordinated rollback, in plain words

Local rollback (point 4) returns a node to its OWN last verified
checkpoint, and checkpoints move with every verified merge - so after
the mesh has moved on, nodes can hold different checkpoints and local
rollback alone leaves them at different recovery points.
`coordinated_rollback.py` (experiment 013) names one recovery point
for the whole set: `take_shared_checkpoint` captures the entry set a
set of nodes is verified to share (it refuses, naming the node, when
they do not share one), and `coordinate_rollback` restores every
participating node to that set through `SyncNode.restore_to_entries`,
which judges the given set with the same verification a received
batch gets before keeping it. Each node's result is then checked
against the checkpoint itself - entry set, materialized state, state
hash, and per-origin chain heads - and a node that skipped the
rollback is reported divergent by that same check, with each
mismatched fact named. It rejoins by restoring the checkpoint, not
by its post-checkpoint state being accepted as the checkpoint.

## Honest limits

- Gate 3's nodes are in-process objects exchanging Python objects by
  method call; partition there is modeled by not calling sync. The
  networked prototype (experiment 007) replaces the transport with
  real localhost HTTP between real processes, and nothing beyond
  localhost. Protected links are now built at prototype scale
  (experiment 014, `protected_link.py`): a server started with TLS
  arguments speaks mutual TLS - encrypted in transit, client
  certificate required, no plaintext fallback - but authentication
  is only possession of a CA-issued certificate (no binding of a
  certificate to a claimed node id, no per-endpoint authorization
  beyond opt-in serving, no revocation or rotation, no identity
  proofing, and the test CA is generated per run on the same
  machine). Still open for the networked prototype: no peer
  discovery (addresses are configured by whoever starts the
  processes), no gossip schedule, no persistence (kill a process
  and its unreplicated in-memory entries are gone), and no scale
  or latency numbers demonstrated or claimed.
- No real Byzantine consensus: no quorum, voting, or BFT protocol.
  Detection is local hash/chain verification plus fork comparison
  when two views meet. A fork nobody ever compares is not detected.
  Gate 5's forced-Byzantine canary test is still ahead.
- Rollback in Gate 3's sync itself is local to the detecting node,
  and peers are not signalled beyond the `SyncResult` handed to the
  caller. Coordinated rollback is now built at prototype scale on
  top of it (experiment 013, `coordinated_rollback.py`): an
  explicit, caller-driven, in-process step that restores every
  participating node to one shared checkpoint and checks each
  against it. There is still no network signalling, no automatic
  trigger on a peer's divergence, and no consensus or vote on the
  checkpoint; rolling back discards legitimate post-checkpoint
  entries along with the fault's context, and detection of a node
  that skipped the rollback is by checkpoint comparison, not by
  sync (its extra entries are valid chain entries, so the check
  must run before sync resumes with that node).
- Whole-chain rewrite limit inherited from Gates 1/2: one edited
  entry is caught, and a fork is caught when both versions are seen,
  but one origin's entire chain rewritten from genesis, shown to a
  node that never saw the original, verifies on its own. An external
  anchor for chain heads is now built at prototype scale
  (experiment 012, `anchor.py` in this package and in
  `packages/registration/`): per-origin heads plus the state hash,
  written to a file outside the node and checked against a
  presented node, which catches the full rewrite in the tests. The
  anchor file is a stand-in - local, same machine, unwitnessed;
  it can be signed since experiment 019 (test case 11 in
  `tests/test-cases/sync-mesh.md`): only the identity that
  signed a mesh anchor may re-anchor it, accepted re-anchors
  append signed, hash-chained history entries inside the anchor
  file, and a consultation configured with that signing identity
  refuses a signature that does not verify as
  `anchor_signature_invalid`. Since experiment 015 an `AnchoredSyncNode`
  configured with an anchor path consults it automatically on
  every receive; a plain `SyncNode` and the networked servers
  still do not call the mesh anchor, and cross-node head comparison at scale
  is still unbuilt.
- Conflict resolution (deterministic last-writer-wins in the total
  order) is a stated prototype rule, not a claim about correct
  production semantics for every key.
- `tests/test-cases/sync-mesh.md` case 4 (sub-millisecond lineage
  under load) is a Gate 5 benchmark. It was not measured or attempted
  here, and no performance number is claimed.
