# 023. Composed watchers: liveness and snapshot delivery over one placed policy, in one cycle

Date: 2026-11-06
Authorization: Max's standing authorization for the entire
Process-Bound Architecture (2026-11-06); the gates are Maren's
internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`.
Code: `packages/policy-library/` (new `composed_watchers.py`:
`ComposedWatcher`, `ComposedReport`, `CYCLE_ORDER`; new
`test_composed_watchers.py`, `demo_composed_watchers.py`).
Both existing watchers are reused exactly - `liveness.py`
(experiment 016) and `snapshot_delivery.py` (experiment 022)
are unmodified, and `replication.py` changed only in its
docstrings (a new outcome 8 paragraph). The coordinator is
built in the same package and the same style (one coordinator
over one `ReplicatedPolicy`, a modeled tick counter, a report
per cycle, a plain-dict event log), because that is where the
placed policy and both watchers live. This experiment does
not touch `proposals/PBA-FIX-001.md` or implement any part of
it, and it does not work on deliberate removal (availability
case 3, experiment 011's blocked item).
Test case: `tests/test-cases/policy-availability.md`, case 8.
The case did not exist before this experiment; it was added
to that file, in the detailed format of case 4/6/7, before
the code was built, with the cycle order, the hard case, the
no-double-action discipline, the single-action cycles, and
the refusal composition all stated in the case itself. The
new suite was run against the unbuilt code first
(`ModuleNotFoundError: No module named 'composed_watchers'`,
the failing-before shape of experiments 016/022) so the case
is proven to be what the code had to satisfy; that failing
output is recorded verbatim at the top of `test-output.txt`.
Raw output: `test-output.txt` in this folder (the tests-first
failure, the pre-build baseline, the full regression across
all four tested packages, the new suite by direct file run,
the new demo, the 016 and 022 demos, the handoff check, and
the leftover-process checks).

## Question

Experiment 022 closed automatic snapshot delivery and stated
its remaining composition fact in plain words: the delivery
watcher and the liveness watcher (016) over the SAME
placement had never been run composed in one scenario - each
was proven against the placement separately, and 022's
heal-after-delivery proof used explicit `lose_holder()` /
`heal()` calls, not 016's automatic trigger. Two uncoordinated
watchers over one placement could double-act, act in the
wrong order, or recruit a replacement over a stale record.
Can the prototype now close that gap at prototype scale: one
coordinator running both watchers over one placed policy in
one composed cycle, in a stated order, with the composed
outcome reported in one place - without rewriting or
duplicating either watcher?

## The design choices this experiment sets, stated plainly

**Cycle order: delivery before liveness, and why.** Every
composed cycle runs the delivery watcher's tick first and
the liveness watcher's tick second (`CYCLE_ORDER =
("delivery", "liveness")` in `composed_watchers.py`, carried
on every `ComposedReport`). The justification is mechanical,
not stylistic: `heal()` recruits a replacement by
constructing a `PolicyNode` over the placement's registry
snapshot, so the record a same-cycle heal recruits over is
whatever the placement holds when the heal fires. Delivering
first means a delivery pending in the cycle window is
already loaded when the liveness check declares a loss and
heals, so the recruit is built over the UPDATED record (the
hard case below: the recruit refuses the policy revoked in
that same cycle). Healing first would recruit over the
record the pending delivery was about to replace - case 6's
stale-recruit window, reopened by ordering alone. No evidence
for the reverse order appeared while building: the only
same-cycle interaction between the watchers runs through
the snapshot a heal constructs over, and that interaction
points one way.

**Refusal composition: the last-good sidecar.** A refused
delivery leaves a problem the order alone cannot solve: the
snapshot file on disk then holds the REFUSED record, and
`heal()` recruits over the placement's snapshot PATH, so a
heal in the same cycle would construct its recruit over the
refused record and fail (anchor_stale) or be refused
(tampered) - the refusal would silently block healing. The
coordinator therefore keeps the bytes of the last record the
placement actually loaded (recorded at construction; moved
only when a delivery STANDS). In a cycle whose delivery is
refused, it writes those bytes to a sidecar file next to the
snapshot (`<snapshot>.last-good-<policy prefix>`), points
the placement's heal path at the sidecar for that cycle's
liveness tick only, and restores the delivery snapshot path
before the cycle returns (in a `finally` block). The heal
recruits a real verified holder over the last good record,
the delivery watcher's own path never moves so its refusal
stays pending and keeps being reported, and the heal cannot
force the refused delivery through: when the cause is fixed,
delivery lands through the watcher's usual reload, to every
holder including the sidecar recruit.

**Composed report.** Each cycle returns one `ComposedReport`
(policy hash, composed tick, the order, the delivery
watcher's `DeliveryReport`, the liveness watcher's
`WatchReport`, and the holder counts before/after the whole
cycle) and appends one `composed_cycle` event naming the
delivery's status and reason, the lost holders, and the
heal's status and recruits. The two watchers' own reports
and event logs remain theirs and are asserted alongside -
the composed report is the one place a cycle is named, not
a replacement record.

## Result: all case 8 outcomes observed, 6 new tests pass, full regression green

Run on Python 3.12.3, standard library only:

```
packages/registration     Ran 20 tests  OK   (unchanged)
packages/policy-library   Ran 39 tests  OK   (33 baseline + 6 new composed watchers)
packages/synchronization  Ran 43 tests  OK   (unchanged)
packages/validation       Ran 18 tests  OK   (unchanged)
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly
```

120 tests total (baseline before this experiment was 114),
every suite on `unittest discover`, the new suite also by
direct file run, the new demo and the 016/022 demos run end
to end, and post-run process checks confirmed zero leftover
`node_server` processes. All of it is quoted verbatim in
`test-output.txt`. No test in this suite sleeps: time is the
watchers' modeled tick counters, advanced explicitly.

| # | Case 8 outcome | Test | Result |
|---|---|---|---|
| 1 | No double action on quiet cycles: three composed cycles with no snapshot change and every holder beating produce no reloads and no heals - every delivery sub-report `no_change`, every liveness sub-report `no_action` with `healing is None`, holder views the SAME entry-list objects after the cycles as before, counts 3 -> 3 on every cycle, no `snapshot_delivered`, `holder_lost`, or `healing_triggered` event in either watcher's log, and three `composed_cycle` events naming all of it | `test_quiet_cycles_no_double_action` | PASS |
| 2 | Exactly one action, delivery only: a revocation lands (file delivered, same-signer re-anchor) while every holder beats; the composed cycle delivers (counts 2 -> 3, head = the revocation entry's hash, 3 loaded) and does nothing else - `lost_node_ids` empty, `healing is None`, holder set unchanged, `serve()` raises `PolicyRevoked` | `test_delivery_only_cycle_exactly_one_action` | PASS |
| 3 | Exactly one action, loss only: no snapshot change; a holder crosses the lost threshold in the third silent cycle; that cycle delivers nothing (`no_change` on every cycle, no `snapshot_delivered` event, surviving holders' views never rebuilt) and heals exactly once - `replacement-d` recruited, floor restored, policy still served | `test_loss_only_cycle_exactly_one_action` | PASS |
| 4 | The hard case in one window: holder-c is primed two beats into silence over quiet composed cycles; the revocation is then delivered over the snapshot file and re-anchored by the same signer; holder-c stays silent in the next composed cycle, so that ONE cycle delivers the revocation (2 -> 3, head = revocation hash), declares holder-c lost, and the automatic heal recruits `replacement-d` over the UPDATED record (healing counts 2 -> 3, floor restored, composed counts 3 -> 3). The composed report and the `composed_cycle` event name all three facts. The recruit's own view contains the revocation (`is_revoked` true) and the recruit alone refuses as `PolicyRevoked`; bob's non-revoked policy, placed over the same snapshot, is delivered by its own watcher and keeps serving with code, author, and Gate 1 record hash matching. Neither `reload_snapshot()`, `lose_holder()`, nor `heal()` is called by the test on this path | `test_revocation_and_loss_same_window_heals_over_updated_record` | PASS |
| 5 | Refusal composition, anchor_stale: the honestly grown snapshot is delivered WITHOUT the re-anchor in the same window where holder-c crosses the threshold; the one composed cycle refuses the delivery as `anchor_stale` (presented count 3 recorded) AND heals - `replacement-d` recruited over the LAST GOOD record, so the recruit's view has no revocation and the placed policy serves exactly as before (alice's code, alice's record hash). The composed report and event record both the refusal (with its reason) and the heal (with its result). The heal did not force the delivery through: the next composed cycle refuses again as `anchor_stale`, and only after the same-signer re-anchor does a later cycle deliver, to every holder including the recruit, after which `serve()` raises `PolicyRevoked` | `test_anchor_stale_refusal_does_not_block_healing_over_last_good` | PASS |
| 6 | Refusal composition, tampered: one entry of the snapshot file is edited in place (021's tamper) in the same loss window; the composed cycle refuses as `tampered_record` and heals over the last good record in the same cycle - recruit holds no revocation, the previous view serves exactly as before, and the next cycle re-reports the same refusal | `test_tampered_refusal_does_not_block_healing_over_last_good` | PASS |

The demo (`python3 packages/policy-library/demo_composed_watchers.py`)
also runs end to end and prints the outcomes in the same
shapes (see `test-output.txt`): quiet cycles quiet, the hard
case in one cycle, and the anchor_stale refusal composed
with a last-good heal, delivered after the re-anchor.

## How the mechanism works, in plain words

`ComposedWatcher` binds one `ReplicatedPolicy` and constructs
both watchers over it: the `SnapshotDeliveryWatcher` (which
records the snapshot's current facts as its baseline) and
the `LivenessWatcher` (which registers the current holders
beating at tick 0, with the coordinator's candidate list).
`tick(beating=[...])` advances the composed counter by one,
runs the delivery tick, interposes the last-good sidecar
swap only when that delivery was refused, runs the liveness
tick with the named beats, restores the placement's snapshot
path in a `finally` block, moves the last-good bytes only if
the delivery stood, and returns the `ComposedReport`. Every
mechanism the cycle relies on is the original one: delivery
is 022's watcher calling 021's reload, loss declaration and
healing are 016's watcher calling 010's heal, and serving is
Gate 2's verification inside each holder. The coordinator
adds the order, the sidecar swap, and the composed record -
nothing else.

Design choice worth naming: the coordinator drives the two
watchers' TICKS rather than reaching inside either one,
because each watcher's tick is its whole contract (advance,
act, report, log) and a coordinator that called sub-steps
would have had to re-implement the boundary between them -
the exact drift risk 016's and 022's process notes warn
against. The one place tick-level driving is not enough is
the refused cycle, where the heal's snapshot source has to
be chosen between the two ticks; that interposition is
confined to the placement's path field, is restored before
the cycle returns, and is stated in the module docstring and
here rather than hidden inside a watcher.

## What failed

The tests-first run failed as designed: before
`composed_watchers.py` existed, the new suite failed at
import (`ModuleNotFoundError: No module named
'composed_watchers'`), recorded verbatim in `test-output.txt`.
After the build, nothing failed: the new suite (6/6 on its
first post-build run and in the recorded full run), the full
`packages/policy-library` discovery (Gate 2, Gate 4,
revocation serving and delivery, self-healing, liveness,
anchor consultation, and snapshot delivery suites
unmodified), the full regression across all four tested
packages, the handoff check, and the new demo plus the 016
and 022 demos all passed on the runs quoted in
`test-output.txt`. No earlier test was modified.

## Honest limits (this is a prototype, not production)

- "Composed" and "automatic" mean precisely "driven by
  `tick()` in the stated order without a separate reload or
  heal call" - 016's and 022's meaning, unchanged. No timer,
  scheduler, thread, or OS file watcher fires `tick()`
  unattended, and the modeled clocks are the counters the
  caller advances. A placement whose coordinator is never
  ticked is exactly as stale and exactly as unhealed as one
  with no watchers.
- One coordinator composes the two watchers over ONE placed
  policy, in-process, on one machine, over one shared
  snapshot file. Two placed policies over the same snapshot
  each need their own coordinator (or delivery watcher, as
  the hard-case test runs for bob's policy), nothing
  coordinates two coordinators, and two coordinators over
  the SAME placement could still double-act - the
  no-double-action proof covers one coordinator's cycles.
- The last-good record is bytes in the coordinator's memory,
  and its sidecar file is written next to the snapshot in
  refused cycles and left in place afterwards, because a
  holder recruited from it names it as its snapshot path
  until a later delivered reload moves it. Nothing cleans
  the sidecar up, protects it from another writer, or
  audits the construction-time baseline: a coordinator
  constructed over a placement whose snapshot file was
  already changed (but never loaded) takes that file as
  last good, 022's baseline honesty inherited.
- The sidecar swap interposes on the placement's private
  snapshot-path field for one liveness tick. That is a
  prototype seam, stated here: a production composition
  would give `heal()` an explicit record source instead of
  borrowing the placement's path, and nothing here claims
  the seam is safe against a second actor mutating the same
  placement mid-cycle (there is no concurrency here at all:
  cycles are sequential method calls).
- A below-floor outcome composes the way the two watchers
  already behave: the liveness watcher's re-checks keep
  re-attempting and re-reporting, and each such composed
  cycle also re-attempts any refused delivery - both
  re-reporting disciplines run, neither suppresses the
  other. Retry policy at real scale (backoff, alerting) is
  as unbuilt as 016 and 022 stated.
- Inherited limits stand unchanged: in-process holders on
  one machine over one shared snapshot file, HMAC prototype
  signing, signed-but-unwitnessed anchors, no consensus, no
  discovery, no persistence, no identity proofing beyond
  node_id, and the experiment 007/014 server processes are
  watched by nothing here - a running server still learns of
  a revocation only on restart, as experiment 021 stated.
- Deliberate removal with notification (availability case 3,
  experiment 011's blocked item) remains unbuilt and
  untouched, and `PBA-FIX-001` remains talk-first and
  untouched by this experiment. No performance, scale, or
  latency number was measured or is claimed here.

## Process captured

Reusable process: when two proven triggers can act on the
same object, composition is an ordering fact and a shared-
resource fact, and both must be found before any code is
written: the order is decided by tracing what the second
action reads (here, the snapshot a heal constructs over),
and the shared-resource hazard is decided by tracing what
the first action leaves behind on its FAILURE path (here, a
refused record sitting at the very path the heal reads -
invisible in either watcher's own tests, because each was
proven with the other's failure absent). Second lesson: a
coordinator that drives whole ticks and confines its one
interposition to a named, restored seam keeps both
mechanisms' guarantees auditable; a coordinator that
reaches inside either mechanism inherits neither one's
proof. Opportunity Stack entry: extends 016's trigger
clause and 022's trigger clause with a composition clause -
two properties of a system that are each proven separately
are not thereby a property of the system together; the
interaction (order, shared state on the failure path, one
record per cycle) needs its own test case, stated before
the build, or the system's claim is a claim about its parts.
What would differ next time: state the refusal-path shared-
resource question in the test case itself ("what does the
second trigger read if the first one fails?") for any future
composition, so the sidecar-shaped answer is designed
against the case rather than discovered against the code.

## Next, per the build order

Availability case 8 closes the composed-watchers item
experiment 022 left open, at prototype scale: a placed
policy under one coordinator no longer depends on which of
its two watchers a caller remembers to drive, and the two
triggers cannot recruit over a record the other was about
to replace or had just refused. Experiment 022's
never-composed limit statement has been replaced in its
README with what this experiment proved. The honest
distance to utilization is unchanged in kind and shorter by
that item: deliberate removal (availability case 3, blocked
on Max in experiment 011), revocation delivery to running
server processes (a restart is still the only path there),
a transport that moves the updated record into snapshot
files (sync scheduling, discovery), unattended watcher
scheduling with a real clock, a witnessed anchor, production
crypto and identity proofing, consensus, persistence, and
Gate 5's open production benchmark. `PBA-FIX-001` remains
talk-first and untouched by this experiment. Nothing here is
deployed beyond the local working copy, and nothing in this
experiment changes that.

[Superseded in part, 2026-11-06, experiment 024: the limit
above - "the experiment 007/014 server processes are
watched by nothing here - a running server still learns of
a revocation only on restart, as experiment 021 stated" -
and the matching item in the Next section ("revocation
delivery to running server processes (a restart is still
the only path there)") were built in experiment 024
(`experiments/024-server-live-reload/`), under sync-mesh
case 13, written tests-first. A running server now reloads
through its own caller-driven snapshot check
(POST /check-snapshot), reusing experiment 022's change
facts and refusal discipline and the PolicyNode's reload;
this experiment's composed in-process watchers are
unchanged and remain the composition proof for placed
policies. What still stands: the server check is driven,
not scheduled; no coordinator watches a server process;
and no transport delivers the updated record into a
server's snapshot file.]
