# 009. Scale run: where the prototype stands at size

Date: 2026-11-06
Authorization: `AUTHORIZATION.md` (Max authorized the entire
architecture 2026-11-06, with the direction to keep building until
it is "complete and tested to a scale where we can start utilizing
it"; the gates are Maren's internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`).
All five gates pass (experiments 002-006), nodes are networked
(experiment 007), and revocation is built (experiment 008). This
run does not add a feature; it measures the stack as it stands, at
sizes the earlier experiments never reached.
Code: `packages/validation/scale_harness.py` (new) and
`packages/validation/test_scale_harness.py` (new, 5 tests). The
harness builds on the earlier packages without modifying them:
Gate 1's `RegistrationChannel`, Gate 3's `SyncNode`, Gate 2's
`PolicyNode` / `verify_registry_snapshot`, and Gate 5's
`TimingStats`, `verify_mesh_lineage`, `verify_entry_lineage`, and
`TARGET_LINEAGE_MS` are all used as built.
Raw output: `test-output.txt` in this folder (the full scale run
verbatim, the new suite by direct file run, the full regression,
and the handoff check), plus `results.json` (the full run's
machine-readable results).
Machine: one shared VM, Python 3.12.3, Linux x86_64, 4 cores,
8 GB RAM. The full profile ran in **99 seconds** wall clock, exit
code 0, every size completed - no size had to be skipped.

## Question

The gates prove the mechanisms work at demonstration size (a
3-entry registry, a 100-entry mesh). What are the real numbers
when the same code is pushed to thousands of registry entries and
ten thousand mesh entries - and which part of the stack gives out
first as size grows?

## Result: everything measured completed and verified; the costs are concentrated in two whole-set re-checks, and the registration write path is what breaks first

### 1. Registration at volume (200 users throughout)

| Registry entries | Register total | Per registration, median | Per registration, p95 | Verify full chain (live) | Verify full chain (reload from disk) | Registry file |
|---:|---:|---:|---:|---:|---:|---:|
| 200 | 0.114 s | 0.538 ms | 0.962 ms | 2.611 ms | 4.161 ms | 98,380 B |
| 1,000 | 2.847 s | 2.447 ms | 5.669 ms | 17.802 ms | 21.308 ms | 492,340 B |
| 5,000 | 74.514 s | 12.676 ms | 33.485 ms | 71.966 ms | 119.238 ms | 2,469,140 B |

Enrolling all 200 users took 0.004 s at every size: enrollment is
not a cost. The chain verifies at every size, live and reloaded.
But the per-registration cost grows with the registry itself
(×4.5 from 200 to 1,000 entries, ×5.2 from 1,000 to 5,000),
because Gate 1's `register()` re-reads and re-parses the entire
registry log on every call to find the chain head. Total build
cost is therefore quadratic: on that shape the last 1,000 of the
5,000 registrations account for over a third of the total build
time, and the mean registration at 5,000 entries is 14.896 ms
against 0.569 ms at 200.

### 2. Sync convergence at size (shuffled delivery)

Delivery shuffled everything Gate 3's design allows to be
shuffled: per-origin batches arrived in a seeded-random order per
node, entries in seeded-random order within each batch. (Batch
granularity is one origin's complete chain, because the receive
path verifies the merged set's per-origin contiguity; a batch
leaving a gap is refused as a broken chain by design. The harness
docstring states this; incremental gap-tolerant streaming was not
measured because the prototype does not have it.)

| Nodes | Mutations per node | Total entries | Mutate total | Delivery total (all nodes) | Per-node delivery | Converged |
|---:|---:|---:|---:|---:|---:|---|
| 10 | 200 | 2,000 | 0.024 s | 0.397 s | 0.038-0.042 s | Yes - one state hash `d339bedf…` |
| 10 | 1,000 | 10,000 | 0.139 s | 2.841 s | 0.238-0.390 s | Yes - one state hash `ee66f1a9…` |

At both sizes every node ends holding every entry and all ten
state hashes are identical. No divergence was detected in clean
delivery. Delivery per node grows faster than the entry count
(each batch receive re-verifies and re-hashes the whole merged
set), but at 10,000 entries it is still under 0.4 s per node.

### 3. Serve path at size

| Registry entries | Snapshot verify + load (PolicyNode build) | Verified serve, median | Verified serve, p95 |
|---:|---:|---:|---:|
| 200 | 3.060 ms | 0.005 ms | 0.006 ms |
| 1,000 | 14.129 ms | 0.005 ms | 0.006 ms |
| 5,000 | 78.956 ms | 0.006 ms | 0.007 ms |

300 serves per size, spread across the whole registry, every serve
returning exactly the registered bytes. The serve itself is flat:
it is a lookup plus two small hashes, and registry size does not
touch it. What grows is the one-time cost of building a serving
node, which re-verifies the whole chain once (linear, and the same
operation as the standalone verify in measurement 1).

### 4. The Gate 5 canary end-to-end lineage, repeated at size

The Gate 5 timed path (re-verify the registry snapshot chain,
re-verify the full mesh lineage, serve verified) run across the
grid. Medians; the target is the 1 ms figure from
`docs/architecture/06`, reported as a miss factor, never a claim.

| Registry | Mesh | Iters | Registry stage | Mesh stage | Serve stage | End to end | Miss factor | Per transformation |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 200 | 100 | 50 | 2.656 ms | 0.902 ms | 0.007 ms | 3.570 ms | ×3.6 | 0.008 ms |
| 200 | 1,000 | 30 | 2.976 ms | 9.469 ms | 0.017 ms | 12.767 ms | ×12.8 | 0.008 ms |
| 200 | 10,000 | 15 | 3.143 ms | 95.590 ms | 0.023 ms | 98.737 ms | ×98.7 | 0.008 ms |
| 1,000 | 100 | 50 | 13.108 ms | 0.910 ms | 0.011 ms | 14.032 ms | ×14.0 | 0.008 ms |
| 1,000 | 1,000 | 30 | 13.356 ms | 9.091 ms | 0.012 ms | 22.480 ms | ×22.5 | 0.008 ms |
| 1,000 | 10,000 | 15 | 15.319 ms | 97.416 ms | 0.028 ms | 113.085 ms | ×113.1 | 0.008 ms |
| 5,000 | 100 | 50 | 72.032 ms | 1.032 ms | 0.024 ms | 73.086 ms | ×73.1 | 0.008 ms |
| 5,000 | 1,000 | 30 | 69.716 ms | 9.505 ms | 0.023 ms | 79.511 ms | ×79.5 | 0.008 ms |
| 5,000 | 10,000 | 15 | 72.670 ms | 96.676 ms | 0.027 ms | 168.986 ms | ×169.0 | 0.008 ms |

Every iteration verified (`all_verified=True` in all nine
configurations). Three facts stand out:

- **The end-to-end number misses the 1 ms target at every size,
  including the smallest grid point** (×3.6 at 200 registry /
  100 mesh). For continuity: experiment 006's native scenario
  (3-entry registry, 100-entry mesh) measured 1.0-1.9 ms; growing
  the registry alone to 200 entries puts the registry stage at
  2.7 ms, already over target before the mesh is counted.
- **The per-transformation cost never moves**: median 0.008 ms at
  every mesh size from 100 to 10,000, p95 0.009-0.010 ms. The
  unit lineage check is not what degrades.
- **The whole end-to-end cost is the two whole-set re-checks.**
  At the largest configuration the mesh lineage stage is 57% of
  the time and the registry chain stage 43%; the verified serve
  is 0.02%. Both big stages are "re-verify everything, from
  scratch, every time" - linear in the size of the set they cover.
  The stages are additive and independent: growing either set
  grows the total by that set's verification cost, as the grid's
  rows and columns show.

### Regression

```
packages/registration     Ran 10 tests  OK
packages/policy-library   Ran  9 tests  OK
packages/synchronization  Ran  7 tests  OK
packages/validation       Ran  9 tests  OK   (4 Gate 5 + 5 new scale tests)
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly
```

35 tests total (baseline before this experiment was 30), all
passing, plus the new harness suite run by direct file. Quoted
verbatim in `test-output.txt`.

## What size the evidence supports utilizing at

- **Up to ~1,000 registered policies and 10,000 mesh entries:
  comfortably.** Registration writes are 2.4 ms at the median,
  a full chain verify is 18 ms, a serving node builds in 14 ms,
  ten nodes converge 10,000 entries at under 0.4 s of delivery
  per node, and a full canary verification is 13-22 ms at mesh
  sizes up to 1,000. Every operation is interactive-grade.
- **At 5,000 policies: usable, with the write path as the named
  caveat.** Reads, serves, verifies, and convergence are all
  still fast (serve 0.006 ms, full verify 72 ms); only
  registration writes are degrading - 12.7 ms at the median and
  rising linearly, 74.5 s to build the registry from empty.
  A workload that registers rarely and serves often fits here
  today; a registration-heavy one does not.
- **The 1 ms end-to-end lineage target is not met at any
  measured size** and the evidence says it cannot be met by this
  design at any size above trivial: the path is defined as
  re-verifying two whole sets, so its cost has a floor of
  (registry verify + mesh verify), both linear. Meeting the
  target would need incremental verification (verify only what
  changed, anchored to a previously verified head), which is a
  design change, not a tuning change. Per-transformation lineage
  (0.008 ms) remains the honest sub-millisecond number in this
  stack.

## What breaks first, in order

1. **Registration writes.** Per-registration cost is linear in
   current registry size (full-log re-read per call), so build
   cost is quadratic. Trend math, labeled as extrapolation and
   not measurement: at the measured slope, a 25,000-entry
   registry would take on the order of 30 minutes to build with
   per-registration medians around 60 ms. This is the first
   wall, and the fix is small and local: cache the chain head in
   the channel instead of re-reading the log to find it. It is
   the clear first fix candidate to raise as a proposal.
2. **Whole-set re-verification on the canary path.** Linear per
   check and incurred in full on every run, so end-to-end
   lineage costs 169 ms at the largest measured configuration.
   Fine as a periodic audit at these sizes; disqualifying as a
   per-request check, and the blocker for the 1 ms target.
3. **Sync delivery's whole-set re-verification.** Each batch
   receive re-verifies and re-hashes the merged set, so delivery
   cost per node grows with (entries × batches). Benign at
   10,000 entries (0.24-0.39 s per node); the same shape as
   item 2, so it steepens next as meshes grow.

What does not break at any measured size: enrollment,
per-transformation lineage, verified serve, standalone full-chain
verification as an occasional audit, and convergence correctness
itself (no lost entries, no divergence, identical hashes at both
sync sizes).

## What failed

Nothing in the run failed: every size completed inside its
budget, every chain verified, every mesh converged, every serve
and canary iteration verified, and the regression is green. One
measurement caution instead of a failure: this is a shared VM,
and the p95/max columns show scheduling noise (single samples up
to 204 ms on the canary path, a 103.8 ms max on one registration
at 5,000 entries). The medians are the stable figures and are
what every claim above rests on; a scratch calibration run
before the harness was written also showed the same shapes at
roughly 2-10× worse absolute numbers under load, so treat the
absolute values as this-run-on-this-machine, and the *shapes*
(the growth rates, the flat costs, the stage shares) as the
durable findings.

## Honest limits (this is a prototype, not production)

- One shared VM, everything in-process or in localhost files,
  Python stdlib only. No network transport is in these numbers
  (experiment 007's localhost serving is a correctness result,
  not a latency one), no concurrent users, no persistence or
  restart under load, no adversarial load.
- The registration numbers measure Gate 1's code exactly as
  built, including its full-log re-read per registration. They
  are evidence about this prototype, not about the architecture's
  ceiling: the write-path fix named above is local to one method.
- "Shuffled delivery" is shuffled at whole-chain batch
  granularity for the reason stated in measurement 2; streaming
  delivery with gaps is refused by Gate 3's design and was not
  measured.
- The canary mesh at size is built through Gate 3's real receive
  path from entries made by Gate 3's own builder, in one
  SyncNode - it is the lineage-verification load that is
  measured, not a multi-node timing.
- Extrapolations (25,000 entries) are arithmetic on the measured
  slope, labeled as such, and were not run.

## Process captured

Reusable process: calibrate before you build the harness. A
20-line scratch probe (kept out of the repo) timed the three
expensive operations at growing sizes first, which set the
harness's sizes, proved every size would finish in minutes, and
predicted the O(n)-per-registration shape the full run then
confirmed - no size had to be aborted or re-run. Second lesson:
when a scale claim decomposes into stages, time the stages inside
the same iterations as the end-to-end path, so the breakdown and
the total are the same measurements and cannot disagree.
Opportunity Stack entry: "calibrate-then-measure scale harness"
(new, from this experiment), extending the Gate 5 entry's
"benchmarks are measured, never asserted" rule to size claims.
What would differ next time: record a second full run back to
back when the machine is shared, so run-to-run variance is itself
a measured number in the record rather than a caution paragraph.

## Next, per the build order

The scale evidence changes the honest distance-to-utilization
list in one place: the registration write path (item 1 above) is
now the nearest known wall, ahead of the items experiment 008
listed (revocation delivery, transport security, discovery and
automatic sync, persistence and restart recovery, production
crypto and identity proofing, coordinated rollback and
consensus, the external chain anchor, and Gate 5's production
benchmark - which this run shows is also an incremental-
verification design question, not only a hardware one). Nothing
here is deployed beyond the local working copy, and nothing in
this experiment changes that.

[Superseded in part, 2026-11-06, experiment 036: this run's
scale evidence is in-process only - Gate 3's SyncNode objects
in one Python process, no servers, no HTTP, no persistence.
Experiment 036 (`experiments/036-process-scale-run/`) now
measures the same sizes at the server-process level: 8 real
`node_server.py` processes, the same 1,000-policy generation,
a 1,000-entry mesh originated over the wire and converged by
035's peer-list sweeps with 033's persistence on, policies
served over HTTP at ~1 ms median, a SIGKILL restart
recovering the exact state in ~0.2 s, and a revocation pushed
around the ring to a 410 everywhere in under a second. Its
headline confirms this run's verdict one layer up: the write
path is still the wall - origination with persistence on
costs a median of ~40 ms per single write at 1,000 entries
and rising (the whole-set re-check per write, this time
033's persist-and-judge), 82% of the scenario's time, while
bulk anti-entropy - this era's expected suspect - converged
the 8 processes in one sweep round of 1.2-1.6 s. This run's
in-process numbers themselves stand as measured.]
