# 005. Gate 4 Policy Availability Prototype

Date: 2026-11-06
Authorization: `AUTHORIZATION.md` (Max authorized the entire architecture;
Gate 4 is Maren's internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`. Gates 1,
2, and 3 passed in experiments 002, 003, and 004, which per that file
is the signal for Gate 4.)
Code: `packages/policy-library/` (new `replication.py`,
`test_availability.py`, `demo_availability.py`), built on Gate 2's
`PolicyNode` unchanged. `packages/registration/registration.py`,
`packages/policy-library/policy_node.py`, and
`packages/synchronization/sync_mesh.py` are unchanged.
Test cases: `tests/test-cases/policy-availability.md`, cases 1-2
(case 3, deliberate removal with notification, is not built here and
remains open; see limits).
Raw output: `test-output.txt` in this folder.

## Question

Can the Gate 4 outcomes be observed in a small, runnable prototype:
the honest sole-host failure shown rather than hidden, and a policy
placed at registration time with a replication floor surviving its
hosts going offline, with the floor reported honestly when it cannot
be met?

## Result: all three outcomes observed, 3 of 3 automated tests pass

Run on Python 3.12.3, standard library only:

```
Ran 3 tests in 0.004s
OK
```

The `packages/policy-library` discovery run is now 6 of 6 (the 3
Gate 2 tests plus the 3 Gate 4 tests). Regression the same day,
unmodified suites: Gate 1, 6 of 6 pass; Gate 3, 3 of 3 pass; the EAI
handoff check (`scripts/verify-handoffs.py`) still passes 3 of 3
vectors. All of it is quoted verbatim in `test-output.txt`.

| # | Gate 4 outcome | Test | Result |
|---|---|---|---|
| 1 | Negative control: a policy held by exactly one node is unavailable after that node goes offline (availability case 1, the honest failure) | `test_negative_control_sole_holder_offline_is_unavailable` | PASS: floor 1, one holder; it serves while online; after `go_offline`, `serve()` raises `PolicyUnavailable` and the live availability report shows 0 online holders, `currently_servable=False`. The failure is the asserted result, not an error in the run. |
| 2 | Replication floor 3 at registration time: policy survives hosts going offline (availability case 2) | `test_replicated_policy_survives_holders_going_offline` | PASS: placement reports 3 holders of floor 3, status `available`. Original host offline: another holder serves the verified bytes (code and record hash match Gate 1's record). Second holder offline: the last holder still serves. All three offline: `PolicyUnavailable`, reported by the live report. |
| 3 | The floor is reported honestly when it cannot be met | `test_floor_reported_honestly_when_it_cannot_be_met` | PASS: floor 3, five candidates (one duplicate node_id, two not opted in) yield exactly 2 independent opted-in holders. The placement report states holder_count 2 vs floor 3, `floor_met=False`, status `below_floor`, in both the object and its dict form. The two holders do serve while online, and losing both is reported as unservable, not hidden. |

The demo (`python3 packages/policy-library/demo_availability.py`)
also runs end to end and prints all outcomes as expected (see
`test-output.txt`): sole holder offline = unavailable, floor 3
survives two holders dropping, unmet floor reports `below_floor`.

## How the mechanism works, in plain words

Placement happens once, at registration time: `place_policy` takes
the registered policy's hash and payload, the verified registry
snapshot, a candidate list (`NodeSpec`: node_id + the operator's
opt-in choice from Gate 2), and a floor. Non-opted-in candidates and
duplicate node_ids are skipped, in order; every eligible candidate
becomes a holder, each a real Gate 2 `PolicyNode`, so every serve
runs Gate 2's verification (snapshot hash chain, code_hash,
policy_hash recompute). `ReplicatedPolicy` tracks modeled liveness:
`serve()` asks the first online holder and raises
`PolicyUnavailable` when none is online. There is deliberately no
central fallback copy anywhere in the object.

## What failed

Nothing in the test run. The Gate 4 suite, the Gate 2 suite in the
same package, both regressions, the EAI check, and the demo passed
on their first full runs, on both invocation styles (`unittest
discover` and direct file run). One design point was settled while
building, not by a failure: a below-floor placement is still placed
on the holders that exist (so it can serve while they are online and
the shortfall can be fixed), but its report never calls it
available; refusing placement outright would have hidden working
holders, and calling it available would have hidden the risk.

## Honest limits (this is a prototype, not production)

- Holders are in-process `PolicyNode` objects on one machine over
  one shared snapshot file. No network, no discovery, no placement
  protocol, nothing about real machines or availability at scale is
  demonstrated. "Offline" is a boolean the tests flip; nothing
  detects a real machine dying, and there is no liveness check or
  heartbeat (proposed for the Gate 5 verification gate; not built).
- The floor is enforced and reported at placement time only. If a
  holder later goes offline permanently, nothing recruits a
  replacement or heals back up to the floor; the live availability
  report shows the shortfall and nothing acts on it.
- "Independent" means distinct node_id only. Three holders could be
  three processes on one physical machine or under one operator,
  and this code cannot tell. Real independence (machines, operators,
  failure domains) is a deployment property, unverified here.
- Serving inherits Gate 2's limits unchanged: snapshot trust is
  tamper-evidence with no external anchor (a whole-file rewrite
  from genesis would verify), and there is no revocation: a holder
  cannot learn a policy was revoked after its snapshot was taken.
- `tests/test-cases/policy-availability.md` case 3, a policy
  deliberately removed by its owner with dependents notified before
  it stops being served, is NOT implemented. That part of layer 07
  stays open.
- No performance, scale, or latency numbers were measured or are
  claimed.

## Process captured

Reusable process: state the negative control as a first-class test
that asserts the honest failure, before building the mechanism that
fixes it, so the fix is measured against a failure the suite itself
proves can happen; and make every placement/availability claim a
report object that pairs the actual count with the promised floor,
so honesty is a field in the data rather than a sentence in the
docs. Extends experiment 004's gate-outcome entry on the Opportunity
Stack. What would differ next time: Gate 5's canary harness should
drive its availability/liveness checks through this module's
`availability_report()` (and Gate 3's fork builder for its Byzantine
case) instead of inventing a second liveness model; and a future
healing build should move holder snapshots through Gate 3's sync
mesh instead of the shared-file copy used here, closing the note
left in experiment 004.

## Next, per the build order

Gate 4 passing is the signal to proceed to Gate 5 (the verification
gate is real: a canary harness measures lineage time per
transformation under load, and a forced Byzantine/divergence fault
halts rollout and rolls back, observed), under the standing
authorization. Two layer-07 items stay open and are named in
`docs/architecture/07-availability-risk-when-a-policy-host-goes-offline.md`:
deliberate-removal notification (availability case 3) and real-scale
availability (network holders, liveness detection, self-healing back
to the floor, verified independence). Not done in this experiment.
