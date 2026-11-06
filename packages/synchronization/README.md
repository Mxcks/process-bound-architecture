# packages/synchronization - Synchronization Layer

Separate layer 2 of the operational pipeline. Synchronization does
exactly one job: propagate state across nodes continuously, with no
central locks, through the Continuous Synchronization Mesh
(`packages/sync-mesh/` is the mesh mechanism; this package is the layer
contract every node implements).

Inputs: state transitions from the runtime, each carrying its EAI.
Outputs: converged state, or a divergence signal that triggers rollback.
Failure mode: split-brain or diverging state views. Detection is this
layer's job; hiding divergence is a hard failure (see Gate 3 and Gate 5).

---

Status: **Gate 3 prototype built and passing** (2026-11-06, experiment
`experiments/004-gate3-sync-mesh/`). Prototype only, not production.
The module docstring in `sync_mesh.py` is the authoritative list of
honest limits. The layer contract above is unchanged; what follows
documents the prototype that now implements it at prototype scale.

## What is here

| File | What it is |
|---|---|
| `sync_mesh.py` | The prototype: `SyncNode`, `StateEntry`, `SyncResult`, per-origin hash chains, EAI per entry (PBA-EAI-v1), deterministic convergence, divergence detection with automated local rollback. Python standard library only. Builds on the Gate 1/2 hash-chain discipline; does not modify or re-implement those packages. |
| `test_sync_mesh.py` | Automated tests, one per Gate 3 outcome (the divergence test exercises all three divergence shapes). |
| `demo.py` | Runnable walkthrough of all three Gate 3 outcomes. |

## How to run it

From the repo root:

```sh
# Tests (either command works)
python3 -m unittest discover -s packages/synchronization -v
python3 packages/synchronization/test_sync_mesh.py

# Demo (concurrent mutations converge, a partition heals, a fork is
# detected and rolled back)
python3 packages/synchronization/demo.py
```

Requires Python 3.10+. No dependencies, no network, nothing is deployed.

## How it works, in plain words

1. A node mutates locally, with no lock and no coordinator: each
   mutation becomes one immutable entry carrying its origin, a
   per-origin sequence number, a Lamport clock value, the key/value,
   an Execution-Attached Identity (PBA-EAI-v1), the hash of the
   origin's previous entry (a per-origin hash chain, the same
   discipline as Gate 1's registry chain), and its own hash.
2. Nodes reconcile by bulk pairwise anti-entropy: `sync_with` swaps
   full entry lists from pre-merge snapshots, `receive_entries`
   merges a batch. Arrival order does not matter, because the
   materialized state is a pure function of the entry set: entries
   applied in the total order (lamport, origin, seq), later writes
   to a key winning. Same set in, same state and same state hash
   out, on every node. That is what "converged" means here, and the
   tests check it three ways (entry sets, materialized state, hash).
3. A partitioned node is just a node nobody syncs with. Because
   per-origin sequences are contiguous, rejoining is the same merge:
   it receives the entries it missed, in any order, and converges.
4. Divergence is never merged. A batch containing a tampered entry
   (hash does not recompute), a forked entry (a second valid hash
   claiming an (origin, seq) slot already held: split-brain), or a
   broken per-origin chain link is refused whole. The node logs a
   divergence event and rolls back to its checkpoint, the last
   locally verified consistent entry set, and the returned
   `SyncResult` reports `divergence_detected=True, rolled_back=True`:
   the automated rollback signal. The node keeps working afterwards:
   an honest sync still converges.

## Honest limits

- In-process nodes on one machine exchanging objects by method call.
  No network transport, peer discovery, gossip schedule, persistence,
  or restart recovery. Partition is modeled by not calling sync.
  Nothing about scale or latency is demonstrated or claimed.
- No real Byzantine consensus: no quorum, voting, or BFT protocol.
  Detection is local hash/chain verification plus fork comparison
  when two views meet. A fork nobody ever compares is not detected.
  Gate 5's forced-Byzantine canary test is still ahead.
- Rollback is local to the detecting node. There is no coordinated
  mesh-wide rollback, and peers are not signalled beyond the
  `SyncResult` handed to the caller.
- Whole-chain rewrite limit inherited from Gates 1/2: one edited
  entry is caught, and a fork is caught when both versions are seen,
  but one origin's entire chain rewritten from genesis, shown to a
  node that never saw the original, verifies on its own. An external
  anchor for chain heads is still unbuilt.
- Conflict resolution (deterministic last-writer-wins in the total
  order) is a stated prototype rule, not a claim about correct
  production semantics for every key.
- `tests/test-cases/sync-mesh.md` case 4 (sub-millisecond lineage
  under load) is a Gate 5 benchmark. It was not measured or attempted
  here, and no performance number is claimed.
