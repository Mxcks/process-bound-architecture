# 010. Self-healing: a lost holder is replaced and the floor is restored

Date: 2026-11-06
Authorization: `AUTHORIZATION.md` (Max authorized the entire
architecture 2026-11-06; the gates are Maren's internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`).
Code: `packages/policy-library/` (`replication.py` extended with
permanent holder loss, shortfall detection, and `heal` /
`HealingReport`; new `test_self_healing.py`,
`demo_self_healing.py`). Gate 4's placement, failover, and reports
are unchanged, and `policy_node.py` is unmodified: a recruit is
constructed exactly the way `place_policy` constructs an original
holder. This experiment does not touch `proposals/PBA-FIX-001.md`
or implement any part of it.
Test case: `tests/test-cases/policy-availability.md`, case 4. The
case did not exist before this experiment; it was added to that
file, in the file's own format, before the code was built, and the
new tests were run against the unextended code first (3 errors,
`AttributeError: ... no attribute 'lose_holder'`) so the case is
proven to be what the code had to satisfy.
Raw output: `test-output.txt` in this folder (the full
`packages/policy-library` discovery, the new suite by direct file
run, the full regression, the handoff check, the new demo, both
earlier policy-library demos, and the leftover-process check).

## Question

Gate 4 (experiment 005) proved a policy placed at a replication
floor of 3 survives its holders going offline, and reported the
floor honestly when placement could not meet it. Its named limit:
the floor was enforced at placement time only. If a holder went
offline *permanently*, the holder count stayed below the floor
forever - reported, never repaired; layer 07 listed self-healing
as open. Can the prototype now repair it: detect that the holder
count is below the floor, recruit a replacement from other
registered, opted-in nodes, copy the policy to the replacement,
restore the floor, and keep serving - and, when no eligible
replacement exists, say below floor instead of pretending?

## Result: all case 4 outcomes observed, 3 new tests pass, full regression green

Run on Python 3.12.3, standard library only:

```
packages/registration     Ran 10 tests  OK   (6 Gate 1 + 4 revocation)
packages/policy-library   Ran 12 tests  OK   (3 Gate 2 + 3 Gate 4 + 3 revocation serving + 3 new)
packages/synchronization  Ran  7 tests  OK   (3 Gate 3 + 4 network)
packages/validation       Ran  9 tests  OK   (4 Gate 5 + 5 scale)
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly
```

38 tests total (baseline before this experiment was 35), every
suite on `unittest discover`, the new suite also by direct file
run, the new demo and both earlier policy-library demos run end to
end, and a post-run process check confirmed zero leftover
`node_server` processes from the network suite. All of it is
quoted verbatim in `test-output.txt`.

| # | Case 4 outcome | Test | Result |
|---|---|---|---|
| 1 | A holder is lost permanently; the holder count drops below the floor and the shortfall is detected and reported, not hidden | `test_healing_restores_floor_after_holder_is_lost` (first half) | PASS: floor 3 placed on 3 holders; `lose_holder("original-host")` drops the count to 2, the placement report returns `holder_count 2, floor_met=False, status below_floor`, `needs_healing()` is True and `shortfall()` is 1. The two survivors still serve while below floor (below floor is Gate 4's risk report, not a refusal). |
| 2 | A replacement is recruited from other opted-in nodes, the policy is copied to it, the floor is restored, and the policy is still served afterward | `test_healing_restores_floor_after_holder_is_lost` (second half) | PASS: `heal(...)` skips a non-opted-in candidate and an existing holder offered again, recruits exactly `replacement-d` (report: 2 -> 3, `floor_restored=True, status healed`), does not take the unneeded spare, and the placement report is back to `available`. Proof the copy is real: after both surviving original holders are also lost, the replacement alone serves the policy with code, policy hash, and Gate 1 record hash all matching. |
| 3 | Negative control (Gate 4 style): when no eligible replacement node exists, the system reports below floor honestly instead of pretending it healed | `test_negative_control_no_eligible_replacement_reports_below_floor` | PASS: candidates are only non-opted-in nodes, an existing holder, and a duplicate of that holder. Healing recruits nobody (report: 2 -> 2, `floor_restored=False, status below_floor`), the placement report still states 2 vs floor 3 as `below_floor`, `needs_healing()` stays True, and the survivors serve while online and report unservable when both go offline. |
| 4 | Boundaries: healing at the floor is a no-op; a temporary offline is not a loss and does not trigger healing; a double loss recruits exactly the shortfall, no more | `test_healing_noop_at_floor_and_recruits_exact_shortfall` | PASS: `heal(...)` at floor with an eligible spare offered returns `status at_floor` and recruits nobody; `go_offline` leaves the holder count (and `needs_healing()`) unchanged; after two holders are lost (`shortfall()` 2), healing recruits exactly the first two eligible candidates in order and stops, leaving the third untaken, and the policy serves. Losing a non-holder raises `KeyError`, never a silent no-op. |

The demo (`python3 packages/policy-library/demo_self_healing.py`)
also runs end to end and prints all four outcomes as expected (see
`test-output.txt`).

## How the mechanism works, in plain words

`ReplicatedPolicy` now separates the two ways a holder stops
serving, because they need different answers. A temporary offline
(Gate 4's `go_offline`) changes nothing about membership: the
holder still counts toward the floor and failover covers serving.
A permanent loss is a new operation, `lose_holder`, which removes
the holder from the set - the count itself drops, which is what
makes the shortfall a countable fact (`needs_healing()`,
`shortfall()`) rather than an inference from liveness flags.

`heal(candidates)` is a single explicit repair step. It does
nothing at all when there is no shortfall. Otherwise it walks the
caller's candidates in order under placement's own eligibility
rules (opted-in only; a node that already holds the policy is
never counted twice; the same node offered twice is taken once),
and for each recruit it performs the same copy placement performs:
construct a real Gate 2 `PolicyNode` over the same verified
registry snapshot, holding the policy payload, opted in. That is
why the replacement can be proven to hold a real copy - it serves
through Gate 2's full verification (snapshot chain, code hash,
policy hash recompute) on every serve, exactly like an original
holder; there is no second, weaker kind of holder. Recruiting
stops the moment the floor is restored. The `HealingReport` pairs
the count before, the recruited ids, and the count after with the
floor, and its status vocabulary is deliberately the placement
report's: `healed` only when the floor is restored, `at_floor`
when there was nothing to heal, `below_floor` when the candidates
ran out first. A partial recruitment is kept - those holders are
real - and reported at its real count.

Design choice worth naming: `place_policy` now stores the
placement context (policy bytes and snapshot path) on the
`ReplicatedPolicy` it returns, because copying to a future
replacement needs exactly what copying to an original holder
needed. A `ReplicatedPolicy` constructed by hand without that
context cannot heal, and `heal` raises `RuntimeError` saying so
rather than recruiting a holder it cannot copy to.

## What failed

The tests-first run failed as designed: before `replication.py`
was extended, all 3 new tests errored on the missing `lose_holder`
(that failure is part of the record above, not hidden). After the
extension, nothing failed: the new suite, the full
`packages/policy-library` discovery (Gate 2, Gate 4, and revocation
serving suites unmodified), the full regression across all four
tested packages, the handoff check, and all three demos passed on
their runs quoted in `test-output.txt`. No earlier test was
modified, and Gate 4's suites pass byte-for-byte as written in
experiment 005.

## Honest limits (this is a prototype, not production)

- Healing is explicit, not automatic: `heal()` runs when a caller
  invokes it, with a candidate list the caller supplies. Nothing
  detects a real machine dying, watches holders, decides that a
  silent holder is permanently lost rather than temporarily
  offline, discovers replacement candidates, or schedules healing.
  "Lost" is a removal the test performs, exactly as "offline" was
  a flag the Gate 4 tests flipped; layer 07's liveness item stays
  open, and with it any claim that this system heals unattended.
- Holders and recruits are in-process `PolicyNode` objects on one
  machine over one shared snapshot file. The "copy" a replacement
  receives is the payload dict plus that same snapshot file given
  to its constructor - no bytes move over a network, no placement
  protocol runs, and experiment 007's networked server processes
  do not heal: a killed process's node is not replaced. Nothing
  about healing across real machines is demonstrated.
- "Independent" and "eligible" inherit Gate 4's meanings exactly:
  distinct node_id and the operator's opt-in flag. A recruit could
  be another process on the same physical machine or under the
  same operator, and this code cannot tell; verified independence
  (machines, operators, failure domains) remains a deployment
  property, unverified here.
- Healing restores the holder count for one policy as constructed
  at placement. Recruits are built over the placement-time snapshot
  and, like all holders here (experiment 008's stated limit), never
  reload: a policy revoked after placement is not un-served by
  healing, and a recruit receives the placement-time record, stale
  window included. Nothing heals a below-floor *serving* state
  caused by revocation or a tampered snapshot.
- Deliberate removal with notification (availability case 3)
  remains unbuilt, as do the external chain anchor and coordinated
  rollback. No performance, scale, or latency numbers were
  measured or are claimed for healing.

## Process captured

Reusable process: when a repair mechanism extends a gate whose
honesty came from its reports, give the repair the same report
shape and the same status vocabulary as the original placement, so
"healed" is held to exactly the standard "available" was: a count
paired with the floor, in the data, with a named status for every
shortfall. Second lesson: separate the temporary state from the
permanent one in the model before building the repair - healing
triggered on Gate 4's offline flag would have recruited duplicates
for holders that were coming back; permanent loss needed its own
operation, and the boundary test (offline does not trigger, loss
does) is what keeps the two from being conflated later.
Opportunity Stack entry: extends experiment 005's gate-outcome
entry with a repair clause (a mechanism that fixes a reported
shortfall must report in the same vocabulary that reported the
shortfall). What would differ next time: record the tests-first
failure output in `test-output.txt` itself, not only in this
README's summary, so the red-to-green sequence is verbatim
evidence like everything else in the file.

## Next, per the build order

Availability case 4 closes the self-healing item layer 07 listed
as open since Gate 4, at prototype scale. The honest distance to
utilization is unchanged in kind and shorter by one item:
deliberate-removal notification (availability case 3), liveness
detection and automatic healing triggers, transport security,
discovery and automatic sync, persistence and restart recovery,
production crypto and identity proofing, coordinated rollback and
consensus, the external chain anchor, revocation delivery to
placed policies, and Gate 5's open production benchmark.
`PBA-FIX-001` remains talk-first and untouched by this experiment.
Nothing here is deployed beyond the local working copy, and
nothing in this experiment changes that.
