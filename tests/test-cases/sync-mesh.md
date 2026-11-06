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
