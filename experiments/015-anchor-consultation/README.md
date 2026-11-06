# 015. Anchor consultation: serving and sync now ask the anchor

Date: 2026-11-06
Authorization: `AUTHORIZATION.md` (Max authorized the entire
architecture 2026-11-06; the gates are Maren's internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`).
Code: `packages/policy-library/` (`policy_node.py` gains opt-in
anchor consultation, new `test_anchor_consultation.py`,
`demo_anchor_consultation.py`) and `packages/synchronization/`
(new `anchored_sync.py`, `test_anchor_consultation.py`,
`demo_anchor_consultation.py`). Experiment 012's check functions
are reused, not duplicated: the policy node loads
`packages/registration/anchor.py` by file path under a private
module name (never importing the registration channel, and immune
to the registration/synchronization anchor.py name collision), and
`AnchoredSyncNode` imports this package's `anchor.py` directly.
No earlier test was modified, and with no anchor configured every
earlier behavior is unchanged (asserted by a new test on each side
and by the unmodified earlier suites passing in the same run).
This experiment does not touch `proposals/PBA-FIX-001.md` or
implement any part of it, and it does not work on deliberate
removal (availability case 3, experiment 011's blocked item).
Test case: `tests/test-cases/registration.md`, case 8, added to
that file before the code was built, with a cross-reference as
case 8 in `tests/test-cases/sync-mesh.md`. The new tests were run
against the unbuilt code first (policy suite: `ImportError:
cannot import name 'AnchorMismatch' from 'policy_node'`; sync
suite: `ModuleNotFoundError: No module named 'anchored_sync'`) so
the case is proven to be what the code had to satisfy; that
failing output is recorded verbatim at the top of
`test-output.txt`.
Raw output: `test-output.txt` in this folder (the tests-first
failure, the full regression across all four tested packages,
both new suites by direct file run, the handoff check, both new
demos, and the leftover-process check).

## Question

Experiment 012 built the external anchor and its check, and named
its own limit in plain words: nothing consults an anchor
automatically. Gate 2's PolicyNode served from the chain alone,
Gate 3 sync never called the anchor check, and the networked
server processes carried no anchor - so a registry or origin
chain rewritten from genesis (internally consistent, passing
every internal check) would be served and synced with the anchor
never asked. Can the prototype now wire the consultation in, at
prototype scale: a serving node and a sync node that are given an
anchor actually refuse the rewrite because of it, while a node
given no anchor behaves exactly as it did before?

## Result: all case 8 outcomes observed, 9 new tests pass, full regression green

Run on Python 3.12.3, standard library only:

```
packages/registration     Ran 14 tests  OK   (6 Gate 1 + 4 revocation + 4 anchor)
packages/policy-library   Ran 17 tests  OK   (3 Gate 2 + 3 Gate 4 + 3 revocation serving + 3 self-healing + 5 new)
packages/synchronization  Ran 20 tests  OK   (3 Gate 3 + 4 network + 2 anchor + 3 coordinated rollback + 4 protected + 4 new)
packages/validation       Ran  9 tests  OK   (4 Gate 5 + 5 scale)
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly
```

60 tests total (baseline before this experiment was 51), every
suite on `unittest discover`, both new suites also by direct file
run, both new demos run end to end, and a post-run process check
confirmed zero leftover `node_server` processes. All of it is
quoted verbatim in `test-output.txt`.

| # | Case 8 outcome | Test | Result |
|---|---|---|---|
| 1 | A Gate 2 node loading a snapshot with an anchor configured consults it: the genuine snapshot reports anchored and serves as before | `test_genuine_snapshot_consults_anchor_and_serves` (policy-library) | PASS: `anchor_status == "anchored"`, the stored AnchorCheck is ok, and the served policy matches the Gate 1 record (code, record hash, author). |
| 2 | Negative control, serving: the rewritten snapshot (passes Gate 2's own `verify_registry_snapshot`) is REFUSED at load, named anchor_mismatch, and never serves | `test_rewritten_snapshot_refused_at_load_anchor_mismatch` | PASS: construction raises `AnchorMismatch` carrying the experiment 012 check (`internal_verifies True, head_matches False, ok False, status anchor_mismatch`). |
| 3 | No anchor, serving: behavior is exactly pre-015 - and stated plainly, an anchorless node serves the rewritten snapshot, the hole 015 closes only where an anchor is configured | `test_no_anchor_behavior_is_exactly_pre_015` (policy-library) | PASS: `anchor_status == "no_anchor"`, no check stored, genuine snapshot serves, and the rewritten snapshot serves mallory's code from an anchorless node. |
| 4 | Honest growth, serving: refused as anchor_stale at load (not a mismatch, not silently served), and after re-anchoring the same snapshot loads and serves old and new policies; the same refusal on the reload path leaves the serving view untouched, then succeeds after re-anchor | `test_honest_growth_is_anchor_stale_until_reanchored`, `test_reload_to_grown_snapshot_is_stale_refused_then_serves_after_reanchor` (policy-library) | PASS: `AnchorStale` at load with `internal_verifies True`; refused reload leaves the node serving alice's policy with status anchored; after `write_anchor` runs again, load and reload both succeed and bob's policy serves. |
| 5 | A Gate 3 anchored node consults the mesh anchor before accepting a peer as converged: a peer holding exactly the anchored set is accepted and converged | `test_genuine_peer_accepted_and_converged_with_anchor` (synchronization) | PASS: 3 entries accepted, no divergence, entry ids / state / state hash identical to the anchored peer's, `anchor_status() == "anchored"`. |
| 6 | Negative control, sync: a peer whose origin chain was rewritten from genesis is flagged divergent, not accepted | `test_rewritten_peer_flagged_anchor_mismatch_not_accepted` (synchronization) | PASS: SyncResult reports `divergence_detected=True, rolled_back=True, reason anchor_mismatch, accepted=0`; the fresh anchored node holds no entries afterward and its divergence log records exactly one `anchor_mismatch` event. |
| 7 | No anchor, sync: behavior is exactly pre-015 - a plain SyncNode and an unanchored AnchoredSyncNode both accept the internally consistent rewrite | `test_no_anchor_behavior_is_exactly_pre_015` (synchronization) | PASS: no divergence on either node, the rewritten state is held, `anchor_status() == "no_anchor"`. |
| 8 | Honest growth, sync: a merged state that extends the anchored state is flagged anchor_stale and refused (state unchanged), and after re-anchoring the same entries are accepted and converged | `test_honest_growth_is_anchor_stale_until_reanchored` (synchronization) | PASS: refusal under reason `anchor_stale` with the pre-growth state hash intact; after re-anchoring from the grown node, the receive lands and both state hashes match. |

Both demos also run end to end and print the same shapes (see
`test-output.txt`): the policy demo shows the anchored serve, the
rewrite refused while an anchorless node serves it, and growth
stale until re-anchored; the sync demo shows the genuine accept,
a plain node accepting the rewrite while a fresh anchored node
flags it, and growth refused then accepted after re-anchor.

## The honest-growth behavior chosen, and why

An anchor pins one head at one moment, so a record that grew
honestly after anchoring mismatches the old anchor exactly as a
rewrite does at the boolean level. Experiment 012's check already
kept the judgements separate (internal verification vs head/count
equality), and its stated lifecycle was: an extended presentation
fails the anchor check until it is re-anchored. This experiment
had to choose what CONSULTATION does with that growth case, and
the choice is:

**Honest growth is classified `anchor_stale`, distinguished from
a rewrite by the chain itself, and refused - at load/reload for a
policy node, as a flagged divergence for a sync node - until the
new head is re-anchored. It is never reported as
`anchor_mismatch` and never silently served as anchored.**

The distinction is proved, not guessed: for the registry, growth
means the presented chain is longer than the anchor AND the entry
at the anchored length still carries the anchored head as its
hash, so the anchored prefix is intact (that hash binds the whole
prefix) and entries were only appended. For the mesh, growth
means every anchored origin head is still present in the merged
state with the same sequence and hash, and the merged state holds
more entries than the anchor. Anything else that is not exactly
the anchored chain - a rewrite, a truncation, a missing origin -
is `anchor_mismatch`.

Why refusal rather than serve-and-report: the repo's standing
style is that a state is never silently accepted and never
claimed beyond what was proved. Serving a grown record "as
anchored" would claim the anchor vouches for entries it never
saw; serving it under a stale label in the serving path would
still hand out bytes on the strength of a check that did not
pass. Refusing under a distinct name loses nothing honest: the
operator learns exactly which case they are in (stale, not
tampered), and the remedy is the lifecycle experiment 012
already recorded - re-anchor the new head, a deliberate act by
whoever holds the anchor - after which the same record serves
and syncs, as both test suites assert. The cost is also stated
plainly below: an anchored node is a convergence gate, and every
honest move past the anchored state waits for a re-anchor.

## How the mechanism works, in plain words

Serving: `PolicyNode` gains an optional `anchor_path`. At
construction the snapshot first passes the node's own standalone
chain verification exactly as before; then, if an anchor is
configured, experiment 012's `check_registry_against_anchor` runs
against the snapshot file, and only an exactly-anchored chain
becomes the node's view. The result is kept on the node
(`anchor_check`, `anchor_status`) so the consultation is
inspectable, not a hidden gate. `reload_snapshot` - the path by
which experiment 008's updated record arrives - consults the same
way BEFORE the view changes, so a refused reload leaves the
previous view (and its anchor state) serving untouched. A
configured anchor that cannot be read raises experiment 012's
AnchorError at load: asking for an anchor and silently serving
without one is the one outcome this design refuses outright.

Sync: `AnchoredSyncNode` subclasses `SyncNode` and overrides only
`receive_entries`. Gate 3's own merge runs first, unchanged - a
tampered, forked, or broken-chain batch diverges under Gate 3's
own reasons, anchor or not. If the merge succeeds, the merged
state (the state the node would be accepting the peer as
converged into) is checked with experiment 012's
`check_node_against_anchor`. Exactly anchored: the merge stands
and Gate 3's success result is returned untouched. Anything else:
the node rolls back to its pre-receive entry set through
`restore_to_entries` (experiment 013's primitive), logs a
divergence event under the anchor reason, and returns a
SyncResult with `divergence_detected=True, rolled_back=True,
accepted=0`. `sync_with` needs no override - it snapshots both
sides and calls each side's `receive_entries`, so an anchored
node's side of any sync is consulted, and a peer with no anchor
configured is judged by its own configuration, exactly as serving
is per-node. An unreadable configured anchor also refuses the
merge (rollback, divergence reason `anchor_unreadable`) and the
MeshAnchorError then propagates to the caller.

## What failed

The tests-first run failed as designed: before the code existed,
the policy suite failed at import (`AnchorMismatch` not yet in
`policy_node`) and the sync suite at import (no `anchored_sync`
module); that failure is recorded verbatim in `test-output.txt`.
During the build, the policy suite's first run failed in a real
way worth recording: loading registration's `anchor.py` by file
path with importlib raised `AttributeError` inside the dataclass
machinery, because a module loaded by spec must be registered in
`sys.modules` before execution for its dataclasses to resolve
their namespace. The loader now registers it first; the fix is in
`policy_node.py` with a comment saying why. After that, nothing
failed: both new suites, the full regression across all four
tested packages, the handoff check, and both demos passed on the
runs quoted in `test-output.txt`, and no earlier test was
modified.

## Honest limits (this is a prototype, not production)

- The anchor is still experiment 012's stand-in: a local file,
  unsigned and unwitnessed, overwritable by whoever can write it.
  Consultation changes WHO ASKS, not what the answer is worth: an
  attacker who rewrites the record AND re-anchors it passes
  consultation exactly as they passed the bare check. Production
  still needs the head held where the record's writer cannot
  reach it, and a rule for who may re-anchor.
- Consultation is per-node and opt-in. A node configured without
  an anchor serves and syncs exactly as before this experiment -
  including, as the tests state plainly, serving and accepting a
  fully rewritten chain. Nothing here makes an anchor mandatory,
  distributes one to nodes, or notices a node that should have
  one and does not.
- An anchored node is a convergence gate, by the design choice
  above: it refuses EVERY move past the anchored state, honest or
  not, until a re-anchor happens. Re-anchoring is a manual,
  operator-side step in this prototype (a `write_anchor` call
  against the accepted new state). There is no anchor lifecycle
  automation, no anchor exchange between nodes, and no quorum or
  witness agreement on a new head.
- The experiment 007/014 server processes do not consult an
  anchor in this experiment: `node_server.py` is unchanged, it
  builds its PolicyNode without an anchor path and its processes
  hold plain SyncNodes. Networked serving and networked sync over
  plaintext or protected links therefore behave exactly as
  experiments 007/014 left them; the consultation built here is
  the in-process nodes'. The Gate 5 verdict likewise does not
  consult an anchor yet.
- The sync consultation judges the merged state as a whole, so it
  inherits the anchor's point-in-time nature: it cannot accept a
  partial batch that is on its way to the anchored state (the
  merged state would not yet BE the anchored state, and would be
  refused as mismatch or stale). At prototype scale sync is bulk
  anti-entropy of full entry lists, which is the shape the tests
  exercise; a streaming or delta sync would need a finer
  consultation rule, and none is claimed.
- Inherited limits stand unchanged: nodes are in-process on one
  machine for the sync side and file copies for the serving side,
  Gate 1 signing is HMAC prototype crypto, there is no Byzantine
  consensus or quorum, no persistence or restart recovery, and no
  performance, scale, or latency numbers were measured or are
  claimed for consultation.
- Deliberate removal with notification (availability case 3,
  experiment 011's blocked item) remains unbuilt and untouched.

## Process captured

Reusable process: wiring an existing check into a live path is a
separate claim from the check existing, and it needs its own
negative control at the LIVE path - experiment 012 proved a
verifier could catch the rewrite; this experiment proves the
serving node and the sync node actually refuse it, and proves it
with the same rewritten artifacts, presented the same way. Second
lesson: when a check's lifecycle makes an honest case (growth)
look like the attack at the boolean level, do not soften the
refusal and do not blur the name - classify the honest case from
evidence inside the artifacts themselves (the anchored head
surviving as the prefix head), refuse under its own name, and
make the remedy (re-anchor) a tested path, not a sentence.
Opportunity Stack entry: extends experiment 012's
outside-comparison clause with a consultation clause (a check
that nothing calls is a finding, not a control: every chain's
outside check must be consulted by the component that serves or
accepts that chain, under the same refusal discipline, or the
gap it closed reopens at the live path). What would differ next
time: wire the anchor path through `node_server.py`'s arguments
in the same experiment as the in-process wiring, so the networked
processes are covered by the same tests instead of named as the
remaining limit.

## Next, per the build order

Anchor consultation closes the item experiments 012-014 listed
as open, at prototype scale, for the in-process serving and sync
paths. The honest distance to utilization is unchanged in kind
and shorter by one item: deliberate-removal notification
(availability case 3, blocked on Max's answers in experiment
011), anchor consultation in the networked server processes and
the Gate 5 verdict, a witnessed or signed anchor for production
(plus a rule for who may re-anchor), liveness detection and
automatic healing triggers, discovery and automatic sync,
persistence and restart recovery, production crypto and identity
proofing, consensus, revocation delivery to placed policies, and
Gate 5's open production benchmark. `PBA-FIX-001` remains
talk-first and untouched by this experiment. Nothing here is
deployed beyond the local working copy, and nothing in this
experiment changes that.
