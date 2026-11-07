# 029. Witnessed anchors: a second identity countersigns the anchor

Experiment 028 closed with a finding, proved not asserted:
under experiment 019's symmetric HMAC model, anyone holding
the current signer's enrollment secret - which every
configured verifier holds - can sign an honestly extended
anchor over a REWRITTEN record, and a server accepts and
serves the rewrite. The finding's own words: what stops a
rewrite from being blessed is the custody of the signer's
secret, and nothing else; its closure is a witnessed anchor
or an asymmetric signature, not another endpoint.

This experiment builds the witnessed half of that closure
with the machinery that exists. Anchor writing gains an
optional witness countersignature: a second enrolled
identity, distinct from the signer, countersigns the signed
anchor under a domain-separated encoding in experiment 019's
prefix discipline, and the countersignature is part of the
anchor's kept history entry for that write. Consultation
points configured with a witness identity - plumbed in the
same opt-in style as the signer in experiment 019 - REQUIRE
a valid countersignature and refuse without one under a new
name, `anchor_witness_invalid`. Experiment 028's finding is
re-run as this experiment's central test at a
witness-configured server.

Cases: registration case 11 and sync-mesh case 18
(`tests/test-cases/registration.md`,
`tests/test-cases/sync-mesh.md`), both written in detail
before any code, in case 4's format.

## Question

Does requiring a second enrolled identity's countersignature
at the anchor's consultation points close experiment 028's
hole at those points - the signer-alone anchor refused, the
wrong-witness anchor refused, the configured witness's
anchor accepted - while every honest flow keeps working and
every point with no witness configured behaves exactly as
experiments 019/028 left it?

## The design choices this experiment sets, stated plainly

- The refusal name is `anchor_witness_invalid`, in the
  existing refusal style: it is the check's status
  (`AnchorCheck.status`, `MeshAnchorCheck.status`), the
  Gate 2 exception (`AnchorWitnessInvalid`, an
  `AnchorRefused` beside `AnchorSignatureInvalid`), the
  divergence reason on the mesh side, the wire delivery
  reason, and the Gate 5 check detail - one name at every
  surface, as `anchor_signature_invalid` is since 019.
- Judgement order is fixed everywhere: the signature's
  judgement first, then the witness's, then the head's. An
  anchor whose signature fails is `anchor_signature_invalid`
  even when it also lacks a valid countersignature; an
  anchor whose signature verifies but whose countersignature
  does not is `anchor_witness_invalid` and never reaches a
  head finding.
- The countersignature covers the SIGNED anchor: the
  canonical encoding (`PBA-ANCHOR-WITNESS-v1`, chain name
  inside it, so the registry witness and the mesh witness
  are domain-separated from each other as well as from the
  signature's `PBA-ANCHOR-SIG-v1`) binds the witness's
  identity, the signer's identity, the signer's signature,
  the anchored head (per-origin heads on the mesh chain),
  the entry count, the history sequence, and the previous
  history entry's hash. It is computed with the witness's
  Gate 1 enrollment secret under the same HMAC-SHA256
  scheme as the signature - deliberately: this experiment
  moves custody, it does not change the model.
- The countersignature lives in the kept history entry for
  its write (inside the entry's hashed body, so editing an
  old entry's countersignature breaks that entry's hash and
  is caught as `anchor_signature_invalid`, case 10's
  discipline) and is mirrored at the anchor's top level,
  exactly as the signature is. An unwitnessed write produces
  a byte-identical anchor to experiment 019's.
- The witness fence binds the anchor's CURRENT write: the
  check verifies the top-level countersignature against the
  last history entry's and recomputes it. Older entries'
  countersignatures ride inside their hashed bodies as
  tamper-evident record; they are not individually
  re-verified (case 11 states this limit in the case
  itself).
- Write discipline: witness and witness secret come
  together or not at all; a witness requires a signer (a
  countersignature countersigns a signature); a witness
  equal to the signer is refused at write time - a
  countersignature by the signer's own identity is not a
  second custody.
- Succession is untouched: re-anchor succession is judged
  on the signer alone, exactly as case 10 set it. A
  different signer's anchor is refused as a different
  signer (write time) / `different_signer` (wire) however
  genuine its countersignature is - the witness judgement
  never runs before succession on the wire, as case 17
  ordered the signature's after it.
- The witness is operator configuration only, everywhere:
  constructor arguments and CLI flags
  (`--anchor-witness` / `--anchor-witness-secret-file`,
  `--mesh-anchor-witness` /
  `--mesh-anchor-witness-secret-file`), read at startup
  like the signer's. No delivery carries a field that
  installs or replaces it; a delivered anchor's witness
  field is data verified AGAINST the configuration, never
  adopted from it. The server suite asserts this directly:
  after a refused delivery countersigned by a new identity,
  the process's configured witness is unchanged and the
  configured witness's own pair still lands.
- Plumbing follows the signer's opt-in shape at every
  surface: `write_anchor` / `write_mesh_anchor` gain
  `witness` + `witness_secret_hex`; the checks gain the
  same pair and record `witness_checked` / `witness_valid` /
  `witness` on the check objects; `PolicyNode`,
  `AnchoredSyncNode`, `NetworkNode`, and the Gate 5
  `run_gate` each gain the pair for their chain. Both None
  (the default) is the pre-029 judgement at that point,
  unmodified.

## Result: all case 11 and case 18 outcomes observed, 22 new tests pass, full regression green

| Outcome | Where proven |
|---|---|
| Countersigned anchor records witness + countersignature (top level and in the history entry) and verifies with both judgements recorded | `test_witnessed_anchor.py`, `test_witnessed_mesh_anchor.py` |
| Signer-alone anchor refused `anchor_witness_invalid` at the check, at Gate 2 load, at an anchored mesh receive (divergence, rolled back), in the Gate 5 verdict (either chain, that chain's check alone), at server startup, at live reload, and in wire anchor delivery | all five new suites |
| Wrong-witness countersignature and tampered top-level countersignature refused under the same name; tampered in-entry countersignature and tampered signer signature stay `anchor_signature_invalid` | `test_witnessed_anchor.py` |
| Write discipline: witness == signer refused, partial witness arguments refused, witness without signer refused, nothing written on any refusal | `test_witnessed_anchor.py`, `test_witnessed_mesh_anchor.py` |
| Countersigned same-signer re-anchor appends history and advances the anchor; the witness-only-configured check (no signer identity) also works | `test_witnessed_anchor.py` |
| Succession: a different signer's genuinely countersigned anchor refused at write and over the wire as a different signer / `different_signer`, audit-logged; the witness judgement never pre-empts it | registration, mesh, and server suites |
| The witness is configuration only: a delivery countersigned by a new identity is refused and installs nothing; the configured witness is unchanged afterward and its pair still lands | `test_witnessed_anchor_in_servers.py` |
| Honest flows under a witness configuration: countersigned pair delivered over the wire (revocation live at 410), countersigned anchor alone lands, live reload lands once the re-anchor is countersigned, mesh `/health` reports `anchored` over a countersigned mesh anchor and `anchor_witness_invalid` over a signer-alone one | server suite |
| No witness configured: behavior exactly as 019/028 - the full earlier suite passes unmodified in the same run, including case 17's finding test ACCEPTING the blessed rewrite at a witnessless server | full regression, below |

Full regression after the build: registration 25, policy-
library 42, synchronization 91, validation 22 - 180/180 OK,
against the pre-build baseline of 158/158 (20 + 39 + 81 +
18) recorded by experiment 028 and re-confirmed for three
packages before the build. EAI handoff verification PASS
3/3. No leftover `node_server` processes (standalone check:
0). One load-affected synchronization re-run during the
baseline pass showed the known `wait_ready` port-file read
race from experiments 026-028 (an empty port file read
mid-write, one error, no assertion failure); it is recorded
in `test-output.txt` and the post-build full run was clean.
`packages/registration/demo_witnessed_anchor.py` walks case
11 end to end and prints every judgement above.

## How the mechanism works, in plain words

Signing said: this head is the head, says one enrolled
identity. Witnessing adds: and a second enrolled identity,
who did not sign it, attests the same signed statement. The
witness does not sign a head; it countersigns the signer's
signature over that head, so the countersignature is
worthless transplanted onto another anchor, another signer,
or another history. A consultation point that is told which
witness to expect then asks two questions instead of one -
did the signer sign this, and did THAT witness countersign
it - and refuses under a different name for each. Nothing
about the anchor's lifecycle changes: the same file, the
same kept history, the same audit log, the same succession
rule; the countersignature simply rides inside the write it
attests.

## The re-run finding, stated exactly as evidenced

Sync-mesh case 18 re-runs experiment 028's finding test at a
server started with a signer AND the witness carol
(`test_witnessed_anchor_in_servers.py`,
`test_finding_pair_refused_until_the_witness_countersigns`).
The pair is byte-for-byte the pair case 17's third leg
accepts: mallory's rewritten record, revocation dropped,
paired with an anchor over the rewrite signed by alice, the
current signer, its history honestly extended from the
server's current anchor.

- Leg (i), no countersignature: REFUSED,
  `anchor_witness_invalid`. The snapshot file and the
  anchor file are byte-for-byte unchanged afterward,
  mallory's policy still serves its original bytes (200)
  and alice's still refuses as revoked (410).
- Leg (ii), the same pair countersigned by bob, an enrolled
  identity who is not the configured witness: REFUSED under
  the same name, same unchanged state.
- Leg (iii), the same pair countersigned by carol, the
  configured witness: ACCEPTED and applied - both files
  become the delivered pair and the rewritten record is
  served (mallory 200 with the rewritten bytes, alice 404:
  her record is not in the rewrite).

Stated plainly, as the case requires: the witness
requirement moves the fence from one secret's custody to
two. A party holding both alice's and carol's secrets
blesses a rewrite at a witness-configured point exactly as
a party holding alice's alone did in experiment 028, and
every configured verifier holds both secrets, exactly as
every verifier held the signer's in 019/028. The symmetric
model is unchanged; what changed is how many custodies must
collude. The asymmetric half of experiment 028's closure -
a signature the verifier cannot mint - remains unbuilt.

## What failed

The tests-first run failed as designed: all five new suites
errored against the unbuilt code on exactly the missing
machinery (the `witness` keyword arguments, the
`AnchorWitnessInvalid` name) - registration 5 errors,
policy-library an ImportError, validation 4, mesh 4,
servers 6 - recorded in `test-output.txt`. During the
build, one design error was caught by the tests and fixed
before the suites passed: the first `AnchorWitnessInvalid`
draft carried its own constructor taking only the check,
which broke the `AnchorRefused` (message, check) shape
every raise site uses; the class now inherits the base
constructor like its siblings. No earlier test was modified
at any point.

## Honest limits (this is a prototype, not production)

- The model stays symmetric, in full: the countersignature
  is HMAC under the witness's Gate 1 enrollment secret, so
  every verifier configured with a witness holds a secret
  that could also forge that witness's countersignatures,
  and the registration channel, which issued both secrets,
  could forge either identity's part. Two colluding
  custodians - or one party holding both secrets - bless a
  rewrite exactly as one did before. This is a custody
  change, not a cryptography change.
- The witness binds the anchor's CURRENT write only. The
  check verifies the top countersignature against the last
  history entry; an older entry's countersignature is
  tamper-evident record inside a hashed body, not a
  separately verified judgement. A deployment that needs
  every historical write witnessed-verified must say so;
  nothing here does it.
- Rollback stands exactly as in 019/028: an older,
  genuinely signed AND countersigned anchor verifies as
  both; against a chain still at that older head it is
  indistinguishable from the current anchor. The kept
  history is tamper-evident going forward, not
  rollback-proof, and witnessing does not change that.
- Witness consultation is per-point and opt-in, like the
  signer and the anchor themselves: a node, server, or gate
  run configured without a witness judges a countersigned
  anchor exactly as experiment 019 judges a signed one,
  under which experiment 028's pair still lands. Nothing
  here makes a witness mandatory, distributes one, or
  notices a consultation point that should have one and
  does not.
- Who may be a witness is operator configuration, as signer
  enrollment already is: any enrolled identity distinct
  from the signer. No governance is designed here - no
  witness sets, no quorum, no rotation or succession for a
  witness, no recovery if a witness's secret is lost, and
  no rule for what happens when the one witness is
  unavailable at re-anchor time (the write simply cannot be
  countersigned, and a witness-configured point then
  refuses the new anchor until one is).
- The anchor is still a local stand-in file, unsigned and
  unwitnessed by anything outside this repo's own machinery:
  no external witness service, no public append-only log,
  no cross-machine attestation. Production still needs the
  head held where the record's writer cannot reach it -
  now, at witness-configured points, countersigned by a
  second custody when it gets there.

## Process captured

Tests first, as always in this repo: registration case 11
and sync-mesh case 18 were written in case 4's detailed
format before any code, the five suites were run against
the unbuilt code (failures recorded), then the machinery
was extended - never replaced - along 019's shapes: the
anchor modules first (registry, then its mesh mirror), then
the consultation points (Gate 2 node, anchored sync node,
Gate 5 probes), then the server (constructor plumbing, the
delivery judgement, CLI flags, the `/health` field). Each
layer's suite was run green before the next layer was
touched. Status docs were amended where earlier experiments
amended their own: the two cases' Status lines, the four
package README test tables, experiment 019's
not-witnessed limit and experiment 028's finding statement
(each with a superseded-in-part note stating what this
experiment proved), and the module docstrings of every file
touched. Reusable process: when a finding names its own
closure, build exactly the named half, re-run the finding
itself as the central test rather than a paraphrase of it,
and state the result in the finding's own terms - including
what did NOT change.

## Next, per the build order

The remaining half of experiment 028's closure is an
asymmetric anchor signature, which needs a key model this
repo does not have yet (Gate 1 enrollment issues symmetric
secrets only) - a design question about what enrollment
MEANS, not a plumbing step, so it is recorded here as the
open item rather than built past. Behind it stand the
recorded gaps unchanged: a witnessed or external anchor
service beyond the local file, signer and witness rotation
and lost-secret recovery, anchor distribution beyond wire
delivery, certificate revocation and lifecycle, node
discovery, unattended drivers for the driven surfaces, mesh
state persistence, real multi-machine deployment, the
deliberate-removal case (availability case 3, still blocked
on its scope answers), PBA-FIX-001 (talk-first, unbuilt),
and the Gate 5 end-to-end 1 ms production benchmark (open,
not passed).
