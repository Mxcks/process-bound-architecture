# 018. Anchor consultation in the server processes and the Gate 5 verdict

Date: 2026-11-06
Authorization: Max's standing authorization for the entire
architecture 2026-11-06; the gates are Maren's internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`.
Code: `packages/synchronization/node_server.py` (gains the opt-in
`--anchor` argument, passed to the PolicyNode it builds;
`NetworkNode` gains `anchor_path` / `anchor_status`; `/health`
gains `anchor_configured` / `anchor_status`; new
`test_anchor_in_servers.py`, `demo_anchor_in_servers.py`) and
`packages/validation/gate5_harness.py` (`run_gate` gains opt-in
`registry_anchor_path` / `mesh_anchor_path` arguments and two
anchor-evidence helpers; new `test_gate5_anchor.py`,
`demo_gate5_anchor.py`). Experiment 012's check functions and
experiment 015's classification are reused, not re-implemented:
the server passes the anchor straight into experiment 015's
PolicyNode consultation, and the verdict classifies by probing
with experiment 015's own components (a PolicyNode over the
snapshot, whose AnchorMismatch / AnchorStale refusals are the
classification, and an AnchoredSyncNode restored to the canary
node's entry set, whose `anchor_status()` is the classification).
No earlier test was modified, and with no anchor configured every
earlier behavior is unchanged (asserted by a new test on each side
and by the unmodified earlier suites passing in the same run).
This experiment does not touch `proposals/PBA-FIX-001.md` or
implement any part of it, and it does not work on deliberate
removal (availability case 3, experiment 011's blocked item).
Test cases: `tests/test-cases/registration.md`, case 9, and
`tests/test-cases/sync-mesh.md`, case 10, both added to those
files before the code was built, in the detailed format of
policy-availability case 4. The new tests were run against the
unbuilt code first (servers: `node_server.py: error: unrecognized
arguments: --anchor`, 3 failures + 1 error of 4, the pass being
the no-anchor control that already described pre-018 behavior;
verdict: `TypeError: Gate5Harness.run_gate() got an unexpected
keyword argument 'registry_anchor_path'`, 4 errors of 5, the pass
being the no-anchor verdict) so the cases are proven to be what
the code had to satisfy; that failing output is recorded verbatim
at the top of `test-output.txt`.
Raw output: `test-output.txt` in this folder (the tests-first
failure, the full regression across all four tested packages,
both new suites by direct file run, the handoff check, both new
demos, and the leftover-process check).

## Question

Experiment 015 wired anchor consultation into the in-process
Gate 2 and Gate 3 nodes and named its own remaining limit in
plain words: the experiment 007/014 server processes built their
PolicyNode without an anchor path, so networked serving never
consulted an anchor, and the Gate 5 verdict included no anchor
evidence at all. Can the prototype now close both halves, at
prototype scale: a server process given an anchor refuses a
rewritten snapshot at startup because of it (over both of its
existing transports), and a Gate 5 run given anchor evidence
fails on that evidence when a chain that passes every internal
check is not the anchored chain - while a server or a gate run
given no anchor behaves exactly as it did before?

## Result: all case 9/10 outcomes observed, 9 new tests pass, full regression green

Run on Python 3.12.3, standard library only (openssl, as in
experiments 014/017, generates the TLS test credentials at
runtime inside the tests; nothing credential-like is committed):

```
packages/registration     Ran 14 tests  OK   (6 Gate 1 + 4 revocation + 4 anchor)
packages/policy-library   Ran 21 tests  OK   (3 Gate 2 + 3 Gate 4 + 3 revocation serving + 3 self-healing + 5 anchor consultation + 4 liveness)
packages/synchronization  Ran 28 tests  OK   (3 Gate 3 + 4 network + 2 anchor + 4 anchor consultation + 3 coordinated rollback + 4 protected + 4 cert binding + 4 new)
packages/validation       Ran 14 tests  OK   (4 Gate 5 + 5 scale + 5 new)
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly
```

77 tests total (baseline before this experiment was 68), every
suite on `unittest discover`, both new suites also by direct file
run, both new demos run end to end, and a post-run process check
confirmed zero leftover `node_server` processes. All of it is
quoted verbatim in `test-output.txt`.

| # | Case 9/10 outcome | Test | Result |
|---|---|---|---|
| 1 | Negative control, servers (plaintext): a server started with `--anchor` over the rewritten snapshot (passes Gate 2's own `verify_registry_snapshot`, asserted in the test) refuses to start, log naming AnchorMismatch / anchor_mismatch | `test_rewritten_refused_and_genuine_serves_plaintext` (synchronization) | PASS: process exits nonzero on its own; its log carries `AnchorMismatch` and `anchor_mismatch`. |
| 2 | Genuine snapshot + anchor, plaintext: starts and serves as before; health reports the anchor | same test | PASS: `anchor_configured True`, `anchor_status == "anchored"`, policy served with code matching the Gate 1 record, and a mesh mutation over HTTP still lands (transport unchanged). |
| 3 | No anchor, servers: behavior is exactly pre-018 - an anchorless server serves the rewritten snapshot, stated plainly | `test_no_anchor_server_serves_rewritten_snapshot_pre_018` | PASS: `anchor_configured False`, `anchor_status == "no_anchor"`, mallory's rewritten policy serves with 200. |
| 4 | Honest growth, servers: refused at startup as anchor_stale (log carries AnchorStale and not AnchorMismatch), and after re-anchoring the same snapshot starts and serves | `test_honest_growth_refused_as_stale_until_reanchored` | PASS: stale refusal under its own name; after `write_anchor` runs again, load succeeds with status anchored and alice's policy serves. |
| 5 | Protected transport (experiment 014 mode): genuine snapshot + anchor starts and serves over mutual TLS; rewritten snapshot + anchor refuses to start, named anchor_mismatch | `test_genuine_serves_and_rewritten_refused_over_tls` | PASS: health over HTTPS reports anchored and the policy crosses with 200; the rewritten TLS server exits nonzero with AnchorMismatch in its log. |
| 6 | No anchors, verdict: no anchor check appears and the verdict is exactly experiment 006's | `test_no_anchor_verdict_has_no_anchor_checks` (validation) | PASS: verdict passed, zero `anchor_evidence*` checks; the unmodified `test_gate5.py` suite also passes in the same discovery run. |
| 7 | Genuine chains + matching anchors, verdict: every earlier check present and passing, the two anchor checks additional and passing as anchored | `test_genuine_anchors_keep_verdict_passing` | PASS: verdict passed, all checks ok, anchor details say anchored with no mismatch or stale classification. |
| 8 | Negative control, verdict: a registry chain rewritten so it passes every internal check the gate runs (snapshot verifies, canary policy present and serving, mesh lineage verifies - all asserted in the test before the gate runs) makes the verdict FAIL, and the anchor check is the only failing check | `test_rewritten_registry_fails_verdict_on_anchor_only` | PASS: `passed False`, failing list is exactly `["anchor_evidence_registry"]`, detail names `anchor_mismatch`; the mesh anchor check still passes. |
| 9 | Honest growth, verdict (registry): FAIL under the name anchor_stale for the registry check only | `test_honest_registry_growth_fails_verdict_as_stale` | PASS: failing list is exactly `["anchor_evidence_registry"]`, detail names `anchor_stale` and does not name `anchor_mismatch`. |
| 10 | Honest growth, verdict (mesh): a mesh converged on one honestly appended entry past its anchor FAILS under the name anchor_stale for the mesh check only | `test_honest_mesh_growth_fails_verdict_as_stale` | PASS: failing list is exactly `["anchor_evidence_mesh"]`, detail names `anchor_stale` and does not name `anchor_mismatch`. |

Both demos also run end to end (see `test-output.txt`): the
server demo shows the anchored genuine serve, the rewrite served
anchorless and refused anchored, and growth stale until
re-anchored; the verdict demo shows passed with no anchors,
passed with genuine anchors, and FAIL on the internally
consistent rewrite with `anchor_evidence_registry` as the only
failing check.

## How the mechanism works, in plain words

Servers: `--anchor <path>` is one new opt-in argument (it
requires `--snapshot`; an anchor without a snapshot is refused
by the argument parser, because the anchor is for that
snapshot's chain). `main` passes it through `NetworkNode` into
the `PolicyNode` construction that already happened at startup,
so experiment 015's consultation - experiment 012's
`check_registry_against_anchor`, experiment 015's classification
and refusals - runs at startup in exactly the place where a
tampered snapshot already refused the start. Nothing else in the
server changes: the same PolicyNode serves, the same transports
carry it (the flag is orthogonal to `--tls-*` and
`--tls-bind-identity`, and the TLS test exercises them together),
and `/health` now also reports `anchor_configured` and
`anchor_status` (the PolicyNode's own status), so a running
server's anchor standing is inspectable rather than assumed. A
server started without `--anchor` builds its PolicyNode without
an anchor path and is, byte for byte in behavior, the
experiment 007/014 server.

Verdict: `run_gate` gains two opt-in keyword arguments. When a
path is given, the corresponding check is computed on the
scenario as built, before the canary and the faults run, and
appended under its own name (`anchor_evidence_registry`,
`anchor_evidence_mesh`); `GateVerdict.passed` is still exactly
"all checks ok", so bad anchor evidence fails the gate through
the same one mechanism as every other check, with no special
verdict path. The classification is reused by construction, not
copied: the registry check builds a probe PolicyNode over the
harness's snapshot with the anchor and reads the outcome from
experiment 015's own code (success carries experiment 012's
AnchorCheck; AnchorStale / AnchorMismatch refusals carry the
status names and the check), and the mesh check restores an
AnchoredSyncNode probe to the canary node's exact entry set and
reads its `anchor_status()`. An unreadable configured anchor, or
a snapshot that fails the probe's internal verification, is a
failed anchor check with the reason in its detail: configured
evidence that cannot be produced is never a silent pass.

## What failed

The tests-first run failed as designed (recorded at the top of
`test-output.txt`). Two real failures during the build, both
worth recording:

- In the full discovery run, the new synchronization suite first
  failed at import: two packages each ship an `anchor.py`, and in
  a shared discovery process `import anchor` had already resolved
  to the synchronization module when the new test asked it for the
  registry module's `write_anchor`. (The direct file run passed,
  which is exactly why the discovery run matters.) The test now
  loads the registry anchor by file path under a private module
  name, the same discipline `policy_node.py` uses; the discovery
  run is green with that fix. The Gate 5 test needed the twin
  discipline: it inserts only its own package path and lets
  `gate5_harness` set the package order it already sets.
- Both new demos failed on their first runs for demo-side
  reasons, not product reasons: the verdict demo ran `run_gate`
  three times on one harness, and a gate run's injected faults
  are recorded in the canary node's divergence log, so a second
  run judged the first run's faults as an unclean run (the
  harness's existing single-run shape, now stated in the demo,
  which builds one fresh scenario per verdict); and the server
  demo read a server's port file in the instant between its
  creation and its flush. Both demos were fixed and run end to
  end; the passing output is in `test-output.txt`.

After those fixes, nothing failed: both new suites, the full
regression across all four tested packages, the handoff check,
and both demos passed on the runs quoted in `test-output.txt`,
and no earlier test was modified.

## Honest limits (this is a prototype, not production)

- The anchor is still experiment 012's stand-in: a local file,
  unsigned and unwitnessed, overwritable by whoever can write it.
  Consultation changes WHO ASKS, not what the answer is worth: an
  attacker who rewrites the record AND re-anchors it passes
  everywhere, exactly as before. Production still needs the head
  held where the record's writer cannot reach it, and a rule for
  who may re-anchor.
- Consultation remains per-process and opt-in. A server started
  without `--anchor` serves exactly as before this experiment -
  including, as the tests state plainly, serving a fully
  rewritten chain - and a gate run without anchor arguments
  judges internal evidence alone, under which the same rewrite
  passes. Nothing here makes an anchor mandatory, distributes
  one, or notices a process or a gate run that should have one
  and does not.
- The server's anchor covers its REGISTRY SNAPSHOT at startup
  only. The mesh state a server process holds is still a plain
  SyncNode: entries it receives over the wire are judged by
  Gate 3's internal verification alone, with no mesh-anchor
  consultation in the networked process (the in-process
  AnchoredSyncNode of experiment 015 remains the mesh
  consultation, and experiment 018's mesh half lives in the
  Gate 5 verdict, whose canary is in-process). A server also does
  not re-consult its registry anchor after startup: a snapshot
  file changed under a running server is not re-read (servers
  build their PolicyNode once, as experiments 007/014 left them).
- An anchored server is a convergence gate, by experiment 015's
  stated design choice, now at startup: it refuses EVERY snapshot
  past the anchored state, honest or not, until a re-anchor
  happens, and the gate's anchor checks inherit the same
  point-in-time nature (honest growth of either chain is a FAIL
  named anchor_stale until the grown state is re-anchored).
  Re-anchoring is a manual, operator-side step; there is no
  anchor lifecycle automation, no anchor exchange, and no quorum
  or witness agreement on a new head.
- The Gate 5 verdict's anchor evidence is about the scenario it
  built: the snapshot file and the canary node's entry set, at
  the moment the gate runs. It is not evidence about any other
  node, any networked process, or any state after the run, and
  the configured anchors are read by file path on this machine.
- Inherited limits stand unchanged: localhost processes on one
  machine, Gate 1 signing is HMAC prototype crypto, there is no
  Byzantine consensus or quorum, no persistence or restart
  recovery for server mesh state, and no performance, scale, or
  latency numbers were measured or are claimed for consultation
  (the synchronization discovery run remains the slow suite, for
  the process-spawning reasons experiments 007/014 recorded).
- Deliberate removal with notification (availability case 3,
  experiment 011's blocked item) remains unbuilt and untouched.

## Process captured

Reusable process: closing a "nothing consults it" gap is not
done when the in-process path consults - the claim has to be
re-proven at every live surface the earlier experiment named
(process startup over each transport, and the verdict), each
with its own negative control presented the way that surface
actually receives it (a snapshot file at spawn time, a rewritten
chain on disk at gate time). Second lesson, the twin of
experiment 015's import fix: in a repo where two packages ship
same-named modules, a test or harness that imports the shared
name is implicitly betting on process-global import order, and
that bet only fails in the shared discovery run - so load the
module you mean by file path, under a private name, and treat
the discovery run as the evidence, not the direct run.
Opportunity Stack entry: extends experiment 015's consultation
clause with a surface clause (an outside check must be consulted
by every component that serves, accepts, or CERTIFIES that
chain - a verdict that never asks is a surface with the gap
still open, even when the serving path is closed). What would
differ next time: when an experiment adds a suite that spawns
processes, run the full discovery before writing the demos, not
after - both build failures here were visible in discovery and
demo runs respectively, and ordering the cheap global check
first shortens the loop.

## Next, per the build order

Anchor consultation now covers every surface experiments
012-017 named for it: in-process serving and sync (015), the
networked server processes at startup (018), and the Gate 5
verdict (018). The honest distance to utilization is unchanged
in kind and shorter by that item: deliberate-removal
notification (availability case 3, blocked on Max's answers in
experiment 011), a witnessed or signed anchor for production
(plus a rule for who may re-anchor), mesh-anchor consultation
inside the networked processes' sync (their mesh side is still
a plain SyncNode), liveness in the server processes (016's
watcher is in-process), discovery and automatic sync,
persistence and restart recovery, production crypto and
identity proofing, consensus, revocation delivery to placed
policies, and Gate 5's open production benchmark.
`PBA-FIX-001` remains talk-first and untouched by this
experiment. Nothing here is deployed beyond the localhost test
processes the tests themselves spawned and cleaned up, and
nothing in this experiment changes that.
