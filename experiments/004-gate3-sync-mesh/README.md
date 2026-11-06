# 004. Gate 3 Synchronization Mesh Prototype

Date: 2026-11-06
Authorization: `AUTHORIZATION.md` (Max authorized the entire architecture;
Gate 3 is Maren's internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`. Gates 1
and 2 passed in experiments 002 and 003, which per that file is the
signal for Gate 3.)
Code: `packages/synchronization/` (`sync_mesh.py`, `test_sync_mesh.py`,
`demo.py`), built on the Gate 1/2 hash-chain discipline.
`packages/registration/registration.py` and
`packages/policy-library/policy_node.py` are unchanged.
Test cases: `tests/test-cases/sync-mesh.md`, cases 1-3.
Raw output: `test-output.txt` in this folder.

## Question

Can the three Gate 3 outcomes be observed in a small, runnable
prototype: concurrent mutations converging with no central lock, a
partitioned node rejoining and converging, and split-brain divergence
detected with automated rollback?

## Result: all three outcomes observed, 3 of 3 automated tests pass

Run on Python 3.12.3, standard library only:

```
Ran 3 tests in 0.004s
OK
```

Regression the same day, unmodified suites: Gate 1, 6 of 6 pass;
Gate 2, 3 of 3 pass; the EAI handoff check
(`scripts/verify-handoffs.py`) still passes 3 of 3 vectors. All of it
is quoted verbatim in `test-output.txt`.

| # | Gate 3 outcome | Test | Result |
|---|---|---|---|
| 1 | Concurrent mutations across nodes converge with no central lock | `test_concurrent_mutations_converge_no_central_lock` | PASS: three nodes each make 3 mutations entirely independently first (no lock, coordinator, or leader exists in the module), including concurrent writes to the same keys. Each node then receives the full 9-entry set in a different shuffled order (seeded shuffle). All three end with identical entry sets, identical materialized state, and identical state hashes. Conflicts resolve deterministically in the (lamport, origin, seq) total order on every node (e.g. `shared-counter-note` ends as `from-c` everywhere). Every entry carries a 64-hex EAI and a hash that recomputes. |
| 2 | A partitioned node rejoins and converges, or rollback fires (convergence implemented) | `test_partitioned_node_rejoins_and_converges` | PASS: after an initial converged state, node-c is partitioned (no sync calls involve it) while node-a and node-b keep mutating and syncing; node-c also mutates locally, including a conflicting write to the same key. During the partition the views genuinely differ (asserted). On rejoin, a full mesh sync converges all three: identical entry sets, state, and hashes, no entries lost, no divergence logged, and the conflicting key holds one identical value on all nodes. |
| 3 | Split-brain / divergence is detected and triggers automated rollback | `test_divergence_detected_and_triggers_rollback` | PASS, three shapes against one converged node: (a) a tampered entry (value changed, hash kept) is refused with reason `tampered_entry`; (b) a forked entry (same (origin, seq) and prev_hash as the real entry, different content, own hash individually valid: split-brain) is refused with reason `forked_entry`, detection by identity conflict, not hash error; (c) an entry extending an origin's history with a wrong prev_hash link is refused with reason `broken_chain`. Each refusal returns `divergence_detected=True, rolled_back=True, accepted=0`, appends to the node's divergence log (all three reasons on record, in order), and leaves the node's materialized state, state hash, and entry set exactly at the pre-attack converged values. The node is not bricked: an honest sync afterwards converges on new legitimate state. |

The demo (`python3 packages/synchronization/demo.py`) also runs end
to end and prints all three outcomes as True (see `test-output.txt`),
including the fork case's `SyncResult` showing the rollback signal.

## What failed

Nothing in the test run. The Gate 3 suite, both regressions, and the
demo passed on their first full runs, on both invocation styles
(`unittest discover` and direct file run). Two design points were
settled while building, not by failures: verification runs over the
merged candidate set as a whole (per-origin contiguity and chain
linkage after a bulk merge), which is what makes out-of-order delivery
safe; and a successful verified merge (or local mutation) is what
takes the checkpoint, so "rollback" always means "back to the last
state this node itself verified", never to a state some other node
asserted.

## Honest limits (this is a prototype, not production)

- Nodes are in-process Python objects on one machine exchanging
  entries by method call. There is no network transport, no peer
  discovery, no gossip schedule, no persistence, and no restart
  recovery. "Partition" is modeled by simply not calling sync, and
  "rejoin" by calling it again. Nothing about many nodes, real
  network partitions, or availability at scale is demonstrated.
- There is NO real Byzantine consensus. Detection is local: hash
  recomputation, per-origin chain verification, and fork comparison
  when two views of the same (origin, seq) meet. There is no quorum,
  no voting, no BFT protocol. A fork that never meets its other
  version is detected by nobody, and a coordinated attacker is out
  of scope. Gate 5 (forced Byzantine fault against a canary harness,
  observed halt and rollback) remains ahead and is not claimed here.
- Rollback is local to the detecting node and restores that node's
  own checkpoint. There is no coordinated mesh-wide rollback
  protocol; peers learn of a divergence only through the `SyncResult`
  returned to whoever called the sync.
- Tamper evidence inherits the Gate 1/2 limit: one edited entry is
  detected, and a fork is detected when both versions are seen, but
  an attacker who rewrites one origin's entire chain from genesis
  and shows it only to nodes that never saw the original produces a
  chain that verifies on its own. Detecting that needs an external
  anchor (witnessed head hash, cross-node head comparison at scale),
  still unbuilt.
- Conflict resolution is deterministic last-writer-wins in the
  (lamport, origin, seq) total order. That is a prototype rule chosen
  so convergence is checkable; it is not a claim that last-writer-wins
  is the right production semantics for every key, and it silently
  discards the losing concurrent write's value (the losing entry
  itself is kept in the log; only its effect on the materialized
  state is superseded).
- Bulk-merge verification assumes anti-entropy rounds that carry
  full entry sets (the tests and demo do exactly that). A streaming
  protocol where entries trickle in one by one, possibly ahead of
  their predecessors, would need a pending-entry buffer; not built.
- `tests/test-cases/sync-mesh.md` case 4, sustained sub-millisecond
  lineage tracing under load, was NOT measured or attempted. It is a
  Gate 5 benchmark. No performance numbers are claimed; the suite's
  ~0.004s runtime says nothing about production load.

## Process captured

Reusable process: one automated test per gate outcome, named for the
outcome, plus a demo that prints the same outcomes for a human reader;
the experiment record quotes the test run verbatim (`test-output.txt`)
instead of summarizing it. Applied from experiment 002's "gate-outcome
test suite" Opportunity Stack entry, with one new note on that entry:
make convergence a pure function of the entry set (deterministic total
order over immutable, hash-chained entries), so convergence is proven
by comparing hashes after adversarial delivery orders rather than by
trusting the delivery mechanism, and make rollback a checkpoint the
node took of its own last verified state, so the rollback target
never depends on another node's claim. What would differ next time:
Gate 4 should reuse Gate 2's serving tests with the policy snapshot
moved by this sync mechanism instead of `shutil.copyfile`, so policy
availability is judged on copies that arrived through the mesh; and
Gate 5's harness should reuse this module's fork builder to force
its Byzantine case, rather than inventing a second fault injector.

## Next, per the build order

Gate 3 passing is the signal to proceed to Gate 4 (a policy survives
its host going offline; today honestly sole-host offline means the
policy drops, and the unlock outcome is replication to a floor of
independent registered nodes at registration time), under the standing
authorization. Not done in this experiment.
