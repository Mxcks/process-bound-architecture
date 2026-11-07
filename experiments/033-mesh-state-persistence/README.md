# Experiment 033: mesh state persistence - a server's mesh state survives its own process death

**Question.** Every surface since experiment 007 has run on mesh
state held in the server process's memory alone, a limit stated
in `node_server.py`'s honest limits and in experiment 007's README
since the first networked build: kill the process and its
unreplicated entries are gone; what survives a kill is exactly
what had already synced to another process. The registry side has
had a durable record since Gate 1 (its log, its snapshots, its
anchor); the mesh side never got one, so the mesh's availability
story has always rested entirely on replication to a peer that
might not exist yet. This experiment adds the mesh side's durable
record: a server started with a mesh state file persists every
accepted change to its mesh state to that file, a restart over
the file recovers the state exactly - entries that never synced
anywhere included, which is the point - and the recovered state
is judged by the mesh's own verification, and by the mesh anchor
where one is configured, before the process serves anything.

Framing: `docs/architecture/07-availability-risk-when-a-policy-host-goes-offline.md`
(the availability question a durable local record answers for the
single-host case replication cannot), `tests/test-cases/sync-mesh.md`
case 22 (this experiment's case, written before the build - the
flag's independence, the file format, the write discipline, the
load judgement and its refusal names, and the anchor gate are all
stated there), Gate 3's whole-set verification
(`restore_to_entries`, experiment 013's primitive, reused for the
load judgement and for judging every write candidate rather than
a second verifier being written), experiment 025's snapshot wire
delivery (the replace-from-verified-candidate discipline this
experiment's writes inherit), experiment 026 (the at-rest anchor
classification that becomes a start gate here), and experiment
007's refused-start mechanics for a snapshot that fails
verification (`MeshStateRefused` out of the node's construction,
the process exiting nonzero naming the refusal).

## Design choices (at prototype scale)

- **The flag is `--mesh-state <path>`, and it is independent.**
  Independent of `--snapshot` (the registry side's record) and of
  `--mesh-anchor` (a second node's countersigned claim): the mesh
  state exists on mesh-only processes too, persistence is its own
  opt-in, and the anchor gate fires only when BOTH are configured.
  Without the flag the process is memory-only, exactly the
  behavior experiments 007-032 shipped, proven by the control test
  in the same suite.
- **Persistence lives inside the mesh operations, not in a
  watcher.** The registry snapshot got an external driver
  (snapshot.py, experiment 022's watcher) because Gate 1's record
  had one writer by construction and a separate checker. The mesh
  state has exactly two writers in the node - its own mutation
  and its one receive (`receive_mesh_entries`, the single receive
  /entries and /sync share) - and both already run under the
  node's lock. So the node persists at the end of those two
  operations (the mutation through a `mutate_mesh` wrapper the
  /mutate handler now calls), before the driving response
  returns: a caller told a change was accepted is told so only
  once the change is durable. No polling interval, no window
  where an acknowledged change exists only in memory, and no
  second component that could fall behind or race the lock.
- **The write discipline is case 14's
  replace-from-verified-candidate.** The full entry set is
  serialized to a candidate file beside the state file, flushed
  and fsynced; the candidate's bytes are read back and judged by
  Gate 3's own whole-set verification (a scratch node's
  `restore_to_entries`); only a candidate that stands is
  `os.replace`d over the state file. A kill at any point leaves
  the previous standing file or the new one, never a torn one,
  and nothing unverified is ever the standing file - the file on
  disk is always a file the mesh's own verification accepts,
  because it was required to pass that verification before it
  became the state. A write happens only when the state hash
  actually moved since the last load or write, so a refused
  receive and a no-op sync rewrite nothing, byte for byte
  (asserted in the suite).
- **The file format is the wire's own entry format.** One JSON
  object per line, `entry_to_dict` serialization with sorted
  keys, in the node's deterministic total entry order - Gate 1's
  log format, the same serialization /entries and /sync already
  move. No second mesh encoding exists to drift from the first,
  and the load deserializes with the wire's own `entry_from_dict`.
- **The load is the mesh judging its own record.** Before the
  process serves anything, the file's entries go through the
  node's own `restore_to_entries` - the same whole-set judge
  Gate 3 applies to state arriving from a peer (every hash
  recomputed, forks refused, every per-origin chain checked) -
  and the recovered set becomes the node's state and its
  checkpoint only if it stands. The refusal travels as
  `MeshStateRefused` (a sibling of the registry side's
  `SnapshotMismatch`), raised out of `NetworkNode` construction
  so the process exits nonzero naming it: Gate 3's own names for
  a judged divergence (`tampered_entry`, `forked_entry`,
  `broken_chain`), and one new name, `mesh_state_unreadable`,
  for a file that does not parse at all - kept distinct from
  Gate 3's names exactly as `anchor_unreadable` was kept distinct
  from the anchor verdicts in experiment 026, because an
  unparseable file was never judged, only unreadable.
- **The anchor gate fires on recovery, and only on recovery.**
  With `--mesh-anchor` also configured, a recovered state is
  consulted against the anchor by the node's own
  `anchor_status()` classification, and any standing other than
  `anchored` refuses the start under the anchor's own name
  (`anchor_stale`, `anchor_mismatch`,
  `anchor_signature_invalid`, `anchor_witness_invalid`,
  `anchor_unreadable`). Experiment 026 made the at-rest
  classification available; persistence is what makes it a gate,
  because the state is now a durable claim the process stands on
  at startup, judged the way the registry snapshot has been
  judged at startup since experiment 018. A fresh start over no
  file yet recovers nothing and gates on nothing (an anchored
  process legitimately starts empty below its anchor and grows
  into it, experiment 026's own posture) - the gate's three
  standings are all proven: anchored recovery starts, honest
  growth recovered past the anchor refuses `anchor_stale` until
  re-anchored, a foreign chain refuses `anchor_mismatch`.
- **`/health` reports the load, not just the standing.**
  `mesh_state_configured` says whether a state file is
  configured; `mesh_state_load` carries the startup report -
  `{"status": "recovered", "entries": N, "state_hash": ...}` or
  `{"status": "fresh", "entries": 0, "state_hash": ...}` (the
  empty state's hash), null when no file is configured - beside
  the mesh anchor fields experiment 026 put on the same surface,
  so an operator can see what the process recovered, not only
  what it currently holds.

## Results

- **Tests written first.** Case 22 was added to
  `tests/test-cases/sync-mesh.md` before the build, in the
  detailed format of cases 12-21. The new suite
  (`packages/synchronization/test_mesh_state_persistence.py`,
  8 tests) ran against the unbuilt code first: FAILED
  (failures=7, errors=1), every failure the named absence - the
  seven `--mesh-state` tests on argparse's "unrecognized
  arguments: --mesh-state", the control on the missing /health
  field (`KeyError: 'mesh_state_configured'`). Both captures are
  in `test-output.txt`.
- **Fresh start and exact recovery.** A server started with
  `--mesh-state` over a file that does not exist reports the load
  `fresh`, holds an empty state, and does NOT create the file at
  startup; the file appears at the first accepted change. After
  two mutations the process is killed with a real SIGKILL and
  restarted over the same file: the load reports `recovered` with
  the same entry count, /state and the state hash are exactly the
  killed process's, and the file on disk holds exactly the
  recovered state's entries in the wire's format.
- **The unreplicated-entries story, completed.** A server mutates
  two entries and syncs with NO ONE. It is killed; a peer syncing
  with the still-dead process's address fails (nothing to learn
  from); the killed process restarts over its file, and the peer
  syncs with the RESTARTED process and converges - learning, over
  /sync and with no re-mutation anywhere, entries that until the
  restart existed only in a dead process's memory and its state
  file. That is the whole point of the durable record: the kill
  no longer decides what survives; the file does, for exactly the
  entries replication had not yet reached.
- **Merges persist; refused receives rewrite nothing.** A merge
  the process accepts is in its state file and recovered after
  its kill, same as its own mutations. A receive the mesh refuses
  (a fork, rolled back whole) leaves the state file byte for byte
  identical, as does a no-op sync: persistence tracks accepted
  state, never attempts, because the write is gated on the state
  hash having moved.
- **The load refusals, by name, nothing served.** A hand-edited
  entry (value changed, hash kept) refuses the start as
  `tampered_entry`; a file carrying two heads for one origin
  refuses as `forked_entry`; a file cut mid-line refuses as
  `mesh_state_unreadable`. Each refusal is the process exiting
  nonzero with `MeshStateRefused` in its log and no port file
  ever written - the refused-start mechanics of experiment 007,
  and no partially recovered state is served in any of them,
  because the load completes (or refuses) inside the node's
  construction, before the HTTP surface exists.
- **The anchor gate on recovery.** An anchored server (mesh
  anchor held under experiment 029's witness countersignature)
  persists, is killed, and restarts `anchored` over the recovered
  state. It then grows honestly past its anchor, is killed, and
  the restart refuses `anchor_stale`; after re-anchoring, the
  same restart starts `anchored` over the same three entries. A
  state file holding a FOREIGN chain under the same anchor
  refuses `anchor_mismatch`. Persistence plus the anchor is the
  mesh side's version of the registry side's startup posture
  since experiment 018: a durable record, and a countersigned
  outside claim it is checked against before it is served from.
- **The control.** Without `--mesh-state`, /health reports the
  persistence unconfigured and the load null, and a kill plus
  restart starts empty - the pre-033 behavior, unchanged.
- **Full regression green.** 221 tests (registration 25,
  policy-library 42, synchronization 132 = 124 pre-existing + 8
  new, validation 22), all suites OK, earlier suites unmodified,
  plus the EAI handoff check (`PASS: 3 EAI vectors recompute
  exactly`). The baseline was 213/213 before the build,
  confirmed before any 033 edit. The demo
  (`demo_mesh_state_persistence.py`) walks the fresh start, the
  SIGKILL recovery, the peer learning the recovered entries, and
  the tampered-file refusal, and exits 0. No `node_server`
  processes were left running after the runs.

## Honest limits

- **The file survives the process, not the machine.** It is a
  local file under the operator's hand on one filesystem. A dead
  disk takes the record with it; replication (the mesh itself)
  remains the answer across machines, and persistence is the
  answer within one machine's process lifetime. Nothing here
  ships the state file anywhere.
- **Tamper-evident on load, not tamper-proof, and wholesale
  rollback stands where no anchor gates it.** Anyone who can
  write the file can replace it WHOLESALE with an older state
  that itself verifies (every hash recomputes, every chain
  checks), and the restart recovers that older state: the load's
  verification proves the file is A valid mesh state, not that it
  is the LATEST one. This is case 10/17's stated limit for every
  durable record in this stack, and the anchor is its stated
  closure: with `--mesh-anchor` configured, the startup gate
  refuses a rolled-back recovery (`anchor_stale`,
  `anchor_mismatch`) before anything is served. Without an
  anchor, the limit stands exactly as it does for the registry
  log.
- **A truncation exactly on a line boundary recovers the shorter
  state.** Such a file is indistinguishable from an honestly
  older, shorter state file, and is judged as one (the previous
  bullet's limit, by the same reasoning). Only a truncation that
  breaks a line - or any edit that breaks a hash, a chain, or the
  encoding - is refused by name. Under the write discipline a
  KILL cannot produce either truncation of the standing file: the
  standing file changes only by atomic replace of a fully written
  candidate, so these cases are an outside hand on the file, not
  a crash artifact.
- **A leftover candidate file is inert.** A kill between the
  candidate's judgement and its replace leaves a
  `<name>.persist-candidate` file beside the state file; nothing
  reads it, the next accepted change rewrites it from the held
  state, and it is never mistaken for the standing state. It is
  visible evidence of an interrupted write, by design.
- **Persistence is per accepted change, synchronously, in the
  operation.** A process under heavy mesh traffic pays a full
  state serialization, a candidate judgement, and an fsync per
  accepted change, inside the node's lock, before the response
  returns. That is the prototype's chosen trade (durability
  before acknowledgement, no loss window) at prototype scale;
  batching, incremental append, or compaction of the record are
  production questions this experiment does not take up, and the
  state file grows with the entry set (entries are removed only
  by the mesh's own rules - Gate 3 has no entry deletion short of
  rollback, so neither does the file).
- **One writer, as everywhere in this stack.** The state file
  belongs to the one process started with it; two processes
  pointed at the same state file are two writers on one record
  with no coordination, outside the supported shape exactly as
  two servers on one registry log are. Nothing here detects it.
- **Prototype scale, plaintext here.** The suite runs plaintext
  loopback servers, like the mesh anchor suites; the persistence
  code is transport-independent (it lives in the node, under the
  handler's lock), but no claim is made about a TLS-configured
  deployment until a suite runs one. As everywhere in this repo:
  prototype evidence on one machine, not a production durability
  guarantee.
