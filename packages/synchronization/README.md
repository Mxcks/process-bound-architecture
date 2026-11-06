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
`experiments/004-gate3-sync-mesh/`), and **networked node processes
built and passing** on top of it (2026-11-06, experiment
`experiments/007-networked-nodes/`): nodes now also run as separate
OS processes exchanging entries and registry snapshots over HTTP on
localhost. Prototype only, not production. The module docstrings in
`sync_mesh.py` and `node_server.py` are the authoritative lists of
honest limits. The layer contract above is unchanged; what follows
documents the prototypes that now implement it at prototype scale.

## What is here

| File | What it is |
|---|---|
| `sync_mesh.py` | The Gate 3 prototype: `SyncNode`, `StateEntry`, `SyncResult`, per-origin hash chains, EAI per entry (PBA-EAI-v1), deterministic convergence, divergence detection with automated local rollback. Python standard library only. Builds on the Gate 1/2 hash-chain discipline; does not modify or re-implement those packages. |
| `test_sync_mesh.py` | Automated tests, one per Gate 3 outcome (the divergence test exercises all three divergence shapes). |
| `demo.py` | Runnable walkthrough of all three Gate 3 outcomes. |
| `node_server.py` | The networked node: one `SyncNode` plus one optional Gate 2 `PolicyNode` per OS process, served over HTTP on localhost (stdlib `http.server` / `urllib` only). Endpoints for health, state, mutate, entry exchange, server-initiated `/sync`, registry snapshot, and verified policy serving. Receipt always runs the existing Gate 3/2 verification; the transport grants no trust. Also carries the client helpers the tests and demo use. |
| `test_network_nodes.py` | Automated tests that spawn real server processes on ephemeral ports: two processes converge over HTTP, snapshot + policy cross the boundary verified (tampered entry rejected on receipt, tampered snapshot refuses startup), and a killed process's survivors still serve and converge. Processes are cleaned up by the tests. |
| `demo_network_nodes.py` | Runnable walkthrough of the networked outcomes: two processes sync and converge, the second serves the policy, it is killed, and the first still serves. |

## How to run it

From the repo root:

```sh
# Tests (either command works)
python3 -m unittest discover -s packages/synchronization -v
python3 packages/synchronization/test_sync_mesh.py

# Demo (concurrent mutations converge, a partition heals, a fork is
# detected and rolled back)
python3 packages/synchronization/demo.py

# Networked nodes: tests spawn and clean up their own server
# processes on ephemeral localhost ports; the demo does the same
# visibly
python3 packages/synchronization/test_network_nodes.py
python3 packages/synchronization/demo_network_nodes.py

# One node server by hand (ephemeral port; prints LISTENING <port>)
python3 packages/synchronization/node_server.py --node-id my-node \
  --snapshot <my-registry-snapshot.jsonl> --payloads <payloads.json> \
  --serve-policies --port-file /tmp/my-node.port
```

Requires Python 3.10+. No dependencies. In-process prototypes use no
network; the networked prototype uses localhost HTTP only, and
nothing is deployed anywhere else.

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

## Networked nodes, in plain words

`node_server.py` is the step experiments 002-006 all named as
unbuilt: a node as a real process. Each server process owns its own
`SyncNode` and, when started with a snapshot copy, its own Gate 2
`PolicyNode`; processes share nothing but HTTP calls. Mutations are
driven into a process over HTTP (`POST /mutate`), anti-entropy is
the same full-list exchange as Gate 3, just serialized (`GET`/`POST
/entries`), and a process can initiate sync with a peer itself
(`POST /sync`: pull, verified merge, push back, peer verifies
again). Registry snapshots cross as raw bytes (`GET /snapshot`) and
are verified by the receiver with Gate 2's standalone verifier
before use; policies are served over HTTP (`GET /policy/<hash>`)
with Gate 2 verification running inside the serving process on
every request. What survives killing a process is exactly what had
already synced to another process, no more: the network tests prove
that shape (a killed node's entries live on in its peers, its
unsynced ones would not).

## Honest limits

- Gate 3's nodes are in-process objects exchanging Python objects by
  method call; partition there is modeled by not calling sync. The
  networked prototype (experiment 007) replaces the transport with
  real localhost HTTP between real processes, and nothing beyond
  localhost: no TLS, no authentication or authorization on any
  endpoint, no peer discovery (addresses are configured by whoever
  starts the processes), no gossip schedule, no persistence (kill a
  process and its unreplicated in-memory entries are gone), and no
  scale or latency numbers demonstrated or claimed.
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
