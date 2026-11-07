# Experiment 037: unattended operation - a server keeps itself current

**Question.** Every surface built through experiment 035 is
caller-driven: the snapshot check (024), the pushes (027/030/
032), and the peer-list sweep (035) all happen only when a
driver calls. A deployment therefore stays converged only
while something outside it keeps driving it. Experiment 036
measured the composed system working when driven. Can a
server now keep ITSELF current unattended: started with an
interval, run its maintenance cycle on that schedule with no
driver calls after start, reusing the driven surfaces' own
mechanisms rather than reimplementing any of them?

Framing: `tests/test-cases/sync-mesh.md` case 25 (this
experiment's case, written before the build - the flag, the
minimum, the cycle order, the liveness stage's honest shape,
the registry push stage, the refusal composition, the health
fields, the control, and the shutdown shape are all stated
there), experiment 023 (the coordinator order and refusal
composition this cycle carries to the server surface),
experiment 024 (the snapshot check stage), experiment 016
(the liveness vocabulary and threshold), experiment 030 (the
paired push round that carries a revocation unattended), and
experiment 035 (the sweep, and the startup-configuration
shape an interval follows).

## Design choices (at prototype scale)

- The flag is `--auto-cycle-interval <seconds>`, a float, in
  the existing startup-flag style. Off by default: without
  it no thread starts and every behavior is exactly
  experiments 007-036's. The sane minimum is 0.2 seconds,
  enforced in `main()` before the process serves: a smaller
  interval refuses the start naming
  `auto_cycle_interval_invalid`, in the refused-start family
  of `peers_unreadable` (035) and `crl_unreadable` (034). The
  interval is startup-read only; changing it is a restart
  act, and no surface delivers one.
- The cycle order is stated once as `AUTO_CYCLE_ORDER =
  ("snapshot_check", "liveness", "registry_push",
  "sync_all")` and carried on every cycle report. The first
  two stages preserve experiment 023's coordinator order
  exactly - delivery before liveness - for 023's reason:
  what later stages spread must be the record the delivery
  stage just settled. The two spread stages follow: the
  registry push carries that settled record to the peers,
  and the mesh sweep carries the mesh state.
- One implementation per stage, by extraction onto the
  node. The driven surfaces' rounds moved from the HTTP
  handler onto `NetworkNode`, bodies unchanged, and the
  handler endpoints are now thin callers:
  `NetworkNode.sync_once` (the per-peer round of 007/017/035),
  `NetworkNode.classify_sync_outcome` and
  `NetworkNode.sync_all_peers` (035's sweep), and
  `NetworkNode.push_anchor_round` (030's paired push,
  including its record-only fallback and its 502 shapes).
  The snapshot stage needed no extraction: it is 024's
  `NetworkNode.check_snapshot`, already the node's own.
  The cycle (`NetworkNode.run_auto_cycle_once`) calls
  exactly these functions, so a driven call and an
  unattended cycle cannot disagree about what one stage
  does - and cases 16/19/24's suites pass unmodified
  against the extracted code, in the same full run.
- The liveness stage is the one stage with no driven
  surface to reuse, and its shape is stated in case 25
  rather than smoothed over: one health probe per
  configured peer per cycle (through the existing
  `fetch_health`), consecutive missed cycles counted on
  the node, classified in experiment 016's vocabulary
  under 016's own constant (`LOST_AFTER_MISSED_BEATS = 3`,
  imported from `liveness.py`, not copied): alive at 0,
  silent_short at 1-2, lost at 3 or more. Its `healing`
  field is null, plainly: a server holds no placed policy
  and no holder floor, so there is nothing for a heal to
  recruit. The 016/023 trigger's honest server analogue is
  that a lost peer is NAMED in the cycle report and on
  /health, and the same cycle's later stages still attempt
  it. Anything stronger would have required inventing a
  server-side placement, which this experiment does not do.
- The registry push stage is what carries a revocation
  unattended, and it pushes what `push_anchor_round`
  already reads: the PolicyNode's LOADED view, never the
  snapshot file. That is experiment 023's last-good
  principle at this surface with no sidecar needed: in a
  cycle whose snapshot check was refused, the push stage
  spreads the last good record and cannot spread the
  refused file, because the refused file is not what the
  node stands on.
- Refusal composition is 023's rule, enforced structurally:
  `run_auto_cycle_once` confines each stage's refusal or
  exception to that stage's report (a refused snapshot
  check under its own name, `anchor_stale` included) and
  always runs the remaining stages and completes the
  cycle; the thread loop has a second, outer fence that
  records a failed cycle rather than dying. Because 024's
  state discipline never moves the last-loaded facts on a
  refusal, the next cycle re-attempts and re-reports the
  same refusal until its cause is fixed - no retry queue,
  as in 022/023/024.
- The thread: one daemon thread per process, waiting on a
  stop event for one interval between cycles (so a stop is
  responsive and a fresh process serves its startup
  standing for one interval before its first cycle),
  stopped and joined in `main()`'s finally. Daemon, so it
  can never keep a stopping process alive; joined, so an
  orderly stop leaves it verifiably dead and the cycle
  count stable.
- /health reports the standing beside 035's fields:
  `auto_cycle_configured`, `auto_cycle_interval`,
  `auto_cycle_cycles`, and `last_auto_cycle` (the last
  cycle's full report: cycle number, order, one report per
  stage, and the state hash after the whole cycle).

## Results

New suite `packages/synchronization/test_unattended_operation.py`:
7 tests, all passing, run against real server processes
(plus one in-process node for the shutdown leg):

- Health and standing: a cycle-off server reports
  configured false, interval null, 0 cycles, last null,
  unchanged through its life; a cycle-on server (0.5 s)
  reports the interval and completes cycles unattended -
  the last cycle report carries the stated order and all
  four stage reports (a mesh-only, peerless server reads
  no_snapshot / no_peers / no_snapshot / no_peers).
- Refused start: an interval of 0.1 s exits nonzero naming
  `auto_cycle_interval_invalid`, and no port file is ever
  written - nothing is served on a bad interval.
- The convergence story: three servers, cycle ON at 0.5 s,
  lists as in case 24 (A listing only B, B listing A and
  C, C listing nobody). A base state was carried
  unattended first; then new entries at A only, through
  the story's one mutate call each. With NO further driver
  calls anywhere, B and C converged: observed wall 0.6 s
  in the recorded run (0.4 s in an earlier run),
  poll-observed at 0.25 s polling, with A at cycle 2 and
  B at cycle 3 when observed, final hashes equal on all
  three and C's materialized state exact. (See the timing
  note below: these are poll-observed lower bounds, read
  when the poll first saw equality, not precise
  convergence latencies.)
- The control: the same setup with the cycle OFF did not
  converge - after 3 s (several would-be cycles) B still
  lacked A's entry, and A reported 0 cycles. The control
  is what proves the cycle did the story's work.
- The revocation leg: two servers over the same record
  under alice-signed anchors, cycle ON, mutually listed.
  The pair was delivered ONCE to A (case 17's delivery,
  lifecycle followed for A alone) - the last driver call.
  B flipped to 410 `policy_revoked` unattended through
  A's automatic registry pushes: observed wall 0.6 s in
  the recorded run (0.9 s in an earlier run),
  poll-observed, A at cycle 1, B at cycle 2 when observed;
  at the end alice refuses 410 at BOTH servers
  and bob serves his exact bytes at both, and B's health
  reports 3 loaded snapshot entries.
- Refusal composition: one anchored server, cycle ON,
  listing a live peer. The revocation growth was copied
  into its snapshot FILE without the re-anchor; its
  automatic cycles refused the snapshot stage as
  `anchor_stale` across at least 2 cycles (the thread did
  not die; the standing `last_snapshot_check` carries the
  same name), the same cycles' sweeps carried the peer's
  mesh entry in, and the previous view kept serving.
  After the same-signer re-anchor fixed the cause outside
  the process, a later automatic cycle - still no driver
  calls - reloaded, and the policy flipped to 410, with
  the cycle count still advancing.
- Shutdown: an in-process node (0.3 s interval) ran at
  least 2 cycles, `stop_auto_cycle()` returned with the
  thread not alive, and the completed count was stable
  across a further 0.8 s. Spawned servers are terminated
  by the suites' usual cleanup; the leftover-process
  check after the full run found none (see test-output.txt).

Demo `demo_unattended_operation.py` walks the story end to
end and prints each stage, ending `DEMO OK`: health
standing, three-server convergence in 0.6 s (poll-observed)
with cycle counts, and a fourth server whose cycles name
a dead peer lost at 3 missed cycles (named after 1.9-2.1 s
across runs) while its sweep still converges with the
live peer.

Full regression: registration 25/25, policy-library
42/42, synchronization 153/153 (146 baseline + 7 new),
validation 22/22 - total 242/242 against the 235 baseline -
and the EAI handoff check PASS 3/3. The synchronization
clean run of record is the fourth full discovery run; the
three runs before it each failed exactly one test for the
reasons recorded next, and no assertion failed in any of
them.

## What failed on the way (recorded, not hidden)

- Tests first, as required: case 25 was written into
  `tests/test-cases/sync-mesh.md` and the new suite was run
  against the unbuilt code before any implementation. It
  failed as designed - 7 tests run, 4 failures and 3
  errors, the servers exiting on `unrecognized arguments:
  --auto-cycle-interval` and the in-process leg erroring on
  the missing constructor argument and methods. The
  capture is reproduced in test-output.txt.
- The new suite passed 7/7 on its first post-build run and
  the demo passed on its first run. The full
  synchronization discovery then took four runs to a clean
  record, and each dirty run is recorded rather than
  smoothed over (full detail in test-output.txt): runs 1
  and 3 each errored exactly one PRE-EXISTING suite
  (`test_cert_node_binding`, then
  `test_server_to_server_delivery`) in its own `wait_ready`
  helper, which read the server's port file in the window
  between the file's creation and its content landing
  (ValueError on an empty read) under machine load
  (average ~4-5); each affected suite re-ran clean
  standalone (4/4 and 7/7), and no assertion failed in any
  run. Run 2's single failure was in THIS experiment's new
  suite, and it was a test bug, not a product bug: the
  refusal-recovery leg asserted the cycle counter had
  advanced in the same poll that first saw the recovered
  policy refuse 410 - but a reload is visible mid-cycle,
  at the snapshot stage, while the counter advances only
  when the cycle completes. The behavior under test was
  correct (the 410 flip and the refusal history were as
  asserted); the leg now waits for the reloading cycle to
  complete before comparing counts, and passed standalone
  and in run 4. Run 4 is the clean run of record:
  153/153, with cases 16/19/24's suites passing unmodified
  against the extracted handler rounds. No earlier test
  was modified; the one modified test is this
  experiment's own, modified before landing for the reason
  stated.

## Honest limits

- Intervals are operator-chosen and startup-read only.
  Changing the interval is a restart act; no surface
  delivers one, and there is no adaptive scheduling, no
  backoff, and no jitter: a fixed wait between sequential
  cycles, exactly as configured. The 0.2 s minimum is a
  prototype floor against a busy loop, not a tuned value.
- The cycle is in-process only: one thread inside the
  server process. A process that is down runs no cycles;
  there is no external supervisor, no cross-process
  coordination of cycles, and two servers' cycles are
  independent clocks that can push and sweep in any
  interleaving - convergence here rests on each mechanism's
  own idempotence and refusal discipline (a no_change push,
  a regression refusal), not on any cycle synchronization.
- A crash between cycles loses the cycles that would have
  run during the outage, and nothing else: the state the
  next start stands on is whatever the mesh persistence
  (033, where configured) and the snapshot file already
  made durable, and the first cycle after restart resumes
  from that standing. Missed cycles are not replayed or
  caught up; the missed-cycle counts in the liveness stage
  are process memory and reset on restart.
- Convergence timing in the tests and demo is
  poll-observed: the suites poll read surfaces every
  0.25 s with bounded waits (45 s) and report the cycles
  and wall time at the poll that first saw the outcome.
  Those numbers are lower-bound observations on a shared,
  loaded machine, not latency measurements, and no
  convergence-time bound is claimed. Timing honesty, as
  required: no timing leg proved flaky in this environment
  (the new suite ran in full six times - first post-build
  run, the test-output capture run, two standalone re-runs,
  and inside four full discovery runs - with convergence
  observed at 0.4-0.9 s each passing time against 45 s
  bounds), and no wait was widened during the build; the
  single new-suite failure in that history was the counter
  race recorded above, a test-logic bug fixed by waiting
  for cycle completion, not a timing stretch. The load
  did bite elsewhere: the pre-existing suites' port-file
  startup race cost two of the four full runs one error
  each, recorded above. If the machine were loaded enough
  to stretch a cycle round past the bound, the tests
  would fail loudly rather than pass slowly, which is the
  intended shape.
- A cycle is sequential over its stages and, within the
  push and sweep stages, sequential over the peer list
  (035's inherited shape): a slow or unreachable peer
  delays the peers behind it in that cycle, bounded only
  by the underlying HTTP calls' own timeouts, and cycles
  never overlap to compensate.
- The liveness stage observes health surfaces only. It
  recruits nothing, replaces nothing, and verifies no
  identity beyond what the transport itself checks: a
  peer that answers health is alive for this stage's
  purposes even if its later sync round refuses. Healing
  at the server surface - recruiting a replacement
  server, restarting a dead one - is unbuilt, as is any
  alerting on a lost peer beyond the cycle report's name.
- The registry push stage runs every cycle against every
  configured peer, so an idle set exchanges no_change
  pushes on the interval forever. At prototype scale that
  is the honest cost of "keeps itself current"; no
  change-gating beyond the push's own no_change judgement
  (which still costs the round trip and the peer's paired
  judgement) is built or claimed.
- Mutual-TLS cycles compose by construction (every stage
  is a driven surface's own round, binding and CRL
  included) but are not separately driven in this
  experiment's suite, which is plaintext like experiment
  007's; the composed behavior is the earlier experiments'
  evidence plus the shared code path, not a new run -
  035's stated position, inherited.
- Everything else stands as the earlier experiments left
  it: no Byzantine consensus or quorum, local-only
  rollback, HMAC prototype signing, no production
  deployment of any of this. Deliberate removal
  (availability case 3, experiment 011) remains blocked on
  Max and untouched, and `PBA-FIX-001` remains talk-first
  and untouched.

## Process captured

Reusable process: unattended operation is a composition
claim, not a new mechanism - the build was three
extractions and one thread. When a driven surface's round
lives inside its HTTP handler, the first step of making it
unattended is moving the round onto the object the cycle
can call, with the endpoint kept as a thin caller, so the
driven proof (the earlier suites, unmodified) becomes the
unattended proof's foundation instead of a parallel
implementation's alibi. Second lesson: the one stage with
no driven counterpart (liveness at the server surface)
must be designed in the case, with its weakest honest
shape stated (naming, not healing), or the build will
quietly invent a stronger claim than the system holds.
Opportunity Stack entry: extends 023's composition clause
with a scheduling clause - a composition proven under a
driver is a claim about the composition, not about the
system unattended; the schedule, the refusal's survival
across cycles, and the off-state control each need their
own stated case before "runs itself" is a property of the
system rather than of its operator. What would differ next
time: state the cycle's stage list in the case as the
extraction list it implies (each stage named with the
surface whose round it must share), so the refactor's
scope is visible before the first test is written.
