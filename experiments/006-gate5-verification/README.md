# 006. Gate 5 Verification Gate Prototype

Date: 2026-11-06
Authorization: `AUTHORIZATION.md` (Max authorized the entire
architecture 2026-11-06; the gates are Maren's internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`. Gates 1,
2, 3, and 4 passed in experiments 002, 003, 004, and 005, which per
that file is the signal for Gate 5, the last of the five gates.)
Code: `packages/validation/` (new `gate5_harness.py`, `test_gate5.py`,
`demo.py`), the validation layer's first code, built on Gates 1-3
without modifying them: `packages/registration/registration.py`,
`packages/policy-library/policy_node.py`, and
`packages/synchronization/sync_mesh.py` are unchanged.
Test cases: `docs/architecture/08` Gate 5 outcomes, and
`tests/test-cases/sync-mesh.md` case 4 (lineage traced in under one
millisecond, a benchmark to be measured, not assumed), which belongs
to this gate.
Raw output: `test-output.txt` in this folder, including five repeated
canary runs so the timing variance is on the record, not averaged away.

## Question

Can the Gate 5 outcomes be observed in a small, runnable prototype:
a canary that actually measures end-to-end lineage time under load
and reports the real numbers against the sub-millisecond benchmark,
a forced Byzantine/divergence fault that halts rollout and rolls
back to the exact pre-fault checkpoint, and one pass/fail verdict
computed from the measured evidence in code?

## Result: all three outcomes observed, 4 of 4 automated tests pass, gate verdict PASS

Run on Python 3.12.3, standard library only:

```
Ran 4 tests in 0.411s
OK
```

The `packages/validation` discovery run is 4 of 4. Regression the
same day, unmodified suites: Gate 1, 6 of 6 pass; Gate 2 + Gate 4
(`packages/policy-library`), 6 of 6 pass; Gate 3, 3 of 3 pass; the
EAI handoff check (`scripts/verify-handoffs.py`) still passes 3 of 3
vectors. All of it is quoted verbatim in `test-output.txt`.

| # | Gate 5 outcome | Test | Result |
|---|---|---|---|
| 1 | A canary run actually measures end-to-end lineage time (register, sync, serve, and time the lineage verification path) under load, reporting real numbers against the 1.0 ms target | `test_canary_measures_real_lineage_times` | PASS: 30 timed runs over a converged 100-entry mesh and a 3-entry registry chain; the reported medians/means are asserted equal to the statistics of the raw samples, so the numbers are measurements, not constants; served bytes matched the registered policy on every run. Measured numbers in the section below. |
| 2 | A forced fault (Byzantine-style fork; also a tampered entry) triggers halt-and-rollback automatically, and state ends exactly at the pre-fault checkpoint | `test_forced_fault_halts_and_rolls_back_to_checkpoint` | PASS: fork detected with reason `forked_entry`, tampered entry with reason `tampered_entry`; both rolled back; after each, materialized state, state hash, and entry-id set are identical to the checkpoint (asserted three ways); rollout state is `halted_and_rolled_back`. |
| 3 | The gate as a whole produces one pass/fail verdict from measured evidence, thresholds checked in code | `test_gate_verdict_from_measured_evidence` and `test_gate_verdict_fails_on_bad_evidence` | PASS: a clean run returns PASS with 9 of 9 checks OK and carries the measured benchmark numbers; with the registry snapshot corrupted on disk, the same code returns FAIL and names `canary_path_completed` as the failing check. The verdict is computed, not hardcoded. |

The demo (`python3 packages/validation/demo.py`) also runs end to
end and prints all three outcomes True with gate verdict PASS (see
`test-output.txt`).

## The measured timing numbers, stated plainly

All numbers are `time.perf_counter_ns()` measurements from this
machine (a shared VM), recorded verbatim in `test-output.txt`.

Per transformation (one entry's lineage: EAI recompute, hash
recompute, chain-link check):

- Median **0.0079-0.0133 ms** across every captured run. The 1.0 ms
  target is met, consistently, by roughly two orders of magnitude.

End to end (the full lineage verification path per run: registry
snapshot chain re-verification, all 100 mesh entries' lineage,
plus a verified serve):

- Five repeated runs of 200 iterations: medians **1.1249, 1.0553,
  0.9984, 1.5239, 1.8959 ms**. The 1.0 ms target was met in one of
  five runs. Means were 5.05-5.98 ms and p95 was 46-63 ms, pulled
  up by scheduling spikes on the shared machine.

So, honestly: **per-transformation lineage is sub-millisecond on
this machine; end-to-end lineage over the whole scenario is not
sustained sub-millisecond** in this Python prototype, and no claim
is made that it is. `docs/architecture/06` sets sustained
sub-millisecond lineage as the production success benchmark; what
Gate 5 proves is that the measurement is real, repeated, and
reported either way. Per `docs/architecture/08`, the benchmark is
reported inside the verdict and is not a pass check, because Gate
5's outcome is that the gate measures and halts, not that a Python
prototype hits a production runtime's number. A Wasm/eBPF runtime
would re-measure on its own hardware; the per-transformation margin
(about 125x) is the encouraging part, the end-to-end Python overhead
(JSON re-encoding and interpreter cost per entry, times 100) is the
expected part.

## How the mechanism works, in plain words

The harness builds one scenario out of the earlier gates, unmodified:
a policy registered through Gate 1's channel, its record copied to an
opted-in Gate 2 serving node, and four Gate 3 mesh nodes that each
wrote 25 entries and converged on the same 100 entries. Timing wraps
the verification path itself, not the setup: chain, lineage, serve.
The fault is not simulated inside the harness: the forked entry is
built with Gate 3's own `build_entry` (an individually valid hash
claiming a false history) and the tampered entry with Gate 3's
`tamper_entry`, and both arrive through the node's normal
`receive_entries` path, so the detection and rollback observed are
Gate 3's real ones; the harness adds the checkpoint comparison
(state, hash, entry set, exactly) and the halt flag. The verdict is
`all(checks)` over nine named checks, each carrying its evidence as
a detail string; the negative test proves the same code says FAIL
when the evidence is bad.

## What failed

No test failed. One build error occurred and was fixed before the
first full run: an incomplete line in `build_scenario` (an unclosed
enrollment call) was a SyntaxError on first import; it was corrected
and the suite then passed on its first full run, on both invocation
styles (`unittest discover` and direct file run).

One result is a miss against a benchmark rather than a failure of a
test, and it is recorded as a miss, not softened: end-to-end lineage
does not sustain sub-millisecond here (section above). Gate 5's own
outcomes (measure, halt, roll back, verdict) do not require it to;
`docs/architecture/06`'s production benchmark remains unmet by this
prototype and is carried forward as an open production claim, not a
passed one.

## Honest limits (this is a prototype, not production)

- In-process, one machine: the "canary" is a local scenario (one
  channel at setup, one serving node, four mesh nodes), not a staged
  edge canary deployment, and "load" is 100 converged entries, not
  thousands of concurrent users or high-velocity real mutations
  (`docs/architecture/06` describes the production shape; this
  prototype demonstrates the gate's mechanics at small scale).
- Halt and rollback are local: Gate 3's per-node rollback plus a
  harness state flag. There is no rollout system to halt, no
  coordinated mesh-wide rollback, and other nodes are not signalled
  beyond the returned `SyncResult`.
- Faults arrive through the normal receive path in-process. Real
  Byzantine behavior across a network, quorum, and BFT consensus
  remain unbuilt (Gate 3's limits, unchanged), as do the external
  anchor that would catch a full-chain rewrite, proof verification
  (zk-SNARK / Merkle attestations), and revocation.
- Timings are this machine's: they include Python and JSON costs a
  production runtime would not have, exclude network costs it
  would, and vary run to run (the five-run spread is in
  `test-output.txt` on purpose).
- The gate judges this one scenario shape. It does not check
  liveness or availability (Gate 4's reports do) and does not
  exercise the runtime-filters package, which remains a contract
  with no code.

## Process captured

Reusable process: never let a gate report its own headline number
from a single run. Capture repeated runs with their spread (median,
mean, p95, and the count of runs meeting the target), assert in the
test that the summary statistics equal the statistics of the raw
samples, and pair every PASS verdict with a negative test that
feeds the same code bad evidence and requires FAIL. That trio
(raw-sample assertion, repeated-run spread, negative-evidence test)
is what makes a verdict evidence rather than ceremony, and it
applies to any future gate or canary in this repo. Extends
experiment 005's gate-outcome entry on the Opportunity Stack. What
would differ next time: record the machine's load average next to
timing runs so the p95 spikes have a stated cause on the record,
and measure the production-shaped path (single policy's lineage
through the runtime filters) once the runtime package has code,
since end-to-end-over-everything is the pessimistic bound, not the
per-policy path a write gate would actually run.

## Next, per the build order

Gate 5 was the last of the five gates in
`docs/architecture/08`: all five now pass at prototype scale under
the standing authorization. What this unlocks, per that file, is
"any production claim" becoming *discussable*, not made: the
architecture is demonstrated end to end in miniature (register,
decentralize, sync, survive, verify), and the open items named
across experiments 002-006 (production crypto and identity
proofing, network transport, coordinated rollback and consensus,
external chain anchor, revocation and deliberate-removal
notification, proof verification, runtime filters code, and the
sustained sub-millisecond end-to-end benchmark) are the honest
distance between this prototype stack and production. Nothing here
is deployed, and nothing in this experiment changes that.
