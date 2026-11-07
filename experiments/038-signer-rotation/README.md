# Experiment 038: anchor signer rotation - trust moves to a new signer

**Question.** Experiments 019 and 029 built signed anchors and an
optional witness countersignature, and both recorded signer rotation
as an open item. In the model as built, the anchor signer's secret
is effectively forever: experiment 028's finding is that any
verifier holding that secret can rewrite history and sign the
rewrite so it is accepted, and there is no legitimate way to move
trust to a new signer. Can the current signer hand signing to a
successor, in a record every verifier can judge, so that the old
signer's anchors stop standing for everything after the handover
while history signed before it stays valid?

Framing: `tests/test-cases/registration.md` case 12 and
`tests/test-cases/sync-mesh.md` case 26 (this experiment's cases,
written before the build - the record format, the refusal names,
the judgement order, the delivery rules, the witness scope
decision, and the limits are all stated there), experiment 019
(signed anchors and the same-identity succession rule this
experiment extends), experiment 028 (the rewrite scenario and the
finding this experiment answers), experiment 029 (the witness
countersignature, whose requirement rotation does not change),
and experiments 030-032 (the push surfaces a rotation travels on,
inside the anchor material it governs).

## Design choices (at prototype scale)

- The rotation record (`PBA-ANCHOR-ROTATION-v1`) follows the
  repo's signed-record conventions: the CURRENT signer signs a
  statement naming the successor, the rotation sequence number,
  and the effective point (the anchor history sequence of the
  successor's first entry, with the head that entry anchors);
  the SUCCESSOR countersigns the same statement accepting it
  (prefix `PBA-ANCHOR-ROTATE-ACCEPT-v1`, covering the current
  signer's signature). Both signatures are required. The record
  lives in the anchor file as a top-level `rotations` list and
  travels inside the anchor object on the existing wire
  surfaces - never as a side channel. An anchor with no
  rotations is byte-identical to experiment 019/029's format.
- The judgement order is extended, not paralleled: internal,
  ROTATION, signature, witness, head. With a rotation chain in
  play, the signature judgement runs segment by segment, each
  history entry under its own signer's secret, from a lineage
  map (`signer_secrets`, identity to secret) held as operator
  configuration beside the existing signer configuration, which
  then names the lineage's root.
- Refusals are named in the repo's style: `anchor_signer_retired`
  (an entry at or after a rotation point signed by the retired
  signer; also a wire delivery signed by a retired lineage
  member), `anchor_rotation_signer_invalid` (a rotation by a
  non-current signer), `anchor_rotation_invalid` (a rotation
  whose signatures do not stand, a missing countersignature
  included), `anchor_rotation_mismatch` (a rotation whose
  stated head or point does not match the entry where it is
  applied).
- Wire succession (deliver-anchor, push, and the mesh surfaces):
  a delivery whose rotations extend the current anchor's by
  whole records chaining from the current signer to the
  delivered signer is a legitimate rotation succession and does
  not trip case 10/11's different-signer rule; a delivery signed
  by a retired lineage member is refused as
  `anchor_signer_retired` BEFORE the different-signer name can
  apply, and audit-logged in the standing event shape.
- Witness scope, decided in the cases before code: the witness
  itself is NOT rotated by this mechanism. The witness is
  operator configuration (case 11: never part of any anchor,
  delivery, or record), so witness rotation is a separate record
  type and configuration act, recorded as its own open item.
  Where a witness is configured, rotation changes nothing about
  the requirement: the successor's anchors need the configured
  witness's countersignature exactly as experiment 029
  established.

## Results

18 new tests pass (registration 7, policy-library consultation 3,
mesh 4, servers 4); the full suite is 260/260 (baseline 242 plus
the 18 new), EAI handoff PASS 3/3. Details in test-output.txt.

- A lineage-configured verifier accepts the successor's anchors
  at and after the rotation point, accepts the pre-rotation
  anchor for heads before the point, and refuses an honestly
  extended anchor signed by the retired signer as
  `anchor_signer_retired` - every signature on it genuine; the
  refusal is the rotation.
- Succession chains: a second rotation (successor to a third
  signer) is judged against the signer current at its point;
  a rotation by a non-current signer, one missing the
  successor's countersignature, and one whose stated head does
  not match are each refused under their own names, at the write
  path (audit-logged as `rotation_rejected`) and at verification.
- Delivery: a rotation arrives inside the anchor on 028's
  deliver-anchor surface and on 030's paired push, byte-for-byte;
  a lineage-configured peer accepts it and then refuses the old
  signer's anchor-alone delivery as `anchor_signer_retired`.
  A server with NO lineage configuration refuses the successor's
  delivery as `different_signer`, as case 17 refuses any
  different signer: rotation is opt-in configuration, exactly
  like signing (019) and witnessing (029) before it.
- Experiment 028's rewrite scenario, re-run under rotation at a
  lineage-and-witness server: (i) the rewrite pair signed by the
  OLD signer is refused as `anchor_signer_retired`; (ii) the
  same rewrite signed by the NEW signer without the witness is
  refused as `anchor_witness_invalid`; (iii) the same rewrite
  signed by the new signer and countersigned by the configured
  witness is ACCEPTED under the standing rules. The fence moved:
  it is now the new signer's secret plus the witness's, and the
  old signer's secret alone no longer opens it.

## What failed on the way (recorded, not hidden)

- One mesh-suite test initially rebuilt the pre-rotation state
  under different node ids; a mesh entry's origin is its node's
  id, so the rebuilt state hash differed. Test-side fix (same
  ids); no product code changed.
- The registration demo's retired-signer step initially stated
  the wrong refusal: with an extra registration before the old
  signer's extension, the extension's head no longer matched
  the rotation's stated head, so `anchor_rotation_mismatch`
  fired first - the cases' own judgement order working as
  stated. The demo now extends at the rotation's own point and
  shows `anchor_signer_retired`.

## Honest limits

- Rotation cannot rescue a verifier that never receives the
  rotation record. A verifier holding only the old anchor
  judges by the old rules; the record travels with the anchor,
  but nothing compels a stale verifier to fetch it.
- A compromised old signer can sign a COMPETING rotation: under
  the symmetric HMAC model every verifier holds every lineage
  secret, so a rotation's signatures prove lineage membership,
  not which of two competing rotations is honest. The design's
  answers are partial and stated plainly: the first rotation
  applied at the write path consumes the sequence number, and a
  verifier already holding the honest rotation refuses a
  disagreeing one as `anchor_rotation_invalid`. Two verifiers
  who each receive a different first rotation diverge, and
  nothing here reconciles them; that closure is the witnessed
  anchor (029) used as the rotation's carrier, or an asymmetric
  signature - both recorded open items, not built here.
- Lost-secret recovery remains unbuilt: rotation requires the
  current signer's signature, so a signer whose secret is LOST
  (not merely compromised) cannot rotate, and no recovery path
  is offered or implied.
- The model stays symmetric: a lineage verifier holds every
  signer's secret and can therefore forge any segment. Rotation
  orders trust in time; it does not reduce what one verifier's
  custody can forge. The witness (029) remains the second
  custody that bounds a rewrite in practice.
- The witness itself does not rotate under this mechanism (see
  the scope decision above); witness rotation is a separate
  record type, still an open item.
- Prototype scale, as everywhere in this repo: one machine,
  enrolled test identities, no real PKI, no production
  deployment.

## Process captured

- Reusable process: extend the existing judgement order at a
  stated point rather than building a parallel path; the
  rotation judgement slots between the internal checks and the
  signature judgement on both chains and on every surface that
  consults (file check, PolicyNode consultation, AnchoredSyncNode
  consultation, both wire deliveries), reusing each chain's own
  anchor module by the loaders the server already had.
- Opportunity Stack entry: none new; this closes the rotation
  open item recorded by experiments 019 and 029 (their READMEs
  are amended in the superseded-in-part style).
- What would differ next time: the demo and one test each spent
  a cycle on state-reconstruction details (node ids inside mesh
  state hashes; the registry head a rotation statement must
  name). A rotation statement names a point in a history;
  building the scenario at exactly that point, not near it, is
  the discipline the cases state and the build kept relearning.
