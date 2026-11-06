# packages/policy-library

Authoring, signing, versioning, and serving of policy libraries as polymorphic payloads.

Status: **Gate 2 prototype built and passing** (2026-11-06, experiment
`experiments/003-gate2-decentralized-policy/`), **Gate 4
availability prototype built and passing** on top of it (2026-11-06,
experiment `experiments/005-gate4-availability/`), **revocation
honored on the serving side** (2026-11-06, experiment
`experiments/008-revocation/`; registration case 6), and
**self-healing holder replacement built and passing** on top of
Gate 4 (2026-11-06, experiment `experiments/010-self-healing/`;
availability case 4), and **anchor consultation in serving built
and passing** (2026-11-06, experiment
`experiments/015-anchor-consultation/`; registration case 8): a
node built with an anchor path consults experiment 012's anchor
at load and on reload, refusing a rewritten snapshot as
`anchor_mismatch` and an honestly grown one as `anchor_stale`
until re-anchored; a node built without one behaves exactly as
before, and **liveness detection with an automatic healing
trigger built and passing** on top of Gate 4 / experiment 010
(2026-11-06, experiment
`experiments/016-liveness-auto-healing/`; availability case 5): a
watcher reads heartbeats from a shared liveness view on a modeled
clock, declares a holder lost only after 3 missed beats (fewer is
silent-short, still a holder, never healed over), and triggers
experiment 010's `heal()` from that detection; a returned lost
holder rejoins as a candidate only. Prototype only, not
production. The module docstrings in `policy_node.py`,
`replication.py`, and `liveness.py` are the authoritative lists
of honest limits.

Authoring, signing, and registering a library is Gate 1's job
(`packages/registration/`). This package is the other half: once a
policy is registered, it does not need the registration channel, or any
central home, ever again in order to be served.

## What is here

| File | What it is |
|---|---|
| `policy_node.py` | The Gate 2 prototype: `PolicyNode`, standalone snapshot verification. Python standard library only. Builds on Gate 1's registration record format; does not modify or re-implement Gate 1. |
| `test_policy_node.py` | Automated tests, one per Gate 2 outcome, plus the verification refusals. |
| `demo.py` | Runnable walkthrough of both Gate 2 outcomes. |
| `replication.py` | The Gate 4 prototype: registration-time placement of a policy on independent opted-in holders with a replication floor (`place_policy`, `ReplicatedPolicy`, placement/availability reports). Builds on `PolicyNode` without modifying it. Extended in experiment 010 with self-healing (permanent holder loss, shortfall detection, `heal`, `HealingReport`); Gate 4 behavior unchanged. |
| `test_availability.py` | Automated tests, one per Gate 4 outcome: sole-holder negative control, floor-3 failover, honest below-floor reporting. |
| `demo_availability.py` | Runnable walkthrough of the Gate 4 outcomes. |
| `test_revocation_serving.py` | Automated tests for case 6 on the serving side: a node with the updated record stops serving; the stale window (still serving before sync, refusing after) is asserted both ways. (Named `_serving` so unittest discovery does not collide with registration's `test_revocation.py`.) |
| `demo_revocation.py` | Runnable walkthrough of the stale window opening and closing. |
| `test_self_healing.py` | Automated tests for availability case 4 (experiment 010), extending `replication.py` (no Gate 4 code rewritten): floor restored after a permanent holder loss, the replacement alone serving verified bytes, the no-eligible-replacement negative control, and the at-floor / exact-shortfall boundaries. |
| `demo_self_healing.py` | Runnable walkthrough of case 4, including the negative control. |
| `test_anchor_consultation.py` | Automated tests for registration case 8 (experiment 015): a `PolicyNode` built with `anchor_path` (in `policy_node.py`, reusing the registration package's experiment 012 check by file-path load) serves a genuine snapshot, refuses the full-rewrite snapshot at load as `AnchorMismatch`, treats no-anchor behavior as exactly pre-015 (including serving the rewrite, stated plainly), and refuses honest growth as `AnchorStale` at load and on reload until re-anchored. |
| `demo_anchor_consultation.py` | Runnable walkthrough of case 8 on the serving side: anchored serve, rewrite refused while an anchorless node serves it, growth stale until re-anchored. |
| `liveness.py` | The experiment 016 prototype: `LivenessView` (shared heartbeat view on a deterministic modeled clock: tick counter plus last-beat ticks, no wall-clock time) and `LivenessWatcher` (three-state classification - alive / silent_short / lost at 3 missed beats - automatic `heal()` trigger on a lost declaration, `WatchReport` plus an event log, returning-holder-as-candidate rule). Reuses `replication.py`'s `lose_holder` / `heal` unchanged. |
| `test_liveness.py` | Automated tests for availability case 5 (experiment 016): lost triggers automatic healing and the replacement alone serves verified; silent-short returns unhealed; lost with no eligible replacement keeps reporting below floor; a returned lost holder is a candidate, not a holder, until a later heal recruits it. |
| `demo_liveness.py` | Runnable walkthrough of case 5, including both negative controls and the returning-holder rule. |

## How to run it

From the repo root:

```sh
# Tests (either command works)
python3 -m unittest discover -s packages/policy-library -v
python3 packages/policy-library/test_policy_node.py

# Demo (registers a policy, copies it to a second node, takes the
# central channel offline, and serves from the node)
python3 packages/policy-library/demo.py

# Gate 4 availability tests and demo (sole-holder failure, floor
# failover, honest below-floor reporting)
python3 packages/policy-library/test_availability.py
python3 packages/policy-library/demo_availability.py

# Revocation tests and demo (case 6: updated record stops serving,
# stale window opens and closes on sync)
python3 packages/policy-library/test_revocation_serving.py
python3 packages/policy-library/demo_revocation.py

# Self-healing tests and demo (availability case 4: a lost holder is
# replaced from opted-in candidates, floor restored; no eligible
# replacement reports below_floor honestly)
python3 packages/policy-library/test_self_healing.py
python3 packages/policy-library/demo_self_healing.py

# Anchor consultation tests and demo (registration case 8: an
# anchored node refuses a rewritten snapshot at load and honest
# growth until re-anchored; no anchor means pre-015 behavior)
python3 packages/policy-library/test_anchor_consultation.py
python3 packages/policy-library/demo_anchor_consultation.py

# Liveness tests and demo (availability case 5: heartbeats on a
# modeled clock, a holder lost at 3 missed beats triggers healing
# automatically; silent-short never does)
python3 packages/policy-library/test_liveness.py
python3 packages/policy-library/demo_liveness.py
```

Requires Python 3.10+. No dependencies, no network, nothing is deployed.

## How it works, in plain words

1. A policy is registered through Gate 1. That writes one hash-chained
   entry into the registry log: that entry is the *registration record*.
2. A node that wants to serve policies holds two things of its own: a
   **copy** of that registry log (its snapshot) and the **policy payload
   bytes**. How the copies arrive is Gate 3's problem (sync); this
   prototype copies files explicitly in the tests/demo.
3. The node verifies for itself, contacting nothing: it recomputes the
   snapshot's hash chain, checks the payload it holds hashes to the
   registered `code_hash`, and checks the `policy_hash` recomputes from
   (author, name, version, payload). Any mismatch is a refusal, never a
   serve.
4. Serving is **opt-in**. `PolicyNode(..., serve_policies=True)`, or the
   operator's own config file via `PolicyNode.from_config(...)` with
   `{"serve_policies": true}`. The default is False: a node that did
   not opt in can hold a verified policy and still will not serve it.
   That flag is how a user's own computer becomes a policy source *by
   their choice* (Gate 2, outcome 2).

## Gate 4: availability, in plain words

`replication.py` answers layer 07's risk (a policy drops when its
host goes offline) at prototype scale. At registration time a policy
is placed, through `place_policy`, on independent opted-in holders
with a **replication floor**: the minimum number of holders that must
hold it before anything calls it available. Holders that did not opt
in, and repeat offers of the same node, never count. The placement
report always pairs the real holder count with the floor: floor met
means status `available`; anything less means `below_floor`, stated,
never hidden. After placement, `ReplicatedPolicy.serve()` asks the
first online holder (Gate 2 verification runs on every serve), so
the policy survives holders going offline down to the last one, and
raises `PolicyUnavailable` when none is online, including the
negative control the risk predicts: one holder, offline, policy
gone.

Gate 4 limits, on top of the Gate 2 limits below: the floor is
enforced and reported at placement time, "offline" is a modeled
flag with no real liveness detection, and "independent" means
distinct node ids, not verified machines or operators. (The
placement-time-only limit was the state at Gate 4; experiment 010
adds explicit self-healing on top of it - see the next section.)
Deliberate removal by the owner (availability test case 3) is not
implemented; note that `place_policy` constructs its holders once
and they never reload, so a placed `ReplicatedPolicy` does not learn
of a later revocation either (see "Revocation and the stale window"
below for the mechanism a single `PolicyNode` does have).

## Self-healing holder replacement, in plain words (case 4)

Experiment 010 closes the gap Gate 4 left named: a holder that dies
for good used to leave the policy below its floor forever, reported
but never repaired. `replication.py` now distinguishes the two ways
a holder stops serving:

1. **Temporarily offline** (`go_offline`): the holder still exists
   and still counts toward the floor. Gate 4 failover serves from
   the others. This never triggers healing.
2. **Lost permanently** (`lose_holder`): the holder is removed and
   the holder count drops. `needs_healing()` and `shortfall()` state
   the gap against the floor as facts, and the placement report goes
   back to `below_floor` - the shortfall is never hidden.

`heal(candidates)` repairs a shortfall, and only a shortfall (at the
floor it is a no-op that recruits nobody, even with spares on
offer). It takes candidates in order under placement's own
eligibility rules - opted-in only, never a node that already holds
the policy, never the same node twice - and copies the policy to
each recruit exactly the way placement does: a real `PolicyNode`
over the same verified registry snapshot, holding the payload, so a
recruit verifies on every serve like any original holder (the tests
prove this by having the replacement serve alone, verified bytes
and record hash intact, after every original holder is gone).
Recruiting stops the moment the floor is restored, taking exactly
the shortfall. The `HealingReport` pairs the holder count before
and after with the floor: `healed` only when the floor is restored,
`at_floor` when there was nothing to heal, and `below_floor` when
the candidates run out first - the negative control, where the only
candidates are non-opted-in or already holders, recruits nobody and
says so. A partial recruitment is kept and reported as the partial
count it is, never rounded up.

Self-healing limits, on top of everything above: healing is an
explicit call by whoever noticed the loss, against a candidate list
the caller supplies. Nothing detects a real machine dying, decides
a silent holder is lost rather than temporarily offline, discovers
candidates, or triggers healing automatically, and the copy a
recruit receives is the in-process payload plus the shared snapshot
file, not a network transfer. (Experiment 016 adds the detection
and the automatic trigger at prototype scale, on a modeled clock -
see the next section; real machine-death detection remains unbuilt.)

## Liveness detection and the automatic trigger, in plain words (case 5)

Experiment 016 closes the detection gap experiment 010 left named,
at prototype scale. `liveness.py` adds two things and reuses
everything else:

1. **A shared liveness view** (`LivenessView`): an integer tick
   counter and, per holder, the tick of its last heartbeat. Time is
   modeled - the tick moves only when a test or demo advances it,
   and a holder beats only when its beat is recorded - so no test
   sleeps and timing cannot flake.
2. **A watcher** (`LivenessWatcher`) over one `ReplicatedPolicy`,
   classifying each holder from missed beats alone (current tick
   minus last beat): **alive** at 0 missed, **silent-short** at 1
   or 2 missed, **lost** at 3 or more missed
   (`LOST_AFTER_MISSED_BEATS = 3`, the threshold stated in case 5).
   Silent-short is Gate 4's temporary-offline case: still a holder,
   still counted, failover covers serving, no healing. The check
   that declares a holder lost removes it through experiment 010's
   `lose_holder()` and calls `heal()` with the watcher's candidate
   list in that same check - the trigger is the detection, not a
   caller. Every check returns a `WatchReport` and appends events
   (`holder_lost`, `healing_triggered`, `below_floor`,
   `holder_returned_as_candidate`) to a log the tests assert.

Negative controls behave as case 5 requires: a briefly silent
holder that beats again before 3 missed beats was never declared
lost and never healed over; a lost holder with no eligible
replacement reports `below_floor`, and every later check re-reports
it (and re-attempts with whatever candidates then exist) instead of
going silent. **Returning-holder rule:** a holder declared lost
that beats again rejoins as a candidate only - never automatically
as a holder, so the count never double-counts - and a later
automatic heal can recruit it like any other candidate, with a
fresh verified copy from `heal()`.

Liveness limits, on top of everything above: the heartbeat is a
method call and the clock is a counter. No real heartbeat moves
over a network, no timer fires unattended, no real machine death
is detected, the fixed threshold of 3 is untuned against any real
interval or jitter, one watcher watches one policy with no
mesh-wide agreement, and candidates are the watcher's list plus
returned holders - nothing is discovered.

## Revocation and the stale window, in plain words (case 6)

Gate 1's channel now records revocations as new entries in the same
hash-chained record (see `packages/registration/README.md`). This
package honors them, with the gap stated plainly:

1. Registration entries and revocation entries are kept apart inside
   the node. If the node's snapshot contains a policy's revocation
   entry, `serve_policy(...)` raises `PolicyRevoked` for it, even
   when the held payload verifies exactly, and `is_revoked(...)`
   reports True.
2. **The stale window is real.** A node built over a snapshot taken
   before the revocation knows nothing about it and keeps serving
   the revoked policy. The tests assert that it does; the window is
   named in case 6 precisely so it is not hidden.
3. The window closes when the updated record reaches the node:
   `reload_snapshot(...)` re-reads a snapshot file (the node's own
   file after a sync copies the new record over it, or a new path),
   re-verifies its hash chain standalone, and only then swaps the
   node's view. A tampered update raises `TamperedRecord` and leaves
   the previous view untouched. After a successful reload the
   revoked policy is refused.
4. Nothing here pushes the update: delivery is the synchronization
   layer's job, the networked server processes of experiment 007
   build their `PolicyNode` at startup and do not reload, and a node
   that never receives the updated record stays stale indefinitely.

## Honest limits

- "Second node" is a second `PolicyNode` over copied files on one
  machine. No network, no peer discovery, no replication protocol, no
  scale claim. Moving copies automatically is the synchronization
  layer's work (Gate 3).
- Snapshot trust inherits Gate 1's limit: the chain detects a single
  edited entry, but a whole-file rewrite from genesis would verify
  against itself. An external anchor for that chain head now exists
  at prototype scale (experiment 012,
  `packages/registration/anchor.py`: head + entry count in a file
  outside the registry's directory, checked against a presented
  snapshot copy as well as the log), and the full-rewrite negative
  control fails that anchor check in its tests. Since experiment
  015, a node built with `anchor_path` DOES consult that anchor
  when serving (at load and on reload) and refuses the rewrite;
  a node built without one does not consult anything and behaves
  exactly as before, and the anchor file is still a local
  stand-in, not a witnessed external service. Since experiment
  018, the experiment 007/014 server processes pass an anchor
  path when started with `--anchor`, so this consultation runs
  at server startup too; without the flag they consult nothing.
- Revocation is honored only from the record a node actually holds:
  there is no revocation push, broadcast, or staleness bound, so the
  stale window above can stay open indefinitely on a node that never
  syncs. Closing that is delivery work (sync scheduling, experiment
  007's open list), not something this package claims.
- The node serves verified bytes; it does not sandbox or execute
  policy code (runtime package's job).
