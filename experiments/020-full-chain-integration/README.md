# 020. Full-chain integration: the whole chain in one scenario

Date: 2026-11-06
Authorization: Max's standing authorization for the entire
Process-Bound Architecture (2026-11-06); the gates are Maren's
internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`.
Code: `packages/synchronization/test_full_chain_integration.py`
(the scenario suite) and
`packages/synchronization/demo_full_chain.py` (the same scenario
as a runnable demo), plus ONE wiring addition in
`packages/policy-library/replication.py`, named below. No other
mechanism's behavior was changed, no earlier test was modified,
and with the wiring's parameters omitted every earlier behavior
is unchanged (asserted by the unmodified earlier suites passing
in the same run: policy-library still 24 of 24). This experiment
does not touch `proposals/PBA-FIX-001.md` or implement any part
of it, and it does not work on deliberate removal (availability
case 3, experiment 011's blocked item).
Test case: `tests/test-cases/full-chain.md`, case 1, written
before the code was built, in the detailed format of
policy-availability case 4, including its finding clause stated
in advance. The new suite was run against the unbuilt code
first (two errors: `place_policy() got an unexpected keyword
argument 'anchor_path'` - the wiring did not exist yet), so the
case is proven to be what the wiring had to satisfy; that
failing output is recorded verbatim at the top of
`test-output.txt`. The two tests that passed in that same
pre-build run are properties of the pre-existing code, and one
of them is Finding F2 below - its passing before any build is
the evidence the finding is real.
Raw output: `test-output.txt` in this folder (the tests-first
failure, the pre-build baseline, the full regression across all
four tested packages, the new suite by direct file run, the
handoff check, the demo, and the leftover-process checks).

## The one code change, named as what it is

A pure wiring addition in `packages/policy-library/
replication.py`: `place_policy()`, `ReplicatedPolicy`, and
`heal()` gain optional `anchor_path` / `anchor_signer` /
`anchor_secret_hex` parameters, all defaulting to None, passed
through unchanged to the `PolicyNode` constructions placement
and healing already perform. Defaulting to None means no
consultation and exactly the pre-020 behavior. With them given,
a placed holder - and a holder `heal()` later recruits - is
constructed with the anchor consultation a standalone
PolicyNode already had (experiments 015/019). Before this
wiring, placement could not carry an anchor at all: Gate 4's
holders were the one serving surface with no way to consult
one. The suite's stage 3 is the first run of placement with
consultation on.

## Question

Every experiment before this one proved its layers singly or
in pairs. Nothing had ever run Max's whole original chain end
to end in one scenario over one policy: a user registers a
policy through the authenticated channel, the policy is placed
on a floor of holders, it is served from another node over a
protected and identity-bound link with anchor consultation on,
state syncs through the mesh, a holder is lost and healed, and
the author revokes the policy and serving stops. If the layers
do not compose, the pairwise passes do not show it. Do they?

## Result: the chain composes, with two findings - 4 new tests pass, full regression green

Run on Python 3.12.3, standard library only:

```
packages/registration     Ran 20 tests  OK   (unchanged)
packages/policy-library   Ran 24 tests  OK   (unchanged count, wiring defaults on)
packages/synchronization  Ran 41 tests  OK   (37 baseline + 4 new full-chain)
packages/validation       Ran 18 tests  OK   (unchanged)
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly
```

103 tests total (baseline before this experiment was 99), the
new suite also by direct file run, the demo run end to end, and
post-run process checks confirmed zero leftover `node_server`
processes after every run. All of it is quoted in
`test-output.txt`.

| # | Stage, in order | Where it is asserted | Result |
|---|---|---|---|
| 1 | Gate 1 registration (002): channel ready before use, alice registers, the execution boundary admits the policy, a validation channel is bound, the chain verifies | `test_full_chain_end_to_end`, stage 1 | PASS |
| 2 | Signed registry anchor (019): written by alice's enrolled identity outside the registry directory; the check verifies with `signature_checked` and `signature_valid` both true | stage 2 | PASS |
| 3 | Placement at floor 3 (005) with anchor consultation (015/019, via the wiring addition): placement report names holder-a/b/c, count 3, floor met, status available | stage 3 | PASS |
| 4 | Gate 2 serving from a second holder (003): a standalone holder-b PolicyNode, anchored, serves bytes whose author and record hash match the Gate 1 record exactly | stage 4 | PASS |
| 5 | Protected, identity-bound process serving (014, 017, 018/019, 007): two node-server processes over mutual TLS with `--tls-bind-identity` and the signed anchor at startup; srv-b's health reports `anchor_status anchored`; the policy fetched over HTTPS from srv-b carries the registered code and `served_by srv-b`; the two processes then sync mesh state over the same link and converge to one state hash, no divergence either side | stage 5 | PASS |
| 6 | Mesh sync and mesh-anchor consultation (004, 015/019): three in-process nodes mutate, exchange, and converge to one hash with no divergence; a signed mesh anchor is written over the converged state; an AnchoredSyncNode with that anchor and its signing identity accepts a merge whose merged state is exactly the anchored state and reports `anchored` | stage 6 | PASS |
| 7 | Liveness and automatic healing (016 over 010): holder-a silent for threshold-1 modeled beats is silent-short, still a holder, no healing; on the next tick it is declared lost and healing fires in the same check, recruiting replacement-d (constructed, through the wiring, with the same anchor consultation); floor restored; the placed policy still serves verified; coordinated state after healing: the mesh from stage 6 is still one converged hash on every node | stage 7 | PASS |
| 8 | Author revocation stops serving (008): alice revokes (author-only, new entry in the same chain); the boundary refuses as BlockedRevoked and the validation channel is no longer honored. Delivery follows the anchor lifecycle: reloading the grown snapshot before re-anchoring is refused as AnchorStale and the holder's previous view still serves (the stale window, composed); after re-anchoring by the SAME signer, the reload stands (2 entries), and the holder refuses as PolicyRevoked. Final state on the surfaces that can receive the record: a fresh node over the updated, re-anchored snapshot refuses, and a placement rebuilt over it refuses | stage 8 | PASS, with Findings F1/F2 on the two surfaces below |
| 9 | Gate 5 verdict (018/019 pattern): a harness scenario with SIGNED anchors on both chains passes, both anchor checks additional and anchored; the same verdict over a registry rewritten to pass every internal check fails, with `anchor_evidence_registry` the only failing check, named `anchor_mismatch` | `test_gate5_verdict_genuine_passes_rewritten_fails` | PASS |

## Findings (successful outcomes of this experiment, not failures to hide)

### F1. Revocation cannot be delivered to a PLACED policy through its public API

`test_finding_placed_holders_cannot_be_reloaded` asserts the
finding exactly as found: `ReplicatedPolicy` exposes no
reload/update method (`hasattr` check for `reload_snapshot`,
`reload`, `update_snapshot`: all False), and after the author
revokes, the grown registry is re-anchored by the same signer,
and the updated record is copied over the placement's own
shared snapshot file, the placed object STILL SERVES the
revoked policy (`replicated.serve()` returns the registered
code). Evidence line, printed by both the suite and the demo:

```
FINDING F1 placed-holders-reload: ReplicatedPolicy exposes a reload/update method: False; after revocation + delivery over its snapshot file, the placed object still serves the revoked policy: True
```

This confirms, by direct assertion at integration scale, the
limit `replication.py`'s docstring and experiment 008's README
already stated in words (holders are constructed once and never
reload). Its composition consequence is new: stages 3 and 7 of
the chain run ON a placed policy, so the chain's final state
cannot be reached on that object - revocation stops serving
only on surfaces that can receive the updated record (a
standalone PolicyNode via `reload_snapshot`, a rebuilt
placement, a restarted server). A future fix is a design
choice (a reload method on ReplicatedPolicy that re-verifies
and re-consults per holder), not a wiring parameter, so under
this experiment's rules it is recorded, not built.

### F2. A revoked policy requested over the network transport drops the connection

`test_finding_revoked_policy_over_network_transport`: srv-b
serves the policy over the protected link (200, code matching);
the author revokes, the registry is re-anchored by the same
signer, the updated snapshot is delivered to the server's own
copy, and the process is RESTARTED over it. The restarted
server starts cleanly and reports `anchor_status anchored` -
and a policy request then produces NO HTTP response at all:

```
FINDING F2 revoked-over-network: policy request after revocation produced: exception:RemoteDisconnected Remote end closed connection without response; server log names PolicyRevoked: True
```

The cause is visible in `node_server.py`: `_serve_policy`
catches `NotServing`, `PolicyNotFound`, and `TamperedRecord`,
and `PolicyRevoked` - a sibling `PolicyLibraryError`, not a
subclass of any of those - is not caught, so it propagates out
of the handler and the connection closes. The policy bytes are
NOT served (the safety property holds), but no client can
distinguish "revoked" from "server died", and the refusal
exists only as a traceback in the server's log. No earlier
suite ever asked a server process for a revoked policy;
experiments 007/014/017/018/019 exercised the endpoint only for
live policies, opt-in refusals, unknown hashes, and tamper.
The fix (catch PolicyRevoked and map it to an HTTP refusal
naming the revocation) changes an existing mechanism's
behavior, so under this experiment's rules it is recorded,
not built. Experiment 008's README carries a
superseded-in-part note for its restart sentence, added with
this experiment.

## What failed

The tests-first run failed as designed (recorded at the top of
`test-output.txt`): the two tests that use placement errored on
the not-yet-built wiring. One further failure during the build,
worth recording: the suite's first attempt died at import,
before any test ran - its `sys.path` insertions put the
registration package ahead of synchronization, so the plain
module name `anchor` (which `anchored_sync.py` imports)
resolved to the registration package's `anchor.py` and
`MeshAnchorError` could not be imported. This is experiment
019's shared-name lesson in its fourth form (module, package
file, test file, and now import ORDER in a cross-package
suite): the fix is to insert the packages in reverse so
synchronization ends at the front, exactly as
`gate5_harness.py`'s own path setup arranges. After that fix
and the wiring addition, nothing failed: the new suite, the
full regression, the handoff check, and the demo all passed on
the runs quoted in `test-output.txt`, and no earlier test was
modified.

## Honest limits (this is a prototype, not production)

- The chain composes at prototype scale on one machine:
  localhost processes, in-process objects, temp files. Nothing
  here is a real multi-machine run, and no stage's inherited
  limits move: HMAC prototype signing, unsigned-by-witness
  anchors, no consensus, no discovery, no persistence.
- Findings F1 and F2 stand open and are the honest distance
  this experiment adds to the record: revocation - the chain's
  last step - does not reach a placed policy through its own
  API at all (F1), and reaches a network client only as a
  dropped connection (F2). "Serving stops after revocation"
  is true today only per surface, as stage 8's table row
  states it: standalone nodes that reload, rebuilt placements,
  and restarted servers (with F2's shape), not the placed
  object the chain itself built.
- The networked processes' mesh side still consults no mesh
  anchor (their sync node is a plain SyncNode, as experiments
  018/019 stated): stage 6's mesh-anchor consultation is
  in-process, and stage 5's network sync is Gate 3 verification
  only. The two mesh halves of the chain have still never run
  in the same process.
- Anchor consultation makes honest growth a manual lifecycle
  step everywhere it is on, and the chain inherits that in
  full: the revocation entry itself is honest growth, so
  delivering a revocation to any anchored surface requires a
  same-signer re-anchor first (stage 8 asserts the stale
  refusal on the way). Nothing automates re-anchoring or
  decides who may hold the signer's secret in production.
- The Gate 5 stage (stage 9) runs over a Gate5Harness
  scenario's evidence, as in experiments 018/019, not over
  this scenario's own nodes: the harness builds its own
  registry, serving node, and mesh, and there is no existing
  mechanism that runs a Gate 5 verdict over an arbitrary
  scenario's objects. The verdict therefore proves the gate
  composes with signed anchors, in the same run as the chain,
  not that it judged this chain's exact entries.
- Liveness remains the modeled clock of experiment 016: beats
  are ticks the test calls, no real heartbeat transport or
  machine-death detection is claimed, and the lost holder in
  stage 7 is lost to the watcher, not a killed process (the
  killed-process case is experiment 007's, over different
  objects than the placed holders).
- No performance, scale, or latency numbers were measured or
  are claimed for the composed chain.

## Process captured

Reusable process: a full-chain integration experiment earns
its keep in the stages that were never paired before, so its
test case named the two riskiest composition surfaces in
advance and gave them their own finding tests, run BEFORE any
new code - and one of them (F2) passed pre-build, which is the
cleanest evidence a finding can have: it cannot be an artifact
of the experiment's own changes. Second lesson: when a chain
stage's final state is claimed, claim it per surface and
assert it per surface; the unqualified sentence "serving
stops" was true on three surfaces and false on the chain's
own placed object, and only the per-surface assertions kept
that visible. Opportunity Stack entry: extends the
composition clause (a system is its layers composed, not its
layers listed: any property claimed of the whole - here,
"revoked means unservable" - is only as true as its weakest
surface, and an integration run that cannot name a finding
has usually not run the surfaces that were never paired).
What would differ next time: give the integration suite's
cross-package import order one explicit check at the top of
the file (assert the loaded `anchor` module is the intended
package's), so the fourth form of the shared-name failure
fails as a named assertion instead of an import traceback.

## Next, per the build order

The whole chain now has one composed run in the record, and
its two findings are the next honest items, ahead of the
longer list experiments 012-019 already carried (deliberate
removal, blocked on Max in experiment 011; a witnessed anchor;
production crypto and identity proofing; consensus;
discovery; persistence): F1, a revocation-delivery path for
placed policies (a reload on ReplicatedPolicy, designed, not
wired in silently), and F2, a clean revoked refusal on the
network transport (catch PolicyRevoked in `_serve_policy` and
map it to an HTTP refusal naming the revocation). Both are
small; both change existing behavior, so both wait for their
own experiment and their own tests-first run. `PBA-FIX-001`
remains talk-first and untouched by this experiment. Nothing
here is deployed beyond the localhost test processes the tests
themselves spawned and cleaned up.
