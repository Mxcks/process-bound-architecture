# 007. Networked Nodes: Real Processes over HTTP

Date: 2026-11-06
Authorization: `AUTHORIZATION.md` (Max authorized the entire
architecture 2026-11-06; the gates are Maren's internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`).
Code: `packages/synchronization/` (new `node_server.py`,
`test_network_nodes.py`, `demo_network_nodes.py`), built on the
earlier gates without modifying them:
`packages/registration/registration.py`,
`packages/policy-library/policy_node.py`, and
`packages/synchronization/sync_mesh.py` are unchanged.
Raw output: `test-output.txt` in this folder (network suite on both
invocation styles, the demo, and the full regression).

## Question

All five gates pass at prototype scale (experiments 002-006), but
every node so far was an object inside one Python process, exchanging
entries by method call. The biggest remaining gap before the system
can start being utilized is that nodes are not real networked
processes. Can one node run as its own OS process, serving and
requesting sync over a real (localhost) network transport, with the
existing Gate 2/3 verification running on receipt rather than being
bypassed by the transport - and can the mesh survive one of those
processes being killed?

## Result: all outcomes observed, 4 of 4 automated tests pass

Run on Python 3.12.3, standard library only:

```
Ran 4 tests
OK
```

(passed on both invocation styles, `unittest discover` and the
direct file run; wall time varied, roughly 8-17 seconds, because the
tests spawn and reap real processes.) Regression the same day,
unmodified suites: Gate 1, 6 of 6 pass; Gate 2 + Gate 4
(`packages/policy-library`), 6 of 6 pass; Gate 3, 3 of 3 pass;
Gate 5, 4 of 4 pass; the EAI handoff check
(`scripts/verify-handoffs.py`) still passes 3 of 3 vectors. Full
`packages/synchronization` discovery is now 7 of 7 (3 Gate 3 + 4
network). After every run, a process check confirmed zero
`node_server` processes were left behind. All of it is quoted
verbatim in `test-output.txt`.

| # | Outcome | Test | Result |
|---|---|---|---|
| 1 | Two real server processes exchange mesh entries across the process boundary and converge | `test_two_processes_exchange_entries_and_converge` | PASS: two processes on distinct ephemeral ports each mutated their own node over HTTP (2 entries each, including a concurrent write to the same key); server-to-server `/sync` merged 2 + 2 entries with no divergence on either side; both processes then reported identical state, identical state hash, and the same 4 entry ids, read back separately from each process. Entries fetched over HTTP also merged cleanly into a fresh local `SyncNode` through the same `receive_entries` verification, landing on the same state hash. |
| 2 | A registry snapshot and a registered policy cross the process boundary, verified on receipt, never bypassing Gate 2/3 verification | `test_snapshot_and_policy_cross_boundary_with_verification`, `test_server_refuses_to_start_over_tampered_snapshot` | PASS: an opted-in process served alice's registered policy over HTTP with code, code hash, validation channel, and record hash all matching the Gate 1 record; a non-opted-in process holding the same verified policy refused with HTTP 403; an unknown hash got 404. The snapshot crossed as raw bytes and verified with Gate 2's standalone `verify_registry_snapshot` on receipt, and a `PolicyNode` built over the received bytes served the policy. A tampered entry pushed over HTTP got Gate 3's `divergence_detected=True, rolled_back=True, reason=tampered_entry` from the receiving process, whose state hash was unchanged. A server started over a tampered snapshot exited nonzero with `TamperedRecord` and never served. |
| 3 | Killing one server process does not take the state or the policy down; survivors still serve and keep converging | `test_killed_process_survivors_still_serve_and_converge` | PASS: three processes converged on entries written by two of them; the second process was SIGKILLed (no shutdown hook) and its port stopped answering; the survivors still served the mesh state (including the killed process's entry, which had replicated before the kill) and the policy; new state written on a survivor after the kill converged onto the other survivor, ending with equal state hashes and 3 entries on both. |

The demo (`python3 packages/synchronization/demo_network_nodes.py`)
also runs end to end and prints all three outcomes True (see
`test-output.txt`): it starts two processes, converges them, serves
the policy from the second, kills it, and serves state and policy
from the first.

## How the mechanism works, in plain words

One server process owns one node's worth of state: a Gate 3
`SyncNode` always, and a Gate 2 `PolicyNode` when the process is
started with its own copy of the registry snapshot plus the payloads
it holds and its operator's opt-in flag. Processes share nothing -
no memory, no files at runtime - only HTTP on localhost (stdlib
`http.server` on the server side, `urllib` on the client side).
Mutations happen inside the owning process: tests write state by
calling `POST /mutate` on the process that should own the entry.
Sync is Gate 3's bulk anti-entropy with JSON in the middle: a client
(or a peer process, through `POST /sync`) pulls the full entry
list, merges it through `receive_entries`, and pushes the merged
list back. Verification is exactly where it was in-process, because
the receiving side of every exchange is the same unmodified code:
entry hashes recompute, forks and broken chains diverge and roll
back, snapshot chains verify before a `PolicyNode` exists, and
payload hashes verify on every serve. The transport adds reach; it
adds no trust.

## What failed

No test failed; the suite and the demo passed on their first full
runs. One design point was narrowed deliberately and is stated
rather than hidden: sync is still full-list anti-entropy (the whole
entry set crosses on every sync, now as JSON), so nothing here says
anything about efficiency at scale, and no latency or throughput
number was measured or is claimed. The availability claim is also
exactly as wide as the test and no wider: what survives a kill is
what had already replicated, which is Gate 4's replication floor
question in process form, not a new guarantee.

## Honest limits (this is a prototype, not production)

- Localhost only, plain HTTP: no TLS, and no authentication or
  authorization on any endpoint - anything that can reach a node's
  port can mutate its state, sync with it, and fetch policies from
  an opted-in node. Suitable for this experiment and nothing else.
- No discovery: peer addresses are configured by whoever starts the
  processes and whoever calls `/sync`. No peer list, no gossip
  schedule, no automatic re-sync or retry; sync happens when asked.
  [Superseded in part by experiment 035 (sync-mesh case
  24): a server started with `--peers <path>` holds a
  standing peer list from startup (read once; editing the
  file is a restart act), and a driven `POST /sync-all`
  sweeps it in order, each peer through the identical
  per-peer round `/sync` runs, each outcome reported
  separately. Discovery itself, membership, gossip, and
  unattended sync remain unbuilt: the list is configuration
  the node holds, not a protocol for finding peers, and
  `/sync` still names its address at call time exactly as
  this experiment left it.]
- No persistence: mesh state is in each process's memory. Killing a
  process loses exactly its unreplicated entries, and a restarted
  node starts empty and must re-sync. (The registry snapshot and
  payloads are files the process is started with; they are inputs,
  not mesh persistence.)
  [Superseded in part by experiment 033 (sync-mesh case
  22): a server started with `--mesh-state <path>` persists
  every accepted mesh change to that file - inside its own
  mutation and its one receive, before the driving response
  returns, under case 14's replace-from-verified-candidate
  discipline - and a restart over the file recovers the
  state exactly, entries that never synced anywhere
  included, the peer learning them from the restarted
  process over `/sync`. The load is judged by the mesh's
  own verification before the process serves
  (`tampered_entry`, `forked_entry`, `broken_chain`,
  `mesh_state_unreadable` refuse the start), and with a
  mesh anchor also configured a recovered state is gated
  on the anchor at startup (`anchor_stale`,
  `anchor_mismatch`, and the signature/witness names
  refuse it). Without the flag this limit stands exactly
  as written: memory only, a restart empty. The file
  survives the process, not the machine, and is
  tamper-evident on load, not tamper-proof.]
- One process per node on one machine. Nothing about multi-machine
  networking, NAT, firewalls, partial network failure between real
  hosts, or scale is demonstrated.
- Inherited limits stand unchanged: Gate 3 has no Byzantine
  consensus, quorum, or coordinated rollback (detection is local,
  rollback is local); the snapshot chain has no external anchor, so
  a whole-chain rewrite shown to a node that never saw the original
  still verifies; Gate 1 signing is HMAC prototype crypto; there is
  still no revocation anywhere in the stack.
- Test processes are spawned on ephemeral ports and cleaned up by
  the tests (terminate, wait, kill if needed, registered as cleanup
  the moment each process starts); the demo cleans up the same way.
  A post-run process check found zero leftover servers.

## Process captured

Reusable process: when a prototype gains a transport, the transport
must be proven to add reach without adding trust. The test shape
that proves it: drive state only through the wire (mutations via
HTTP, never by touching the server's objects), make at least one
exchange server-to-server rather than relayed by the test process,
reuse the existing verification entry points unmodified on every
receipt path, and pair each happy-path crossing with the same
tamper cases the in-process suite used (tampered entry over HTTP,
tampered snapshot at startup), now expecting refusal across the
boundary. Add the kill test as a process test, not a flag test:
SIGKILL, confirm the port is dead, then require service from the
survivors. Extends experiment 006's gate-outcome entry on the
Opportunity Stack (evidence-shaped verdicts) with a transport
clause. What would differ next time: record each server's log next
to the test output when a spawn fails (currently the failing test
quotes the log in its assertion, which works but scatters the
evidence), and give the client helpers a shared home once a second
package needs them, instead of importing across packages.

## Next, per the build order

All five gates pass, and nodes are now real processes on a real
(localhost) transport. The honest distance to utilization, from the
open items across experiments 002-007: transport security (TLS and
endpoint authentication) before any non-localhost use, discovery
and automatic sync scheduling, mesh persistence and restart
recovery, production crypto and identity proofing (Gate 1),
coordinated rollback and consensus, the external chain anchor,
revocation and deliberate-removal notification, and proof
verification - plus Gate 5's still-open production benchmark
(sustained sub-millisecond end-to-end lineage), which a networked
runtime would have to re-measure with network costs included.
Nothing here is deployed beyond the localhost test processes the
tests themselves spawned and cleaned up, and nothing in this
experiment changes that.
