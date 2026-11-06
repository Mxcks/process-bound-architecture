# 022. Automatic snapshot delivery: a watcher calls the reload experiment 021 built

Date: 2026-11-06
Authorization: Max's standing authorization for the entire
Process-Bound Architecture (2026-11-06); the gates are Maren's
internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`.
Code: `packages/policy-library/` (new `snapshot_delivery.py`:
`SnapshotFacts`, `snapshot_facts()`, `DeliveryReport`,
`SnapshotDeliveryWatcher`; new `test_snapshot_delivery.py`,
`demo_snapshot_delivery.py`). Experiment 021's
`ReplicatedPolicy.reload_snapshot()` is reused exactly - no
existing mechanism was modified; `replication.py` changed only
in its docstrings (a new outcome 7 paragraph and the
stale-window limit restated for what this experiment proved).
The watcher is built in the style of experiment 016's
`LivenessWatcher` (one watcher over one `ReplicatedPolicy`, a
modeled tick counter, a report per check, a plain-dict event
log) and in the same package, because that is where the placed
policy and the reload live. This experiment does not touch
`proposals/PBA-FIX-001.md` or implement any part of it, and it
does not work on deliberate removal (availability case 3,
experiment 011's blocked item).
Test case: `tests/test-cases/policy-availability.md`, case 7.
The case did not exist before this experiment; it was added to
that file, in the detailed format of case 4/6, before the code
was built, with every outcome below - including the healing
interaction - stated in the case itself. The new suite was run
against the unbuilt code first
(`ModuleNotFoundError: No module named 'snapshot_delivery'`,
the failing-before shape of experiment 016) so the case is
proven to be what the code had to satisfy; that failing output
is recorded verbatim at the top of `test-output.txt`.
Raw output: `test-output.txt` in this folder (the tests-first
failure, the pre-build baseline, the full regression across
all four tested packages, the new suite by direct file run,
the new demo, the handoff check, and the leftover-process
checks).

## Question

Experiment 021 fixed experiment 020's finding F1 by giving a
placed policy a reload path, and stated its own remaining fact
in plain words: delivery is an explicit call, not a push -
nothing calls `reload_snapshot()`, the liveness watcher (016)
does not trigger a reload, a placed policy that is never
reloaded serves from its placement-time view indefinitely, and
a heal between a revocation and a reload recruits a
replacement over the stale record. Can the prototype now close
the trigger half of that gap at prototype scale: a watcher
that notices, on its own checks, that the registry snapshot a
placed policy was built over has changed, and delivers the
change through 021's reload - while keeping 021's refusal
discipline (verify and consult before any view changes, a
refused reload leaves the previous view untouched) and 016's
reporting discipline (every check reports, a refusal
re-reports instead of going silent)?

## The design choices this experiment sets, stated plainly

**Change-detection fact.** A check compares the snapshot
file's PRESENTED facts with the facts the placed policy last
loaded: the entry count and the chain head (the last entry's
`entry_hash` - the same two facts experiment 012's anchor
pins), plus a SHA-256 fingerprint of the file's raw bytes.
The fingerprint is in the comparison for one honest reason:
head and count alone cannot see experiment 021's own tamper,
an in-place edit of an entry body that leaves its
`entry_hash` field standing - a watcher blind to that edit
could never refuse it. Head and count are what reports and
events record (before/after, as case 7 requires); the
fingerprint only decides "changed". Computing the presented
facts verifies nothing - verification is the reload's job,
and it runs, in full, on every delivered change.

**Watcher shape.** `SnapshotDeliveryWatcher` binds one
`ReplicatedPolicy` and the snapshot path that policy's
holders were built over (read from the placement itself
unless a path is given). Construction records the baseline
facts and delivers nothing: the watcher delivers changes
presented AFTER it starts watching a placement that is
current with its snapshot, exactly as 016's watcher starts
from holders beating at tick 0. `tick()` advances the modeled
counter by one and runs `check()`; time is the counter, and
no code path reads a wall clock.

**Refusal vocabulary.** A refused delivery is reported under
the reload's own names: the anchor refusals carry their
status string (`anchor_stale`, `anchor_mismatch`,
`anchor_signature_invalid`), and `TamperedRecord` maps to
`tampered_record`. The watcher invents no refusal of its own
on the delivery path (one exception, stated in the module:
a snapshot file that cannot even be read or parsed is
reported refused under its exception's class name, and the
previous view keeps serving).

## Result: all case 7 outcomes observed, 5 new tests pass, full regression green

Run on Python 3.12.3, standard library only:

```
packages/registration     Ran 20 tests  OK   (unchanged)
packages/policy-library   Ran 33 tests  OK   (28 baseline + 5 new snapshot delivery)
packages/synchronization  Ran 43 tests  OK   (unchanged)
packages/validation       Ran 18 tests  OK   (unchanged)
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly
```

114 tests total (baseline before this experiment was 109),
every suite on `unittest discover`, the new suite also by
direct file run, the new demo run end to end, and post-run
process checks confirmed zero leftover `node_server`
processes. All of it is quoted verbatim in `test-output.txt`.
No test in this suite sleeps: time is the modeled tick
counter, advanced explicitly.

| # | Case 7 outcome | Test | Result |
|---|---|---|---|
| 1 | Clean delivery, automatic: the author revokes, the grown registry is copied over the placements' shared snapshot file, the grown registry is re-anchored by the SAME signer (020/021's anchor lifecycle), and ONE watcher tick - no `reload_snapshot()` call from the test - delivers: the report carries before head = the placement-time head (bob's entry), after head = the revocation entry's hash, counts 2 -> 3, loaded entries 3; the `snapshot_delivered` event records the same before/after; holder views are rebuilt (their entry-list objects change); `serve()` on the placed policy raises `PolicyRevoked` with the holder set unchanged; a later check over the now-current snapshot reports `no_change` | `test_clean_change_delivered_automatically_revocation_takes_effect` | PASS |
| 2 | No-change discipline, asserted in the same test before the revocation: an unchanged tick reports `no_change` with before = after facts, no `snapshot_delivered` event, and every holder's view is the SAME entry-list object after the check as before - no reload churn on quiet ticks | `test_clean_change_delivered_automatically_revocation_takes_effect` (first half) | PASS |
| 3 | Non-revoked policies unaffected: bob's policy, placed over the same snapshot and watched by its own watcher (one watcher per placed policy, 016's shape), is delivered the same change by its watcher's tick and keeps serving - code, author, and Gate 1 record hash all matching | `test_clean_change_delivered_automatically_revocation_takes_effect` (bob half) | PASS |
| 4 | Negative control 1 (anchor lifecycle not followed): the honestly grown snapshot is delivered over the file WITHOUT the re-anchor; the watcher refuses in that check as `anchor_stale` (presented facts recorded 2 -> 3, nothing loaded), the placed policy keeps serving its previous view, and two further checks each return a fresh `delivery_refused` / `anchor_stale` report - 3 refusal events in the log, never silent. The refusal is not sticky past its cause: after the same-signer re-anchor, the next check delivers and `serve()` raises `PolicyRevoked` | `test_anchor_stale_delivery_refused_and_keeps_reporting` | PASS |
| 5 | Negative control 2 (tampered delivery): one entry of the snapshot file is edited in place, experiment 021's tamper exactly (the fingerprint is what lets the watcher see an edit head+count cannot); the delivery is refused as `tampered_record`, the previous view serves exactly as before (same code, same record hash), and the next check re-reports the same refusal (2 refusal events) | `test_tampered_delivery_refused_and_keeps_reporting` | PASS |
| 6 | Negative control 3 (rewritten delivery): the snapshot file is replaced with a full rewrite from genesis (experiment 012's negative control - a different author's internally consistent chain); with anchor consultation configured the delivery is refused as `anchor_mismatch`, the presented count is recorded as 1, and the genuine placed policy keeps serving alice's code | `test_rewritten_delivery_refused_anchor_mismatch` | PASS |
| 7 | Healing interaction (case 6's stale-recruit window): a holder is lost BEFORE the delivery lands, so the shortfall is open while the change is in flight; the automatic delivery lands; the heal that then runs recruits `replacement-d` over the UPDATED record - the recruit's own view contains the revocation (`is_revoked` true), and after both surviving original holders are lost the recruit alone refuses the policy as `PolicyRevoked` instead of serving it from a stale copy | `test_heal_after_automatic_delivery_recruits_over_updated_record` | PASS |

The demo (`python3 packages/policy-library/demo_snapshot_delivery.py`)
also runs end to end and prints the outcomes in the same
shapes (see `test-output.txt`): unchanged check quiet,
revocation delivered in one tick after the re-anchor,
`anchor_stale` refused and re-reported then delivered after
the lifecycle, tampered delivery refused with the previous
view serving.

## How the mechanism works, in plain words

`snapshot_facts()` reads the snapshot file once and returns
its presented facts; `check()` compares them with the
watcher's last-loaded facts. Equal: a `no_change`
`DeliveryReport`, nothing called, nothing rebuilt. Different:
the watcher calls the placed policy's `reload_snapshot()`
(experiment 021, reused exactly - its phase 1 validates the
delivered snapshot once, anchor consultation included, before
any holder's view changes, and phase 2 rebuilds every holder
through its own reload). Success moves the last-loaded facts
to the presented facts and appends a `snapshot_delivered`
event carrying the before/after heads and counts and the
loaded entry count. Any exception from the reload is caught
by the watcher and turned into a `delivery_refused` report
under the refusal's own name, with a matching
`snapshot_delivery_refused` event; the last-loaded facts do
NOT move, which is precisely what makes the next check
re-attempt the same delivery and re-report the same refusal -
the re-reporting is a consequence of the state discipline,
not a separate retry loop. That is also why following the
anchor lifecycle after a stale refusal is sufficient: the
cause changed, the same presented facts now pass the reload's
consultation, and the pending delivery lands.

Design choice worth naming: the watcher triggers the
placement's own reload rather than delivering per holder,
because 021 built the atomicity there (one validation before
any holder changes); a watcher that reloaded holders one by
one would have re-created the partial-delivery window 021's
two phases exist to close. Second choice: detection and
delivery live in a NEW module rather than inside 016's
`LivenessWatcher`, because the two watchers watch different
facts (beats vs snapshot facts) on different triggers, and
016's module states its own scope honestly; composing the two
(one placed policy watched for liveness AND for delivery) is
possible today by constructing both over the same placement -
the tests here construct the delivery watcher alongside
explicit `lose_holder`/`heal()` calls for row 7 - and is
recorded as unbuilt composition, not claimed (see limits).
(Update, experiment 023: that composition is now built and
proven - a thin coordinator drives both watchers over one
placement in one cycle, delivery before liveness - see the
limit bullet below, as amended by 023.)

## What failed

The tests-first run failed as designed: before
`snapshot_delivery.py` existed, the new suite failed at
import (`ModuleNotFoundError: No module named
'snapshot_delivery'`), recorded verbatim in
`test-output.txt`. After the build, nothing failed: the new
suite (5/5 on its first post-build run and in the recorded
full run), the full `packages/policy-library` discovery
(Gate 2, Gate 4, revocation serving and delivery,
self-healing, liveness, and anchor consultation suites
unmodified), the full regression across all four tested
packages, the handoff check, and the new demo all passed on
the runs quoted in `test-output.txt`. No earlier test was
modified. One demo correction during the build, recorded
honestly: the demo's first draft shared one setup across its
three scenarios, which would have placed scenario 2 over an
already-revoked snapshot; the demo was rewritten so each
scenario builds a fresh setup (the demo file's `_Setup`),
and only the rewritten demo's output is quoted.

## Honest limits (this is a prototype, not production)

- "Automatic" means precisely "triggered by check()/tick()
  without a separate reload call" - experiment 016's meaning,
  unchanged. No timer, scheduler, OS file watcher, or
  background thread fires `check()` unattended, and the
  modeled clock is a counter the caller advances. A placed
  policy whose watcher is never checked stays stale exactly
  as long as it would have without one: the window is now
  bounded by the watcher's checks, not removed.
- The watcher notices that the snapshot FILE changed. How
  the updated record gets into that file is still the
  synchronization layer's delivery, performed in the tests
  by an explicit file copy, as in every earlier experiment:
  there is no push, no broadcast, no network transport, and
  the experiment 007/014 server processes are not watched -
  a running server still learns of a revocation only on
  restart, as experiment 021 stated. The server-process half
  of that statement is superseded by experiment 024
  (sync-mesh case 13): a running server now runs this same
  discipline at its own surface - a caller-driven
  POST /check-snapshot check compares the same presented
  facts and calls the same PolicyNode reload - so a running
  server learns of a revocation without a restart when its
  check is driven. What remains unbuilt is unchanged in
  kind: the server check is driven, not scheduled, and no
  transport moves the record into the file.
- One watcher watches one placed policy. Two placed policies
  over the same snapshot need two watchers (the tests run
  exactly that), and nothing in THIS experiment coordinates
  them. As written, this bullet also stated that the delivery
  watcher and the liveness watcher (016) over the SAME
  placement had never been run composed, and that Row 7 proved
  the delivery-then-heal ordering only through explicit
  `lose_holder()` / `heal()` calls, not through 016's
  automatic trigger. That statement is superseded by
  experiment 023 (availability case 8), which composed the
  two watchers over one placement under a single coordinator
  (delivery before liveness in every cycle) and proved, with
  016's automatic trigger: a revocation and a threshold-
  crossing loss in the same cycle window deliver, declare,
  and heal over the updated record in that one cycle; quiet
  cycles double-act in neither watcher; and a refused
  delivery in a cycle does not block that cycle's heal, which
  recruits over the last good record. What remains unbuilt
  after 023 is narrower: no coordination between separate
  placements' coordinators, no unattended scheduling, and the
  server processes still unwatched.
- Detection costs a full read of the snapshot file (parse
  for head/count, hash for the fingerprint) on every check,
  and delivery still costs 021's full chain verification on
  every change. At prototype scale both are trivial; at real
  scale the check cadence against snapshot size is a tuning
  question nobody has measured, and no performance, scale,
  or latency number is measured or claimed here.
- The baseline is construction-time: a watcher constructed
  over a placement whose snapshot file was ALREADY changed
  (but never reloaded) takes the changed file as its
  baseline and delivers nothing. Constructing the watcher is
  a statement that the placement is current with its file;
  nothing audits that statement.
- Inherited limits stand unchanged: in-process holders on
  one machine over one shared snapshot file, HMAC prototype
  signing, signed-but-unwitnessed anchors, no consensus, no
  discovery, no persistence, no identity proofing beyond
  node_id.
- Deliberate removal with notification (availability case 3,
  experiment 011's blocked item) remains unbuilt and
  untouched, and `PBA-FIX-001` remains talk-first and
  untouched by this experiment.

## Process captured

Reusable process: an automatic trigger for an existing manual
mechanism should reuse the mechanism's refusal discipline as
its reporting vocabulary, and should represent "pending" as
unmoved state rather than as a retry queue: because the
watcher's last-loaded facts move only on a delivery that
stood, re-reporting a refusal and recovering once its cause
is fixed are the same code path as the first attempt, and
there is no second place for the pending state to drift.
Second lesson: a change-detection fact must be chosen against
the tamper the previous experiment actually demonstrated,
not against the tamper that is convenient to detect -
head+count, the obvious facts, are blind to 021's in-place
edit, and the fingerprint exists in the comparison for
exactly that reason, stated where the choice is made.
Opportunity Stack entry: extends experiment 021's delivery
clause (a state change is only as real as its path to every
surface) with a trigger clause: a path that only runs when
someone remembers to walk it is a tool, not a property of
the system - the detection that a delivery is owed, the
firing, and the outcome (including a refusal that keeps
being reported) must be one recorded event chain, or the
system's claim to deliver is a claim about its operator.
What would differ next time: give the composed-watchers
scenario (liveness + delivery over one placement, a loss and
a revocation landing in the same tick window) its own test
case up front, so the two triggers' interaction is proven by
its own row instead of recorded as an unbuilt composition.
(Done: experiment 023, availability case 8, written before
its code in exactly that way.)

## Next, per the build order

Availability case 7 closes the automatic-trigger item
experiment 021 left open, at prototype scale: a placed policy
with a delivery watcher no longer depends on a caller
remembering to reload. The honest distance to utilization is
unchanged in kind and shorter by that item: deliberate
removal (availability case 3, blocked on Max in experiment
011), revocation delivery to running server processes (a
restart is still the only path there), a transport that
moves the updated record into snapshot files (sync
scheduling, discovery), unattended watcher scheduling with a
real clock, a witnessed anchor, production crypto and identity proofing,
consensus, persistence, and Gate 5's open production
benchmark. `PBA-FIX-001` remains talk-first and untouched by
this experiment. Nothing here is deployed beyond the local
working copy, and nothing in this experiment changes that.
