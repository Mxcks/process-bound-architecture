# Test Cases: Continuous Synchronization Mesh (layer 05)

1. Concurrent mutations across many nodes converge with no central lock.
2. A partitioned node rejoins and its state converges, or rollback fires.
3. Any split-brain or diverging state view triggers automated rollback.
4. Lineage for a transformation is traced in under one millisecond under load (benchmark, to be measured, not assumed).
5. External anchor for chain heads: a node's per-origin chain heads are anchored outside the node's own state and checked against that anchor, so a fully rewritten origin chain that verifies on its own still fails. Detailed case: `tests/test-cases/registration.md` case 7 (experiment 012).
6. Coordinated rollback (gaps flagged in experiments 004 and 006):
   a set of nodes that have converged take a shared checkpoint - one
   entry set every node is verified to hold identically (same
   materialized state, same state hash, same per-origin chain heads).
   After the checkpoint, the mesh moves on (further honest mutations
   sync normally), and then a fault of a kind this layer already
   detects (a forked entry, sync-mesh case 3) is injected into one
   node. Expected: that node's local rollback returns it only to its
   own last verified state, which is not the shared checkpoint, so
   local rollback alone leaves the nodes at different states; a
   coordinated rollback is then triggered and EVERY participating
   node is restored to the shared checkpoint - identical entry set,
   materialized state, state hash, and per-origin chain heads on all
   of them, exactly equal to the checkpoint, not merely equal to
   each other - and the participating mesh still converges on new
   changes afterward (a node whose own entries were rolled back can
   mutate again on its restored chain head). Negative control, in
   the style of availability case 1: a node that does NOT take part
   in the coordinated rollback and keeps its post-checkpoint state
   is detected as divergent afterward by the checkpoint check
   (entry set, state, and chain heads all named as mismatched), is
   not admitted as being at the checkpoint, and can rejoin only by
   itself restoring the checkpoint, after which it converges with
   the others. Status: implemented at prototype scale in experiment
   013 (explicit, caller-driven coordination step in-process; no
   network signalling, consensus vote, or automatic trigger is
   claimed).
7. Protected node links (added 2026-11-06, experiment 014):
   experiment 007's networked node processes talk over a local
   connection that is unencrypted, with no sign-in or authentication,
   and nodes find each other only by set addresses. This case closes
   the first two parts of that limit; discovery by set addresses
   stays as-is and remains a stated limit. Expected: two node
   processes exchange entries and a registered policy over a
   connection that is encrypted in transit, proven by a plaintext
   capture of the traffic through a forwarding proxy: the captured
   bytes contain neither the policy code nor any entry key or value,
   while the exchange itself succeeds over that same captured path.
   A connection from an unauthenticated peer is refused: a client
   holding no credential, and a client holding a credential issued
   by a different authority, each fail to connect at all, so neither
   can sync entries nor receive a policy. A tampered entry sent over
   the protected link is still caught by the existing Gate 3
   verification on receipt (divergence detected, rolled back, state
   unchanged), so encryption does not bypass Gate 3 or Gate 1/2
   registration checks: a node that did not opt in to serving still
   refuses over the protected link, and the snapshot still verifies
   on receipt. The experiment 007 behaviors still pass over the
   protected link: two processes converge, a policy crosses with
   verification, and the survivors still serve and keep converging
   after one process is killed. Negative control, in the style of
   availability case 1: the same servers, reached with plain HTTP
   instead of the protected transport, answer nothing usable, so
   the protected port does not fall back to plaintext. Status:
   implemented at prototype scale in experiment 014 (mutual TLS
   between localhost processes, test credentials generated inside
   the tests at runtime; no production identity proofing, no
   certificate lifecycle, and no discovery is claimed).
8. Anchor consultation in sync (gap flagged in experiment 012):
   a sync node constructed with a mesh anchor path consults that
   anchor - experiment 012's check, reused - on the merged state
   before accepting a peer's entries as converged. Expected: a
   peer holding exactly the anchored entry set is accepted and
   the nodes converge; a peer whose origin chain was rewritten
   from genesis (internally consistent; a node with no anchor
   accepts it, which is experiment 012's negative control) is
   flagged divergent with reason anchor_mismatch, its entries are
   not accepted, and the anchored node's state is unchanged.
   Honest growth is named separately, as in registration case 8:
   a merged state that contains everything anchored, intact,
   plus honestly appended entries is anchor_stale, refused until
   the new state is re-anchored, then accepted. With no anchor
   configured, sync behavior is exactly the pre-consultation
   behavior. Detailed case: `tests/test-cases/registration.md`
   case 8. Status: implemented at prototype scale in experiment
   015 (in-process nodes; the experiment 007/014 server processes
   still do not consult an anchor, and no anchor is consulted by
   the Gate 5 verdict).
9. Certificate binding to a claimed node id (added 2026-11-06,
   experiment 017): experiment 014's protected links authenticate
   possession of a CA-issued certificate and nothing more - the
   certificate's name is never checked against the node id a
   process claims, so any enrolled node's certificate could be
   presented while claiming to be any other node. This case binds
   the two. Certificates are issued for a specific node id, with
   that id carried in the certificate itself (a URI subjectAltName
   of the form `urn:pba:node:<node-id>`, generated at runtime
   inside the tests exactly as experiment 014 generates its
   credentials; no certificate, key, or credential file exists in
   this repository). A protected process started with identity
   binding enabled refuses to start at all when the id in its own
   certificate differs from the node id it was started claiming.
   When a binding-enabled node ACCEPTS a link, the connecting
   peer's certificate id is checked against the node id that peer
   claims on the link: a match is served exactly as in experiment
   014 (entries sync and converge, a policy crosses verified),
   and a mismatch is refused with a plainly stated
   identity_mismatch naming the claimed id and the certified id,
   on the health, entries, and policy paths alike. When a
   binding-enabled node INITIATES a link (the server-to-server
   /sync direction experiment 014 exposes), it checks the peer
   server's certificate id against the node id that peer claims
   in its own health response before any entries are pulled: a
   mismatch is refused as identity_mismatch and no state moves.
   The mismatch refusal must be DISTINCT from experiment 014's
   refusals (no credential, and a credential from a rogue CA,
   both of which fail the TLS handshake itself and receive no
   HTTP answer): the mismatched peer's credential is otherwise
   valid - issued by the same CA the server trusts, so the
   handshake succeeds on possession alone - and the same
   certificate is served when the claim matches the id it was
   issued for, proving identity binding, not possession, is what
   refused it. After every mismatch refusal the server still
   serves a correctly identified peer. A peer process claiming an
   id its certificate was not issued for, started without binding
   (experiment 014 mode), is likewise refused when it initiates
   sync toward a binding-enabled node: the accepting node sees
   the claim/cert mismatch on the incoming link and nothing
   merges in either node. Status: implemented at prototype scale
   in experiment 017 (localhost processes, runtime-generated test
   certificates carrying the node id as a URI SAN, binding opt-in
   per process via `--tls-bind-identity`; no identity proofing of
   who SHOULD hold a certificate, no certificate revocation or
   lifecycle, and no per-endpoint authorization beyond the
   identity check is claimed).
10. Anchor consultation in the networked server processes and in
   the Gate 5 verdict (added 2026-11-06, experiment 018): the
   server-process half of registration case 9 (a server started
   with `--anchor` consults experiment 012's registry anchor for
   its snapshot at startup and refuses a rewritten snapshot as
   anchor_mismatch and honest growth as anchor_stale, over
   plaintext and protected transports alike, while a server
   without an anchor behaves exactly as experiments 007/014 left
   it), and the mesh half of the Gate 5 verdict: run_gate can be
   given a mesh anchor path (experiment 012's mesh anchor for the
   canary node's converged state), consulted with
   check_node_against_anchor and classified by experiment 015's
   AnchoredSyncNode classification, entering the verdict as its
   own named check. Expected: a converged mesh holding exactly
   the anchored entry set keeps the verdict passing exactly as
   before; a mesh that grew honestly past its anchor (every
   anchored origin head still present intact, entries only
   appended, all nodes converged on the grown state) makes the
   verdict FAIL under the name anchor_stale for the mesh check,
   not as a mismatch and not as a silent pass. With no mesh
   anchor given, no mesh anchor check is in the verdict and its
   behavior is unchanged. Detailed case:
   `tests/test-cases/registration.md` case 9. Status:
   IMPLEMENTED at prototype scale in experiment 018 (the
   server-process mesh side still holds a plain SyncNode and
   consults no mesh anchor; the mesh anchor enters through the
   Gate 5 verdict, as this case states).
11. Signed mesh anchors with a kept history (added 2026-11-06,
   experiment 019; the mesh half of registration case 10): the
   mesh anchor of case 5 can be written SIGNED by an enrolled
   Gate 1 identity, reusing the same domain-separated HMAC
   anchor-signing scheme as the registry anchor (the canonical
   encoding covers the per-origin heads, the state hash, the
   entry count, the signer, the history sequence, and the
   previous history entry's hash). Expected: the AnchoredSyncNode
   of case 8, when configured with a signing identity, verifies
   the mesh anchor's signature before accepting a peer's entries
   as converged: a genuine peer whose merged state matches a
   correctly signed anchor is accepted exactly as in case 8; a
   merged state that matches an anchor whose signature is
   missing, made by a different secret, or tampered with is
   refused as a divergence under its own reason,
   anchor_signature_invalid (rolled back, nothing accepted),
   never as anchor_mismatch, never as anchor_stale, and never
   silently accepted; the Gate 5 verdict's mesh anchor check
   (case 10) fails under the same name for the same anchors.
   Re-anchoring follows registration case 10's rule, stated
   there before the build: a signed mesh anchor can be
   re-anchored only by the same identity that signed it, a
   different enrolled identity's attempt is refused and logged
   in the anchor audit log beside the anchor file, and each
   accepted re-anchor appends a signed, hash-chained history
   entry (per-origin heads, state hash, count, sequence,
   signer) inside the anchor file, whose tampering is caught by
   history verification. With no signing identity configured,
   mesh anchors - signed or unsigned - are judged by their
   heads exactly as in cases 5 and 8, and the earlier suites
   pass unmodified in the same run. Status: IMPLEMENTED at
   prototype scale (2026-11-06, experiment
   `experiments/019-signed-anchors/`); the case was written
   before the build. Tests:
   `packages/synchronization/test_signed_mesh_anchor.py` (signed
   mesh anchor verifies with its history, missing/tampered
   signature is anchor_signature_invalid, same-identity
   re-anchor appends history and a different identity is
   refused and logged, tampered history caught, an
   identity-configured AnchoredSyncNode accepts a genuine peer
   and refuses a tampered-signature anchor as an
   anchor_signature_invalid divergence, and without an identity
   the signed anchor is judged by its heads exactly as before)
   and `packages/validation/test_gate5_signed_anchor.py` (the
   verdict's mesh half, shared with registration case 10).
12. A revoked policy requested over the network transport
   (added 2026-11-06, experiment 021; finding F2 in experiment
   020): a node server process (experiment 007) serves a
   registered policy over HTTP; the policy's author revokes it
   (registration case 6), the updated record is delivered to
   the server's own snapshot copy, and the process is restarted
   over it - the delivery shape experiment 008 stated for
   servers, which build their PolicyNode at startup. Expected:
   a request for the revoked policy returns a clean HTTP
   refusal in the policy endpoint's existing refusal shape
   (a JSON body whose `error` names the refusal, with a
   `detail`), naming the revocation - error `policy_revoked`,
   status 410 - instead of the connection dropping with no
   response at all, which is what experiment 020 found (the
   endpoint caught NotServing, PolicyNotFound, and
   TamperedRecord, but not PolicyRevoked, a sibling refusal, so
   it propagated out of the handler). The revoked policy's
   bytes are never served, over experiment 007's plaintext
   transport and over experiment 014's protected (mutual TLS)
   transport alike. Negative control, in the style of
   availability case 1: the refusal must not wedge the server -
   after refusing the revoked policy, the same process still
   answers health and still serves a policy that was NOT
   revoked. Status: implemented at prototype scale in
   experiment 021 (localhost processes, delivery by restart
   over an updated snapshot as in experiment 008; a running
   server still gains no reload path in this case, and no push
   delivery is claimed).
13. A running server learns of a revocation without a restart
   (added 2026-11-06, experiment 024): since experiment 007, a
   node server process builds its PolicyNode once at startup
   and never reloads, so a revocation (registration case 6)
   reaches a network client only after the process is restarted
   over an updated snapshot - the delivery shape cases 10-12
   all used. Experiments 022/023 closed the trigger half of
   that gap for in-process placed policies (a watcher notices
   the shared snapshot file changed and calls experiment 021's
   reload itself); the experiment 007/014 server processes were
   explicitly left out of both. This case closes it for them. A
   running server - started over a registry snapshot, with
   `--anchor` and, where configured, `--anchor-signer` plus
   `--anchor-secret-file` as in cases 10/11 - exposes a
   snapshot check on its existing POST surface:
   `POST /check-snapshot`, driven by a new client helper
   (`check_snapshot`, in the style of `mutate` and
   `request_sync`). One check compares the server's own
   snapshot file's presented facts - the entry count, the
   chain head, and a SHA-256 fingerprint of the file's raw
   bytes, experiment 022's change fact, reused from
   `snapshot_delivery.snapshot_facts` - with the facts the
   running PolicyNode last loaded (recorded at startup, and
   moved only by a reload that stood, experiment 022's state
   discipline). Expected:
   - No change: the check reports status `no_change`, calls
     nothing, and serving is undisturbed - the same policy
     serves with the same bytes before and after the check,
     and the check's report carries `loaded_entries` null (no
     reload occurred), in the no-churn discipline of
     experiment 022.
   - A clean change: the updated record is delivered into the
     server's own snapshot copy (an explicit file copy, the
     delivery shape of every earlier experiment - no push,
     broadcast, or network transport of the record is added)
     and the anchor lifecycle is followed (the grown head
     re-anchored, by the same signer where the anchor is
     signed, experiments 012/019). ONE check then reloads the
     running PolicyNode in that same check, with experiment
     015's reload semantics (verify the chain and consult the
     anchor before the view changes; the reload is experiment
     021's mechanism, reused, not a new one). Afterward, a
     request for the revoked policy over the wire returns case
     12's clean refusal - status 410, error `policy_revoked` -
     on experiment 007's plaintext transport and on experiment
     014's protected (mutual TLS) transport alike; a policy
     that was NOT revoked keeps serving with status 200 and
     its exact registered bytes; and `/health` reports the
     server's current standing after the reload: its anchor
     status, the loaded snapshot entry count, and the last
     check's report.
   - A change that should not be trusted is refused by the
     reload under its own names, exactly as in-process
     (experiments 015/019/022): honest growth delivered
     WITHOUT the re-anchor is refused as `anchor_stale`; an
     entry edited in place (experiment 021's tamper - the
     fingerprint is what lets the check see an edit that head
     and count cannot) as `tampered_record`; and a snapshot
     file replaced with a full rewrite from genesis
     (experiment 012's negative control, internally
     consistent) as `anchor_mismatch`. On every refusal the
     running server keeps serving its previous view - the
     policy whose revocation it has not yet accepted still
     serves, which is the stale window, stated and not hidden
     - the check's report names the refusal, `/health` carries
     the last check's report with the refusal's name, and
     every later check re-reports the same refusal until the
     cause is fixed; after the cause is fixed (the re-anchor,
     an untampered delivery), the next check delivers, exactly
     as experiment 022's watcher behaves.
   - Restart behavior is unchanged: a server STARTED over a
     rewritten snapshot with an anchor configured still
     refuses to start as `anchor_mismatch`, exactly as in
     cases 10/11 (the unmodified experiment 018/019 suites
     assert it, and this case's suite asserts it again) - the
     live check is an addition to a running server, not a
     weakening of the startup gate.
   - A server with no snapshot configured reports the check
     as `no_snapshot` and is otherwise undisturbed (its mesh
     endpoints behave exactly as in experiment 007).
   The check is driven by the caller through the endpoint: no
   timer, scheduler, background thread, or file watcher inside
   the server fires it unattended, and nothing here claims
   more than experiment 022's meaning of automatic, at the
   process surface. Status: implemented at prototype scale
   in experiment 024 (`experiments/024-server-live-reload/`);
   the case was written before the build. Tests:
   `packages/synchronization/test_server_live_reload.py`
   (no-change check reloads nothing and serving is
   undisturbed; clean delivery reloads in one check and the
   revoked policy is refused 410 over plaintext while a
   non-revoked policy keeps serving and health reports the
   new standing; anchor_stale refused and re-reported until
   the re-anchor, then delivered; tampered delivery refused
   as tampered_record with the previous view serving, then
   delivered once the genuine record is delivered and
   anchored; rewritten delivery refused as anchor_mismatch
   with the previous view serving, and restoring the genuine
   file reports no_change; the full flow over mutual TLS
   with a signed anchor - stale refusal, same-signer
   re-anchor, reload in one check, clean 410 over the
   protected link; restart over a rewritten snapshot still
   refused at startup; a mesh-only server reports
   no_snapshot).
14. A running server receives the updated record over the
   wire (added 2026-11-06, experiment 025): case 13 closed
   the reload half of server delivery - a running server
   reloads when a check is driven - and stated its own
   remaining limit in the same breath: getting the updated
   record INTO the server's snapshot file is still an
   explicit file copy made outside the process; no push,
   broadcast, or network transport of the record to a
   running server exists, and case 12's restart shape is the
   only other way a record moves. The mesh side has moved
   its state between processes since case 4; the registry
   record itself has never travelled. This case closes the
   transport half. A running server - started over a
   registry snapshot, with `--anchor` and, where configured,
   `--anchor-signer` plus `--anchor-secret-file` as in cases
   10/11/13 - accepts a registry snapshot delivery on its
   existing POST surface: `POST /deliver-snapshot`, driven
   by a new client helper (`deliver_snapshot`, in the style
   of `mutate`, `request_sync`, and `check_snapshot`). The
   delivery carries the snapshot ENTRIES themselves (the
   Gate 1 log entries as JSON), never a file path: the
   receiver is handed data, and data is what it verifies.
   Expected:
   - A delivery of exactly the entries the server already
     stands on reports `no_change` and changes nothing: the
     snapshot file is not rewritten and no reload occurs
     (the report's `loaded_entries` is null), in case 13's
     no-churn discipline.
   - A wire delivery is judged exactly as strictly as a file
     delivery (cases 8/10/13): the delivered entries are
     verified as a chain and consulted against the server's
     configured anchor - and, where a signer is configured,
     that anchor's signature - BEFORE anything changes, by
     the same consultation the reload runs (experiment
     015/019, reused through a candidate verification, not a
     new judgement). Only a delivery that stands is applied,
     through case 13's reload path (experiment 021's
     mechanism): the server's own snapshot file is rewritten
     from the verified delivery (serialized in Gate 1's own
     log format) and the running PolicyNode reloads from it,
     so the file and the serving view move together and a
     later restart or case 13 check sees the same record -
     a check driven right after a stood delivery reports
     `no_change`. This file-rewrite choice is stated, not
     hidden: the server's snapshot file remains its one
     durable record (case 13's check, the startup gate, and
     GET /snapshot all read it), so a wire delivery that did
     not land in the file would split the process's record
     from its view.
   - The clean flow: the author revokes (registration case
     6), the deliverer follows the anchor lifecycle (the
     grown head re-anchored, by the same signer where the
     anchor is signed, experiments 012/019), and the grown
     entries are pushed to the RUNNING server. The delivery
     reports `delivered` with the before/after entry counts
     and chain heads and the loaded entry count; afterward a
     request for the revoked policy over the wire returns
     case 12's clean refusal - status 410, error
     `policy_revoked` - a policy that was NOT revoked keeps
     serving with status 200 and its exact registered bytes,
     and `/health` reports the new standing: the loaded
     snapshot entry count and the last delivery's report.
     No restart, and no manual file copy anywhere in the
     flow.
   - Refusals, each named, each leaving the serving view
     AND the server's snapshot file byte-for-byte unchanged,
     each reported in the delivery's response and carried on
     `/health` as the last delivery's report, and none of
     them wedging the surface (a later clean delivery still
     lands): honest growth pushed WITHOUT the re-anchor is
     refused as `anchor_stale`; an entry edited in transit
     (its body changed, its entry_hash left standing,
     experiment 021's tamper) as `tampered_record`; a chain
     rewritten from genesis (experiment 012's negative
     control, internally consistent) as `anchor_mismatch`;
     where a signer is configured, a delivery whose grown
     head was anchored by an identity the server is NOT
     configured to trust (the anchor it consults signed by a
     different enrolled identity) as
     `anchor_signature_invalid`; and a delivery that goes
     BACKWARD - the delivered entries are exactly an earlier
     prefix of the very chain the server already stands on
     (fewer entries, every delivered hash the loaded hash at
     its index) - as `snapshot_regression`, a name this case
     sets: the existing vocabulary names anchor judgements
     and record tampering, and none of them says "this is
     our own chain, only older"; a shorter REWRITE is not a
     prefix of the standing chain and is not this refusal -
     it falls through to verification and is refused as
     `anchor_mismatch`, so the backward name cannot be used
     to launder a rewrite.
   - The full flow runs over experiment 014's protected
     transport with experiment 017's identity binding on:
     the pushing peer presents a certificate issued for its
     own node id and claims that id, the receiving server
     checks claim against certificate as at every endpoint,
     and the signed-anchor refusals and the clean delivery
     behave exactly as over plaintext.
   - Existing behavior is unchanged: case 13's
     `POST /check-snapshot`, the startup gate of cases 10/11
     (a server STARTED over a rewritten snapshot with an
     anchor still refuses to start), and the mesh sync
     surface all behave exactly as before, proven by the
     unmodified earlier suites in the same run.
   The delivery is driven by the pushing peer: no timer,
   scheduler, or gossip inside the server solicits one, and
   the anchor itself still arrives out of band (its file is
   the local stand-in of experiments 012-019; this case
   transports the record, not the anchor). Status:
   implemented at prototype scale in experiment 025
   (`experiments/025-snapshot-wire-transport/`); the case
   was written before the build. Tests:
   `packages/synchronization/test_snapshot_wire_transport.py`
   (no-change delivery rewrites nothing and reloads nothing;
   clean wire delivery of the revocation record over
   plaintext - stale refused first, same-signer lifecycle,
   delivered, 410 over the wire, file and view together,
   check-after-delivery no_change; tampered entry in transit
   refused as tampered_record with file and view unchanged,
   then a clean delivery lands; rewritten chain refused as
   anchor_mismatch, not snapshot_regression; backward
   delivery refused as snapshot_regression with the standing
   record and file untouched, then a further clean growth
   still lands; a mesh-only server reports no_snapshot; the
   startup gate re-asserted; the full flow over mutual TLS
   with identity binding and a signed anchor - stale,
   differently-signed anchor refused as
   anchor_signature_invalid, same-signer anchor, delivered,
   clean 410 over the protected link).
15. Mesh-anchor consultation over the wire for server
   processes (added 2026-11-06, experiment 026): case 8 built
   anchor consultation for Gate 3 sync in-process - an
   AnchoredSyncNode consults experiment 012's mesh anchor on
   the merged state before accepting a peer's entries - and
   case 11 added signed mesh anchors to that consultation.
   But the consultation has only ever run between in-process
   objects: the experiment 007/014 server processes hold a
   plain SyncNode for their mesh side and consult no mesh
   anchor, so a rewritten peer's mesh state - flagged
   divergent by an in-process anchored node - is accepted by
   a running server without that check (experiment 020's
   full-chain run proved its stage 6 mesh-anchor consultation
   in-process only, and experiments 024/025 restated the
   server-side gap in their limits). This case closes it: the
   server process gains the mesh half of the consultation its
   registry half has had since case 10. A node server started
   with `--mesh-anchor` - and, where the mesh anchor is
   signed, `--mesh-anchor-signer` plus
   `--mesh-anchor-secret-file`, flags in the style of
   `--anchor` / `--anchor-signer` / `--anchor-secret-file`
   but independent of `--snapshot` (the mesh side exists on
   mesh-only servers too; the signer pair requires
   `--mesh-anchor`, and signer and secret file require each
   other) - holds an AnchoredSyncNode (case 8's node, reused
   unmodified) for its mesh side instead of a plain SyncNode,
   so every mesh receive the process already performs -
   POST /entries and both sides of POST /sync, which already
   call receive_entries - goes through case 8's consultation
   with no new endpoint and no new receive logic. Expected:
   - Honest convergence is unchanged: two server processes
     mutate and sync exactly as in case 4/7; a mesh anchor is
     written over the converged state (case 5, from one
     node's view, held outside both processes' state); one
     of the pair is then restarted with `--mesh-anchor` -
     mesh state is in-process memory (experiment 007's
     no-persistence limit), so the restarted process holds
     nothing, and its /health classifies its own standing
     against the anchor with case 8's own classification
     (`mesh_anchor_status`, beside `mesh_anchor_configured`,
     in the style of the registry half's `anchor_status` /
     `anchor_configured`): not yet the anchored state and
     not an extension of it, `anchor_mismatch`, until it
     syncs. It initiates /sync toward the honest peer that
     still holds the converged state; the merged state is
     exactly the anchored state, the consultation accepts,
     and the two processes converge on the anchored state
     hash with no divergence; afterward /health reports
     `mesh_anchor_status` "anchored".
   - A rewritten peer is flagged divergent over the wire: a
     peer process claiming the same node id as an honest
     origin, whose chain was built from genesis with
     different mutations (every hash recomputing, acceptable
     to a node with no anchor - case 5/8's negative control),
     syncs with an anchored server that has never seen the
     honest originals. The anchored server's side of the sync
     reports divergence with reason `anchor_mismatch` (case
     8's vocabulary, unchanged), accepts nothing, and its own
     mesh state is exactly what it was before the sync;
     /health's `mesh_anchor_status` names the same standing,
     `anchor_mismatch`. Negative control, stated plainly as
     in case 8: a server started WITHOUT `--mesh-anchor`
     syncing with the same rewritten peer accepts the rewrite
     and converges to it - that acceptance is the hole this
     case closes only where a mesh anchor is configured.
   - Honest growth is `anchor_stale` over the wire: with the
     anchored server converged on the anchored state, the
     honest peer mutates once more and the anchored server
     syncs again; the merged state contains everything
     anchored, intact, plus the honest append, so the sync
     is refused under reason `anchor_stale` - never as a
     mismatch, and never silently adopted past the anchor -
     and the anchored server still holds exactly the
     anchored state (the stale name is the sync result's
     report; the server's own standing on /health remains
     "anchored", because the anchored state is what it
     kept); after the grown state is re-anchored (by the
     same signer where the anchor is signed, case 11), the
     same sync is accepted and the processes converge on the
     grown state.
   - Tampering and bad signatures are refused under the
     names that already judge them, never adopted: an entry
     edited in transit (its body changed, its entry_hash
     left standing) is refused by Gate 3's own verification
     on receipt - reason `tampered_entry`, case 8's stated
     order (Gate 3 first, anchor or not) - with the anchored
     server's state unchanged; and where a signing identity
     is configured, a mesh anchor whose signature does not
     verify (the same heads anchored by a different enrolled
     identity, case 11) makes the consultation refuse under
     reason `anchor_signature_invalid` - in the sync result
     and as /health's `mesh_anchor_status` - until a
     genuinely signed anchor is restored, after which the
     honest-growth lifecycle above proceeds under its own
     names (`anchor_stale`, then acceptance after the
     same-signer re-anchor).
   - A configured mesh anchor that cannot be read refuses in
     shape, never by dropping the connection (experiment
     025's discipline, and the lesson of experiment 020's
     finding F2): /health still answers, reporting
     `mesh_anchor_status` "anchor_unreadable" (case 8's
     divergence reason for the same condition), and a mesh
     receive returns the existing SyncResult shape with
     divergence_detected true, rolled_back true, reason
     `anchor_unreadable`, the node's state unchanged;
     restoring the anchor file restores normal consultation.
     This is the one wire adaptation this case makes to case
     8's in-process behavior, which raises after rolling
     back; it is stated here, not hidden.
   - Servers started without a mesh anchor behave exactly as
     today: /health reports `mesh_anchor_configured` false
     and `mesh_anchor_status` "no_anchor", sync is case
     4/7's plain sync, and the registry-side behavior of
     cases 10-14 (the startup gate, the live reload, the
     wire delivery) is untouched - proven by the unmodified
     earlier suites in the same run. The consultation is
     proven over experiment 007's plaintext transport and
     over experiment 014's protected (mutual TLS) transport,
     the protected leg with a signed mesh anchor and its
     signing identity configured.
   Status: implemented at prototype scale in experiment
   026 (`experiments/026-mesh-anchor-wire/`); the case was
   written before the build. Tests:
   `packages/synchronization/test_mesh_anchor_wire.py`
   (honest convergence onto exactly the anchored state
   after a `--mesh-anchor` restart, with the at-rest
   `anchor_mismatch` classification stated; a rewritten
   peer process flagged `anchor_mismatch` over /sync with
   the anchored server's state unchanged, and accepted by
   a mesh-anchorless server as the stated control; honest
   growth `anchor_stale` until re-anchored; an entry
   edited in transit refused as `tampered_entry`; a
   differently-signed mesh anchor refused as
   `anchor_signature_invalid` until the genuine signed
   anchor is restored, then `anchor_stale` and the
   same-signer re-anchor; an unreadable mesh anchor
   refused in shape as `anchor_unreadable` with /health
   answering; the rewritten-then-honest flow over mutual
   TLS with a signed mesh anchor).
16. One server pushes its standing record to another server
   (added 2026-11-06, experiment 027): case 14 gave a running
   server a wire delivery surface - `POST /deliver-snapshot`,
   judged exactly as strictly as a file delivery - and
   stated its remaining limit in the same breath: the
   pushing peer in every case 14 test is a credential-
   holding node CLIENT driving the client helpers; no server
   process ever pushes its standing record to another
   server, so two running servers cannot bring each other
   current on the registry side even though their mesh sides
   have synced since case 4. This case closes that gap with
   the smallest addition that composes with what exists:
   the RECEIVING side is case 14's surface, unmodified, and
   the sending side is one new driven endpoint on the
   pushing server, in the exact style of case 4's `/sync`.
   A node server exposes `POST /push-snapshot` with payload
   `{"peer": "<base URL>"}`, driven by a new client helper
   (`push_snapshot`, in the style of `request_sync`, returning
   the (status, body) pair as `fetch_policy` does, because a
   push's refusal can ride a non-200 status and the caller
   must see its body). Driven on a running server, the push:
   - sends the entries of the record the pushing server's
     PolicyNode STANDS ON (its loaded, verified entries -
     what it serves from, which cases 13/14 keep in step
     with its snapshot file) to the peer's case 14
     `POST /deliver-snapshot`, over the same transport the
     pushing process serves on (its client helpers present
     its own credential and claim its own node id, as its
     `/sync` initiation already does);
   - returns, on HTTP 200, a report carrying the pushing
     server's node id, the peer URL, `status` equal to the
     peer's delivery status (case 14's vocabulary:
     `delivered`, `delivery_refused`, `no_change`,
     `no_snapshot`), and the peer's delivery report itself
     under `delivery` - the peer judges the push, and its
     report, refusals included, is the push's result, not a
     verdict the sender re-derives;
   - records the push's outcome on the pushing server's
     `/health` as `last_snapshot_push`, beside case 13's
     `last_snapshot_check` and case 14's
     `last_snapshot_delivery` (null until the first push is
     driven);
   - answers a pushing server with no PolicyNode (a
     mesh-only server) on HTTP 200 with status `no_snapshot`
     and `delivery` null - there is no record to push - and
     is otherwise undisturbed;
   - refuses in `/sync`'s shape when the push cannot reach
     judgement: a peer that cannot be reached, or a peer
     whose transport refuses the pushing server before case
     14's endpoint runs, is reported on HTTP 502 with an
     `error` naming the refusal - over experiment 017's
     binding transport, `identity_mismatch` with the
     `claimed` and `certified` ids, so a transport refusal
     is named in the push result, never hidden behind a
     generic failure. With identity binding on, the pushing
     server also runs case 11's initiating-side check on
     the peer (its certificate id against the id it claims)
     before anything is pushed, exactly as its `/sync`
     initiation does.
   Expected:
   - The end-to-end story, over two running server
     processes on experiment 007's plaintext transport: both
     start over copies of the same 2-entry record with an
     anchor over it (each server consults its own anchor
     file; the anchor lifecycle below is performed for both,
     as out-of-band anchor delivery would reach both). The
     author revokes (registration case 6) and the lifecycle
     is followed (the grown head re-anchored); the grown
     record reaches server A through A's own accepted case
     14 delivery (reports `delivered`). A push from A to B
     is then driven: the push reports `delivered` with the
     peer's report (counts 2 -> 3, head moved, loaded 3);
     B - never restarted, its snapshot file never copied by
     hand anywhere in the story - now answers a request for
     the revoked policy with case 12's clean refusal (410,
     `policy_revoked`), a policy that was NOT revoked keeps
     serving with 200 and its exact bytes, B's snapshot file
     is byte-for-byte the delivered record, B's `/health`
     reports the loaded count 3 and the `delivered` last
     delivery, and A's `/health` carries the push as its
     last push. The same two processes' mesh surface is
     undisturbed: a mutation on A still converges to B
     through case 4's `/sync`.
   - A push to a peer that already stands on the identical
     record is case 14's `no_change`, not an error and not
     a rewrite: the push reports `no_change` with the peer
     report's `loaded_entries` null, and the peer's snapshot
     file is byte-for-byte unwritten.
   - A push that would move the peer BACKWARD is refused
     under case 14's backward name, reported in the push
     result: server B is brought ahead of A (a further
     growth delivered to B alone through B's own case 14
     delivery, lifecycle followed), and A's push of its
     older record - exactly an earlier prefix of the chain
     B stands on - reports `delivery_refused` with reason
     `snapshot_regression`; B's file, view, and standing are
     unchanged (its loaded count stays ahead, the later
     record still stands), and A is unchanged.
   - A push of a record whose anchor lifecycle the peer
     cannot accept is refused under the lifecycle's own
     name, reported in the push result: the grown record
     reaches A (A's anchor advanced, A's case 14 delivery
     accepted), but the peer's anchor still stands over the
     old head, so A's push reports `delivery_refused` with
     reason `anchor_stale`; the peer's file and view are
     unchanged (the stale window on the peer, stated: it
     still serves the policy whose revocation it has not
     accepted). Once the lifecycle is completed for the peer
     (its anchor advanced over the grown head), the same
     push delivers and the peer flips to case 12's refusal.
   - A peer with no anchor cannot launder a rewrite through
     a push either - and an unanchored server cannot spread
     one to an anchored peer: a server started WITHOUT
     `--anchor` accepts experiment 012's rewritten record
     through its own case 14 delivery (no consultation: the
     acceptance case 5/8 state plainly for anchorless
     nodes), then pushes it to a peer whose anchor pins the
     genuine chain; the push reports `delivery_refused`
     with reason `anchor_mismatch`, and the anchored peer's
     file, view, and standing are unchanged.
   - The full flow over experiment 014's protected transport
     with experiment 017's identity binding on and a signed
     anchor (as in case 14's protected leg): server A (its
     certificate issued for its own node id, binding on)
     accepts the revocation delivery with the same-signer
     lifecycle followed, then pushes to server B over HTTPS;
     the push reports `delivered`, B's revoked policy
     refuses 410 over the protected link, B's file is the
     delivered record, and B's `/health` reports the new
     standing.
   - The identity refusal across servers, at the transport,
     exactly as in case 11: a pushing server started in
     experiment 014 mode (mutual TLS, binding OFF) claiming
     a node id its certificate was NOT issued for - the
     false-claiming process case 11's notes describe - is
     driven to push to a peer with binding on. The peer's
     accepting-side check refuses the pushing server's
     connection before case 14's endpoint runs: the push is
     reported on HTTP 502 with error `identity_mismatch`,
     naming the claimed id (what the pushing process claims)
     and the certified id (what its certificate was issued
     for), and the peer's file, view, and standing are
     unchanged - it never even records a delivery.
   - Servers on which the new surface is never driven behave
     exactly as today: case 14's delivery, case 13's check,
     the startup gate, and the mesh surface are untouched,
     proven by the unmodified earlier suites in the same
     run.
   The push is driven by a client call, as `/sync` is: no
   timer, scheduler, gossip, or discovery inside either
   server fires one unattended, and this case transports the
   record, not the anchor - each peer's anchor still arrives
   out of band, and the lifecycle steps above are performed
   per server, as anchor delivery would reach each. Status:
   implemented at prototype scale in experiment 027
   (`experiments/027-server-to-server-delivery/`); the case
   was written before the build. Tests:
   `packages/synchronization/test_server_to_server_delivery.py`
   (the end-to-end story over two running plaintext servers -
   case 14 delivery to A, one driven push to B, 410 over the
   wire, B's file byte-for-byte the delivered record, the
   mesh sync undisturbed, a second push `no_change`; a
   backward push to an ahead peer refused as
   `snapshot_regression` in the push result; a push refused
   as `anchor_stale` while the peer's anchor stands over the
   old head, delivered once the lifecycle completes for the
   peer; an anchorless sender's rewrite refused by the
   anchored peer as `anchor_mismatch`; a mesh-only sender
   reporting `no_snapshot`; the full flow over mutual TLS
   with identity binding and a signed anchor; a
   false-claiming pusher refused at the binding peer's
   transport as `identity_mismatch`, 502 naming claimed and
   certified, the peer recording no delivery).
