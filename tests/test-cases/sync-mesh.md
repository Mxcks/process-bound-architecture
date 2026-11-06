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
