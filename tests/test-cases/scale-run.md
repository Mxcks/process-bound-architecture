# Test Cases: The Scale Run at Server-Process Level (experiment 036)

1. Process-level scale run (added 2026-11-06, experiment 036):
   experiment 009 measured the stack at size IN PROCESS (about
   1,000 registered policies, 10,000 mesh entries, 10 in-process
   nodes converging in about 2.8 s of delivery) and stated its
   own limit in the same record: no network transport is in
   those numbers, no persistence or restart, no concurrent
   processes. Every networked experiment since (007-035) ran
   the real server processes at demonstration size only: a
   handful of processes, a few entries, two or three policies.
   This case runs ONE measured scenario over the real
   `node_server.py` processes, at the sizes below, through the
   surfaces the earlier experiments built, changing no package
   behavior. It is a measurement run: NO time target is set,
   and no number is claimed as passed or failed. What it
   produces is the measured shape of the process-level system
   on this machine, in the style of experiments 006 and 009:
   stage times from the same run as the totals they compose,
   medians as the stable figures, and every reduction of scale
   reported as a ladder with its failure evidence.

   Configuration (stated exactly; anything not listed is off):
   - N server processes (`node_server.py`, plaintext HTTP on
     127.0.0.1, as in experiment 007; TLS is off in this run -
     experiments 014/017 measured the protected link's
     correctness, and this case measures the unprotected
     transport's scale shape).
   - Every process serves policies (`--serve-policies`) from
     its OWN copy of one shared registry snapshot holding
     about 1,000 registered policies, with the full payloads
     file (policy_hash -> code hex) the channel produced.
   - Registry anchor: ON, signed (experiment 019) by one
     enrolled identity ("anchor-signer") whose secret the
     harness holds only in memory and in per-process secret
     files inside the run's work directory; every process is
     started with `--anchor`, `--anchor-signer`, and
     `--anchor-secret-file` pointing at its own anchor file
     over the base snapshot. Witness (experiment 029): OFF.
     Mesh anchor (experiment 026): OFF - the mesh-anchor
     lifecycle is a per-growth operator act across every
     process, proven functionally in 026/031/032; this case
     measures the mesh's transport and persistence shape, and
     says so rather than measuring refusal handling.
   - Mesh state persistence: ON (experiment 033,
     `--mesh-state`) at every process, its own state file.
   - Peer lists: ON (experiment 035, `--peers`); the lists
     form a ring: process i lists process i-1 and process i+1
     (mod N), so the set is connected and no driver names an
     address at call time.
   - The registry is generated through the real Gate 1
     channel with experiment 009's approach (200 enrolled
     author users sharing the registrations round-robin,
     deterministic code bodies `policy body <user> #<j>`),
     because 009's `measure_registration` does not return the
     enrollment secrets this scenario's later legs need (the
     anchor signer's, and the revocation author's). One
     channel object lives for the whole scenario in the
     harness process; enrollment writes only to the audit
     log, never to the registry chain.

   The scenario, in order (each phase timed, each outcome
   asserted, timings recorded per phase):
   (a) Generation: enroll the authors and the anchor signer;
       register the policies; write the snapshot copies, the
       payloads file, and one signed anchor per process over
       the base log. Measured: enroll time, registration
       total and per-registration median/p95 (009's shape),
       snapshot and payloads file sizes.
   (b) Startup: start all N processes (fixed high ports,
       scanned free first). Measured: per-process wall time
       from spawn to answering `/health` - the snapshot's
       verify-and-load at 1,000 entries inside a real process
       start, interpreter start included, stated as such.
   (c) Origination: M mesh entries are mutated at process 0
       only, deterministic keys and values (`scale-key-<i>` /
       `scale-value-<i>`). Measured: total wall time and
       per-mutation median/p95, which at this surface is the
       durability-before-acknowledgement cost of 033's
       persist-on-every-accepted-change at growing state
       sizes, over HTTP.
   (d) Convergence: rounds of driven sweeps - in each round,
       every process in order 0..N-1 is driven through
       `POST /sync-all` over its ring list - until every
       process reports the same mesh state hash, or the
       round budget (12) is spent. Measured: rounds taken,
       per-round wall time, total convergence wall time, the
       `/entries` payload size at the converged set (the
       full-set transfer unit), and an ESTIMATED total bytes
       moved (per-peer sync rounds counted across all
       sweeps x 2 directions x that payload size), labeled
       an estimate. After convergence, one further sweep at
       process 0 is timed: the cost of confirming "nothing
       changed" when bulk anti-entropy still moves the full
       lists both ways. Process memory (VmRSS per server
       process, from /proc) is sampled at this point.
   (e) Serving at scale: seeded-random samples (seed
       20261106, experiment 009's date seed) of policy
       fetches over the wire from processes OTHER than
       process 0, plus a stated control sample from process
       0; every fetch must return 200 with exactly the
       registered bytes. Measured: per-fetch median/p95/max
       across the samples.
   (f) Kill and recovery: process 3 is killed with SIGKILL
       (a real kill, as in 033's suite) and restarted with
       identical arguments on the same port. Measured:
       restart wall time. Asserted: `/health` reports the
       mesh state load as recovered with exactly the
       converged entry count, the restarted process's state
       hash equals the converged hash before any new sync,
       and one further round of sweeps leaves all hashes
       equal.
   (g) Revocation at scale: the first registered policy's
       author revokes it through the channel (008); the
       registry grows by one entry; process 0's anchor file
       is re-written over the grown log by the same signer
       (019's lifecycle, for process 0 alone, as in 030);
       the pair (grown record + advanced anchor) is
       delivered to process 0 through its paired surface
       (028) and must stand; then the pair is pushed
       server-to-server around the ring (030:
       0->1, 1->2, ..., N-2->N-1), each push's report
       asserted delivered. Measured: per-push wall time and
       the leg's total. Final state, asserted at EVERY
       process over the wire: the revoked policy refuses
       410 `policy_revoked`, and a named non-revoked policy
       still serves 200 with its exact bytes.
   (h) Cleanup is part of the contract, as in experiments
       007-035: every spawned process is terminated by the
       harness in a finally block, and the run reports the
       count of its own processes still alive at the end
       (required: 0) beside a /proc scan for node_server
       processes.

   The ladder (stated before any run; a completed smaller
   run with an honest ladder is a successful outcome of this
   case, a claimed larger run without evidence is not):
   - Rung 1: 8 processes, 1,000 policies, 1,000 mesh entries.
   - Rung 2: 8 processes, 1,000 policies, 500 mesh entries.
   - Rung 3: 6 processes, 1,000 policies, 500 mesh entries.
   - Rung 4: 4 processes, 500 policies, 250 mesh entries.
   - Rung 5: 4 processes, 200 policies, 100 mesh entries.
   A rung FAILS if any phase raises, any assertion fails, or
   a phase spends its budget (generation 600 s; startup 60 s
   for any one process; origination 600 s; convergence 12
   rounds or 600 s; any single push 60 s; the whole rung
   1,800 s). A failed rung is recorded with the phase that
   failed and the failure's own text, its processes are all
   cleaned up, and the next rung is tried. The run reports
   the ladder in full: every rung attempted, its outcome,
   and the rung that completed with all measurements.

   What this case does NOT do: it sets no target and claims
   no pass/fail on any time; it changes no package behavior
   to improve a number (a genuine defect the harness exposes
   is recorded as a finding with evidence and the run
   finishes around it where possible, unfixed); it does not
   implement or pre-judge proposals/PBA-FIX-001 (the run
   measures the current design, whole-set re-verification
   and bulk anti-entropy included, exactly as built); and it
   says nothing about multi-machine behavior - every process
   is on this one shared VM, whose load is itself part of
   the recorded conditions.
