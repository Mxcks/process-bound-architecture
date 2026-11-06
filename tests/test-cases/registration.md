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
