# 013. Coordinated rollback: every node returns to the same checkpoint

Date: 2026-11-06
Authorization: `AUTHORIZATION.md` (Max authorized the entire
architecture 2026-11-06; the gates are Maren's internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`).
Code: `packages/synchronization/` (new `coordinated_rollback.py`,
`test_coordinated_rollback.py`, `demo_coordinated_rollback.py`; one
additive primitive in `sync_mesh.py`, `SyncNode.restore_to_entries`,
with its module docstring's rollback limit updated to point here).
No other earlier module's behavior was modified, and Gate 3's local
rollback, Gate 5's harness, and experiment 012's anchor code are
unchanged - the coordinator reuses Gate 3's fork machinery and
`anchor.py`'s `origin_heads`. This experiment does not touch
`proposals/PBA-FIX-001.md` or implement any part of it, and it does
not work on deliberate removal (availability case 3, experiment
011's blocked item).
Test case: `tests/test-cases/sync-mesh.md`, case 6, added to that
file before the code was built, in the detailed format of
`tests/test-cases/policy-availability.md` case 4. The new tests
were run against the unbuilt code first (import failure,
`ModuleNotFoundError: No module named 'coordinated_rollback'`) so
the case is proven to be what the code had to satisfy; that
failing output is recorded verbatim at the top of
`test-output.txt`.
Raw output: `test-output.txt` in this folder (the tests-first
failure, the full regression across all four tested packages, the
new suite by direct file run, the handoff check, the new demo, the
Gate 3 demo, and the leftover-process check).

## Question

Gate 3 (experiment 004) proved a node that detects divergence
rolls back automatically, and Gate 5 (experiment 006) proved a
forced fault halts and rolls back to a checkpoint in a single
context - but both rollbacks are local: a node restores its OWN
last verified checkpoint, and checkpoints are taken after every
successful merge and mutation, so nodes that last verified
different states hold different checkpoints. When a fault is found
after the mesh has moved on, each node picking its own recovery
point can leave them diverged from each other again. Can the
prototype now prove coordination: every participating node rolls
back to the SAME shared checkpoint together, the mesh converges on
new changes afterward, and a node that sits the rollback out is
detected as divergent instead of silently accepted?

## Result: all case 6 outcomes observed, 3 new tests pass, full regression green

Run on Python 3.12.3, standard library only:

```
packages/registration     Ran 14 tests  OK   (6 Gate 1 + 4 revocation + 4 anchor)
packages/policy-library   Ran 12 tests  OK   (3 Gate 2 + 3 Gate 4 + 3 revocation serving + 3 self-healing)
packages/synchronization  Ran 12 tests  OK   (3 Gate 3 + 4 network + 2 anchor + 3 new)
packages/validation       Ran  9 tests  OK   (4 Gate 5 + 5 scale)
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly
```

47 tests total (baseline before this experiment was 44), every
suite on `unittest discover`, the new suite also by direct file
run, the new demo and the Gate 3 demo run end to end, and a
post-run process check confirmed zero leftover `node_server`
processes from the network suite. All of it is quoted verbatim in
`test-output.txt`.

| # | Case 6 outcome | Test | Result |
|---|---|---|---|
| 1 | A shared checkpoint is captured from a converged mesh (one entry set, state, state hash, and per-origin chain heads every node is verified to hold identically), and capturing one from nodes that do not share a state is refused, naming why | `test_coordinated_rollback_restores_shared_checkpoint_on_all_nodes` (first part) and `test_negative_control_node_that_skips_rollback_is_detected_divergent` | PASS: the checkpoint over three converged nodes carries all four facts (node-a head at seq 1 asserted against the node's own top entry); all three nodes check `at_checkpoint`; taking one over a converged node plus a divergent outsider raises `CoordinatedRollbackError`, and taking one over the post-rollback set that still includes the skipped node raises it again. |
| 2 | A fault is introduced after the checkpoint with the repo's existing machinery, and local rollback alone observably does not reach the shared checkpoint | `test_coordinated_rollback_restores_shared_checkpoint_on_all_nodes` (middle) | PASS: after honest post-checkpoint mutations sync across all nodes, a forked entry built with Gate 3's `build_entry` (individually valid hash, false history for node-a's first slot) is injected through node-b's normal `receive_entries`; node-b detects it (`forked_entry`), rolls back locally - and lands on the post-checkpoint converged state, its own last verified checkpoint, asserted NOT equal to the shared checkpoint's hash. Node-a and node-c then advance further without node-b, so the three recovery points genuinely differ before coordination. |
| 3 | The coordinated rollback restores every participating node to the shared checkpoint: identical entry set, materialized state, state hash, and per-origin chain heads across all nodes afterward, each verified against the checkpoint itself | `test_coordinated_rollback_restores_shared_checkpoint_on_all_nodes` (second half) | PASS: the report returns `status coordinated_rollback_complete`, `all_restored True`, one result per node in order, each `rolled_back True, restored_exactly True` with the checkpoint's state hash; afterward every node's entry set, state, state hash, and `origin_heads` equal the checkpoint's own recorded values, not merely each other's. |
| 4 | The mesh still converges on new changes after the rollback, including a mutation by a node whose own post-checkpoint entries were rolled back | `test_mesh_converges_on_new_changes_after_coordinated_rollback` | PASS: node-a's first post-rollback mutation extends its restored chain head exactly (`seq` = checkpoint top seq + 1, `prev_hash` = checkpoint head hash, asserted) - the restore rebuilt its own-chain bookkeeping, so Gate 3's chain verification accepts it at every peer; after further mutations and a full mesh sync all three nodes hold identical entry sets, state, and hashes, the resumed keys are present, the rolled-back post-checkpoint key is gone, and no node logged a divergence. |
| 5 | Negative control: a node that does NOT take part in the coordinated rollback (keeps the post-checkpoint state) is detected as divergent afterward, not silently accepted | `test_negative_control_node_that_skips_rollback_is_detected_divergent` | PASS: of four converged nodes only three roll back; the checkpoint check then reports node-a/b/c `at_checkpoint` and node-d `divergent` with `entry_ids_match`, `state_matches`, and `heads_match` each False, and `divergent_node_ids` returns exactly `["node-d"]`. The set including node-d cannot be re-certified as sharing a checkpoint. Node-d rejoins only by restoring the checkpoint itself, after which a full mesh sync converges all four on new work. |

The demo (`python3 packages/synchronization/demo_coordinated_rollback.py`)
also runs end to end and prints all five outcomes true, including
the local-rollback miss and the skipped node's divergent check
(see `test-output.txt`).

## How the mechanism works, in plain words

A shared checkpoint is a fact, not an assertion: taking one
compares every node's entry set, materialized state, and state
hash against the first node's and fails, naming the node, when
any differ. What it records is the entry set itself plus the
facts a node will later be judged by - the state, its hash, and
each origin's chain head in experiment 012's `origin_heads` shape,
reused rather than reinvented.

The one new primitive lives in Gate 3's module because it is a
property of a node: `restore_to_entries` replaces a node's entry
set with a given one, but only after judging that set exactly as
a received batch is judged (hash recomputation, no forked
duplicate identity, whole-set chain verification); a set that
fails is refused through the node's normal divergence path and
never kept. On success the restored set becomes the node's
checkpoint too, and its own-chain bookkeeping is rebuilt from the
restored head - the detail test 4 pins down, because a restore
that left a node's next sequence number pointing past the
rolled-back entries would make its next honest mutation a broken
chain at every peer.

The coordinator itself is deliberately thin: for each
participating node it calls the restore, then checks THAT node
against the checkpoint itself and records the per-node result.
`all_restored` is the conjunction of those individual checks, so
no node is ever reported restored on a peer's evidence. The
checkpoint check that powers the negative control is the same
comparison used after the rollback, run over any set of nodes:
at the checkpoint, or divergent with the mismatched facts named.

## What failed

The tests-first run failed as designed: before
`coordinated_rollback.py` existed, the new suite failed at import
(`ModuleNotFoundError`), and that failure is recorded verbatim in
`test-output.txt`, not only summarized here. After the modules
were built, nothing failed: the new suite, the full regression
across all four tested packages, the handoff check, and both
demos passed on their runs quoted in `test-output.txt`. No
earlier test was modified, and Gate 3's and Gate 5's suites pass
exactly as written in experiments 004 and 006.

## Honest limits (this is a prototype, not production)

- Coordination is an explicit, caller-driven function call,
  in-process, over `SyncNode` objects on one machine. Nothing
  signals nodes over a network (experiment 007's server processes
  do not carry this), no peer's divergence triggers a rollback
  automatically anywhere else, and there is no vote, quorum, or
  consensus on which checkpoint is the shared one - the checkpoint
  is the state `take_shared_checkpoint` verified the given nodes
  shared at capture time, and when to take one and when to roll
  back to it are the caller's decisions.
- Rolling back discards every post-checkpoint entry on the
  participating nodes, legitimate ones included: the tests move
  the mesh on with honest mutations and the coordinated rollback
  removes them along with the fault's context. Recovery of
  legitimate post-checkpoint work (replay, or re-sync from a node
  that kept it) is not built; a node that kept the post-checkpoint
  state is treated as divergent by the checkpoint check even
  though its entries are honest and its chains verify.
- Detection of a skipped rollback is by checkpoint comparison,
  not by Gate 3 sync. The skipped node's extra entries are valid
  chain entries, and a rolled-back node that synced with it
  BEFORE the check would accept them and move off the checkpoint
  again. The order the prototype proves is: check first, exclude
  or restore the divergent node, then resume sync. A coordinator
  that enforces that order on its own is not built.
- The fault is one fork arriving through one node's receive path,
  the same construction Gates 3 and 5 use. A fault that left
  different nodes holding different forked histories of the same
  origin at rollback time (true split-brain) is not constructed
  in the tests - Gate 3's receive path refuses forks on sight, so
  that state cannot arise through sync - and no result is claimed
  for it, even though the restore would mechanically put every
  participant on the checkpoint set.
- The checkpoint lives in the caller's memory: no persistence, no
  restart recovery, no checkpoint history or retention rule, and
  no performance, scale, or latency numbers were measured or are
  claimed for coordinating a rollback.
- Deliberate removal with notification (availability case 3,
  experiment 011's blocked item) remains unbuilt and untouched,
  as do consensus, transport security, and automatic triggers.

## Process captured

Reusable process: when a mechanism that was local is made
coordinated, the coordination layer should be thin and the
primitive should live in the local module, judged by the local
module's own verification - the coordinator's whole job is
naming one shared fact (the checkpoint), applying the primitive
per node, and checking each node against that fact itself, never
inferring one node's standing from another's. Second lesson: the
gap demonstration belongs INSIDE the main test - local rollback
landing observably somewhere else than the shared checkpoint is
asserted, not narrated - because a coordinated rollback that
passes on a mesh where local rollback already agreed would prove
nothing. Opportunity Stack entry: extends Gate 3's rollback entry
(experiment 004: rollback is a checkpoint the node took of its own
last verified state) with a coordination clause (a shared
checkpoint must be a state verified as shared at capture time,
and every participant's restoration is verified against the
checkpoint itself, per node). What would differ next time: give
`restore_to_entries` its own direct negative test (a tampered
restore set refused through the divergence path) in Gate 3's
suite rather than only exercising it through the coordinator, so
the primitive's failure shape is pinned where the primitive
lives.

## Next, per the build order

Coordinated rollback closes the item experiments 004-012 listed
as open, at prototype scale. The honest distance to utilization
is unchanged in kind and shorter by one item: deliberate-removal
notification (availability case 3, blocked on Max's answers in
experiment 011), liveness detection and automatic healing
triggers, transport security (encrypted and authenticated links
between real machines), discovery and automatic sync, persistence
and restart recovery, production crypto and identity proofing,
consensus, revocation delivery to placed policies, anchor
consultation wired into serving/sync/the Gate 5 verdict, a
witnessed or signed anchor for production, coordinated rollback
wired into the networked processes with an automatic trigger,
and Gate 5's open production benchmark. `PBA-FIX-001` remains
talk-first and untouched by this experiment. Nothing here is
deployed beyond the local working copy, and nothing in this
experiment changes that.
