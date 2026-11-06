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
holder rejoins as a candidate only, and **automatic snapshot
delivery to placed policies built and passing** on top of
experiment 021's reload path (2026-11-06, experiment
`experiments/022-automatic-snapshot-delivery/`; availability
case 7): a watcher in the liveness watcher's style compares the
shared snapshot file's presented facts (entry count, chain head,
file fingerprint) with the facts the placed policy last loaded
and calls `reload_snapshot()` itself on a change, re-reporting a
refused delivery on every check until its cause is fixed, and
**the two watchers composed over one placed policy built and
passing** (2026-11-06, experiment
`experiments/023-composed-watchers/`; availability case 8): a
thin coordinator drives the delivery watcher and the liveness
watcher over the same placement in one cycle, delivery before
liveness, so a revocation and a lost holder in the same window
are delivered, declared, and healed over the updated record in
that cycle, and a refused delivery does not block a heal, which
recruits over the last good record.
Prototype only, not production. The module docstrings in
`policy_node.py`, `replication.py`, `liveness.py`,
`snapshot_delivery.py`, and `composed_watchers.py` are the
authoritative lists of honest limits.

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
| `replication.py` | The Gate 4 prototype: registration-time placement of a policy on independent opted-in holders with a replication floor (`place_policy`, `ReplicatedPolicy`, placement/availability reports). Builds on `PolicyNode` without modifying it. Extended in experiment 010 with self-healing (permanent holder loss, shortfall detection, `heal`, `HealingReport`); Gate 4 behavior unchanged. Extended in experiment 021 with `reload_snapshot()` (revocation delivery to placed holders, availability case 6). |
| `test_availability.py` | Automated tests, one per Gate 4 outcome: sole-holder negative control, floor-3 failover, honest below-floor reporting. |
| `demo_availability.py` | Runnable walkthrough of the Gate 4 outcomes. |
| `test_revocation_serving.py` | Automated tests for case 6 on the serving side: a node with the updated record stops serving; the stale window (still serving before sync, refusing after) is asserted both ways. (Named `_serving` so unittest discovery does not collide with registration's `test_revocation.py`.) |
| `test_revocation_delivery.py` | Automated tests for availability case 6 (experiment 021, fixing experiment 020's finding F1): `ReplicatedPolicy.reload_snapshot()` (in `replication.py`, experiment 021) delivers a revocation to every placed holder - stale-refused before re-anchoring, `PolicyRevoked` after; a tampered reload leaves the serving state untouched; a non-revoked policy keeps serving after the same reload; and with no reload called the placed object still serves from its placement-time view, asserted on purpose. |
| `demo_revocation.py` | Runnable walkthrough of the stale window opening and closing. |
| `test_self_healing.py` | Automated tests for availability case 4 (experiment 010), extending `replication.py` (no Gate 4 code rewritten): floor restored after a permanent holder loss, the replacement alone serving verified bytes, the no-eligible-replacement negative control, and the at-floor / exact-shortfall boundaries. |
| `demo_self_healing.py` | Runnable walkthrough of case 4, including the negative control. |
| `test_anchor_consultation.py` | Automated tests for registration case 8 (experiment 015): a `PolicyNode` built with `anchor_path` (in `policy_node.py`, reusing the registration package's experiment 012 check by file-path load) serves a genuine snapshot, refuses the full-rewrite snapshot at load as `AnchorMismatch`, treats no-anchor behavior as exactly pre-015 (including serving the rewrite, stated plainly), and refuses honest growth as `AnchorStale` at load and on reload until re-anchored. |
| `demo_anchor_consultation.py` | Runnable walkthrough of case 8 on the serving side: anchored serve, rewrite refused while an anchorless node serves it, growth stale until re-anchored. |
| `test_signed_anchor_consultation.py` | Automated tests for registration case 10's serving half (experiment 019): a `PolicyNode` built with `anchor_signer` + `anchor_secret_hex` serves a correctly signed anchor, refuses a missing or tampered signature at load as `AnchorSignatureInvalid` (`anchor_signature_invalid`), and without a signing identity judges a signed anchor by its head exactly as in case 8. |
| `liveness.py` | The experiment 016 prototype: `LivenessView` (shared heartbeat view on a deterministic modeled clock: tick counter plus last-beat ticks, no wall-clock time) and `LivenessWatcher` (three-state classification - alive / silent_short / lost at 3 missed beats - automatic `heal()` trigger on a lost declaration, `WatchReport` plus an event log, returning-holder-as-candidate rule). Reuses `replication.py`'s `lose_holder` / `heal` unchanged. |
| `test_liveness.py` | Automated tests for availability case 5 (experiment 016): lost triggers automatic healing and the replacement alone serves verified; silent-short returns unhealed; lost with no eligible replacement keeps reporting below floor; a returned lost holder is a candidate, not a holder, until a later heal recruits it. |
| `demo_liveness.py` | Runnable walkthrough of case 5, including both negative controls and the returning-holder rule. |
| `snapshot_delivery.py` | The experiment 022 prototype: `SnapshotDeliveryWatcher` (one watcher over one `ReplicatedPolicy`, modeled tick counter, `DeliveryReport` plus an event log) and `snapshot_facts` (the presented facts: entry count, chain head, file fingerprint). On a changed snapshot the watcher calls `replication.py`'s `reload_snapshot()` (experiment 021) in the same check; a refused delivery is re-reported with its reason on every later check; an unchanged check calls nothing. |
| `test_snapshot_delivery.py` | Automated tests for availability case 7 (experiment 022): a clean change is delivered automatically in one tick and the revoked placed policy stops serving while a non-revoked policy over the same snapshot keeps serving; an anchor_stale delivery is refused and re-reported until the same-signer re-anchor, then delivered; tampered and rewritten deliveries are refused under their own names; an unchanged check rebuilds no holder view; a heal after delivery recruits over the updated record. |
| `demo_snapshot_delivery.py` | Runnable walkthrough of case 7: unchanged check, automatic delivery after revocation + re-anchor, anchor_stale refusal and re-reporting, tampered refusal. |
| `composed_watchers.py` | The experiment 023 prototype: `ComposedWatcher`, a thin coordinator that constructs one `SnapshotDeliveryWatcher` and one `LivenessWatcher` over the same `ReplicatedPolicy` and drives them in one cycle, delivery before liveness, returning one `ComposedReport` (plus a `composed_cycle` event) per cycle. Reuses both watchers and `replication.py` unchanged; in a refused-delivery cycle it points the placement's heal path at a last-good sidecar file for that cycle's liveness tick only, so healing recruits over the last record that stood. |
| `test_composed_watchers.py` | Automated tests for availability case 8 (experiment 023): quiet cycles reload nothing and heal nothing; a delivery-only cycle delivers without healing; a loss-only cycle heals without delivering; the hard case (revocation + threshold-crossing loss in one window) delivers, declares, and heals over the updated record in one cycle, the recruit refusing the revoked policy while a non-revoked policy keeps serving; anchor_stale and tampered refusals in a loss window do not block the heal, which recruits over the last good record, and the refused delivery is not forced through. |
| `demo_composed_watchers.py` | Runnable walkthrough of case 8: quiet composed cycles, the hard case in one window, and refusal composition with the heal over the last good record. |

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

# Automatic snapshot delivery tests and demo (availability case 7:
# a watcher compares the snapshot's presented facts on modeled
# ticks and calls reload_snapshot() itself on a change; refused
# deliveries re-report, unchanged checks do nothing)
python3 packages/policy-library/test_snapshot_delivery.py
python3 packages/policy-library/demo_snapshot_delivery.py

# Composed watchers tests and demo (availability case 8: one
# coordinator ticks the delivery and liveness watchers over one
# placement, delivery before liveness; quiet cycles do nothing,
# a refusal in a cycle does not block that cycle's heal)
python3 packages/policy-library/test_composed_watchers.py
python3 packages/policy-library/demo_composed_watchers.py
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

## Automatic snapshot delivery, in plain words (case 7)

Experiment 022 closes the trigger gap experiment 021 left named,
at prototype scale. `snapshot_delivery.py` adds one watcher in
experiment 016's style and reuses everything else:

1. **A delivery watcher** (`SnapshotDeliveryWatcher`) over one
   `ReplicatedPolicy`, on a modeled tick counter. Each check
   computes the shared snapshot file's **presented facts** -
   entry count and chain head (the facts the anchor pins), plus
   a SHA-256 fingerprint of the file's bytes, which is part of
   the change fact because head and count alone cannot see an
   in-place edit that leaves both untouched - and compares them
   with the facts the placed policy last loaded. Construction
   records the baseline and delivers nothing.
2. **The trigger is the check.** On a difference the watcher
   calls experiment 021's `reload_snapshot()` in that same
   check: a clean delivery (snapshot verifies; with anchor
   consultation, the same-signer re-anchor already done) takes
   effect immediately - a revoked placed policy stops serving,
   a non-revoked one keeps serving - and is recorded as a
   `snapshot_delivered` event with the before/after heads and
   counts. A refused delivery is recorded under the reload's
   own refusal name (`anchor_stale`, `tampered_record`,
   `anchor_mismatch`, `anchor_signature_invalid`), the previous
   view keeps serving, the last-loaded facts do not move, and
   every later check re-attempts and re-reports it until the
   cause is fixed, the re-reporting discipline of case 5. An
   unchanged check reports `no_change` and calls nothing, so
   holder views are never rebuilt on quiet ticks. After a
   delivery, a later `heal()` recruits over the updated record,
   closing case 6's stale-recruit window on this path.

Delivery limits, on top of everything above: "automatic" means
triggered by a check the caller drives - no timer, scheduler,
or file watcher fires it unattended; the watcher notices that
the snapshot FILE changed, and how the new record got into the
file remains the synchronization layer's delivery, exactly as
before; one watcher watches one placed policy, and the
networked server processes are not watched and still learn of
a revocation only on restart.

## Composed watchers, in plain words (case 8)

Experiment 023 closes the composition gap experiment 022 left
named, at prototype scale. `composed_watchers.py` adds one
coordinator and reuses both watchers exactly:

1. **One cycle, one order.** `ComposedWatcher` constructs the
   delivery watcher and the liveness watcher over the SAME
   `ReplicatedPolicy`. Each `tick(beating=[...])` is one
   composed cycle: the delivery watcher's tick runs first, the
   liveness watcher's tick (beats recorded, holders classified)
   runs second, and the cycle returns one `ComposedReport`
   carrying both sub-reports, with one `composed_cycle` event
   naming the delivery's status and reason, the lost holders,
   and the heal's result. The order - delivery before
   liveness - is the point: a heal recruits by constructing a
   holder over the placement's snapshot, so delivering first
   means a same-cycle heal recruits over the newest record,
   and healing first would reopen case 6's stale-recruit
   window by ordering alone.
2. **The hard case and the quiet case are both proven.** A
   revocation (delivered over the file, anchor lifecycle
   followed) and a holder crossing the lost threshold in the
   same window produce, in one cycle: delivered, declared
   lost, healed over the updated record - the recruit refuses
   the revoked policy and a non-revoked policy over the same
   snapshot keeps serving. Quiet cycles do neither watcher's
   work (no reload, no view rebuild, no heal); a delivery-only
   cycle does not heal and a loss-only cycle does not deliver.
3. **A refusal does not block healing, and healing does not
   force a delivery.** When a cycle's delivery is refused, the
   file on disk holds the refused record, so the coordinator
   keeps the bytes of the last record that stood (recorded at
   construction, moved only when a delivery stands), writes
   them to a sidecar file next to the snapshot, and points the
   placement's heal path at the sidecar for that cycle's
   liveness tick only, restoring the delivery path before the
   cycle returns. A genuinely lost holder is therefore healed
   over the last good record in the same cycle, the delivery
   watcher's refusal stays pending and keeps being reported,
   and once the cause is fixed a normal cycle delivers to
   every holder, the sidecar recruit included.

Composition limits, on top of everything above: one
coordinator composes the watchers over ONE placement and
nothing coordinates two coordinators (two over the same
placement could still double-act); the last-good record is
bytes in the coordinator's memory and a sidecar file left
next to the snapshot, unprotected and uncleaned; the clocks
are still the watchers' modeled counters and no tick fires
unattended.

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
4. Nothing here pushes the update into a node's snapshot file:
   delivery of the record to the file is the synchronization
   layer's job. Since experiment 022 a placed policy's delivery
   watcher (case 7, previous section) calls the reload itself
   once the updated record is in the file, so a watched placed
   policy's window closes at the watcher's next check; the
   networked server processes of experiment 007 build their
   `PolicyNode` at startup and do not reload, and a node that
   never receives the updated record stays stale indefinitely.

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
  there is no revocation push or broadcast into a snapshot file,
  and no staleness bound, so the stale window above can stay open
  indefinitely on a node that never syncs. For a placed policy,
  experiment 022's delivery watcher bounds the window by its own
  checks once the record reaches the file (case 7); the remaining
  closing work is sync scheduling and transport (experiment 007's
  open list), not something this package claims.
- The node serves verified bytes; it does not sandbox or execute
  policy code (runtime package's job).
