# Experiment 035: a configured peer list with a driven sync-all - a node knows its peers, and one sweep converges a set

**Question.** Every networked experiment since 007 has stated
the same remaining transport gap in its honest limits: no
discovery. Peer addresses were configured by whoever started
the processes AND by whoever called `/sync`, one pair at a
time, so a node held no standing notion of who its peers are.
Keeping even a small set of servers converged took an outside
driver that knew every address in the set and drove each pair
by hand; the nodes themselves knew nothing. This experiment
gives a node a standing peer list - a file handed to it at
startup - and one driven sweep over that list, `POST
/sync-all`, in which the server itself initiates the sync
with each configured peer, in list order, through the
existing `/sync` mechanics per peer, and reports every peer's
outcome in one result.

Framing: `tests/test-cases/sync-mesh.md` case 24 (this
experiment's case, written before the build - the flag, the
file format, the health shape, the outcome vocabulary, the
self choice, the no-list edge, and the startup-read limit are
all stated there), experiment 007 (the networked transport
and the no-discovery limit this experiment narrows),
experiment 026 (the mesh-anchor consultation the sweep's
anchored legs run through, unchanged), experiment 033 (the
startup-configuration shape: an operator file read once at
start, its load reported on `/health`), and experiment 034
(the refused-start discipline a bad list mirrors:
`peers_unreadable` beside `crl_unreadable`).

## Design choices (at prototype scale)

- The flag is `--peers <path>`, in the existing startup-flag
  style. The file format is plain text and is the whole
  format: one peer base URL per line; blank lines and lines
  whose first non-space character is `#` are ignored; a
  trailing slash is stripped; an exact duplicate of an
  earlier line is dropped after its first occurrence. Each
  remaining line must be an `http://` or `https://` base
  URL. Parsing and validation live in `read_peer_list`,
  called from `main()` before the process serves: a file
  that cannot be read, or a content line that is not such a
  URL, refuses the start naming `peers_unreadable` (argparse
  error, exit 2), in the family of `crl_unreadable`,
  `mesh_state_unreadable`, and `anchor_unreadable`. A
  process never runs believing it holds a peer list it does
  not hold.
- `/health` reports the list itself: `peers_configured`
  (whether the flag was given) and `peers` (the parsed list,
  or null when unconfigured), beside `last_sync_all` (the
  most recent sweep's full response body, null until the
  first sweep). The case states the choice and its reason:
  `/health` is the operator's standing surface and, at this
  prototype's localhost scale, a peer address is not a
  credential. A count-and-hash shape was the alternative;
  the list is small, operator-written, and already on the
  operator's own command line, so listing it whole is the
  honest operator surface here. That judgement is
  localhost-scoped and stated as such.
- The sweep is `POST /sync-all`, driven, in the family of
  `/check-snapshot` and the push surfaces: it takes no peer
  argument, because the list is the argument, fixed at
  startup. A client helper, `request_sync_all`, drives it
  in `request_sync`'s style.
- Per-peer sync is the existing `/sync` path, not a second
  implementation: `_sync_with_peer`'s round was extracted
  unchanged into `_sync_once` (same initiating-side identity
  check, same verified pull, merge, and push-back, same
  status codes and bodies, same printed refusal line), with
  `/sync` kept as its thin caller and the sweep as its
  second caller. One code path, two callers: the surfaces
  cannot disagree about what one peer sync does, and an
  anchored peer is consulted in a sweep exactly as in a
  hand-driven sync because it is the same receive.
- The report: the sweep always answers HTTP 200 - per-peer
  outcomes are the content, not transport errors, as a
  refused delivery is a delivery outcome. Each result, in
  list order, carries the peer URL, an `outcome` in case
  24's vocabulary (`converged`, `refused`, `unreachable`,
  `transport_failed`, `self`), the `reason` where the round
  produced one (a divergence's own SyncResult reason -
  `anchor_mismatch`, `anchor_stale`, `forked_entry`, a Gate
  3 name - or `identity_mismatch`, with claimed/certified
  carried), the transport error's own text as `detail`
  where the round never completed, and the two SyncResults
  the round produced (`self`, `peer_result`) plus the
  round's post-sync state hash where it completed. The top
  level carries `status`: `no_peers` when the sweep has no
  peers to attempt (unconfigured, a configured-but-empty
  list, or a list naming only this process), `converged`
  when every attempted peer converged, `partial` otherwise,
  and this process's state hash after the whole sweep.
- Independence is structural, not a retry policy: the sweep
  is a loop that classifies and collects; no outcome breaks
  it. An unreachable first peer and a refusing second leave
  the third peer's round fully attempted and reported.
- Self: a list entry equal to this process's own base URL
  as `main()` recorded it at binding (`own_base_url`,
  scheme from the TLS configuration, the configured host
  and the bound port) is skipped and reported with outcome
  `self`, in order, with no contact made. The alternative -
  refusing the start over a self entry - was not chosen:
  the list is the operator's statement about the set, a
  self entry is a fact about the set's spelling, and the
  sweep is where the operator can see it. Self-knowledge is
  the bound address the process was started on; an entry
  reaching the same process under a DIFFERENT spelling is
  not detected as self and syncs as any peer would,
  harmlessly (its own entries, verified and merged back).
  That edge is stated in the case, not discovered later.
- The list is read once at startup. Changing the file is a
  restart act in this experiment, exactly as case 23
  stated for the CRL: a running process never re-reads it,
  no endpoint delivers a list, and no push carries one.

## Results

New suite `packages/synchronization/test_peer_list_sync_all.py`:
7 tests, all passing, run against real server processes:

- Health: a `--peers` file with a comment, a blank line, a
  trailing-slash URL, and an exact duplicate parses to the
  two real peers in order; `/health` reports
  `peers_configured` true, the list itself, and a null
  `last_sync_all`; two listless servers report
  `peers_configured` false and `peers` null in the same run.
- The three-server story: C with no list, B listing A and
  C, A listing only B, all standing on one base state
  converged through the lists themselves. New entries at A
  only; one sweep at A converges A with B while C provably
  lacks the entries (`level` absent from C's `/state`); one
  sweep at B carries them to C. Final state hashes equal on
  all three processes and C's materialized state is exactly
  the four entries. No driver named an address at call
  time: the two sweeps named only the sweeper.
- Independence: a list putting a stopped server's address
  first and a live peer second reports the dead peer
  `unreachable` and the live peer `converged` in one sweep,
  top status `partial`, the live peer holding the sweeper's
  entry afterward, and `/health` carrying the whole sweep
  as `last_sync_all`.
- The no-list edge: an unconfigured server's sweep answers
  `no_peers` with an empty result list, and a hand-driven
  `/sync` from the same process converges exactly as
  before - the configuration edge in the existing style,
  stated in the case.
- Self: a restarted server whose list names its own bound
  address first and a real peer second reports the own
  entry `self` (no contact, null reason) and the real peer
  `converged`, top status `converged`.
- Refused starts: a peers file with a non-URL content line
  and a peers path that does not exist each exit nonzero
  naming `peers_unreadable`, and no port file is ever
  written - nothing is served on a bad list.
- The anchored legs, under experiment 026's consultation:
  sweeper X (mesh anchor over the honest state, holding
  nothing) lists a rewritten peer first and the honest peer
  second. The sweep refuses the rewrite as `anchor_mismatch`
  and converges the honest peer in the same sweep; X ends
  holding exactly the honest state, the honest peer is
  unchanged, and the rewrite is adopted by nobody. After
  honest growth at the honest peer, the next sweep refuses
  the growth leg as `anchor_stale` (and the rewritten peer
  one layer earlier, as `forked_entry`, because X now holds
  the genuine origin chain - case 15's stated order: Gate 3
  first, consultation second); after re-anchoring, the same
  sweep converges the honest leg, X's and the honest peer's
  hashes agree, and the rewritten values are nowhere in the
  honest set.

Demo `demo_peer_list_sync_all.py` walks the story end to
end over four servers and prints each stage, ending `DEMO
OK`.

Full regression, same run: registration 25/25,
policy-library 42/42, synchronization 146/146 (139 before
this experiment, +7 new), validation 22/22 - 235/235 total,
with the EAI handoff check PASS 3/3. No leftover
`node_server` processes after any run (verified by direct
process listing; see test-output.txt for the one misleading
counter reading and what it actually counted).

## What failed on the way (recorded, not hidden)

- Tests first, as required: the new suite was written and
  run against the unbuilt code before any implementation.
  It failed as designed - 7 tests run, 6 failures and 1
  error, the servers exiting on `unrecognized arguments:
  --peers` and `peers_unreadable` nowhere on the surface.
  The capture is reproduced in test-output.txt.
- Two tests as first drafted carried a real bug of the
  test's own making: the story test and the self test learn
  a server's URL from a short-lived boot process so another
  server's list can name it, but a restarted server binds a
  NEW ephemeral port, so the pre-written list would have
  named a dead address. Caught by reading the draft before
  the first run, not by a failure: `ServerProcess` gained
  an explicit port and both servers restart on the boot
  process's port, so the listed URL is the real one. The
  fixed form is what ran, before and after.
- A first attempt at a pre-build baseline run overlapped
  the build (its synchronization discovery picked up the
  new suite mid-edit and reported 146 tests with 5 errors).
  That run is contaminated and is not cited anywhere as a
  baseline; the pre-build baseline is experiment 034's
  recorded 228/228, and the baseline claim this experiment
  stands on is the clean full run after the build,
  235/235, in test-output.txt.

## Honest limits

- The list is a startup fact. Editing the file is a
  restart act; a running process never re-reads it, no
  endpoint delivers or receives a list, and no push
  carries one. Live list delivery would be a new surface
  with its own judgement problem (whose list, judged how)
  and is not built.
- This is not discovery, and claims none: no dynamic
  membership, no joining or leaving, no membership
  governance, no peer learns of a peer it was not handed
  at startup, and no address is ever resolved, announced,
  or gossiped. Those are architecture questions - what
  membership MEANS in this system - and this experiment
  does not open them. What changed is only who holds the
  addresses: the node itself, from startup, instead of
  every caller, every time.
- The sweep is driven, like every surface in this stack:
  no gossip schedule, no timer, no automatic re-sync or
  retry. Convergence happens when a client drives
  `/sync-all`, and only along the lists as configured: a
  set whose lists do not connect it does not converge, and
  the sweep reports exactly the legs it attempted - it
  does not route around a missing list entry.
- The sweep is sequential, in list order, over Gate 3's
  bulk anti-entropy (full entry lists both ways per peer,
  inherited unchanged). No parallelism, no deltas, and no
  performance, scale, or latency numbers are measured or
  claimed; a slow peer delays the results behind it in the
  same sweep, and there is no per-peer timeout beyond what
  the underlying HTTP calls themselves do.
- Self detection is by the one spelling `main()` recorded
  at binding. The same process reached under a different
  spelling of its own address (a different host name for
  the same socket) is not detected as self; it syncs with
  itself as a harmless no-op merge, stated in the case.
- `/health` reports the peer list whole. At localhost
  prototype scale a peer address is not a credential; on
  any wider transport the list's shape on a health surface
  would need re-judging, and this experiment does not make
  that judgement.
- Mutual-TLS sweeps compose by construction (a sweep round
  is `/sync`'s round, binding and CRL included) but are
  not separately driven in this experiment's suite, which
  is plaintext like experiment 007's; the composed behavior
  is the earlier experiments' evidence plus the shared code
  path, not a new run.
- Everything else stands as the earlier experiments left
  it: no Byzantine consensus or quorum, local-only
  rollback, HMAC prototype signing, no production
  deployment of any of this.


[Superseded in part, 2026-11-06, experiment 037: the limit
above - "The sweep is driven, like every surface in this
stack: no gossip schedule, no timer, no automatic re-sync
or retry" - was built in experiment 037
(`experiments/037-unattended-operation/`), under sync-mesh
case 25, written tests-first. A server started with
`--auto-cycle-interval` now runs the sweep unattended as
the last stage of its own maintenance cycle (with the
snapshot check, a peer liveness stage, and the paired
registry push before it, in case 25's stated order),
through this experiment's sweep moved onto the node
unchanged. What still stands: without the flag the sweep
is driven exactly as this experiment left it, the list is
still a startup fact, and this is still not discovery.]
