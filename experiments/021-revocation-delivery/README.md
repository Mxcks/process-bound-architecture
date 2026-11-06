# 021. Revocation delivery: findings F1 and F2 of experiment 020, fixed

Date: 2026-11-06
Authorization: Max's standing authorization for the entire
Process-Bound Architecture (2026-11-06); the gates are Maren's
internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`.
Code: `packages/policy-library/replication.py`
(`ReplicatedPolicy` gains `reload_snapshot()` /
`update_snapshot`, fixing F1) and
`packages/synchronization/node_server.py` (`_serve_policy`
catches `PolicyRevoked` and maps it to an HTTP refusal, fixing
F2), with new suites
`packages/policy-library/test_revocation_delivery.py` (4 tests)
and `packages/synchronization/test_revoked_policy_network.py`
(2 tests). Both fixes are purely technical and follow existing
shapes exactly: F1 reuses `PolicyNode.reload_snapshot`'s
verification and anchor consultation (experiments 008/015) per
holder, and F2 adds one `except` clause in the endpoint's
existing refusal mapping. No other mechanism's behavior was
changed, and no earlier test was modified except experiment
020's two finding tests, whose assertions this experiment
updates to the fixed behavior (below). This experiment does
not touch `proposals/PBA-FIX-001.md` or implement any part of
it, and it does not work on deliberate removal (availability
case 3, experiment 011's blocked item).
Test cases: `tests/test-cases/policy-availability.md`, case 6
(F1), and `tests/test-cases/sync-mesh.md`, case 12 (F2), both
added to those files before the code was fixed, in the
detailed format of policy-availability case 4. The new suites
and the updated 020 suite were run against the unfixed code
first and failed in exactly the findings' shapes
(`AttributeError: 'ReplicatedPolicy' object has no attribute
'reload_snapshot'`; `RemoteDisconnected` on both transports),
so the cases are proven to be what the fixes had to satisfy;
that failing output is recorded verbatim at the top of
`test-output.txt`. For F2 the failing-before evidence already
existed in nature - experiment 020's suite asserted the drop
itself - and this experiment captured the equivalent failing
assertions for both fixes before changing code.
Raw output: `test-output.txt` in this folder (the tests-first
failures, the pre-build baseline, the full regression across
all four tested packages, both new suites and the updated 020
suite by direct file run, the handoff check, and the
leftover-process checks).

## Question

Experiment 020 ran the whole chain end to end and proved that
its last step - revocation - reached only some surfaces. Two
findings stood open. F1: a policy PLACED on holders (the object
the chain itself builds in its placement and healing stages)
could not receive a revocation at all, because
`ReplicatedPolicy` exposed no reload path and its holders were
built once over the placement-time snapshot. F2: a revoked
policy requested over the network transport WAS refused, but
as a dropped connection with no HTTP response, because
`_serve_policy` never caught `PolicyRevoked`. Can both be
fixed in the existing shapes, so that revocation reaches the
placed object and the network client as what it is - a named
refusal - without changing anything else?

## Result: both findings fixed - 6 new tests pass, 020's stage 8 now asserts the fixed behavior, full regression green

Run on Python 3.12.3, standard library only:

```
packages/registration     Ran 20 tests  OK   (unchanged)
packages/policy-library   Ran 28 tests  OK   (24 baseline + 4 new revocation delivery)
packages/synchronization  Ran 43 tests  OK   (41 baseline + 2 new revoked-over-network; 020's suite still 4, updated assertions)
packages/validation       Ran 18 tests  OK   (unchanged)
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly
```

109 tests total (baseline before this experiment was 103),
both new suites and the updated 020 suite also by direct file
run, and post-run process checks confirmed zero leftover
`node_server` processes after every run. All of it is quoted
in `test-output.txt`.

| # | Outcome | Where it is asserted | Result |
|---|---|---|---|
| 1 | F1 fixed, delivery: after alice revokes, the grown registry is copied over the placement's shared snapshot file, and `reload_snapshot()` makes every holder stop serving - `serve()` on the placed policy raises `PolicyRevoked`; holder set unchanged | `test_reload_delivers_revocation_to_every_holder` (policy-library) and stage 8's updated F1 test in `test_full_chain_integration.py` | PASS |
| 2 | F1, the anchor lifecycle on the reload path (015/019/020 stage 8): reloading the grown snapshot BEFORE re-anchoring is refused as `AnchorStale` and the placed policy still serves; after re-anchoring by the SAME signer the reload stands and returns the entry count (3 in the new suite: two registrations + one revocation; 2 in 020's suite) | `test_reload_delivers_revocation_to_every_holder`; 020's `test_placed_holders_reload_delivers_revocation` | PASS |
| 3 | F1, refused reload leaves state untouched: a reload over a tampered snapshot raises `TamperedRecord` and the placed policy still serves exactly as before (same code, same record hash), exactly as `PolicyNode` behaves | `test_tampered_reload_refused_and_state_untouched` | PASS |
| 4 | F1, non-revoked policies unaffected: bob's policy, placed the same way and reloaded over the same updated snapshot, keeps serving verified after alice's revocation lands | `test_non_revoked_policy_keeps_serving_after_same_reload` | PASS |
| 5 | F1, the remaining fact, stated not hidden: with NO reload called - revocation recorded, re-anchored, and delivered over the snapshot file - the placed object still serves from its placement-time view. Delivery is an explicit call, not a push | `test_no_reload_keeps_placement_time_view` | PASS (asserted on purpose) |
| 6 | F2 fixed, plaintext (007 transport): a server restarted over the updated snapshot answers a request for the revoked policy with a clean HTTP refusal - status 410, body `{"error": "policy_revoked", "detail": ...}` naming the revocation - no dropped connection, no policy bytes; afterward the same process answers health and serves bob's non-revoked policy with 200 | `test_revoked_policy_clean_refusal_over_plaintext` (synchronization) and 020's updated F2 test (over its TLS stage-5 setup) | PASS |
| 7 | F2 fixed, protected link (014 transport): the same clean 410 `policy_revoked` refusal over mutual TLS, and the server keeps serving afterward | `test_revoked_policy_clean_refusal_over_tls` | PASS |
| 8 | Experiment 020's full chain still composes: stages 1-9 pass unchanged, and its two finding tests now assert outcomes 1/2 and 6 above where they previously asserted the findings | `test_full_chain_integration.py` (4 tests, updated) | PASS |

## How the fixes work, in plain words

F1 (`replication.py`): `ReplicatedPolicy.reload_snapshot()`
mirrors `PolicyNode.reload_snapshot` in two phases, so the
refusal discipline is identical, not similar. Phase 1
validates the delivered snapshot ONCE, before any holder's
view changes, by constructing a throwaway `PolicyNode` exactly
as a holder is constructed - same snapshot file, same anchor
consultation this placement carries (the parameters experiment
020 wired into placement) - so a tampered snapshot raises
`TamperedRecord`, honest growth raises `AnchorStale`, a
rewrite raises `AnchorMismatch`, and a badly signed anchor
raises `AnchorSignatureInvalid`, with every holder still
serving its previous view. Phase 2 then calls each holder's
own `reload_snapshot()` in placement order; each holder
re-verifies before rebuilding its own view (that is
`PolicyNode`'s existing behavior, reused, not re-implemented),
and the delivered path becomes the placement's snapshot path,
so a later `heal()` recruits replacements over the updated
record rather than the stale one. The method returns the
entry count, as `PolicyNode`'s does, and `update_snapshot` is
kept as its alias, as on `PolicyNode`. Because `serve()` walks
holders in order and propagates a holder's refusal, a revoked
placed policy is refused as `PolicyRevoked` from the first
online holder - not masked as unavailability, and not skipped
past in search of a holder that would still serve it.

F2 (`node_server.py`): one `except PolicyRevoked` clause in
`_serve_policy`, alongside the three refusals already mapped
there, sending the endpoint's existing refusal shape - a JSON
body with `error` and `detail` - at status 410 Gone, error
`policy_revoked`, detail carrying `PolicyRevoked`'s own
message ("policy revoked by its author (alice): <hash>"). 410
is the one new choice in this experiment and it is a naming
choice, not a mechanism change: the policy existed, its author
took it back, and it is not coming back under this hash
(revocation is final in this prototype, experiment 008), which
is what 410 says and what neither 403 (not opted in) nor 404
(unknown) nor 409 (tampered) says.

Experiment 020's record: its README carries a
superseded-in-part note, appended in that experiment's style
(the note 019 appended to 015's README), stating that F1/F2
were fixed here; its finding text stands unrewritten as the
record of what 020 found. Its suite's two finding tests were
renamed to name the fixed behavior and assert it, including
the before-the-reload placement-time view in F1's test, so the
suite still states the remaining fact instead of dropping it.

## What failed

The tests-first runs failed as designed (recorded at the top
of `test-output.txt`): the F1 suite failed on the missing
method, both F2 tests errored with the dropped connection
itself, and the updated 020 suite failed/errored the same two
ways. After the two fixes, nothing failed: the new suites, the
updated 020 suite, the full regression across all four tested
packages, and the handoff check all passed on the runs quoted
in `test-output.txt`, and no earlier test besides 020's two
finding tests was modified. One process note: the full
regression's synchronization discovery took longer than usual
in this run (242.660s against a ~40-60s norm in earlier
experiments) on a shared machine; it completed green and no
leftover process survived it, and no timing number is claimed
from it either way.

## Honest limits (this is a prototype, not production)

- F1's fix is a CALL, not a delivery system. Nothing pushes,
  broadcasts, or schedules the updated record to a placed
  policy, and nothing calls `reload_snapshot()` automatically:
  a placed policy that is never reloaded serves from its
  placement-time view indefinitely (asserted in outcome 5,
  the same stale window experiment 008 named for standalone
  nodes, now with a closing path on this surface too). The
  liveness watcher (016) does not trigger a reload, and
  healing recruits over whatever snapshot the placement last
  reloaded to - a heal between revocation and reload still
  recruits a holder over the stale record, which then refuses
  only after it reloads or is rebuilt.
- Phase 1's single validation makes a refused reload atomic
  across holders against every refusal the snapshot itself can
  produce (tamper, stale, mismatch, signature). It is not a
  transaction against the file changing BETWEEN phase 1 and a
  holder's phase 2 reload: each holder re-verifies before it
  rebuilds, so a mid-reload change can refuse a later holder
  after an earlier one reloaded. On one machine with the
  caller delivering the file, that window is the caller's own
  doing; a real multi-machine delivery would need the sync
  layer to present one consistent snapshot, which is Gate 3's
  existing terms, unchanged here.
- F2's fix covers the policy ENDPOINT's response shape only.
  A running server still builds its `PolicyNode` at startup
  and never reloads, so a revocation reaches a network client
  only after the server is restarted over an updated snapshot
  (experiment 008's stated shape); the fix changes what the
  client can tell about the refusal, not when the server
  learns of it. The refusal is also unauthenticated
  information: any client that can reach the endpoint learns
  that a hash was revoked, exactly as it already learned
  not-serving / not-found / tampered from the same mapping.
- The 410 status is this prototype's choice, recorded above;
  no client in the repo depended on the previous behavior
  (there was no response to depend on), and no external client
  compatibility is claimed.
- Inherited limits stand unchanged: localhost processes and
  in-process objects on one machine, HMAC prototype signing,
  signed-but-unwitnessed anchors, no consensus, no discovery,
  no persistence, no performance numbers measured or claimed.
- Deliberate removal with notification (availability case 3,
  experiment 011's blocked item) remains unbuilt and
  untouched, and `PBA-FIX-001` remains talk-first and
  untouched by this experiment.

## Process captured

Reusable process: a finding earns a fix in the shape the
finding was recorded in. Both fixes here are the smallest
change inside an existing discipline - F1 reuses
`PolicyNode`'s per-holder reload rather than inventing a
placement-level verifier, and F2 adds one clause to an
existing refusal mapping rather than a new endpoint - and
each fix's tests are the finding's own assertions inverted,
plus the negative controls the finding's surface implies
(refused reload leaves state untouched; the server keeps
serving after refusing). Second lesson: when an experiment
updates an earlier suite's assertions, the earlier experiment
keeps its history in words (a superseded-in-part note,
appended) and changes its truth in code (the assertions) -
never the reverse. Opportunity Stack entry: extends the
composition clause of experiment 020 (a property claimed of
the whole is only as true as its weakest surface) with a
delivery clause: a state change is only as real as its path
to every surface that serves from the old state, and a
surface with no path is a finding, however correct the record
itself is. What would differ next time: record, in the finding
itself, the exact refusal name and status the fix should use
(F2's 410 / `policy_revoked` had to be chosen in this
experiment); a finding that names its expected refusal shape
makes the fixing experiment a pure verification.

## Next, per the build order

Both of experiment 020's findings are closed at prototype
scale. The honest distance to utilization is unchanged in kind
and shorter by those two items: deliberate removal
(availability case 3, blocked on Max in experiment 011), a
witnessed anchor, production crypto and identity proofing,
consensus, discovery, persistence, revocation PUSH delivery
(an automatic path from the record to placed policies and
running servers - this experiment built the receiving end,
not the sending end), and Gate 5's open production benchmark.
`PBA-FIX-001` remains talk-first and untouched by this
experiment. Nothing here is deployed beyond the localhost test
processes the tests themselves spawned and cleaned up.

[Superseded in part, 2026-11-06, experiment 022: the first
honest limit above - "nothing calls `reload_snapshot()`
automatically", the liveness watcher does not trigger a
reload, and a heal between revocation and reload recruits
over the stale record - was built in experiment 022
(`experiments/022-automatic-snapshot-delivery/`), under its
own tests-first run. A `SnapshotDeliveryWatcher` in the style
of experiment 016's liveness watcher now calls this
experiment's `reload_snapshot()` itself, in the check that
notices the shared snapshot file changed (comparing its
presented entry count, chain head, and file fingerprint with
the facts the placed policy last loaded): a clean delivery
takes effect in that check, a refused delivery (tampered,
anchor_stale, anchor_mismatch) is re-reported on every later
check until its cause is fixed, an unchanged check calls
nothing, and a heal after a delivery recruits over the
updated record. This experiment's receiving end is unchanged
and is exactly what the watcher reuses. What still stands
from the limit, restated in 022's own record: nothing pushes
or broadcasts the updated record INTO the snapshot file,
nothing schedules the watcher's checks unattended (its clock
is modeled and caller-driven), the watcher watches placed
policies only - a running server still learns of a revocation
only on restart - and the "revocation PUSH delivery" item in
the Next section above is superseded only to that extent: the
receiving trigger now exists, the sending transport does
not.]
