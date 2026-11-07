# 036. Scale run at the server-process level

Date: 2026-11-06
Authorization: `AUTHORIZATION.md` (Max authorized the entire
architecture 2026-11-06; Maren owns technical design and
execution and reports results). Experiments 001-010 and 012-035
are done; experiment 011 (deliberate removal) is blocked on a
scope question for Max and is not touched here, and neither is
`proposals/PBA-FIX-001.md` - this run measures the current
design as built and changes no package's behavior.

Case: `tests/test-cases/scale-run.md` case 1, written before
running, including the ladder rule the run was held to.
Code: `packages/synchronization/process_scale_harness.py`
(new). It is a measurement harness, not a test module: the
filename does not match unittest's `test*.py` discovery
pattern, it is not part of the suites, and the full suite
below ran the same 235 tests as the baseline before it
existed. It reuses the packages as built - Gate 1's
`RegistrationChannel` and signed-anchor writer (009's
generation approach), the experiment 007 server
(`node_server.py`, unmodified) with experiment 035's `--peers`
lists and 033's `--mesh-state` persistence, 030's paired
delivery and server-to-server push drivers, and Gate 5's
`TimingStats`.
Raw output: `test-output.txt` in this folder (both completed
runs verbatim, the first attempt's ladder record, and the
full regression), plus `results-run-a.json` and
`results-run-b.json` (the two completed runs' machine-readable
results).
Machine: one shared VM, Python 3.12.3, Linux x86_64, 4 cores,
~8 GB RAM, loopback only. Load average at the start of every
attempt was between 5.0 and 5.7 on the 5- and 15-minute
figures - a contended machine, and the numbers below carry
that warning throughout.

## Question

Experiment 009 proved scale in-process: about 1,000 policies,
10,000 entries, 10 in-process nodes converging in about
2.8 s. But no run had ever pushed the actual server processes
- 007's servers over HTTP, with 035's peer lists and
sync-all, 033's persistence, a 1,000-policy snapshot served
under 012-019's signed registry anchor - past a handful of
nodes and a few entries. Where does the process-level system
actually stand at 009's sizes, and where does the time go?
No target was set for this run and none is claimed as passed
or failed: it is a measurement.

## What ran

Per rung (scale-run case 1): N real `node_server.py`
processes, each started with its own copy of the same
registry snapshot (1,000 registered policies, 009's
generation: 200 users, 5 policies each, deterministic bodies
and codes) and payloads file, a signed registry anchor
(enrolled identity `anchor-signer`), `--serve-policies`,
mesh state persistence ON (each process its own state file),
and a peer-list file naming its two ring neighbors (process i
lists i-1 and i+1 mod N), so the lists form one connected
ring. Plaintext HTTP (TLS off - 014/034 measured the
protected link; it is not the variable here). Witness OFF.
Mesh anchor OFF, stated plainly: the mesh anchor's lifecycle
is exercised per growth in 026/031/032, and switching it on
here would have the harness re-anchor the mesh by hand
between phases, measuring the operator instead of the
transport; the registry side carries the standard anchor.

The drive: 1,000 mesh entries originate at process 0 through
its real mutate path (persistence on, so every accepted
change is persisted and judged); driven `POST /sync-all`
sweeps sweep the ring until all processes report the same
state hash; 80 seeded fetches of registered policies from
processes other than the originator plus 20 control fetches
at the originator, exact bytes asserted; process 3 is killed
with SIGKILL, restarted over its state file, and must recover
the exact state and re-converge; one revocation is registered
in the generation registry, its updated record and re-signed
anchor are delivered paired (030) to process 0, pushed
server-to-server around the ring, and the revoked policy must
refuse 410 `policy_revoked` at every process while the
control policy still serves exactly.

## The ladder, honestly

The harness first refused to import at all: two packages ship
an `anchor.py`, and the harness's path setup let the
registration one shadow the synchronization one `anchored_
sync` needs - the same collision `policy_node.py`'s docstring
documents, met from the other side. Fixed in the harness
(path order forced); no rung had been attempted.

Attempt 1 then ran the ladder and EVERY rung (1: 8x1000x1000,
2: 8x1000x500, 3: 6x1000x500, 4: 4x500x250, 5: 4x200x100)
failed at the same phase, `kill_restart`, with "port N not
free within 15.0s after SIGKILL" - after every earlier phase
had completed at every rung. The defect was the harness's,
not the system's: its port-free probe bound a plain socket,
while the server binds with `SO_REUSEADDR`
(`http.server.HTTPServer.allow_reuse_address`), so the probe
reported the killed server's ports busy for the whole
TIME_WAIT lifetime of their accepted connections although the
server could have rebound them at once. The probe was fixed
to bind the way the server binds; no package changed. Rung
1's attempt-1 measurements through the serving phase are
preserved in `test-output.txt`; rungs 2-5's per-rung detail
files were removed with the attempt-1 workdirs before the
rerun and are NOT retained - only their ladder failure lines
survive, and they are in `test-output.txt` verbatim.

Attempts 2 and 3 (runs A and B) each completed rung 1 at the
first attempt, and the ladder stopped there as designed:
**8 processes, 1,000 policies, 1,000 mesh entries is the
scale this run stands on**, measured twice.

## Results (rung 1: 8 processes, 1,000 policies, 1,000 entries)

| Phase | Attempt 1 (partial, contended) | Run A | Run B |
|---|---|---|---|
| Generation: 1,000 registrations by 200 users | 11.49 s (median 4.18 ms) | 3.11 s (median 2.75 ms, p95 5.11 ms) | 3.09 s (median 2.70 ms) |
| Startup per process (spawn to /health, snapshot + anchor loaded) | 1.00-2.42 s | 0.21-0.36 s | 0.20-0.26 s |
| Origination: 1,000 mutates at process 0, persistence on | 159.13 s (median 129.1 ms) | 41.60 s (median 39.6 ms, p95 66.2 ms) | 44.44 s (median 39.7 ms) |
| - per-mutate, first 20 vs last 20 (median) | 68.0 ms -> 268.2 ms | 20.5 ms -> 56.4 ms | 19.0 ms -> 69.7 ms |
| Convergence: sweep rounds to one hash at all 8 | 1 round, 6.72 s | 1 round, 1.63 s | 1 round, 1.24 s |
| /entries payload; estimated bytes moved in the round | 337,595 B; ~10.8 MB | same | same |
| No-op sweep at origin after convergence | 0.73 s | 0.084 s | 0.088 s |
| Resident memory per process after convergence | 29.7-30.4 MB | 35.5-36.4 MB | 35.5-36.7 MB |
| Serving: 80 fetches from non-origin processes | median 4.00 ms, p95 66.7 ms | median 0.82 ms, p95 1.37 ms, max 3.53 ms | median 1.15 ms, p95 2.36 ms, max 23.2 ms |
| Serving: 20 control fetches at the originator | median 2.31 ms, p95 70.0 ms | median 1.06 ms, p95 2.36 ms | median 0.66 ms, p95 1.19 ms |
| Kill/restart: SIGKILL process 3, restart, exact recovery | FAILED (harness probe, above) | 0.21 s, 1,000 entries, hash exact; post-restart sweep round 0.94 s | 0.21 s, 1,000 entries, hash exact; sweep round 0.78 s |
| Revocation: paired delivery + 7 pushes around the ring | not reached | 0.80 s total (0.089-0.201 s per push); 410 at all 8, control serving | 0.65 s total; 410 at all 8, control serving |
| Whole scenario | - | 51.0 s | 52.8 s |

Determinism check the scenario did not have to pass but
did: the converged state hash is IDENTICAL in all three
attempts (`f7ab1f5a...f47d7`) - same generated content, same
mesh, every time.

## Where the time went, and what dominates

Origination is the scenario: 41.6 of run A's 51.0 s (82%).
Everything else - generation, eight startups, convergence,
100 fetches, a kill and restart, a revocation pushed around
the ring - is the remaining 18%.

1. **The write path is the wall, at process level too, and
   it is 033's persistence discipline that prices it.** Each
   accepted mutate re-serializes the whole mesh state,
   writes it, and re-judges the written file by restoring it
   into a fresh node before swapping it in (experiment 033's
   replace-from-a-verified-candidate rule). That is O(state)
   per accepted change, so growing a mesh one write at a
   time is O(n^2) overall: the per-mutate median climbs from
   ~20 ms at 20 entries to ~56-70 ms at 1,000 (attempt 1,
   under heavier contention: 68 ms to 268 ms). This is the
   same shape 009 found in Gate 1's registration write path
   (full-log re-read per registration), one layer up: the
   prototype's write paths re-check whole sets per write,
   and at 1,000 entries that is already the dominant cost of
   the whole system. Nothing here fixes it; the number is
   the finding.
2. **The expected suspect is refuted at this scale.** Bulk
   anti-entropy moving full entry lists was the named
   suspect going in. One sweep round - 16 per-peer syncs,
   each exchanging the full 337,595-byte entry list in both
   directions, an estimated ~10.8 MB moved - converged all
   8 processes in 1.24-1.63 s on the calm runs (6.72 s
   contended). Wasteful in bytes, cheap in seconds, on
   loopback. On a real network the byte volume would be
   taxed by bandwidth and latency this run does not have;
   the honest statement is that bulk transfer is not the
   bottleneck HERE, not that it never becomes one.
3. **Serving is fast through the whole stack.** A fetch over
   HTTP from another process - snapshot lookup, registry
   anchor consultation, payload read, serve - medians
   0.82-1.15 ms on the calm runs, p95 under 2.4 ms: Gate-5-
   shaped read latency at the process level, at 1,000
   policies. The contended attempt's p95 of ~67 ms shows
   what a loaded shared CPU does to the tail; the median
   held at 4 ms even then.
4. **Recovery and revocation are cheap at this size.**
   Restart from the state file: 0.21 s to healthy with the
   exact 1,000-entry state. A revocation delivered paired
   and pushed around an 8-process ring: under a second,
   end to end, 410 everywhere.

## Suite

Unchanged, and the harness is not in it (see Code above):

```
packages/registration       Ran  25 tests  OK
packages/policy-library     Ran  42 tests  OK
packages/synchronization    Ran 146 tests  OK
packages/validation         Ran  22 tests  OK
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly.
```

Cleanup verified after every attempt: 0 of the harness's own
processes alive, and a /proc scan finds 0 `node_server`
processes on the machine (a count taken through a wrapper
whose own command line names the pattern counts the wrapper -
experiment 035's pitfall, avoided by scanning /proc directly
and inspecting any match).

## What the numbers do and do not support

Supported: at 8 real server processes, 1,000 registered
policies under a signed anchor, and a 1,000-entry persisted
mesh, the system as built starts in well under a second per
process, converges in one sweep round, serves at ~1 ms
median over HTTP, survives a SIGKILL with exact recovery in
~0.2 s, and propagates a revocation around the ring in under
a second - while growing the mesh by single writes costs
tens of milliseconds per write and rising. The distance-to-
utilization list from 009 stands, with its first item now
measured at two layers: whole-set re-checks on write paths
are the nearest wall.

Not supported: anything about a real network (this is one
machine, loopback, no latency, loss, or bandwidth limit);
anything about TLS or mesh-anchor costs at size (both off
here, measured at small size in 014/026/031/032/034);
anything about multi-writer origination (the scenario is
single-writer by design, per the brief); anything past
1,000/1,000 (no larger rung was attempted - rung 1 completed,
so the ladder never needed a smaller one, and no larger one
was in it); and benchmark-grade precision (a shared VM whose
load average sat at ~5-6 throughout: attempt 1 measured the
same phases 3-4x slower than runs A and B under a similar
reported load, so read every number as order-of-magnitude
prototype evidence; the two calm runs agree within ~40% per
phase, most within ~10%).

## Process captured

Reusable process: the honest ladder, held. The ladder's
discipline - per-rung attempts, budgets, the failure named
by phase with its evidence, a smaller completed run counting
as success - is what kept a harness defect from masquerading
as a product limit: five identical failures at one phase,
after every earlier phase had passed at five different
scales, is a signature no honest reading could assign to the
system under test. Second lesson, the twin of 033's: a probe
must judge a resource the way its real consumer judges it -
the port probe now binds with the server's own socket
options. Third, 009's process point applied to itself: its
"what would differ next time" asked for a second full run so
variance is measured, not cautioned about; this run did
that, and the variance turned out to be the attempt-1 story.
What would differ next time: keep per-rung capture files
until the record is written (attempt 1's rungs 2-5 detail
was lost to an over-eager cleanup between attempts - the
failure lines survive, the phase timings do not), and put
the calibration probe's numbers in the experiment folder
rather than /tmp.
Opportunity Stack entry: extends "calibrate-then-measure
scale harness" (009) with "ladder with per-rung evidence,
and probes that judge resources the way their consumers do".

## Next, per the build order

The remaining named gaps are unchanged in kind and now
better priced: the write-path shape (whole-set re-check per
write, at registration in 009 and at mesh persistence here)
is the nearest known wall for utilization at larger sizes;
asymmetric signatures (the 028/029 fence, still symmetric
custody at root), unattended drivers (the sweeps here are
driven, as in 035), real multi-machine runs, and Gate 5's
1 ms production benchmark on production-shaped hardware all
stand where the earlier experiments left them. Nothing here
is deployed beyond the local working copy, and nothing in
this experiment changes that.
