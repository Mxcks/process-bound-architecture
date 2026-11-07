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
11. Witnessed anchors: a second identity countersigns the
   anchor (added 2026-11-06, experiment 029; gap proven in
   experiment 028's finding on case 10's model: under the
   symmetric HMAC signing of case 10, what stops a rewritten
   record from being blessed by a new anchor is the custody
   of the signer's secret, and nothing else - every verifier
   configured with the signer's identity holds that secret
   and could sign a genuine anchor over a rewrite, and
   experiment 028's third leg accepted exactly that pair over
   the wire. The finding named its closure: a witnessed
   anchor or an asymmetric signature. This case builds the
   witnessed half with the machinery that exists): a signed
   anchor (case 10) can be COUNTERSIGNED at write time by a
   second enrolled identity, the witness, distinct from the
   signer. The countersignature reuses Gate 1's signing
   machinery exactly - the witness's enrollment secret, the
   same HMAC-SHA256 scheme - over a domain-separated
   canonical encoding of its own (a witness prefix, distinct
   from the anchor-signing prefix, so neither signature can
   be replayed as the other), and it countersigns THIS
   signed anchor: the encoding covers the signer's identity
   and signature together with the anchored head, the count,
   the history sequence, and the previous history entry's
   hash. The witness and the witness's secret are given
   together or neither, in case 10's opt-in style; a witness
   equal to the signer is refused at write time, because a
   countersignature by the signer's own identity is not a
   second custody. The countersignature is part of the kept
   history entry for that write - the entry records its
   witness and the countersignature inside the entry's
   hashed body, so editing either breaks the entry hash the
   way editing the signer's fields does - and the anchor's
   top level mirrors the last entry's witness fields,
   exactly as it mirrors the signer's. A write that presents
   no witness produces an anchor indistinguishable from a
   case 10 anchor. Expected at every consultation point that
   verifies the signature today (the Gate 2 PolicyNode of
   case 8, the server startup and Gate 5 verdict of case 9,
   the wire judgements of sync-mesh cases 17/18, and the mesh
   half in sync-mesh case 18): a consultation configured
   with a witness identity (the witness's identity plus the
   verification secret for that identity, both or neither)
   REQUIRES a valid countersignature by that witness. An
   anchor signed by the signer alone - no countersignature -
   is refused under its own new name, anchor_witness_invalid;
   an anchor countersigned by a different identity than the
   configured witness, or whose top-level countersignature
   was tampered with, is refused under the same name (a
   countersignature edited INSIDE a history entry breaks
   that entry's hash and is caught as
   anchor_signature_invalid, exactly as case 10's history
   tampering is); and the
   refusal leaves the consulted record, the serving view,
   and the current anchor exactly as they were. Judgement
   order, stated before the build: the signature judgement
   of case 10 precedes the witness judgement - an anchor
   whose signature does not verify is
   anchor_signature_invalid even when its countersignature
   is also wrong - and the witness judgement precedes the
   head judgement, as the signature judgement does. A
   correctly countersigned anchor whose head matches is
   anchored exactly as in case 10, with the check recording
   that the witness was checked and verified. Succession,
   stated plainly: the countersignature is not a succession
   instrument. Case 10's same-identity rule still fires
   first, in the order cases 10 and 17 set - a re-anchor or
   a delivered anchor signed by a different signer is
   refused as different_signer however genuinely the
   configured witness countersigned it - and who the witness
   IS is an operator configuration act (a constructor
   argument, a startup flag), never part of any anchor,
   delivery, or record: no delivered object can install,
   replace, or widen the configured witness. Backward
   compatibility, stated plainly: with no witness configured
   at a consultation point, its judgement is exactly case
   10's - a countersigned anchor is judged by signer and
   head, an uncountersigned one exactly as before - and all
   earlier suites pass unmodified in the same run. Stated
   limit, not hidden: the witness requirement binds the
   anchor's CURRENT write (the top-level countersignature,
   tied to the last history entry). Countersignatures on
   older history entries are part of those entries' hashed
   bodies - tamper-evident record - and are not individually
   re-verified by a witness-configured check; a history that
   predates the witness configuration carries unwitnessed
   entries, and the fence the configuration adds is over the
   write it is asked to accept now. The model stays
   symmetric: the fence moves from one secret's custody to
   two, and two colluding custodians (or one party holding
   both secrets) are exactly as able to bless a rewrite as
   one was in case 10; nothing here is or claims to be an
   asymmetric signature or an external witness service.
   Secrets are generated inside the tests at runtime
   (enrollment), never written to this repository. Status:
   IMPLEMENTED at prototype scale (2026-11-06, experiment
   `experiments/029-witnessed-anchors/`); the case was
   written before the build, and the tests-first failure and
   the passing results are recorded in that experiment's
   `test-output.txt`. Tests:
   `packages/registration/test_witnessed_anchor.py` (a
   countersigned anchor records its witness and verifies
   with the witness checked; a signer-alone anchor, a
   wrong-witness countersignature, and a tampered
   countersignature are each anchor_witness_invalid; a
   witness equal to the signer is refused at write;
   same-signer re-anchor countersigned appends history and
   a different signer countersigned by the witness is still
   refused as different_signer; no witness configured judges
   exactly as case 10), and
   `packages/policy-library/test_witnessed_anchor_consultation.py`
   (Gate 2 serves a countersigned anchor with a witness
   configured, refuses a signer-alone anchor and a
   wrong-witness anchor at load as anchor_witness_invalid,
   judges exactly as case 10 without one).
12. Anchor signer rotation: the anchor's signing identity can be
   handed over to a successor, by a record both signers sign
   (added 2026-11-06, experiment 038; gaps recorded in
   experiments 019 and 029: case 10's same-identity rule governs
   re-anchoring, not succession - there is no signer rotation, no
   transfer of anchor authority, and no recovery when a signer's
   secret is lost - and experiment 028 proved what that costs:
   under the symmetric model every verifier holding the current
   signer's secret can bless a rewrite, and the signer's secret
   is effectively forever, because there is no legitimate way to
   move trust to a new signer and retire the old one): a signed
   anchor (case 10) can be ROTATED from its current signer to a
   successor signer by a rotation record, and the rotation -
   not a new same-identity rule exception - is what then makes
   the successor's anchors legitimate at a verifier that holds
   the rotation.
   The rotation record, stated before the build: a record in
   the signed-record style of cases 10 and 11, format
   PBA-ANCHOR-ROTATION-v1, naming the chain, a rotation
   sequence number (0 for the first rotation of an anchor,
   increasing by one per rotation), the current signer
   (from_signer), the successor signer (to_signer, a different
   enrolled identity), the point the rotation takes effect at -
   the anchor history sequence of the first entry the successor
   will sign (effective_anchor_seq), together with the chain
   head and entry count that entry anchors
   (effective_chain_head, effective_entry_count) - and TWO
   signatures over domain-separated canonical encodings in
   case 10/11's prefix discipline (PBA-ANCHOR-ROTATE-v1 for
   the current signer's statement, PBA-ANCHOR-ROTATE-ACCEPT-v1
   for the successor's countersignature, which covers the
   current signer's signature, so neither can be replayed as
   an anchor signature, a witness countersignature, or the
   other half of this record): the CURRENT signer signs the
   statement, and the SUCCESSOR countersigns the same
   statement accepting it. Both signatures are required for
   the rotation to stand; a rotation with only one is not a
   rotation. Rotation records are kept in the anchor file
   itself, in a top-level rotations list beside the kept
   history - the anchor material they govern is where they
   live - and they travel with that material wherever it
   travels (the wire surfaces of sync-mesh case 26 included),
   never as an unauthenticated side channel: a rotation record
   no anchor carries, and no verifier holds, changes nothing.
   The write path: the successor's first signed write over an
   anchor signed by the current signer presents the rotation
   record (and, in this symmetric model, the outgoing signer's
   secret, so the write can verify the statement it acts on -
   the rotation is a joint act and both parties' secrets are
   present at the handover, exactly as a verifier's are). The
   write is refused, the anchor left byte-for-byte unchanged,
   and the refusal appended to the anchor audit log (event
   rotation_rejected, case 10's log) under its own name when:
   the rotation's from_signer is not the anchor's current
   signer, or its to_signer is not the presenting writer -
   anchor_rotation_signer_invalid; the rotation's sequence is
   not the next one, its signatures do not verify, or its
   countersignature is missing - anchor_rotation_invalid; or
   its effective point is not the write being made (the
   effective history sequence is not the sequence this write
   takes, or the stated head or count is not the head or count
   this write anchors) - anchor_rotation_mismatch. An accepted
   rotation appends the record to the anchor's rotations and
   the successor's entry to the kept history in the same
   write; the successor's later writes are ordinary
   same-identity writes under case 10. A second rotation
   (successor to a third signer) chains identically: its
   from_signer is the signer current at its point. Writing
   over an anchor that already carries rotations requires the
   lineage secrets (a map of enrolled identity to secret for
   every signer in the anchor's lineage) so the segmented
   history can be verified; without them the write is refused
   as anchor_rotation_invalid rather than judged partially.
   The check path: a check configured with the lineage
   secrets (signer_secrets, in case 10's opt-in style, beside
   the existing signer and witness configuration) judges an
   anchor carrying rotations rotation-first among the
   identity judgements - after the chain's own internal
   verification, before the signature judgement of case 10,
   the witness judgement of case 11, and the head judgement -
   and AnchorCheck records rotation_checked, rotation_valid,
   the specific rotation refusal name when one fires, and the
   signer current at the anchor's tip. A verifier may also
   HOLD rotation records (a rotations argument, from a
   delivery it accepted): the held records and the anchor's
   own records must agree exactly, or the judgement is
   anchor_rotation_invalid. The rotation chain is judged in
   full: sequence order; each rotation's from_signer is the
   signer current just before its effective point and its
   to_signer is a different identity; effective points
   strictly increase and fall inside the anchor's history (a
   point at the history's end is a pending rotation, judged
   for its signatures and its signer chain, its head judged
   when the successor's first entry lands); each signature
   and countersignature verifies under the lineage secrets
   (a lineage identity with no secret in the map is
   anchor_rotation_invalid: in this model an unverifiable
   rotation is not a rotation); and the history entry at each
   effective point carries exactly the stated head and count.
   Expected outcomes at such a verifier: an anchor whose
   history crosses a valid rotation - earlier entries by the
   old signer, the entry at the effective point and later
   entries by the successor - verifies, with the signature
   judgement run segment by segment (each entry under its own
   signer's secret) and the anchor accepted as anchored when
   its head matches; the old signer's entries BEFORE the
   rotation point stay valid, so an anchor whose tip is still
   before the point (or a registry still at a pre-rotation
   head) is judged exactly as case 10 judges it; an anchor
   carrying entries at or after the rotation point signed by
   the RETIRED signer is refused as anchor_signer_retired -
   under that name, ahead of the signature judgement, because
   retired is the fact about it - and the same name refuses
   an anchor whose tip signer is a retired signer however its
   history is arranged; a rotation signed by a signer who is
   not current at its point is refused as
   anchor_rotation_signer_invalid; a rotation missing its
   countersignature, or with a signature that does not
   verify, is refused as anchor_rotation_invalid; and a
   rotation whose stated head or count does not match the
   history entry at its effective point is refused as
   anchor_rotation_mismatch. None of these is ever a valid
   anchor, and none is reported as anchor_signature_invalid,
   anchor_witness_invalid, anchor_mismatch, or anchor_stale.
   Witness interplay, stated plainly: rotation changes WHO
   signs, never the witness rule. Where a witness is
   configured (case 11), the successor's entries require the
   configured witness's countersignature exactly as the old
   signer's did, judged after the rotation and signature
   judgements in case 11's order; a rotated anchor whose
   current write the witness did not countersign is
   anchor_witness_invalid. WITNESS ROTATION IS OUT OF THIS
   MECHANISM'S SCOPE, a scope chosen in this case before any
   code: the witness is operator configuration (case 11: a
   constructor argument or startup flag, never part of any
   anchor, delivery, or record), so no record inside an
   anchor can rotate the witness a consultation point
   requires - a delivered rotation naming a new witness would
   be a delivered object installing configuration, which case
   11 forbids. Rotating a witness is a separate record type
   and a configuration act at each consultation point,
   recorded here as its own open item, not built.
   Competing rotations, stated rather than hidden: a
   compromised old signer can sign a competing rotation to an
   identity it controls, and under this symmetric model no
   check in this case distinguishes that record from an
   honest one before it is applied - the two are the same
   object, as experiment 028 said of its pair. What the
   design answers is narrower and is the honest answer:
   rotation is judged against the anchor's own kept history
   and the verifier's held records, so a competing rotation
   can only stand where it is DELIVERED and accepted, a
   verifier that already holds the honest rotation refuses a
   disagreeing one as anchor_rotation_invalid, and the first
   rotation applied at the write path consumes the sequence -
   a second record claiming the same rotation sequence from
   the same signer is refused once the first stands in the
   anchor. A verifier that never receives any rotation record
   keeps judging the old signer exactly as case 10 does:
   rotation cannot rescue a verifier it never reaches, and
   this case claims nothing else for it. Lost-secret recovery
   is NOT built: rotation requires the current signer's
   signature, so a signer whose secret is lost cannot rotate,
   exactly as it cannot re-anchor (case 10's stated limit
   stands); recovery remains an operator-level replacement,
   indistinguishable from attack by this prototype, as before.
   Backward compatibility, stated plainly: with no lineage
   secrets configured and no rotations in play, every
   judgement is exactly cases 10/11's - an unrotated anchor is
   indistinguishable from a case 10/11 anchor (a write with no
   rotation produces no rotations list), experiment 019's
   different-signer write refusal and experiment 028's
   different_signer wire refusal stand for any delivery not
   carrying a valid rotation - and all earlier suites pass
   unmodified in the same run. Secrets are generated inside
   the tests at runtime (enrollment), never written to this
   repository. Status: IMPLEMENTED at prototype scale
   (2026-11-06, experiment
   `experiments/038-signer-rotation/`); the case was written
   before the build, and the tests-first failure and the
   passing results are recorded in that experiment's
   `test-output.txt`. Tests:
   `packages/registration/test_signer_rotation.py` (the
   rotation record's two signatures and its refusal shapes at
   the write path - non-current signer, missing
   countersignature, wrong effective head - the segmented
   history verifying after rotation, the pre-rotation anchor
   still accepted, the retired signer refused as
   anchor_signer_retired, the chained second rotation, and
   the witness requirement unchanged across the rotation),
   `packages/policy-library/test_signer_rotation_consultation.py`
   (Gate 2 serves a rotated anchor under a lineage
   configuration, refuses a retired signer's anchor at load
   as anchor_signer_retired, judges exactly as case 10
   without a lineage configuration), and the mesh and wire
   halves in sync-mesh case 26.
