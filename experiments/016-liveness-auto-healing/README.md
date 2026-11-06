# 016. Liveness detection: a lost holder is detected and healing triggers itself

Date: 2026-11-06
Authorization: `AUTHORIZATION.md` (Max authorized the entire
architecture 2026-11-06; the gates are Maren's internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`).
Code: `packages/policy-library/` (new `liveness.py`:
`LivenessView`, `LivenessWatcher`, `WatchReport`; new
`test_liveness.py`, `demo_liveness.py`). Experiment 010's healing
machinery is reused, not duplicated: the watcher calls
`replication.py`'s `lose_holder()` / `needs_healing()` / `heal()`
exactly as they stand, under placement's own eligibility rules,
and `replication.py` and `policy_node.py` are unmodified. The Gate 5
package's liveness proposal (the verification gate is where a
liveness check was proposed, experiment 005/006 READMEs and
`gate5_harness.py`'s own note) is the place this experiment was
pointed at; the check itself is built in the policy-library package
because that is where the holders, the floor, and `heal()` live,
and Gate 5 does not consult it (stated as a limit below). This
experiment does not touch `proposals/PBA-FIX-001.md` or implement
any part of it, and it does not work on deliberate removal
(availability case 3, experiment 011's blocked item).
Test case: `tests/test-cases/policy-availability.md`, case 5. The
case did not exist before this experiment; it was added to that
file, in the detailed format of case 4, before the code was built,
with both design rules (the lost threshold and the returning-holder
rule) stated in the case itself. The new tests were run against the
unbuilt code first (`ModuleNotFoundError: No module named
'liveness'`) so the case is proven to be what the code had to
satisfy; that failing output is recorded verbatim at the top of
`test-output.txt`.
Raw output: `test-output.txt` in this folder (the tests-first
failure, the full `packages/policy-library` discovery, the new
suite by direct file run, the full regression across all four
tested packages, the handoff check, the new demo, the
self-healing / Gate 4 / Gate 2 demos, and the leftover-process
check).

## Question

Experiment 010 built self-healing and named its own limits in
plain words: healing is an explicit `heal()` call made by whoever
noticed the loss, nothing detects that a holder is lost rather
than temporarily offline, and nothing triggers healing
automatically. Layer 07's liveness item stayed open on exactly
those three points. Can the prototype now close them at prototype
scale: holders signaling liveness into a shared view, a watcher
that tells a brief silence from a loss, and healing that fires
from the detection itself - without weakening any of experiment
010's honesty (eligibility, reports, below-floor when no
replacement exists)?

## The two rules this experiment sets, stated plainly

**Lost vs offline rule.** A holder's state is counted in missed
heartbeats on a modeled clock: it beats at a tick, and each tick
it does not beat is one missed beat. At 0 missed beats it is
**alive**. At 1 or 2 missed beats it is **silent-short**: Gate 4's
temporary-offline case in liveness form - still a holder, still
counted toward the floor, failover covers serving, and no healing
is triggered, ever, for as long as it stays under the threshold.
At **3 missed beats** (`LOST_AFTER_MISSED_BEATS = 3`, stated in
case 5, in `liveness.py`, and here) it is **lost**: the watcher
removes it through `lose_holder()`, so the holder count itself
drops, and `heal()` triggers from that declaration in the same
check. The threshold is a single fixed count, a purely technical
prototype choice; what it protects is architectural - a temporary
silence must never recruit a duplicate for a holder that is coming
back, the conflation experiment 010's boundary test was written to
prevent.

**Returning-holder rule.** A holder that was declared lost and
later beats again rejoins as a **candidate only**. The return is
recorded (`holder_returned_as_candidate`), the node joins the
watcher's candidate pool with the opt-in it had as a holder, and
it is never re-added as a holder by the return itself - so the
holder count never double-counts it (a floor of 3 stays 3, never
becomes 4 because the lost node woke up). If a later loss drops
the count again, the automatic heal can recruit the returned node
exactly like any other eligible candidate, and only then is it a
holder again, holding a fresh verified copy from `heal()`, not
its stale pre-loss one.

## Result: all case 5 outcomes observed, 4 new tests pass, full regression green

Run on Python 3.12.3, standard library only:

```
packages/registration     Ran 14 tests  OK   (6 Gate 1 + 4 revocation + 4 anchor)
packages/policy-library   Ran 21 tests  OK   (3 Gate 2 + 3 Gate 4 + 3 revocation serving + 3 self-healing + 5 anchor consultation + 4 new)
packages/synchronization  Ran 20 tests  OK   (3 Gate 3 + 4 network + 2 anchor + 4 anchor consultation + 3 coordinated rollback + 4 protected)
packages/validation       Ran  9 tests  OK   (4 Gate 5 + 5 scale)
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly
```

64 tests total (baseline before this experiment was 60), every
suite on `unittest discover`, the new suite also by direct file
run, the new demo and the self-healing, Gate 4, and Gate 2 demos
run end to end, and a post-run process check confirmed zero
leftover `node_server` processes from the network suite. All of
it is quoted verbatim in `test-output.txt`. No test in this suite
sleeps: time is the modeled tick counter, advanced explicitly.

| # | Case 5 outcome | Test | Result |
|---|---|---|---|
| 1 | Holders beat into a shared view; a watcher classifies alive / silent-short / lost from missed beats; at 2 missed beats the silent holder is still a holder and no healing triggers; on the 3rd missed beat it is declared lost, the count drops, and healing triggers AUTOMATICALLY from the detection - replacement recruited under placement's eligibility rules (non-opted-in and already-holder candidates skipped, unneeded spare untaken), floor restored, policy still served, detection / trigger / outcome all recorded | `test_lost_holder_triggers_automatic_healing_and_floor_restored` | PASS: alive baseline reports `no_action`; after 2 silent ticks the state is `silent_short`, `healing is None`, holders unchanged, serve succeeds; one tick later `lost_node_ids == ("original-host",)`, the same check's `HealingReport` is 2 -> 3, `floor_restored=True, status healed`, recruited exactly `replacement-d`; the event log holds `holder_lost` (missed_beats 3) and `healing_triggered` (status healed). Proof the auto-recruited copy is real: after both surviving original holders are lost, the replacement alone serves with code, policy hash, and Gate 1 record hash all matching. Neither `heal()` nor `lose_holder()` is called by the test on this path. |
| 2 | Negative control 1: a holder silent briefly (also flipped offline, Gate 4's case) returns before the threshold - never healed over, never loses holder status, and is simply alive as the same holder when it beats again | `test_silent_short_holder_returns_and_is_never_healed_over` | PASS: at 2 missed beats the report is `silent_short` with no healing and no `healing_triggered` event in the log, holders unchanged, failover serves; after `come_online` and one beating tick the state is `alive`, status `no_action`, placement report back to `available`. |
| 3 | Negative control 2: a holder is declared lost and no eligible replacement exists - the report says below floor honestly, and later checks KEEP reporting the loss instead of going silent | `test_lost_with_no_replacement_keeps_reporting_below_floor` | PASS: the losing check's heal recruits nobody (candidates only non-opted-in, an existing holder, and a duplicate), status `below_floor`, count 2 vs floor 3, survivors serve; two further checks each return a fresh `below_floor` WatchReport with a below-floor HealingReport, and the log holds at least 3 `below_floor` events (the losing check plus both re-checks). |
| 4 | Returning-holder rule: a lost, healed-over holder that beats again is a candidate, not a holder (count stays 3, never 4), and a later loss recruits it through the same automatic heal, as a candidate | `test_returned_lost_holder_rejoins_as_candidate_not_holder` | PASS: after auto-healing to `replacement-d`, the returned `original-host` appears in `returned_candidate_node_ids` and the candidate pool, is absent from the holder set, the placement count is exactly 3, and exactly one `holder_returned_as_candidate` event is logged; when `holder-b` is then lost by silence, the automatic heal recruits `original-host`, count restored to exactly 3, policy serves. |

The demo (`python3 packages/policy-library/demo_liveness.py`)
also runs end to end and prints all four outcomes in the same
shapes (see `test-output.txt`).

## How the mechanism works, in plain words

`LivenessView` is deliberately policy-blind: a tick counter and,
per node id, the tick of its last beat. A holder's state is a pure
function of missed beats (current tick minus last beat), which is
what makes the three states exact and the tests deterministic -
there is no wall clock anywhere in `liveness.py`, and no code path
reads one.

`LivenessWatcher` binds one `ReplicatedPolicy`, one view, and one
candidate list. `tick(beating=[...])` is one modeled moment:
advance one tick, record the named beats, then `check()`.
`check()` classifies the current holders; for each holder at or
past the threshold it appends a `holder_lost` event, removes the
holder through experiment 010's `lose_holder()`, and - the count
now being below the floor - calls `heal()` with the candidate
list in that same check, appending a `healing_triggered` event
carrying the heal's own report fields. Recruits are registered in
the view beating at the current tick, so a replacement is alive
from the moment it joins and is not instantly re-classified from
a stale last-beat. A check that finds no new loss but a still-open
shortfall re-runs `heal()` against the current candidate pool:
that is what keeps an unhealed loss reported on every check (row
3 above) and what lets a candidate that appears later - including
a returned holder - close the shortfall without a new loss having
to happen first. Every check returns a `WatchReport` (tick,
per-holder states, silent-short and lost ids, returned candidates,
the `HealingReport` or None, counts before/after, floor, and a
status in a vocabulary that extends experiment 010's: `healed`,
`below_floor`, `silent_short`, `no_action`) and appends it to
`watcher.reports`; the log and the reports are the same record the
tests assert, not a second summary.

Design choice worth naming: detection removes the holder through
`lose_holder()` rather than flagging it, because experiment 010
built the shortfall as a countable fact (count vs floor), and a
liveness flag layered beside the count would have created two
sources of truth for "how many holders are there" - the exact
ambiguity Gate 4's reports exist to prevent. The cost of that
choice is the returning-holder rule: removal is real, so a return
must be a re-recruitment, and the candidate-only rule is what
makes that safe against double-counting.

## What failed

The tests-first run failed as designed: before `liveness.py`
existed, the new suite failed at import (`ModuleNotFoundError: No
module named 'liveness'`), recorded verbatim in
`test-output.txt`. After the build, nothing failed: the new suite
(4/4 on its first post-build run and in the recorded full run),
the full `packages/policy-library` discovery (Gate 2, Gate 4,
revocation serving, self-healing, and anchor consultation suites
unmodified), the full regression across all four tested packages,
the handoff check, and all four demos passed on the runs quoted
in `test-output.txt`. No earlier test was modified.

## Honest limits (this is a prototype, not production)

- The heartbeat is a method call and the clock is a counter the
  caller advances. Nothing detects a real machine dying, a process
  crashing, or a network partition; no heartbeat moves over any
  network, no timer or scheduler fires `check()` unattended, and
  "automatic" means precisely "triggered by the check that detects
  the loss, with no separate heal call" - not "runs on its own in
  the background". A production watcher still needs a real clock,
  a heartbeat transport, and scheduling, none of which is built.
- The lost threshold is one fixed count of missed modeled beats
  (3), a prototype choice stated in case 5 and in the code. It is
  not tuned against any real heartbeat interval, jitter, clock
  skew, or load, and a holder that is merely slow past the
  threshold is declared lost exactly like a dead one. The
  returning-holder rule bounds that mistake (no double count, the
  return is only a candidacy), but a slow holder still loses its
  holder status and its replacement still costs a copy.
- The Gate 5 verification gate does not consult liveness: this
  experiment built the check where the holders and `heal()` live
  (the policy-library package), and `gate5_harness.py` is
  unchanged. Wiring the verdict to ask the watcher is unbuilt.
- Healing inherits experiment 010's limits whole: holders and
  recruits are in-process `PolicyNode` objects on one machine over
  one shared snapshot file, "independent" and "eligible" mean
  distinct node_id and the opt-in flag, recruits are built over
  the placement-time snapshot and never reload a later revocation,
  and the experiment 007/014 networked server processes do not
  heal or watch liveness - a killed process's node is detected by
  nothing here.
- Candidates are the watcher's construction-time list plus
  returned holders under the stated rule. Nothing discovers
  candidates, verifies a returned node's identity beyond its
  node_id (the same node_id arriving from a different machine
  would be taken as the returned holder - identity proofing is
  Gate 1's open production item, inherited, not solved), or
  coordinates two watchers: one watcher watches one policy, and
  two watchers over the same policy could double-heal.
- A below-floor re-check re-runs `heal()` on every check while
  the shortfall is open. At prototype scale that is the honest
  reporting behavior case 5 requires; at real scale it needs a
  retry policy (backoff, alerting thresholds), and none is
  designed or claimed here.
- Deliberate removal with notification (availability case 3)
  remains unbuilt, as experiment 011 recorded it blocked on Max's
  answers. No performance, scale, or latency numbers were
  measured or are claimed for liveness or the trigger.

## Process captured

Reusable process: when an automatic trigger is built on top of a
manual mechanism whose honesty came from its reports, the trigger
must fire the manual mechanism itself - same function, same
eligibility, same report object - and add only detection and a
record of the firing; a parallel "auto" path would drift from the
manual one's guarantees the first time either changed. Second
lesson: make the new mechanism's states a pure function of one
recorded quantity (here, missed beats in one view) so every state
the system claims is recomputable by a reader from the record,
and the negative controls (silent-short, returning) are the same
function evaluated at different counts, not special cases.
Opportunity Stack entry: extends experiment 010's repair clause
with a trigger clause (a repair that only runs when a human
notices is a tool, not a property of the system: the detection
that a repair is owed, the firing, and the outcome must be one
recorded event chain, or the system's claim to heal is a claim
about its operator). What would differ next time: give the
watcher a modeled candidate-arrival event in the test set itself
(a candidate appearing between checks closing an open shortfall),
so the re-check path is proven by its own row and not only by the
keeps-reporting control and the returning-holder row together.

## Next, per the build order

Availability case 5 closes the liveness-detection and
automatic-trigger items layer 07 and experiments 010-015 listed as
open, at prototype scale. The honest distance to utilization is
unchanged in kind and shorter by those items: deliberate-removal
notification (availability case 3, blocked on Max's answers in
experiment 011), Gate 5 consulting liveness and the anchor,
anchor consultation in the networked server processes, a
witnessed or signed anchor for production (plus a rule for who
may re-anchor), discovery and automatic sync, a real heartbeat
transport and watcher scheduling, persistence and restart
recovery, production crypto and identity proofing, consensus,
revocation delivery to placed policies, and Gate 5's open
production benchmark. `PBA-FIX-001` remains talk-first and
untouched by this experiment. Nothing here is deployed beyond the
local working copy, and nothing in this experiment changes that.
