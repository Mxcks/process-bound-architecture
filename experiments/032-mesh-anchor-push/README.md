# 032. Server-to-server mesh anchor push: one server sends its mesh anchor to a peer

Experiment 028 gave a running server a surface that
RECEIVES its registry anchor over the wire, experiment 030
a server that pushes its own, and experiment 031 the mesh
anchor's receiving surface, `POST /deliver-mesh-anchor`.
The mesh half was left where the registry half stood
between 028 and 030: the sending end of the mesh anchor's
surface was a client, so advancing a peer's mesh anchor
from the one kind of process that holds a mesh anchor of
its own still took a credential-holding client in the
middle, or a hand at the peer. This experiment closes the
sender's half for the mesh anchor: a running server gains
one driven push surface, `POST /push-mesh-anchor`, in
case 19's shape, sending the anchor object its own mesh
anchor file holds to a peer's `POST /deliver-mesh-anchor`
- experiment 031's surface, unmodified - and the peer's
delivery report is the push's result.

The case was written before the build, as sync-mesh
case 21, in the detailed format of cases 12-20, and the
tests were written against it before the implementation:
the tests-first run errors all 10 tests on exactly the
named absences (nine on `node_server` has no attribute
`push_mesh_anchor`, the tenth on the missing
`last_mesh_anchor_push` health field), and the pre-build
full discovery run shows the existing suites green
underneath it (registration 25, policy-library 42,
synchronization 114, validation 22 - the baseline
203/203, plus the EAI handoff check 3/3). Real output for
every claim below is in `test-output.txt` in this folder.

## Question

Can a running node server push its own mesh anchor to a
peer server - the anchor object's own JSON, read from the
file the sender itself is judged by - so that the peer
judges it under exactly experiment 031's rules,
unmodified, and after an accepted push the behavior
changes for real: a sync the peer was refused
`anchor_stale` converges after ONE push, over two running
server processes, with no restart, no client driving the
peer, and no hand touching the peer's mesh anchor file?

## The design choices this experiment sets, stated plainly

- The surface mirrors cases 16/19 exactly: `POST
  /push-mesh-anchor` carrying `{"peer": "<base URL>"}`, a
  `push_mesh_anchor` client helper in `push_anchor`'s
  style returning the (status, body) pair, a
  `_push_mesh_anchor_to_peer` handler method in
  `_push_anchor_to_peer`'s shape, and the last push's
  outcome on `/health` as `last_mesh_anchor_push`, beside
  `last_mesh_anchor_delivery`.
- What is pushed is the anchor object the sender's own
  MESH anchor file holds, read from that file under the
  node lock and sent as the parsed object, never as a
  path. For the mesh anchor the file IS the standing,
  exactly as case 19 set it for the registry anchor: the
  process's `AnchoredSyncNode` consults that very file on
  every mesh receive, so the file and the standing agree
  by construction. The sender re-derives no verdict and
  judges nothing itself.
- The receiving side is experiment 031's
  `deliver_mesh_anchor` judgement, UNMODIFIED: this
  experiment adds no judgement, no refusal name, and no
  acceptance standing to the mesh anchor's wire rules.
  The peer's delivery report - `delivered`,
  `delivery_refused` with case 20's reason, `no_change` -
  is the push's result, reported, not re-derived.
- The sender's own shapes, stated in the case before the
  build: no mesh anchor configured is 200
  `no_mesh_anchor` with `delivery` null and no contact
  with the peer (case 20's own name for the mesh half's
  configuration edge; the mesh push has no record-only
  fallback and no second form, because case 20's surface
  has no paired form for it to degrade into); its own
  mesh anchor file unreadable or unparseable at push
  time is 502 `own anchor unreadable`, recorded, the
  peer never contacted.
- Refusals that never reach judgement mirror cases
  16/19: the initiating-side identity check first where
  binding is on; a peer whose transport refuses the
  sender before the delivery endpoint runs is reported
  on 502 with the claimed and certified ids carried
  through; an unreachable peer is the third 502, in
  `/sync`'s wording.

## Result: all case 21 outcomes observed, 10 new tests pass, full regression green

- The core story, over two running server processes:
  both servers stand on the same converged mesh state
  under the same alice-signed mesh anchor, each
  consulting its own copy; the mesh grows honestly at
  the SENDER; the peer's sync toward the sender refuses
  `anchor_stale` and the peer keeps exactly the anchored
  state; the sender's mesh anchor is advanced by the
  proper lifecycle AT THE SENDER (alice re-anchors the
  grown head from the sender's own anchor file, its kept
  history extended); ONE push reports `delivered` (the
  peer's delivery report: counts 3 -> 4, history
  sequence 0 -> 1, signer alice); the peer's mesh anchor
  file ends byte-for-byte the anchor the sender sent,
  which is byte-for-byte the sender's own anchor file;
  the sender's `/health` records the push as
  `last_mesh_anchor_push`, the peer's records the
  delivery as `last_mesh_anchor_delivery` and classifies
  its not-yet-converged held state `anchor_mismatch` -
  the anchor moved first, the state catches up by sync,
  in that order, as in case 20 - and the same sync then
  converges, both servers `anchored`. No restart, no
  client driving the peer, no hand at the peer's file.
  A second identical push is `no_change` and rewrites
  nothing.
- The refusals, each reported in the push result under
  case 20's own name, each leaving the peer's mesh
  anchor file byte-for-byte unchanged and its mesh state
  untouched: a sender whose mesh anchor is signed by bob
  pushing to an alice-anchored peer is `different_signer`,
  the refusal audit-logged beside the peer's mesh anchor
  in case 11's shape for the mesh chain; a sender holding
  an earlier version of the lineage pushing to a peer
  standing on a later version of the same lineage over
  the same head (the peer's anchor refreshed alone under
  case 20 first) is `anchor_regression`, the peer
  keeping the later anchor byte-for-byte; the sender's
  hand-tampered anchor file (one kept-history entry's
  recorded state hash edited, entry hashes left standing)
  is `anchor_signature_invalid` at the peer; a foreign
  chain's anchor in a fresh lineage, pushed by a server
  standing under it, is `anchor_mismatch` at the head
  judgement.
- The witness fence across servers (case 18's
  discipline, carried to the mesh half): both servers
  stand under the same carol-countersigned anchor; the
  peer is mesh-witness-configured, the sender is not.
  The sender advances its anchor signer-alone - a
  process that is not witness-configured writes no
  countersignature - and the push is refused
  `anchor_witness_invalid`, the peer unchanged. Once the
  sender's anchor over the same grown head is
  countersigned by carol AT THE SENDER, the same push
  delivers, the peer's anchor file is byte-for-byte the
  sender's, and the peer's sync converges.
- The sender's own shapes: a pushing process with no
  mesh anchor configured answers 200 `no_mesh_anchor`,
  recorded on its `/health`, and the peer records no
  mesh anchor delivery; a sender whose mesh anchor file
  was deleted after startup answers 502 `own anchor
  unreadable`, recorded, and the peer is never contacted
  (its `last_mesh_anchor_delivery` stays null).
- The full story over experiment 014's protected
  transport with experiment 017's identity binding on and
  a signed mesh anchor: stale-refused sync, ONE push over
  HTTPS delivered, the same sync converged over the
  protected link, both `/health` surfaces reporting the
  new standing. And a pusher in experiment 014 mode
  (mutual TLS, binding OFF) claiming an id its
  certificate was not issued for, holding a mesh anchor
  of its own so the push is what it attempts, is refused
  at the binding peer's transport: the push reports 502
  `identity_mismatch` naming claimed and certified, the
  peer records no mesh anchor delivery, and the refusal
  appears in the peer's log.
- Full regression: registration 25, policy-library 42,
  synchronization 124 (the existing 114 plus this
  experiment's 10), validation 22 - 213 tests, all
  passing; the execution-bound identity handoff check
  passes 3/3; no `node_server` processes left running
  after any run.

## The lineage finding at this surface, stated exactly as the evidence shows it

Case 20's finding repeats for the mesh half's sender,
by the same construction: a successor mesh anchor over a
REWRITTEN mesh chain, written from the current anchor
file with the current signer's secret and its history
honestly extended, is an anchor this push carries and
the peer's unmodified experiment 031 judgement accepts -
experiment 031 proved the acceptance for a client's
delivery, and the push changes only who sends, never
what is judged. A sender legitimately holding the
signer's secret can push an anchor blessing a rewrite
and the peer accepts it exactly as it accepted a
hand-placed successor before any wire existed. Custody
of that one secret is the whole fence at this surface
too; at a witness-configured peer the fence is two
secrets' custody (experiment 029), and the asymmetric
half of the closure remains unbuilt, as it was. This
experiment claims no new refusal for the finding's
shape: the foreign-anchor leg above refuses only an
anchor that succeeds nothing and pairs with nothing.

## How the mechanism works, in plain words

Two servers each keep a signed note - the mesh anchor -
saying which mesh state they trust, and each checks that
note on every sync. When the mesh grows at one server,
the other server's note goes stale and its sync is
refused. Before this experiment, fixing the stale note
meant a person or a separate client program carrying the
new note to the refused server. Now the grown server can
hand its own updated note directly to the other server:
it reads the note out of the file it is itself judged
by, sends the note's contents over the wire, and the
receiving server inspects the note with exactly the same
checks it already applied to notes delivered by a client
- right signer, not an older note, signature sound,
witness's countersignature where one is required, and a
note that belongs to the chain of notes it already
trusts. If the note passes, the receiving server files
it in place of its old one, and the sync that was refused
now works. The sending server decides nothing about
whether its note is good; the receiving server's
judgement is the whole decision, and its verdict -
acceptance or the refusal's own name - is what the push
reports back.

## What failed

- Nothing in the build failed on its first run: the new
  suite passed 10/10 against the implementation as
  written, and the demo ran end to end. Two blemishes
  are recorded rather than smoothed over. First, the
  pre-build baseline's first synchronization count run,
  executed under load, errored one test (030's
  `test_paired_push`) on the known port-file race in that
  suite's `wait_ready` helper, which has no retry guard;
  the error is recorded verbatim in `test-output.txt`
  and a clean re-run passed 114/114, which is the
  baseline of record. This experiment's own helper
  carries experiment 031's retry guard and did not race
  in any run. Second, the case's first witness-leg draft
  described the two servers starting from DIFFERENT
  anchor files (the peer's countersigned, the sender's
  signer-alone over the same head); reading experiment
  031's judgement code before writing the tests showed
  that geometry cannot be accepted after countersigning,
  because the lineage standing compares kept-history
  entries in full and the two files' first entries would
  differ. Case 21's witness bullet was rewritten before
  the build to the shared-lineage geometry the judgement
  actually accepts - both servers under the same
  countersigned anchor, the SENDER's advance being what
  starts signer-alone - and that is the geometry the
  tests prove. The case text in the repo is the
  corrected text; the correction happened before any
  code, and is recorded here because the first draft
  exists in this session's history, not in the repo.

## Honest limits (this is a prototype, not production)

- The push is driven, never unattended or solicited: a
  client call on the sender fires it, as with every push
  surface in this repo. No timer, scheduler, gossip, or
  discovery inside either server fires one, nothing
  retries one, and a peer nobody pushes to stays exactly
  as stale as experiment 031 left it.
- The push transports the sender's mesh anchor only,
  judged against the peer's HELD state with no pairing
  (case 20's stated decision, inherited): after a
  delivered push the peer's anchor can stand ahead of
  its held state, and its `/health` classifies that
  standing `anchor_mismatch` until a sync catches the
  state up - proved above as the middle standing, in
  that order, not hidden.
- The push performs no lifecycle step for the peer:
  succession, freshness, and countersignature are the
  sender's completed facts. A sender that has not
  completed its lifecycle gets the peer's refusal in the
  push result, and nothing at the peer fixes it.
- There is no per-endpoint authorization beyond the
  transport: anyone who can drive `POST /push-mesh-anchor`
  on a server can ask it to push its anchor to any
  address; the peer's judgement is the fence on what
  lands, and on plaintext localhost there is no fence on
  who may ask. Mutual TLS with identity binding
  authenticates the pusher's credential and binds it to
  its claimed id, as in experiments 014/017, and no
  further.
- The symmetric-model lineage finding stands at this
  surface (stated in its own section above): the fence
  is the custody of the signer's secret, and of the
  witness's too where one is configured. No signer or
  witness rotation or recovery exists on either chain.
- Localhost one-machine prototype throughout: peer
  addresses are set, not discovered; mesh state lives in
  process memory only; the mesh anchor remains a local
  file an operator can replace or roll back by hand;
  there is no witnessed or external anchor service. Real
  multi-machine deployment, real PKI and identity
  proofing, and certificate revocation remain unbuilt,
  as every earlier experiment recorded.

## Process captured

The reusable process this build adds to the ones 030 and
031 recorded: closing a client-driven surface's sender
half is a MIRROR build, not a new design - the sender
copies the previous sender build's shapes exactly
(surface, report, refusal family, health field), the
receiver stays byte-for-byte unmodified, and the only
new judgement is none. The step that earned its place
here happened before any code: READ THE RECEIVER'S
JUDGEMENT CODE before fixing the test geometry, because
the acceptance standings are exact (the lineage standing
compares kept-history entries in full) and a plausible
scenario - the witness leg's first draft - can be
unprovable as drawn while sounding identical in prose.
The control discipline is the one 030 named: the new
path is proved against the old state IN THE SAME SETUP,
the peer's sync refusing `anchor_stale` in the state the
single push then closes. Opportunity Stack entry: both
anchors now travel in both directions between servers -
deliver, push, consult - so the prototype's wire
lifecycle is complete for both halves of a peer's
currency; the named open wire work is unattended drivers
for surfaces that are all still driven, and the
asymmetric half of the closure the finding names. What
would differ next time: nothing about the captures -
this session kept every run capture under the workspace
from the start (the lesson 030 paid for), and no
evidence had to be reproduced.

## Next, per the build order

Unattended drivers for the surfaces that are all still
driven, and the items the earlier experiments recorded:
real multi-machine deployment, certificate revocation
and lifecycle, node discovery beyond set addresses, the
Gate 5 production-scale timing benchmark, the asymmetric
half of the closure the finding names, and availability
case 3's deliberate-removal semantics, which remain
blocked on the scope decision recorded in experiment 011.
