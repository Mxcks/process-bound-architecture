# Test Cases: Registration (layer 04)

1. Unregistered bytecode is blocked at the execution boundary.
2. A library with a bad signature is rejected and the rejection is logged.
3. A correctly signed library from any user registers through the
   authenticated channel and becomes loadable, with its policy hash in the record.
4. Registration of the same library twice yields one deterministic record, not two conflicting ones.
5. A validation channel is bound on registration and on nothing else.
6. Revocation (gap flagged in experiment 002): a registered library that
   is later revoked by its author is blocked at the execution boundary
   after revocation, its validation channel is no longer honored, and
   the revocation itself is recorded as a new entry in the
   tamper-evident record. The original registration entry is not
   deleted or rewritten, and a node holding only the pre-revocation
   record is a known stale state this case must name, not hide.
   Status: IMPLEMENTED (2026-11-06, experiment
   `experiments/008-revocation/`). Only the author, authenticated by
   their channel enrollment and a revocation signature, can revoke;
   a non-author attempt is rejected and audit-logged. Tests:
   `packages/registration/test_revocation.py` (author revokes,
   boundary and validation channel refuse, new chain entry with the
   original untouched, non-author and bad-signature rejections) and
   `packages/policy-library/test_revocation_serving.py` (a node with
   the updated record stops serving; a stale node keeps serving until
   the updated record arrives, then stops).
7. External chain anchor (gap flagged in experiments 002-010):
   the chain head (the registry record's latest entry hash, with the
   entry count) is written to an anchor file that lives OUTSIDE the
   registry's own directory, and a verifier presented with a registry
   (the channel's log or a Gate 2 snapshot copy of it, which is the
   same chain) checks it against that outside anchor, not only
   against itself. Expected: the genuine registry verifies against
   its anchor; a registry whose head or length has moved on from the
   anchor (a truncated or extended presentation) fails the anchor
   check; and the mesh's per-origin chain heads, the second chain
   the docs name (packages/synchronization/sync_mesh.py), are
   anchored and checked the same way. Negative control, in the
   style of availability case 1: a registry rewritten from genesis
   with different content, recomputed so that it passes its own
   internal chain verification cleanly, must FAIL the anchor check,
   because its head does not match the head that was anchored from
   outside it; a fully rewritten mesh origin chain that verifies on
   its own must fail the same way. The anchor location at prototype
   scale is a stand-in (a separate file outside the registry
   directory), not a real external service, and the case claims
   nothing about anchoring itself being witnessed or unforgeable.
   Status: IMPLEMENTED at prototype scale (2026-11-06, experiment
   `experiments/012-external-chain-anchor/`). Tests:
   `packages/registration/test_anchor.py` (anchor captures the head
   outside the registry directory and the genuine registry
   verifies, full-rewrite negative control, single-entry tamper,
   truncation/extension until re-anchored) and
   `packages/synchronization/test_anchor.py` (per-origin mesh heads
   anchored, converged peers verify, rewritten origin chain
   negative control).
8. Anchor consultation in serving and sync (gap flagged in
   experiment 012: the anchor exists and its check works, but
   nothing consulted it automatically - Gate 2's PolicyNode served
   from the chain alone and Gate 3 sync never called the anchor
   check, so a rewritten record would be served and synced with
   the anchor never asked): a Gate 2 policy node constructed with
   an anchor path consults that anchor for its registry snapshot
   at load (and on snapshot reload, experiment 008's path), reusing
   experiment 012's check, and a Gate 3 sync node constructed with
   a mesh anchor path consults that anchor on the merged state
   before accepting a peer's entries as converged. Expected: a
   genuine snapshot whose head matches its anchor serves exactly
   as before; a snapshot whose chain was rewritten from genesis
   (internally consistent, so it passes the chain's own
   verification - experiment 012's negative control) is REFUSED at
   load with the refusal named anchor_mismatch, and a peer whose
   origin chain was rewritten the same way is flagged divergent
   (reason anchor_mismatch) and not accepted by an anchored sync
   node. With NO anchor configured, behavior is exactly the
   pre-consultation behavior: absence of an anchor is not an
   error, a node serves and syncs exactly as experiments 003/004
   built, and the earlier suites pass unmodified in the same run.
   Honest growth, stated explicitly rather than hidden: a record
   that grew honestly after anchoring (internal verification
   passes, and the anchored head is still the head of the
   presented chain's prefix of the anchored length - everything
   that was anchored is intact, entries were only appended) is
   NOT a rewrite, so it is not reported as anchor_mismatch; it is
   also NOT silently served as anchored. It is reported as
   anchor_stale and refused (at load for a policy node; as a
   flagged anchor_stale divergence for a sync node) until the
   new head is re-anchored, which is exactly the lifecycle
   experiment 012 stated for its check (an extended presentation
   fails until re-anchored). After re-anchoring, the grown record
   serves and syncs again. A truncated presentation is not
   growth: the anchored head is not its prefix head, so it is
   anchor_mismatch. Status: IMPLEMENTED at prototype scale
   (2026-11-06, experiment
   `experiments/015-anchor-consultation/`). Tests:
   `packages/policy-library/test_anchor_consultation.py`
   (genuine snapshot serves with an anchor, full-rewrite snapshot
   refused at load as anchor_mismatch, no-anchor behavior
   unchanged including serving a rewritten snapshot exactly as
   before, honest growth refused as anchor_stale at load and on
   reload until re-anchored and then serving) and
   `packages/synchronization/test_anchor_consultation.py`
   (genuine peer accepted and converged with an anchor, rewritten
   peer flagged anchor_mismatch and not accepted, no-anchor sync
   accepts the rewritten chain exactly as before, honest growth
   flagged anchor_stale and refused until re-anchored and then
   accepted).
