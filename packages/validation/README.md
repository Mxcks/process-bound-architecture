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
(2026-11-06, experiment `experiments/006-gate5-verification/`), and
**anchor evidence in the verdict built and passing** (2026-11-06,
experiment `experiments/018-anchor-in-servers-and-verdict/`):
run_gate takes opt-in registry and mesh anchor paths (experiment
012's checks, experiment 015's classification, reused) and adds
each configured anchor to the verdict as its own named check; with
none configured the verdict is exactly experiment 006's.
Gate 1 passed in experiment 002, which unblocked this layer's build
per the build order above; the Gate 5 harness is this layer's first
code. A scale harness (`scale_harness.py`, experiment
`experiments/009-scale-run/`) now measures this layer's timed path
and the whole stack at sizes up to a 5,000-entry registry and a
10,000-entry mesh; see "Scale, measured" below. Prototype only,
not production. The module docstrings in `gate5_harness.py` and
`scale_harness.py` are the authoritative lists of honest limits.

## What is here

| File | What it is |
|---|---|
| `gate5_harness.py` | The Gate 5 prototype: a canary scenario built on Gates 1-3 (registration, verified serving node, converged sync mesh), real lineage timing, forced-fault injection with halt-and-rollback, and one verdict computed from the checks. Python standard library only. Builds on the earlier packages; does not modify them. |
| `test_gate5.py` | Automated tests, one per Gate 5 outcome, plus a negative test proving the verdict fails on bad evidence. |
| `demo.py` | Runnable walkthrough of all three Gate 5 outcomes, printing the measured numbers and the verdict. |
| `test_gate5_anchor.py` | Automated tests for anchor evidence in the verdict (experiment 018): no-anchor verdict unchanged, genuine anchors keep it passing, a rewritten registry that passes every internal check fails it on the anchor check alone (anchor_mismatch), honest growth of either chain fails it as anchor_stale. |
| `demo_gate5_anchor.py` | Runnable walkthrough of the anchor-evidence verdicts: no anchors, genuine anchors, and the internally consistent rewrite failing on anchor evidence alone. |
| `test_gate5_signed_anchor.py` | Automated tests for signed-anchor evidence in the verdict (experiment 019): genuine signed anchors with their signing identities keep the verdict passing, a tampered signature on either chain fails the verdict on that chain's anchor check alone (`anchor_signature_invalid`), and without a signing identity signed anchors are judged exactly as experiment 018. |
| `test_gate5_witnessed_anchor.py` | Automated tests for witnessed-anchor evidence in the verdict (experiment 029): countersigned anchors on both chains with their witness identities keep the verdict passing, a signer-alone anchor on either chain fails the verdict on that chain's anchor check alone (`anchor_witness_invalid`), and without a witness identity countersigned anchors are judged exactly as experiment 019. |
| `scale_harness.py` | The scale harness (experiment 009): registration at volume (200 / 1,000 / 5,000 entries, 200 users), sync convergence at size (10 nodes, up to 10,000 entries, shuffled delivery), verified serve against large snapshots, and the Gate 5 canary lineage path across a registry × mesh size grid. Runnable as a CLI: `python3 packages/validation/scale_harness.py --profile full`. |
| `test_scale_harness.py` | Automated tests that the scale harness runs and reports sane results at modest size. The big numbers are measured results in experiment 009, not unit assertions. |

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

## Scale, measured (experiment 009)

The scale harness ran the full stack at size on this machine
(99 s wall for the full profile; tables and verbatim output in
`experiments/009-scale-run/`). Headline medians:

- Registration, 200 users: building a 5,000-entry registry took
  74.5 s because the per-registration cost grows with registry
  size (median 0.54 ms at 200 entries, 2.45 ms at 1,000,
  12.68 ms at 5,000 - Gate 1's `register()` re-reads the whole
  log per call). Full chain verify stays cheap: 72 ms at 5,000.
- Sync: 10 nodes converged 10,000 entries under shuffled delivery
  with identical state hashes, 0.24-0.39 s of delivery per node.
- Serve: verified serve is flat at ~0.005 ms at every registry
  size; building a serving node (one full chain verify) is
  79 ms at 5,000 entries.
- Canary lineage end to end misses the 1 ms target at every
  measured size: 3.57 ms at registry 200 / mesh 100 up to
  168.99 ms at registry 5,000 / mesh 10,000 (miss factor ×169).
  The cost is the two whole-set re-verifications (registry chain
  + mesh lineage); per-transformation lineage stays at 0.008 ms
  at every size. Meeting 1 ms end to end would need incremental
  verification, a design change, not tuning.

The size the evidence supports utilizing at: ~1,000 policies and
10,000 mesh entries comfortably; 5,000 policies with the
registration write path as the named caveat. What breaks first
is the registration write path.

## Honest limits

- In-process, one machine, four mesh nodes, 100 entries. Not a
  staged edge canary, not thousands of concurrent users, and the
  proposals' scale claims are not demonstrated.
- Halt and rollback are Gate 3's local node rollback plus a harness
  flag: no coordinated mesh-wide rollback, no rollout system, no
  signalling beyond the returned result.
- Faults are injected through the normal receive path. A fork is
  only detected once both versions meet, and a full-chain rewrite
  with the original suppressed still verifies on internal evidence
  alone: an external anchor for both chains exists (experiment
  012) and the verdict consults it when the caller configures one
  (experiment 018), but an unconfigured gate run judges internal
  evidence alone. The fork half is an inherited Gate 3 limit.
- Timings include Python interpreter and JSON costs a production
  runtime would not have, and exclude network costs it would.
- Proof verification from the proposals (zk-SNARK / Merkle
  attestations) is unbuilt. (Revocation, absent when this layer
  was first built, was added in experiment 008; its stale-window
  limit is recorded there.)
- Scale limits are now measured, not guessed: see "Scale,
  measured" above and experiment 009 for what breaks first (the
  registration write path) and the sizes the evidence supports.
