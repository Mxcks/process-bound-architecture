# 026. Mesh-anchor consultation over the wire: server processes run the anchored node

Date: 2026-11-06
Authorization: <redacted>
Process-Bound Architecture (2026-11-06); the gates are Maren's
internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`.
Code: `packages/synchronization/` (`node_server.py` gains a
mesh-anchor flag family - `--mesh-anchor`,
`--mesh-anchor-signer`, `--mesh-anchor-secret-file` - under
which the process's mesh side is experiment 015's
`AnchoredSyncNode` instead of a plain `SyncNode`, one
`NetworkNode.receive_mesh_entries` receive that every mesh
surface uses, and two `/health` fields; new
`test_mesh_anchor_wire.py`, new `demo_mesh_anchor_wire.py`).
No mechanism was rebuilt: the consultation is experiment
015's, reused unmodified - `AnchoredSyncNode`'s receive,
`anchor.py`'s check, and the 015/019 refusal vocabulary are
exactly the in-process ones, because the server's mesh
surfaces (POST /entries, both sides of POST /sync) already
call `receive_entries`; swapping the node class is the whole
wiring. One wire adaptation is new and is named in the case
itself: where the in-process consultation raises on an
unreadable configured anchor, the wire receive returns the
refusal in the SyncResult shape as `anchor_unreadable`.
`packages/synchronization/README.md` gained the two table
rows and the `node_server.py` row's extension; the
in-process-only limit statements in experiments 020, 024,
and 025's READMEs and in `node_server.py`'s,
`anchored_sync.py`'s, and `anchor.py`'s docstrings carry
superseded-in-part notes pointing here. This experiment
does not touch `proposals/PBA-FIX-001.md` or implement any
part of it, and it does not work on deliberate removal
(availability case 3, experiment 011's blocked item).
Test case: `tests/test-cases/sync-mesh.md`, case 15. The
case did not exist before this experiment; it was added to
that file, in the detailed format of cases 12/13/14, before
the code was built, with every outcome below - including
the at-rest classification, the negative control, the
unreadable-anchor adaptation, and the unchanged
no-anchor behavior - stated in the case itself. The new
suite was run against the unbuilt code first (seven
failures, each on exactly the absence the case names: the
server exiting on argparse's "unrecognized arguments:
--mesh-anchor"), so the case is proven to be what the code
had to satisfy; that failing output is recorded verbatim at
the top of `test-output.txt`.
Raw output: `test-output.txt` in this folder (the
tests-first failure, the pre-build baseline, the full
regression across all four tested packages - including the
first synchronization discovery's load-race errors and the
clean re-run that supersedes it, both recorded - the new
suite by direct file run, the new demo, and the
leftover-process checks).

## Question

Experiment 015 built anchor consultation for Gate 3 sync,
and experiment 019 added signed mesh anchors to it - but
only between in-process objects. The server processes of
experiments 007/014 hold a plain `SyncNode` for their mesh
side and consult no mesh anchor, so the one peer shape the
anchor exists to catch - an origin chain rewritten from
genesis, internally consistent, never seen in its original
form by the victim - is flagged divergent by an in-process
anchored node and accepted without that check by a running
server. Experiment 020's full-chain run proved its stage 6
mesh-anchor consultation in-process only, and experiments
024/025 restated the gap in their limits. Can the
consultation now run where the mesh traffic actually moves
- inside the running server processes, over the wire -
with no new endpoint, no new receive logic, and no change
at all for a server that does not configure a mesh anchor?

## The design choices this experiment sets, stated plainly

**Flags, in the registry family's style, independent of
it.** `--mesh-anchor PATH`, `--mesh-anchor-signer ID`,
`--mesh-anchor-secret-file PATH` mirror `--anchor` /
`--anchor-signer` / `--anchor-secret-file` (experiments
018/019) with the same validation (signer and secret file
require each other; the signer requires the anchor) and
the same reason for the file form (the secret does not ride
in the process command line). Unlike `--anchor` they do
NOT require `--snapshot`: the mesh side exists on
mesh-only servers too, and the mesh anchor is for the sync
chain, not the registry chain. The two anchor families are
independent - a server may configure either, both, or
neither.

**The node class is the wiring.** `NetworkNode` constructs
`AnchoredSyncNode` when a mesh anchor is configured and a
plain `SyncNode` otherwise. Nothing else about the mesh
surface changes: POST /entries and both sides of POST
/sync already funnel through `receive_entries`, so the
consultation - Gate 3's merge first, then the anchor check
on the merged state, rollback on refusal - runs on every
mesh receive the process performs, over plaintext and over
mutual TLS alike, because it lives below the transport.
The peer's side of a sync is judged by the peer's own
configuration, exactly as experiment 015 states for
in-process consultation: consultation is per-node.

**One receive, and the one wire adaptation.**
`NetworkNode.receive_mesh_entries` is the single receive
both mesh surfaces call. With no mesh anchor configured it
is `SyncNode.receive_entries` untouched. With one
configured it is `AnchoredSyncNode`'s consultation, which
returns its refusals in the SyncResult shape - except for
a configured anchor that cannot be read, where the
in-process node rolls the merge back, logs the divergence,
and raises `MeshAnchorError`. Over the wire that raise is
translated into the same shape (accepted 0,
divergence_detected true, rolled_back true, reason
`anchor_unreadable`), and `/health` reports the same name
for the standing: a refusal is a sync outcome in the
response, never a dropped connection - experiment 025's
discipline, and the lesson of experiment 020's finding F2.
The adaptation is stated in case 15 itself, not hidden.

**Health, beside the registry half's fields.** `/health`
gains `mesh_anchor_configured` (bool) and
`mesh_anchor_status` (string), in the style of
`anchor_configured` / `anchor_status`. The status is
experiment 015's own `anchor_status()` classification of
the mesh state the process currently holds - `anchored`,
`anchor_stale`, `anchor_mismatch`,
`anchor_signature_invalid` where a signing identity is
configured, `no_anchor` where none is - with the
unreadable case reported as `anchor_unreadable` rather
than raising out of /health (health is where an operator
looks when sync is already refusing). One consequence is
stated plainly wherever it is asserted: the classification
is of the node's CURRENT state, so a server restarted with
a mesh anchor - which holds nothing, mesh state being
process memory - classifies as `anchor_mismatch` until it
syncs onto the anchored state. That is not a startup
refusal (the mesh side has no startup gate; experiment
015's consultation is a receive-time judgement, and there
is no mesh state at startup to gate), and it is the honest
answer to "what do you hold, against this anchor".

**Lazy import, on purpose.** `node_server.py` imports
`AnchoredSyncNode` and `MeshAnchorError` lazily, on first
use by a process that configures a mesh anchor, not at
module top level. Two packages ship an `anchor.py`, and a
plain `import anchor` resolves by sys.path order;
`node_server` is imported by consumers whose path order is
theirs (one earlier demo imports the REGISTRATION anchor by
plain name after importing `node_server`). A top-level
import here was tried first and broke exactly that way
(recorded under What failed): in a test process whose path
put the registration package first, `node_server` bound
the wrong anchor module at import time. The lazy import
leaves every existing consumer's resolution exactly as it
was; in the server process itself, the file's own
directory heads sys.path, so the intra-package imports
resolve as they do inside `anchored_sync.py`.

## Result: all case 15 outcomes observed, 7 new tests pass, full regression green

Run on Python 3.12.3, standard library only:

```
packages/registration     Ran 20 tests  OK   (unchanged)
packages/policy-library   Ran 39 tests  OK   (unchanged)
packages/synchronization  Ran 64 tests  OK   (57 baseline + 7 new mesh-anchor wire)
packages/validation       Ran 18 tests  OK   (unchanged)
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly
```

141 tests total (baseline before this experiment was
134), every suite on `unittest discover`, the new suite
also by direct file run, the new demo run end to end, and
the leftover `node_server` process check at 0 after every
run of this experiment's suites (see test-output.txt for
the two mid-capture counts of 1 on a heavily shared
machine, and the checks that settle it). All of it is
quoted in `test-output.txt`, including the one blemish:
the first post-build synchronization discovery, run under
heavy parallel load, errored 3 of 64 tests on a port-file
read race in the suites' own `wait_ready` helpers - the
identical failure shape in two unmodified earlier suites
and in this experiment's copy of the helper, with no
assertion failing anywhere - and the discovery was re-run
clean (64/64 in 122.124s against the loaded run's
596.837s) as the regression of record. No leg of the new
suite ever runs more than two server processes at once.

| # | Case 15 outcome | Test | Result |
|---|---|---|---|
| 1 | Honest convergence over the wire, plaintext: two plain servers converge as in case 4/7; the mesh anchor is written over the converged state; mesh-a restarts with `--mesh-anchor` holding nothing, `/health` reporting `mesh_anchor_configured` true and `mesh_anchor_status` `anchor_mismatch` (015's classification of an empty node against a 3-entry anchor, stated in the case); one /sync toward the honest peer lands it on exactly the anchored state (accepted 3, state hash equal, no divergence) and `/health` reports `anchored`. The plain peer reports `mesh_anchor_configured` false and `mesh_anchor_status` `no_anchor` throughout | `test_honest_convergence_with_mesh_anchor` | PASS |
| 2 | Rewritten peer process flagged over the wire: a process claiming mesh-a whose chain was rebuilt from genesis with different mutations (every hash recomputing) syncs with an anchored server that never saw the originals; the server's side of the /sync reports divergence, reason `anchor_mismatch`, accepted 0, rolled back, its state hash and entry set exactly what they were (empty), and `/health` names `anchor_mismatch`. Negative control: a server started WITHOUT `--mesh-anchor` syncs with the same attacker and accepts the rewrite (`greeting: pwned` in its state) - the hole this case closes only where configured, stated plainly | `test_rewritten_peer_flagged_anchor_mismatch_over_the_wire` | PASS |
| 3 | Honest growth is `anchor_stale` over the wire: the converged anchored server syncs after one more honest mutation on the peer; refused under `anchor_stale` (not a mismatch, not adopted), the anchored server keeping exactly the anchored state hash and `/health` standing `anchored`; after the grown state is re-anchored, the same sync lands (accepted 1, hashes converge, 4 entries) | `test_honest_growth_anchor_stale_until_reanchored` | PASS |
| 4 | Tampering in transit: an entry edited in transit (value changed, `entry_hash` left standing) pushed to the converged anchored server is refused by Gate 3's own verification as `tampered_entry` - case 8's stated order, anchor or not - with the anchored state unchanged and `/health` standing `anchored` | `test_tampered_entry_in_transit_refused` | PASS |
| 5 | Unreadable mesh anchor refuses in shape: with the anchor file deleted, `/health` still answers 200 with `mesh_anchor_status` `anchor_unreadable`, and a push returns the SyncResult shape (divergence, rolled back, reason `anchor_unreadable`, accepted 0) instead of dropping the connection; the state is unchanged, and restoring the anchor file restores normal consultation (next push clean, standing `anchored`) | `test_unreadable_mesh_anchor_refused_in_shape` | PASS |
| 6 | Signed mesh anchor over the wire (case 11 at the process surface): an anchored server configured with `--mesh-anchor-signer alice` converges on the genuinely signed anchor; the same heads then arrive anchored by a DIFFERENT enrolled identity (bob, staged at a separate path and copied over, as an out-of-band anchor delivery arrives) and the next sync refuses as `anchor_signature_invalid` - in the sync result and as `/health`'s `mesh_anchor_status` - adopting nothing; restoring alice's anchor returns the lifecycle to its own names (the pending honest growth refuses as `anchor_stale`), and after alice's same-signer re-anchor (kept history appended) the sync lands and the processes converge at 4 entries | `test_signed_mesh_anchor_signature_invalid_then_lifecycle` | PASS |
| 7 | The consultation over the protected transport: two mutual-TLS servers converge; the mesh anchor is written SIGNED over their state; a fresh TLS server configured with the mesh anchor and its signing identity is pushed a rewritten mesh-a chain over POST /entries (built in-process in case 5/8's shape) and refuses it as `anchor_mismatch`, adopting nothing, `/health` naming the same standing; the same server then converges with the honest peer over a server-to-server /sync (accepted 3, anchored state hash) and reports `anchored` - all over mutual TLS | `test_mesh_anchor_over_protected_link` | PASS |
| 8 | No-anchor behavior unchanged, and registry-side behavior untouched: servers without `--mesh-anchor` report the new fields as false / `no_anchor` (rows 1-2) and sync exactly as in cases 4/7, and the unmodified suites of experiments 007-025 - networked nodes, protected links, cert binding, anchor in servers (registry half), signed anchors in servers, revoked-policy network, live reload, wire transport, the full-chain integration - pass in the same full run | full regression | PASS |

The demo (`python3 packages/synchronization/demo_mesh_anchor_wire.py`)
also runs end to end and prints the outcomes in the same
shapes (see `test-output.txt`): two plain servers converge,
the anchor is written, one restarts anchored and converges
through the consultation, growth is refused `anchor_stale`
until re-anchored, and a fresh anchored server refuses a
rewritten chain as `anchor_mismatch`, adopting nothing.
The earlier demo most at risk from this build,
`demo_anchor_in_servers.py` (which imports the registration
anchor by plain name after importing `node_server`), was
also re-run end to end and passes unchanged - the lazy
import's reason, verified.

## How the mechanism works, in plain words

A server that is told where its mesh anchor lives builds
its mesh side out of the anchored node experiment 015
already proved, instead of the plain one. From then on,
every batch of mesh entries that arrives - pushed to it,
or pulled by its own sync - is merged by Gate 3's
verification as always, and then, before the merge is
allowed to stand, the merged state is held up against the
anchor: exactly the anchored state stands; a state built
on a rewritten history is rolled back and reported
divergent under the anchor's own names; honest growth past
the anchor is refused as stale until someone re-anchors
the grown state, by the same signer where the anchor is
signed. The server's health endpoint tells the same story
about the state it currently holds. A server told nothing
about a mesh anchor runs the plain node it has run since
experiment 007, byte for byte the same behavior.

## What failed

- The tests-first run failed as designed (recorded at the
  top of `test-output.txt`): all seven tests failed on the
  server's argparse refusal of `--mesh-anchor`, the absence
  the case names.
- The first build attempt put `from anchor import
  MeshAnchorError` and the `AnchoredSyncNode` import at
  `node_server.py`'s top level. In the new suite's own
  process that bound the REGISTRATION package's `anchor.py`
  (the suite puts that package on sys.path for the Gate 1
  channel, ahead of this package), and `node_server` failed
  to import at all. This is the two-anchor.py collision
  `policy_node.py`'s docstring already describes; the fix
  is the lazy import recorded in the design choices, which
  also protects the one earlier consumer that imports the
  registration anchor by plain name after importing
  `node_server` (verified by re-running its demo).
- The first full synchronization discovery after the build
  errored 3 of 64 tests under heavy parallel load, all on
  the same port-file read race in the suites' `wait_ready`
  helpers (the file exists before the port is written into
  it), two of them in unmodified earlier suites. Recorded
  verbatim in `test-output.txt`; the clean re-run is the
  regression of record. No behavior under test failed in
  either run.

## Honest limits (this is a prototype, not production)

- Consultation remains per-node and opt-in per process,
  exactly as experiment 015 built it: a server started
  without `--mesh-anchor` accepts a rewritten peer's state
  as it always has (asserted as the negative control, not
  hidden), and an anchored server's sync still pushes its
  merged state to a plain peer, whose side is judged by the
  peer's own configuration. Nothing here makes the mesh
  anchored by default or by agreement.
- The mesh anchor inherits every limit of experiments
  012/019: a local stand-in file, unwitnessed, held by
  whoever runs the server; signed anchors inherit Gate 1's
  symmetric HMAC limit (every verifier holds a secret that
  could forge); the file can be deleted or rolled back (a
  deletion now refuses in shape as `anchor_unreadable`
  rather than dropping connections, but nothing restores
  it). Anchor distribution - how a server comes to hold the
  CURRENT anchor, and who may advance it - is still out of
  band and unbuilt, as it was for the registry half.
- The anchor still pins ONE state: an anchored server
  refuses every merge that moves past the anchored state,
  honest or not, until the new state is re-anchored by
  whoever holds the anchor. Over the wire this makes
  re-anchoring a step in every honest sync round that
  changes state - the convergence-gate character experiment
  015 stated, now visible at the process surface. Nothing
  automates re-anchoring or anchor propagation between
  processes.
- There is no mesh-anchor startup gate, deliberately: the
  consultation is a receive-time judgement, and a freshly
  started process holds no mesh state to gate. Its
  at-rest `/health` classification before its first sync is
  `anchor_mismatch` (it does not hold the anchored state),
  which an operator must read as "not yet converged", not
  as an attack signal; the experiment states this rather
  than inventing a fourth standing name.
- Inherited limits stand unchanged: localhost processes on
  one machine, no persistence of mesh state (a restarted
  anchored server re-converges from a peer; if no peer
  holds the anchored state, it cannot re-form it), no
  discovery, no gossip or unattended sync, no per-endpoint
  authorization beyond the transport and experiment 017's
  identity binding, no certificate lifecycle, HMAC
  prototype signing.
- Deliberate removal with notification (availability case
  3, experiment 011's blocked item) remains unbuilt and
  untouched, and `PBA-FIX-001` remains talk-first and
  untouched by this experiment.

## Process captured

Reusable process: when an in-process mechanism moves to a
process surface, the cheapest correct wiring is the one
that changes the CLASS of the object the surface already
uses, not the surface - every receive the server already
performed inherited the consultation because they all
funnel through one method on one object, and the proof
obligation shrank to "the swap happened and the old
surfaces still behave". Second lesson, from the import
failure: a module that is imported by many consumers in
shared processes must not bind an ambiguous name at import
time on behalf of a feature most consumers never enable;
lazy binding on first use keeps the blast radius equal to
the feature's own footprint. Opportunity Stack entry:
extends experiment 025's arrival-parity clause to the mesh
chain - a verification discipline now holds of a chain when
every surface that can accept it (in-process node, server
receive, server-initiated sync) runs the same consultation
object and the same refusal vocabulary; parity is a
property of the object under the surface, not of any one
surface. What would differ next time: state the at-rest
classification of a freshly started anchored node in the
case BEFORE the tests assert it (case 15 did - the
`anchor_mismatch` before the first sync is written into
the case text, so the suite's assertion reads as a stated
property, not a discovered quirk; keep doing that).

## Next, per the build order

Sync-mesh case 15 closes the last in-process-only item the
server experiments carried: mesh-anchor consultation now
runs in the server processes where mesh traffic moves,
under the same opt-in discipline as everywhere else in
this architecture. The mesh-anchor limit statements in
experiments 020, 024, and 025's records and in the three
module docstrings carry superseded-in-part notes pointing
here. The honest distance to utilization is unchanged in
kind and shorter by that item: deliberate removal
(availability case 3, blocked on Max in experiment 011),
anchor distribution and a witnessed anchor, unattended
sync scheduling with a real clock, persistence and restart
recovery for mesh state, discovery, and the Gate 5
end-to-end 1 ms benchmark from experiment 006, which
remains an open production benchmark and is NOT passed.
