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
9. Anchor consultation in the networked server processes and in
   the Gate 5 verdict (added 2026-11-06, experiment 018; gap
   flagged in experiment 015: anchor consultation was wired into
   the in-process Gate 2 and Gate 3 nodes, but the experiment
   007/014 server processes in
   `packages/synchronization/node_server.py` built their
   PolicyNode without an anchor path, so networked serving never
   consulted an anchor, and the Gate 5 verification verdict in
   `packages/validation/gate5_harness.py` included no anchor
   evidence at all): a node server process started with an anchor
   configured for its registry snapshot (a new opt-in `--anchor`
   argument, experiment 012's registry anchor for that snapshot's
   chain, passed to the PolicyNode it builds, reusing experiment
   015's consultation and classification exactly) consults that
   anchor at startup, in the same plain style as its existing
   refusal to start over a tampered snapshot. Expected: a server
   whose snapshot is the genuine anchored chain starts and serves
   normally over BOTH existing transports (experiment 007's
   plaintext mode and experiment 014's mutual-TLS mode), and its
   health response reports the anchor as configured and anchored;
   a server whose snapshot was rewritten from genesis with
   different content (internally consistent, so it passes the
   snapshot's own chain verification - experiment 012's negative
   control, and the exact snapshot a server without an anchor
   serves) REFUSES TO START, exits nonzero, and its log names the
   refusal anchor_mismatch (experiment 012's check status,
   experiment 015's AnchorMismatch). Negative control, in the
   style of availability case 1: a server started WITHOUT an
   anchor over that same rewritten snapshot starts and serves it
   exactly as before this case existed, and reports no anchor
   configured - absence of an anchor is not an error, behavior
   without one is exactly the pre-018 behavior, and the earlier
   networked and protected suites pass unmodified in the same
   run. Honest growth, stated explicitly as in case 8: a snapshot
   that grew honestly after anchoring (the anchored head is still
   the head of the presented chain's prefix of the anchored
   length, entries only appended) is refused at startup as
   anchor_stale, under its own name - not as anchor_mismatch, and
   never silently started as anchored - until the new head is
   re-anchored, after which the same server starts and serves.
   Gate 5 verdict, same case: the Gate 5 harness's run_gate can
   take anchor evidence into account (opt-in arguments carrying
   a registry anchor path and a mesh anchor path; with neither
   given, the verdict's checks and behavior are exactly
   experiment 006's). When anchor checks are part of a gate run,
   each configured anchor is consulted with experiment 012's
   check functions and classified with experiment 015's
   classification (anchored / anchor_mismatch / anchor_stale),
   and the result enters the verdict as its own named check.
   Expected: a genuine registry chain and a genuine mesh state
   with matching anchors keep the verdict exactly as experiments
   006/009 produced it (passed, every earlier check unchanged and
   passing, the anchor checks additional and passing); a registry
   chain rewritten so that it passes every internal check the
   gate already runs (the snapshot verifies on its own, the
   canary policy is present and serves, the mesh lineage
   verifies) makes the verdict FAIL, and the anchor check is the
   failing check, named anchor_mismatch; honest growth of either
   anchored chain past its anchor makes the verdict FAIL under
   the name anchor_stale for that chain's check, never a pass as
   anchored and never reported as a mismatch. Status:
   IMPLEMENTED at prototype scale (2026-11-06, experiment
   `experiments/018-anchor-in-servers-and-verdict/`); the case
   was written before the build, and the tests-first failure and
   passing results are recorded in that experiment's
   `test-output.txt`. Tests:
   `packages/synchronization/test_anchor_in_servers.py` (genuine
   anchored server serves over plaintext and mutual TLS,
   rewritten snapshot refused at startup as anchor_mismatch,
   anchorless server serves the rewrite exactly as before,
   honest growth refused as anchor_stale until re-anchored) and
   `packages/validation/test_gate5_anchor.py` (no-anchor verdict
   unchanged, genuine anchors keep the verdict passing, rewrite
   passing every internal check fails the verdict on the anchor
   check alone, honest growth of either chain fails the verdict
   as anchor_stale).
10. Signed anchors with a kept history (added 2026-11-06,
   experiment 019; gaps flagged in experiments 012, 015 and 018:
   the external anchor is unsigned and unwitnessed, so whoever
   can write the anchor file can re-anchor a rewrite and pass
   every consultation built since, and re-anchoring overwrites,
   so there is no anchor history to audit): an anchor (this
   case's registry anchor, and the mesh anchor in sync-mesh case
   11) can be written SIGNED, by an enrolled identity, reusing
   Gate 1's existing signing machinery exactly - the identity's
   enrollment secret and the same HMAC-SHA256 scheme as
   `sign_library` / `sign_revocation`, with a domain-separated
   canonical encoding for anchor signing so an anchor signature
   can never be replayed as a library or revocation signature
   or the other way round; standard library only, no new crypto.
   Expected: a signed anchor records its signer and signature;
   every consultation point that exists today (the Gate 2
   PolicyNode of case 8, the Gate 3 AnchoredSyncNode of
   sync-mesh case 8, the experiment 018 server startup, and the
   experiment 018 Gate 5 verdict) verifies the anchor's
   signature WHEN a signing identity is configured for that
   consultation (the signer's identity plus the verification
   secret for that identity, the same secret the enrollment
   issued - symmetric HMAC means the verifier holds the secret,
   which is Gate 1's stated prototype limit and is not hidden).
   An anchor whose signature is missing, made by a different
   secret, or tampered with after signing is a failure under
   its own name, anchor_signature_invalid: distinct from
   anchor_mismatch (the head is not the anchored head) and from
   anchor_stale (honest growth past the anchor), and never
   treated as a valid anchor by any consultation point; a
   correctly signed anchor whose head matches is anchored
   exactly as before. Re-anchor rule, stated before the build:
   re-anchoring a SIGNED anchor requires a valid signature from
   the SAME identity that signed the current anchor; an attempt
   signed by a different enrolled identity (signing correctly
   as themselves, with their own secret) is refused, the
   current anchor is left unchanged, and the refusal is logged
   (an anchor audit log beside the anchor file, the same style
   as Gate 1's audit log). A first signed write over an unsigned
   anchor, or over no anchor, establishes the signer and starts
   the signed history; that transition is the one exception,
   stated here, because an unsigned anchor has no signer to
   match. Kept history: a signed re-anchor APPENDS to a history
   kept inside the anchor file instead of overwriting silently -
   each history entry records the anchored head (and count),
   its sequence number, and its signer - and the history itself
   is verifiable in the same style as this repo's other records:
   entries are hash-chained to the previous entry (sequence
   0..n-1, prev-hash linkage, entry-hash recomputation) and each
   entry is signed by its signer, so a tampered history entry
   (an edited head, count, or signer inside an old entry) is
   caught by history verification, and when a signing identity
   is configured a consultation treats a history that does not
   verify as anchor_signature_invalid. Backward compatibility,
   stated plainly: unsigned anchors from experiments 012-018
   keep working exactly as before when no signing identity is
   configured, a consultation configured without a signing
   identity judges a signed anchor by its head exactly as it
   judged an unsigned one, and all earlier suites pass
   unmodified in the same run. Secrets are generated inside the
   tests at runtime (enrollment), never written to this
   repository. Status: IMPLEMENTED at prototype scale
   (2026-11-06, experiment `experiments/019-signed-anchors/`);
   the case was written before the build, and the tests-first
   failure and the passing results are recorded in that
   experiment's `test-output.txt`. Tests:
   `packages/registration/test_signed_anchor.py` (signed anchor
   verifies with its history, missing/wrong/tampered signature
   is anchor_signature_invalid, same-identity re-anchor appends
   history, different-identity re-anchor refused and logged,
   tampered history caught, unsigned behavior unchanged),
   `packages/policy-library/test_signed_anchor_consultation.py`
   (Gate 2 serves a correctly signed anchor with an identity
   configured, refuses a missing or tampered signature at load
   as anchor_signature_invalid, judges by head alone without
   one), `packages/synchronization/test_signed_anchor_in_servers.py`
   (server startup with a signing identity serves the genuine
   signed anchor and refuses a tampered signature as
   anchor_signature_invalid; without an identity it behaves as
   experiment 018), and
   `packages/validation/test_gate5_signed_anchor.py` (genuine
   signed anchors keep the verdict passing, a tampered
   signature on either chain fails the verdict on that chain's
   anchor check alone, named anchor_signature_invalid).
