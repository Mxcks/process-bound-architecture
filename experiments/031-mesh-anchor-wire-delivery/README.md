# 031. Mesh anchor delivery over the wire: a running server receives its mesh anchor

Experiment 028 gave a running server a surface that RECEIVES
its registry anchor over the wire, and experiment 030 a
server that pushes its own. Case 15's mesh anchor
(experiment 026) was left out of both: a server started
with `--mesh-anchor` reads a file placed by hand, and the
mesh anchor's lifecycle at it - the same-signer re-anchor
of case 11, the witness countersignature of case 18 - is
completed by a hand replacing the file, or not at all. The
grown state a sync refuses as `anchor_stale` (case 15's
central refusal) can be adopted only after that manual
act. This experiment closes the mesh half: a running
server gains one driven delivery surface for its mesh
anchor, `POST /deliver-mesh-anchor`, judged before
acceptance by the mesh side's own machinery, and case 15's
scenario is driven forward by a wire delivery instead of a
hand-placed file.

The case was written before the build, as sync-mesh
case 20, in the detailed format of cases 12-19, and the
tests were written against it before the implementation:
the tests-first run errors all 14 tests on exactly the
named absence (`node_server` has no attribute
`deliver_mesh_anchor`), and the pre-build full discovery
run shows the existing suites green underneath it
(registration 25, policy-library 42, synchronization's
existing 100, validation 22 - the baseline 189/189).
Real output for every claim below is in `test-output.txt`
in this folder.

## Question

Can a running node server, started with mesh-anchor
configuration, receive its mesh anchor over the wire -
the anchor object's own JSON, never a path - judged
before acceptance with the mesh side's own machinery
(experiment 015's consultation, 019's signed-anchor
rules, 029's witness rule where a mesh witness is
configured, in the judgement order those experiments
set), so that after an accepted delivery the behavior
changes for real: a sync that was refused `anchor_stale`
before the delivery converges after it, over two running
server processes, with no hand touching the server's
anchor file?

And the pairing question, answered in the case before
building: a mesh anchor is judged against the mesh state
the server HOLDS at delivery time, not against entries
delivered in the same call - mesh entries already have
their own receive path with its own consultation
(case 15). No paired form was built; the held-state
judgement is what this experiment proves.

## The design choices this experiment sets, stated plainly

- The surface mirrors case 17's exactly: `POST
  /deliver-mesh-anchor` carrying `{"anchor": {...}}`, a
  `deliver_mesh_anchor` client helper in `deliver_anchor`'s
  style, a `NetworkNode.deliver_mesh_anchor` method in
  `deliver_anchor`'s shape (candidate file beside the
  anchor, judged before application, one file replacement
  on acceptance, the candidate always cleaned up), and
  the last delivery's report on `/health` as
  `last_mesh_anchor_delivery`, beside `mesh_anchor_status`.
- The judgement reuses the mesh anchor machinery, never
  copies it: `node_server` gains one lazy loader,
  `_mesh_anchor_module()`, resolving this package's own
  `anchor.py` the way `anchored_sync.py` resolves it, and
  the delivery is judged by that module's reader,
  signature verification, witness verification, audit
  log, and check. The registry loader (`_registry_anchor`)
  is a different package's module and is not used here.
- The judgement order is case 20's, which is the order
  the earlier experiments set: configuration
  (`no_mesh_anchor`); the delivered body must read as a
  mesh anchor; the CURRENT anchor must read
  (`anchor_unreadable`, case 15's name); byte-identical is
  `no_change`; succession `different_signer` (case 11's
  same-identity rule, audit-logged in case 11's event
  shape with `"chain": "sync-mesh"`); the backward fence
  `anchor_regression` in two shapes - (i) the delivered
  kept history is a proper prefix of the current anchor's
  (an earlier version of this very anchor file), and
  (ii) the delivered anchor pins an earlier prefix of the
  chain the current anchor pins, PROVED against the held
  entries by an exact subset proof (the held entries at
  or below the delivered heads are exactly the delivered
  entry set: same count, same recomputed state hash, same
  heads), so the fence fires only on what the held
  entries can prove and a foreign chain's head falls
  through to the head judgement; signature and kept
  history (`anchor_signature_invalid`); the witness
  countersignature where a mesh witness is configured
  (`anchor_witness_invalid`); then the head judgement
  against the HELD mesh state with case 8's check.
- The head judgement accepts in exactly two standings:
  the delivered anchor pins exactly the held state (the
  check is ok - the catch-up delivery), or it is the
  current anchor's SUCCESSOR IN ITS OWN KEPT LINEAGE (its
  kept history has the current anchor's as a proper
  prefix) and the held state does not already stand
  honestly past it. The successor may pin a state the
  server does not hold yet: that is the re-anchor
  lifecycle arriving by wire, and every later receive is
  judged against the new anchor by case 8's consultation,
  exactly as against a hand-placed re-anchor. Honest
  growth held past the delivered anchor refuses as
  `anchor_stale` (the same exact subset proof, run the
  other way); anything else refuses as `anchor_mismatch`.
  An unsigned delivered anchor has no history, so it can
  only be accepted in the exact-pins-held standing.
- Application replaces the mesh anchor FILE only
  (`os.replace` from the verified candidate). The mesh
  state is process memory and is not the delivery's to
  change; the standing `/health` reports afterward is the
  new anchor's verdict on the held state, read fresh by
  the `AnchoredSyncNode` consultation. Every refusal
  leaves the mesh anchor file byte-for-byte unchanged,
  the held state untouched, and sync behavior exactly as
  it was.
- Servers started without mesh-anchor configuration
  report `no_mesh_anchor` and behave exactly as today;
  the registry anchor surfaces (028/030) are untouched;
  all earlier suites pass unmodified.

## Result: all case 20 outcomes observed, 14 new tests pass, full regression green

- The core story, over two running server processes:
  converged and anchored, the honest peer grows, the sync
  refuses `anchor_stale`; the successor anchor (alice, the
  current anchor's kept history extended by case 11's own
  write path) is DELIVERED; the anchor file at the server
  is byte-for-byte the delivered successor; the same sync
  then converges, and `/health` reports `anchored` with
  the delivery's report as `last_mesh_anchor_delivery`.
  No hand placed the file. The middle standing is case
  15's own: between delivery and convergence the held
  state classifies `anchor_mismatch` against the advanced
  anchor - the anchor moved first, the state catches up
  by sync, in that order.
- The catch-up standing: a server's own mutations never
  consult, so its standing can lag its own anchor; an
  anchor pinning exactly the grown held state is delivered
  onto it and the standing becomes `anchored` with no
  sync. A byte-identical delivery is `no_change` and
  rewrites nothing.
- The refusals, each with the mesh anchor file
  byte-for-byte unchanged and sync behavior undisturbed:
  a foreign chain's anchor in a FRESH lineage (the same
  signer, a first anchor over a different chain) is
  `anchor_mismatch`; a different signer's anchor over the
  same state is `different_signer`, audit-logged beside
  the mesh anchor; a tampered kept history is
  `anchor_signature_invalid`; an earlier version of the
  server's own anchor file is `anchor_regression`
  (shape i); an unsigned anchor pinning an earlier prefix
  of the chain a head-only server's anchor pins is
  `anchor_regression` too (shape ii) - the fence's second
  shape proved on the server shape where the first
  cannot fire; an anchor over an intermediate state the
  held state already stands honestly past is
  `anchor_stale`; a hand-corrupted current anchor refuses
  the delivery as `anchor_unreadable`, `/health` still
  answering, and restoring the genuine file restores
  normal delivery.
- The witness fence at delivery (case 18 at this
  surface): at a server configured with mesh witness
  carol, a signer-alone successor and a successor
  countersigned by bob are both `anchor_witness_invalid`;
  carol's countersigned successor is delivered and the
  sync it unblocks converges.
- The full flow over experiment 014's protected
  transport: the successor anchor is delivered over
  mutual TLS and the stale-refused sync converges over
  the protected link.
- The configuration edge: a server started without
  `--mesh-anchor` reports `no_mesh_anchor`, over the wire
  and in-process alike, and is otherwise undisturbed.
- Full regression: registration 25, policy-library 42,
  synchronization 114 (the existing 100 plus this
  experiment's 14), validation 22 - 203 tests, all
  passing; the execution-bound identity handoff check
  passes 3/3; no `node_server` processes left running
  after any run.

## The lineage finding, stated exactly as the evidence shows it

A successor anchor over experiment 012's REWRITTEN mesh
chain - the same keys with different values, the same
length, internally consistent - written from the server's
CURRENT anchor file by case 11's own write path with the
CURRENT signer's secret (alice's enrollment secret, which
every verifier of a signed anchor legitimately holds in
experiment 019's symmetric model: the secret that verifies
is the secret that signs), is ACCEPTED at this surface:
succession passes (same signer), the backward fence
passes (its kept history honestly extends the current
anchor's; it pins no earlier prefix), the signature
verifies (the history is honestly extended and honestly
signed). The delivery stands, the server's anchor file
becomes the rewrite's successor anchor, its held genuine
state classifies `anchor_mismatch` against it, and its
next honest sync is refused under that name.

This is case 17's finding restated for the mesh half,
with the same shape and the same fence: the wire adds no
weakness, because a hand-placed successor anchor is
accepted by the same consultation (case 15 never sees how
the file arrived), and what stops a rewrite from being
blessed at this surface is the custody of the signer's
secret, and nothing else. At a witness-configured server
the fence is two secrets' custody (case 18's rule,
re-proven at this surface above); the model stays
symmetric. The asymmetric half of the closure still needs
a key model Gate 1 enrollment does not have, exactly as
experiment 028 recorded for the registry half.

## How the mechanism works, in plain words

Before this experiment, a mesh-anchored server's anchor
was a file an operator placed by hand: the server read it
at startup, consulted it on every receive, and when the
mesh honestly outgrew it the only way forward was another
hand placing a newer file. Now the newer file can arrive
the way the registry's does: a client posts the anchor
object itself to the running server, the server writes it
to a scratch candidate beside the real anchor and puts it
through every judgement the mesh side already knows -
whose signature, whose countersignature, whether it
points backward, whether its own kept record of itself is
honest, and finally what it claims about the state this
server actually holds - and only a delivery that passes
all of them replaces the anchor file. The state itself is
never the delivery's to touch: the anchor moves first,
the state catches up by the ordinary sync, and the
consultation judges that catch-up against the new anchor
exactly as it judged everything before it.

## What failed

- The first post-build run of the new suite failed one
  test on an expectation error IN THE TEST, not the code:
  the unreadable-anchor test built its successor anchor
  over the peer's UNGROWN state, so the post-delivery
  standing it asserted (`anchor_mismatch`) was wrong -
  the anchor pinned exactly the held state and `anchored`
  was the correct verdict. The test was fixed to grow the
  peer first (the scenario the case describes), and one
  harness call in that fix used a method name the copied
  harness does not have; both were test-side, both fixed,
  and the suite then ran 14/14 clean, twice, plus once
  inside the full discovery run. No implementation change
  came out of either failure.
- A pre-build baseline capture was re-run while the
  implementation edits were landing, so its synchronization
  discovery mixed the new suite against a half-built
  server (114 ran, the new 14 erroring). The clean numbers
  stand on the runs recorded in `test-output.txt`: the
  tests-first run against the untouched code, and the
  post-build full regression run with nothing else in
  flight.

## Honest limits (this is a prototype, not production)

- The delivery is driven, never unattended or solicited:
  a client call fires it, as with every delivery surface
  in this repo. No timer, scheduler, gossip, or discovery
  inside the server fires one, nothing retries one, and a
  server nobody delivers to keeps the anchor it started
  with, exactly as stale as experiment 026 left it.
- There is no server-driven mesh anchor push: no server
  SENDS its mesh anchor onward in this experiment (the
  registry half gained its sender in experiment 030, one
  experiment after its receiving surface in 028; the mesh
  half's sender is the same shape of remaining work, not
  built here). The sending end of this surface is a
  client.
- No pairing, by the case's stated decision: the anchor
  is judged against the state the server holds, never
  against entries in the same call. A delivery for a
  state the server has not reached yet stands only as a
  lineage successor, and the state catches up by sync
  under the new anchor's consultation - there is no
  atomic anchor-plus-entries step on the mesh side, and
  the intermediate standing (anchor ahead of state,
  classified `anchor_mismatch` on `/health`) is a real,
  observable window, exactly as a hand-placed re-anchor
  leaves it.
- The backward fence's second shape fires only on what
  the held entries can prove: a delivered anchor pinning
  an earlier prefix of a chain the server does NOT hold
  falls through to the head judgement and is refused
  there as `anchor_mismatch`, not under the backward
  name. Stated in the case, proved by the foreign-chain
  leg.
- The lineage finding above is a limit, not a footnote:
  under the symmetric model, whoever holds the signer's
  secret (and the witness's, where configured) can bless
  a rewrite at this surface. Custody is the fence.
- The anchor remains a local file even now that it can
  arrive over the wire: an operator can still replace or
  roll it back by hand, nothing here holds the head where
  the mesh state's writer cannot reach it, and there is
  still no witnessed or external anchor service.
- Localhost prototype transport and identity, unchanged
  from experiments 007-030: no discovery, no persistence
  of mesh state beyond process memory, no certificate
  revocation or lifecycle, no signer or witness rotation
  or lost-secret recovery, and no production timing claim
  of any kind.

## Process captured

The reusable process: when one half of a paired concept
gains a wire surface, the other half's version is built
as a MIRROR, not a port - same endpoint shape, report
shape, refusal discipline, and tests-first order, but
every judgement delegated to the second half's OWN
machinery (here the mesh anchor module, loaded the way
its own package resolves it), and the one genuine design
question the mirror raises (pairing) decided in the case
before building, with the reason recorded: mesh entries
already had a consulted receive path, so the anchor is
judged against held state, full stop. The finding
discipline also mirrored: case 17's three-leg finding was
re-run in this half's shape rather than assumed to carry
over, and it carried over exactly, which is itself the
evidence that the fence is the key model and not either
surface. Opportunity Stack entry: both anchors now travel
to a running server; the wire lifecycle's remaining named
gaps are the mesh anchor's server-driven push and
unattended drivers for surfaces that are all still
driven. What would differ next time: when a test fails on
first run, check whether the scenario the test BUILT is
the scenario the case describes before touching the
implementation - here the one failure was the test
building the successor over an ungrown state, and the
implementation was right.

## Next, per the build order

The mesh anchor's server-driven push (a `POST
/push-mesh-anchor` in case 19's shape, sending this
process's own mesh anchor to a peer's surface built
here), and unattended drivers for the surfaces that are
all still driven. The build order otherwise continues to
the items the earlier experiments recorded: real
multi-machine deployment, certificate revocation and
lifecycle, node discovery beyond set addresses, the
Gate 5 production-scale timing benchmark, the asymmetric
half of the closure the finding names, and availability
case 3's deliberate-removal semantics, which remain
blocked on the scope decision recorded in experiment 011.
