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
17. A running server receives its registry anchor over the
   wire, judged before acceptance and judged together with
   the record it blesses (added 2026-11-06, experiment 028):
   cases 13/14/16 move the registry RECORD to a running
   server (a file copy reconciled by a driven check, a
   client push, a server push), and every one of them states
   the same remaining limit: the anchor the delivery is
   consulted against arrives OUT OF BAND - the anchor file
   is the local stand-in of experiments 012-019, placed by
   hand, and completing the anchor lifecycle (the
   same-signer re-anchor of registration case 10) at a
   server is a manual act no wire performs. A record
   delivery whose lifecycle was not performed by hand at
   that server is refused `anchor_stale` (case 14), and
   case 16's peer whose anchor was not advanced refuses a
   push the same way. This case closes the transport half
   of the anchor lifecycle: the anchor itself can now be
   delivered over the wire to a running server, judged
   exactly as strictly as the record, and - when it arrives
   together with the record it blesses - judged together
   with it, so neither half of the lifecycle is applied
   unless both stand. The receiving surface is one new
   driven endpoint on the server, in case 14's style:
   `POST /deliver-anchor`, driven by a new client helper
   (`deliver_anchor`, in the style of `deliver_snapshot`,
   taking the anchor as a parsed anchor object and,
   optionally, the snapshot entries it blesses). The
   payload is `{"anchor": {...}}` for an anchor alone, or
   `{"anchor": {...}, "entries": [...]}` for the paired
   delivery: the anchor object's own JSON - the content of
   an experiment 012/019 anchor file, as data, never a file
   path, for case 14's stated reason (a receiver handed a
   path has been handed nothing it can verify). The
   response is the delivery's report, refusals included, on
   HTTP 200 exactly as case 14's report is a delivery
   outcome rather than a transport error; a body whose
   anchor is not an object, or whose entries are present
   but not a list of entry objects, is a 400 in the
   /deliver-snapshot handler's shape. The report carries
   the server's node id, the delivery's status, the
   refusal's own name as `reason` where refused, whether
   the delivery was paired, the anchor's before/after heads,
   entry counts, and history sequences, the delivered
   anchor's signer, the loaded entry count when a paired
   record was applied, and the server's anchor status after
   the delivery. `/health` gains `last_anchor_delivery`
   (the last anchor delivery's report, or null before the
   first), beside `last_snapshot_delivery` - this case is
   about the REGISTRY anchor only; the mesh anchor of case
   15 still arrives out of band. The delivery judges the
   anchor BEFORE anything changes, in this order, each
   judgement named:
   - Configuration first: a process started without
     `--anchor` consults no registry anchor, and an anchor
     delivery to it reports `no_anchor` - configuration is
     a startup choice, and a delivery never turns
     consultation on. A process with an anchor configured
     but no PolicyNode (no snapshot) reports `no_snapshot`:
     there is no record to pair an anchor against.
   - The delivered anchor must read as an anchor: it is
     serialized to a candidate file beside the server's
     anchor, in the anchor file's own format, and read with
     experiment 012's reader; a body that is not a registry
     anchor is refused as `delivery_refused` under the
     anchor module's own error name, as experiment 022's
     naming reports any refusal that carries no status of
     its own.
   - An anchor delivery byte-for-byte identical to the
     anchor file the server already holds is `no_change`:
     nothing is written and nothing is re-judged, in case
     14's no-change discipline.
   - Succession, judged anchor to anchor (registration case
     10's same-identity rule, over the wire): if the
     server's current anchor is SIGNED, a delivered anchor
     must be signed by the SAME identity; a delivered
     anchor signed by a different identity - or not signed
     at all - is refused as `different_signer`, the refusal
     is appended to the anchor audit log beside the server's
     anchor file in case 10's event shape
     (`reanchor_rejected` / `different_signer`, naming the
     attempted and current signers and the attempted head
     and count), and the current anchor file is
     byte-for-byte unchanged. A delivered signed anchor over
     an UNSIGNED current anchor establishes the signer,
     case 10's stated exception.
   - The backward fence, fenced as case 14 fences
     `snapshot_regression`: a delivered anchor that is an
     earlier state is refused as `anchor_regression`, and
     the refusal is audit-logged in the same shape -
     earlier state meaning exactly one of: the delivered
     anchor's kept history is a proper prefix of the
     current anchor's kept history (an earlier version of
     this very anchor file - the rollback experiment 019
     names), or the delivered anchor pins an earlier prefix
     of the chain the server stands on (its entry count is
     smaller than the standing record's, and its head is
     that chain's entry hash at that count - the genesis
     head for count 0). A head that is an earlier state of
     NEITHER the server's anchor nor its chain never reaches
     this name: a foreign chain's anchor falls through to
     the pairing judgement, so the backward name cannot
     launder a rewrite into a lesser finding.
   - Signature and history, under the server's configured
     identity where one is configured (case 10): with
     `--anchor-signer` set, the delivered anchor's
     signature and kept history must verify for that
     identity (experiment 019's verification, reused) - a
     wrong signer succession did not already catch, a
     tampered signature, or a kept history that does not
     verify is refused as `anchor_signature_invalid`,
     before any head judgement, as in every consultation
     since experiment 019. With no identity configured, a
     signed delivered anchor's kept history must still
     verify as a chain (sequences, linkage, entry hashes,
     and signer consistency - the part of experiment 019's
     history verification that needs no secret); an unsigned
     delivered anchor is judged by its head alone, as in
     experiments 012-018.
   - Pairing - the rule that a delivered anchor is accepted
     only for a chain the server can check it against:
     * Anchor alone: the delivered anchor's head and count
       must be exactly the head and count of the record the
       server stands on, judged against the server's
       snapshot file (the presentation every consultation
       since experiment 015 has judged; cases 13/14 keep
       the file and the view in step). This is the catch-up
       pairing: the anchor arrives for a record already
       held - a re-anchoring of the standing head, or a
       replacement for an anchor file rolled back by hand.
       An anchor whose head matches no chain the server
       holds is refused as `anchor_unpaired`, the name this
       case sets: it pairs with nothing here, and nothing
       changes.
     * Anchor and record together: the pair is judged
       together, through case 14's discipline with the
       delivered anchor standing in for the server's own.
       Case 14's hash-only pre-judgements run on the
       delivered entries first: identical to the standing
       chain, the pair reduces to the anchor-alone judgement
       above; exactly an earlier prefix of it, the delivery
       is refused as `snapshot_regression` and NEITHER half
       is applied. Anything else is judged by a full
       PolicyNode construction over a candidate record file
       with the candidate anchor file as its anchor and
       this process's signing configuration - experiment
       015/019's consultation - which refuses a record that
       outruns its delivered anchor as `anchor_stale`, a
       record of another chain as `anchor_mismatch`, and a
       tampered record as `tampered_record`, each before
       anything changes. Only a pair that stands TOGETHER
       is applied: the server's snapshot file is replaced
       from the verified delivered record (case 14's
       application), the server's anchor file is replaced
       from the verified delivered anchor, and the running
       PolicyNode reloads once from the replaced pair, so
       the server ends serving the delivered record under
       the delivered anchor - a revocation the record
       carries takes effect over the wire with no restart
       and no manual lifecycle step at this server. A
       refused pair applies NEITHER half: both files
       byte-for-byte unchanged, the previous view serving,
       the refusal carried in the report and in `/health`'s
       `last_anchor_delivery`.
   - An accepted anchor-alone delivery is applied in
     miniature the same way: the anchor file is replaced
     from the verified delivered anchor, and the running
     PolicyNode reloads its standing record against it
     (experiment 015/021's reload, re-consulting the anchor
     the node now holds), so the `anchor_status` `/health`
     reports after the delivery is the new anchor's verdict
     on the standing record, not a cached one.
   Expected:
   - The anchor-alone pairings over plaintext, with a signed
     anchor and its signing identity configured (case 10's
     protected configuration): a byte-identical anchor
     delivery reports `no_change` and writes nothing; the
     deliverer then re-anchors the standing head at the
     source - the kept history grows by one entry over the
     SAME head - and that anchor, delivered alone, reports
     `delivered`: the server's anchor file is byte-for-byte
     the delivered anchor, `/health` reports `anchored`
     under it and carries the delivery as its last one, and
     the standing record serves exactly as before.
   - An anchor alone over a head the server does not hold
     - the SAME identity's genuine signed anchor over
     experiment 012's rewritten chain - is refused as
     `anchor_unpaired`: identity and signature both pass,
     the head pairs with nothing here, the server's anchor
     file is byte-for-byte unchanged, and serving is
     undisturbed.
   - The pair judged together: the author revokes
     (registration case 6) and the record grows; the pair of
     the grown record with the server's OLD anchor is
     refused as `anchor_stale` and NEITHER file changes -
     the lifecycle half cannot be skipped by pairing; the
     NEW anchor (the same signer, over the grown head)
     delivered alone next is refused as `anchor_unpaired` -
     it pairs with the record the server does not hold yet,
     not the one it stands on; the pair of the grown record
     with the new anchor then reports `delivered`, both of
     the server's files are byte-for-byte the delivered
     pair, the revoked policy returns case 12's clean
     refusal (410, `policy_revoked`), the non-revoked policy
     keeps serving with its exact bytes, `/health` reports
     the new standing under the delivered anchor, and a
     case 13 check right after reports `no_change`.
   - Succession over the wire, on a server whose anchor
     consultation is head-only (no signing identity
     configured; the anchor it holds is signed by alice, as
     experiments 012-018's head-only consultation accepts):
     an anchor signed by a DIFFERENT enrolled identity -
     bob's genuine signed anchor over the very same chain -
     delivered alone is refused as `different_signer`; the
     anchor file is byte-for-byte unchanged; the audit log
     beside it gains the `reanchor_rejected` /
     `different_signer` event naming bob as attempted and
     alice as current; serving is undisturbed.
   - A tampered kept history, on a signer-configured
     server: the genuine next anchor with one history
     entry's recorded head edited in the delivered object
     (the entry hashes left standing, experiment 021's
     tamper shape) is refused as
     `anchor_signature_invalid`, before any head judgement;
     neither of the server's files changes and serving is
     undisturbed.
   - The backward fence, both of its shapes: (i) an earlier
     version of the server's own anchor file - the same
     signed anchor one re-anchoring earlier, its kept
     history a proper prefix of the current one's -
     delivered alone is refused as `anchor_regression`,
     audit-logged in case 10's shape, the current anchor
     byte-for-byte unchanged; (ii) an anchor pinning an
     earlier prefix of the standing chain - the same
     identity's genuine signed anchor over the truncated
     record - is refused as `anchor_regression` the same
     way, although its signature and kept history verify:
     it is refused for where it points, not for how it is
     signed.
   - THE REWRITE FINDING, stated as the evidence shows it -
     this case does not pretend a refusal the code does not
     produce. The deliverer builds experiment 012's
     rewritten registry (mallory's chain, the same length
     as the genuine one, internally consistent - it passes
     every chain verification of its own) and anchors over
     that rewrite. (i) The pair of the rewritten record
     with the server's GENUINE anchor is refused as
     `anchor_mismatch`: experiment 012's original
     protection, intact - a rewrite cannot pass under an
     anchor that was not re-signed over it. (ii) The pair
     with an anchor over the rewrite signed by a DIFFERENT
     identity (bob) is refused as `different_signer` at the
     succession judgement, audit-logged: identity custody
     is a fence. (iii) The pair with an anchor over the
     rewrite signed by the CURRENT signer's identity
     (alice) - its kept history honestly extended from the
     server's current anchor by case 10's own write path,
     by a deliverer who legitimately holds alice's
     enrollment secret in this prototype's symmetric model,
     as every verifier of a signed anchor does - is
     ACCEPTED and reported `delivered`: succession passes
     (same identity), the backward fence does not fire (the
     rewrite's head is an earlier state of neither the
     genuine chain nor the anchor, and the delivered
     history extends the current one rather than prefixing
     it), the signature verifies, and the pair stands
     together under experiment 015/019's own consultation -
     the server ends serving the rewritten record under the
     delivered anchor (mallory's policy serves with its
     exact bytes from a server that holds its payload; the
     genuine policies are no longer in its view). What
     stops a rewrite from being blessed is therefore
     exactly the custody of the signer's secret, and
     nothing else: the wire adds no weakness - an anchor
     placed by hand is accepted by the same consultation,
     as experiments 018/019 already showed at startup - and
     the weakness the acceptance exposes is experiment
     019's recorded symmetric-model limit (verification
     requires the secret, so a verifier can forge), now
     reachable through one more arrival path. Production's
     answer is unchanged and unbuilt: asymmetric signing,
     and an anchor held where the record's writer cannot
     reach it.
   - Configuration edges: a server started without
     `--anchor` reports an anchor delivery as `no_anchor`
     and is otherwise undisturbed; a node constructed with
     an anchor path but no snapshot reports `no_snapshot`.
   - The full flow over experiment 014's protected transport
     with experiment 017's identity binding and a signed
     anchor (as in case 14's protected leg): the paired
     delivery - grown record plus its same-signer anchor -
     over HTTPS reports `delivered`, the revoked policy
     refuses 410 over the protected link, the non-revoked
     policy serves, and `/health` reports the new standing;
     an anchor-alone delivery of the anchor the server then
     holds reports `no_change` over the same link.
   - Servers on which the new surface is never driven behave
     exactly as today: the startup gate, cases 13/14/16's
     surfaces, and the mesh surface are untouched, proven by
     the unmodified earlier suites in the same run.
   Status: implemented at prototype scale in experiment
   028 (`experiments/028-anchor-wire-delivery/`); the case
   was written before the build. Tests:
   `packages/synchronization/test_anchor_wire_delivery.py`
   (the anchor-alone pairings over plaintext with a signed
   anchor and its identity configured - identical
   `no_change`, the refreshed anchor over the standing head
   delivered; a genuine signed anchor over a foreign chain
   refused as `anchor_unpaired`; the pair judged together -
   grown record + old anchor `anchor_stale` with neither
   half applied, the new anchor alone `anchor_unpaired`,
   the pair `delivered` with both files byte-for-byte the
   delivered pair, 410 over the wire, a case 13 check
   `no_change` after; succession on a head-only server -
   bob's anchor over the same chain refused as
   `different_signer` and audit-logged in case 10's shape;
   a tampered kept history refused as
   `anchor_signature_invalid`; the backward fence in both
   shapes refused as `anchor_regression`, the
   earlier-version leg audit-logged; the rewrite finding in
   its three legs - genuine anchor `anchor_mismatch`, a
   different identity's `different_signer`, the current
   signer's honestly extended anchor over the rewrite
   ACCEPTED and served; the configuration edges `no_anchor`
   and `no_snapshot`; the paired flow over mutual TLS with
   identity binding and a signed anchor, and the
   anchor-alone `no_change` over the same link).
18. Witnessed anchors on the mesh chain and at the server
   processes (added 2026-11-06, experiment 029; the mesh and
   wire half of registration case 11): the mesh anchor of
   case 11 gains the same optional witness countersignature
   as registration case 11 sets for the registry anchor -
   the same Gate 1 HMAC scheme, a domain-separated witness
   encoding for this chain covering the signer's identity
   and signature with the per-origin heads, the state hash,
   the count, the history sequence, and the previous
   history entry's hash - recorded in the kept history
   entry for that write and mirrored at the anchor's top
   level. The Gate 3 AnchoredSyncNode of case 8, configured
   with a witness identity in case 11's opt-in style (both
   the identity and its secret, or neither), requires the
   countersignature exactly as it requires the signature:
   a merge whose anchor judgement fails the witness check
   is refused as a divergence named anchor_witness_invalid,
   rolled back, nothing accepted, and anchor_status()
   reports the same name; with no witness configured the
   node behaves exactly as cases 8/11 built. At the server
   processes, the witness is startup configuration in the
   style of case 10's signing flags - an anchor witness
   flag pair for the registry anchor and, independently,
   for the mesh anchor (case 15) - passed to the same
   consultations the signer already reaches: the startup
   construction, the live reload of case 13, the candidate
   construction of case 14, and the anchor judgements of
   case 17, whose signature judgement gains the witness
   judgement immediately after it, under the same name,
   before pairing. Expected: a server started over a
   signer-alone anchor with a witness configured refuses to
   start, its log naming anchor_witness_invalid; a live
   reload against a signer-alone re-anchor is refused under
   the same name with the previous view serving, and lands
   once the anchor is countersigned; case 17's delivered
   anchors are judged for their countersignature under the
   same discipline. The central assertion is registration
   case 11's finding re-run at a witness-configured server:
   the pair experiment 028 ACCEPTED - a rewritten record
   with an anchor over the rewrite signed by the current
   signer, its kept history honestly extended - is REFUSED
   as anchor_witness_invalid when it carries no
   countersignature, refused again under the same name when
   countersigned by an identity who is not the configured
   witness, and ACCEPTED when countersigned by the
   configured witness, the server then serving the rewritten
   record, stated as the finding's new form: the fence is
   now the custody of two secrets, the signer's and the
   witness's, and the symmetric model itself is unchanged.
   The honest flows of cases 13/14/17 keep working under a
   witness configuration whenever the anchor lifecycle is
   countersigned, and a server with NO witness configured
   behaves exactly as experiment 028 left it - including
   accepting the finding's pair, as the unmodified case 17
   suite proves in the same run. The Gate 5 verdict (case
   9's anchor evidence, registration case 11) takes the
   witness as opt-in arguments beside the signer for both
   chains: a signer-alone anchor fails that chain's anchor
   check under the name anchor_witness_invalid and nothing
   else, and countersigned anchors keep the verdict passing.
   Status: IMPLEMENTED at prototype scale (2026-11-06,
   experiment `experiments/029-witnessed-anchors/`); the case
   was written before the build, and the tests-first failure
   and the passing results are recorded in that experiment's
   `test-output.txt`. Tests:
   `packages/synchronization/test_witnessed_mesh_anchor.py`
   (a countersigned mesh anchor verifies with the witness
   checked; a signer-alone mesh anchor is
   anchor_witness_invalid at the check and at an
   AnchoredSyncNode's receive and standing; a different
   signer countersigned by the witness is still refused at
   write as different_signer),
   `packages/synchronization/test_witnessed_anchor_in_servers.py`
   (startup over a signer-alone anchor refused naming
   anchor_witness_invalid, startup over a countersigned
   anchor serves; the live reload refused then landed; the
   finding re-run in its three new legs at a
   witness-configured server; succession over the wire -
   a pair signed by a different signer and countersigned by
   the configured witness still refused as different_signer;
   the witness as configuration only - no delivery installs
   or replaces it), and
   `packages/validation/test_gate5_witnessed_anchor.py`
   (countersigned anchors keep the verdict passing with
   witnesses configured for both chains; a signer-alone
   registry anchor fails the verdict on the registry anchor
   check alone, named anchor_witness_invalid; the same for
   the mesh anchor; no witness configured judges exactly as
   case 10).
