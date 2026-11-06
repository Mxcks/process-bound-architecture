# packages/validation - Validation Layer

Separate layer 3 of the operational pipeline. Validation does exactly
one job: enforce behavioral boundaries on payloads and pipelines, using
validation channels bound at registration (layer 04). It runs only on
registered libraries. Unregistered code never reaches validation; it is
blocked at the execution boundary first.

Inputs: payload + EAI + bound validation channel.
Outputs: pass, or drop/isolate in real time, with the result logged to
the immutable execution log.
Failure mode: gate bypass or unverified channel execution = hard failure.
Blocked by: Gate 1 registration (see AUTHORIZATION.md build order).

Status: **Gate 5 verification gate prototype built and passing**
(2026-11-06, experiment `experiments/006-gate5-verification/`).
Gate 1 passed in experiment 002, which unblocked this layer's build
per the build order above; the Gate 5 harness is this layer's first
code. Prototype only, not production. The module docstring in
`gate5_harness.py` is the authoritative list of honest limits.

## What is here

| File | What it is |
|---|---|
| `gate5_harness.py` | The Gate 5 prototype: a canary scenario built on Gates 1-3 (registration, verified serving node, converged sync mesh), real lineage timing, forced-fault injection with halt-and-rollback, and one verdict computed from the checks. Python standard library only. Builds on the earlier packages; does not modify them. |
| `test_gate5.py` | Automated tests, one per Gate 5 outcome, plus a negative test proving the verdict fails on bad evidence. |
| `demo.py` | Runnable walkthrough of all three Gate 5 outcomes, printing the measured numbers and the verdict. |

## How to run it

From the repo root:

```sh
# Tests (either command works)
python3 -m unittest discover -s packages/validation -v
python3 packages/validation/test_gate5.py

# Demo (registers a policy, syncs a 100-entry mesh, times lineage,
# injects faults, prints the gate verdict)
python3 packages/validation/demo.py
```

Requires Python 3.10+. No dependencies, no network, nothing is deployed.

## How Gate 5 works, in plain words

1. The gate builds one scenario: a policy registered through Gate 1,
   a copy of its record on an opted-in serving node (Gate 2), and a
   mesh of four nodes that each wrote 25 entries and converged
   (Gate 3). One mesh node is the canary node the gate watches.
2. The canary run times the lineage verification path, over and over:
   re-verify the registry snapshot's hash chain, re-verify every
   mesh entry's identity and hash and chain link, and serve the
   policy through the verified serve. It also times one entry's
   lineage on its own (per transformation). The timings are real
   `perf_counter_ns()` measurements from the machine running it.
3. The gate then takes a checkpoint and injects two bad entries
   through the normal sync path: a Byzantine-style fork (valid hash,
   false history) and a tampered entry (hash no longer recomputes).
   Both must be detected, both must roll the canary node back to the
   exact checkpoint (same state, same state hash, same entry set),
   and the rollout must be marked halted.
4. The verdict is computed from those checks in code: all pass means
   PASS, any failure means FAIL with the failing check named. A test
   corrupts the snapshot on disk and the same code returns FAIL, so
   the verdict is evidence, not a constant.

## The sub-millisecond benchmark, stated plainly

`docs/architecture/06` sets sub-millisecond lineage as the success
benchmark and `docs/architecture/08` says Gate 5 is where that number
is *measured*, not promised. Measured on this machine (details and
full runs in `experiments/006-gate5-verification/`):

- Per transformation (one entry's lineage): median about
  **0.008 ms**, consistent across repeated runs. Under the target,
  by a wide margin.
- End to end (the full verification path over a 100-entry mesh, a
  3-entry registry chain, and a verified serve): median between
  about **1.0 and 1.9 ms** across repeated runs, landing under the
  1.0 ms target in only one of five runs, with p95 spikes of
  46-63 ms from shared-machine scheduling noise. **Not sustained
  sub-millisecond**, and this README does not claim it.

The benchmark is reported inside every gate verdict and is
deliberately not one of the verdict's pass checks: Gate 5's outcome
is that lineage is measured and faults halt and roll back, observed.
A production runtime (Wasm/eBPF, per the proposals) would have to
re-measure on its own hardware before claiming the number.

## Honest limits

- In-process, one machine, four mesh nodes, 100 entries. Not a
  staged edge canary, not thousands of concurrent users, and the
  proposals' scale claims are not demonstrated.
- Halt and rollback are Gate 3's local node rollback plus a harness
  flag: no coordinated mesh-wide rollback, no rollout system, no
  signalling beyond the returned result.
- Faults are injected through the normal receive path. A fork is
  only detected once both versions meet, and a full-chain rewrite
  with the original suppressed would still verify (no external
  anchor exists yet). Both are inherited Gate 3 limits.
- Timings include Python interpreter and JSON costs a production
  runtime would not have, and exclude network costs it would.
- Proof verification from the proposals (zk-SNARK / Merkle
  attestations) is unbuilt; revocation still does not exist.
