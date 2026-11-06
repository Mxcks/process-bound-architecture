# 003. Gate 2 Decentralized Policy Prototype

Date: 2026-11-06
Authorization: `AUTHORIZATION.md` (Max authorized the entire architecture;
Gate 2 is Maren's internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`. Gate 1
passed in experiment 002, which per that file is the signal for Gate 2.)
Code: `packages/policy-library/` (`policy_node.py`, `test_policy_node.py`,
`demo.py`), built on `packages/registration/registration.py` unchanged.
Raw output: `test-output.txt` in this folder.

## Question

Can both Gate 2 outcomes be observed in a small, runnable prototype:
a registered policy served with no central home, from a node that
verifies it from the registration record alone?

## Result: both outcomes observed, 3 of 3 automated tests pass

Run on Python 3.12.3, standard library only:

```
Ran 3 tests in 0.022s
OK
```

(Gate 1 regression check the same day: its 6 tests still pass,
unmodified.)

| # | Gate 2 outcome | Test | Result |
|---|---|---|---|
| 1 | One registered policy is served from a second, independent node with the central registration point offline at runtime; the node verifies from the registration record (hash chain + policy hash) without contacting the channel | `test_policy_served_from_second_node_with_channel_offline`, `test_node_verifies_from_registration_record_not_channel` | PASS: the policy is registered centrally, the registry snapshot and payload are copied to a second node, the central channel's files are then moved away entirely, and the node still serves byte-identical policy with the original record hash and validation channel. `PolicyNode` never receives or references the channel. Refusals prove the verification is real, not assumed: an unknown policy hash raises `PolicyNotFound`, a payload whose bytes do not match the registered `code_hash` raises `TamperedRecord`, and a snapshot with one entry edited breaks the hash chain and is refused at node construction with `TamperedRecord`. |
| 2 | A user's own computer can act as that policy source by their choice, and a node that did not opt in does not serve | `test_user_node_serves_only_by_opt_in` | PASS: two nodes are built from their operators' own JSON config files (`PolicyNode.from_config`). The opted-in user node serves the policy; the non-opted-in node `holds_policy(...) == True` for the same verified policy and raises `NotServing` on serve. The constructor default is also False and refuses the same way. |

The demo (`python3 packages/policy-library/demo.py`) runs end to end
and prints both outcomes as True (see `test-output.txt`), including
the same user node flipping from refusing to serving when it opts in.

## What failed

Nothing in the test run. The suite passed on its first run and on both
invocation styles (`unittest discover` and direct file run). One
design point was settled while building, not by a failure: node
construction verifies the whole snapshot chain up front, so a tampered
record fails at construction rather than at first serve. That is
deliberate: a node over a bad record should not exist in a servable
state at all.

## Side work in this experiment, per the Gate 1 follow-up

Experiment 002 flagged that Gate 1 has no revocation. The revocation
**test case** is now written: `tests/test-cases/registration.md`,
case 6 (appended; cases 1-5 unchanged). It requires that a revoked
library is blocked at the boundary, its validation channel stops being
honored, and the revocation is a new chain entry that does not delete
or rewrite the original registration. Revocation itself is **not**
built in this experiment, and case 6 is not claimed as passing
anywhere. Naming its stale-node consequence in the case itself (a
Gate 2 node holding only a pre-revocation snapshot) is part of the
case text, because Gate 2 made that gap concrete.

## Honest limits (this is a prototype, not production)

- "Second, independent node" means a second `PolicyNode` over a copied
  file in the same test process, on one machine. There is no network
  transport, peer discovery, or automatic replication: the snapshot and
  payload are copied explicitly by `shutil.copyfile` in the tests/demo.
  Automatic movement of copies is the synchronization mesh (Gate 3),
  which does not exist yet, so nothing about serving at scale, or
  about many nodes, is demonstrated or claimed.
- "Channel offline" is modeled by moving the central channel's files
  away and deleting the setup channel object. That proves the serving
  path has no dependency on the channel (it is not in the node's API),
  but a real deployment's failure modes (partial reachability, stale
  snapshots, split views) are Gate 3 / Gate 5 territory, untested here.
- Snapshot verification inherits Gate 1's tamper limit: one edited
  entry is detected, but an attacker who rewrites the entire snapshot
  from genesis produces a chain that recomputes cleanly. Detecting
  that needs an external anchor (witnessed head hash, cross-node
  comparison), which is not built. The node's checks also duplicate
  Gate 1's canonical-encoding code by design (a serving node must not
  need the channel package's runtime), so the two implementations must
  be kept in lockstep; the EAI-style canonical schema work is where
  that belongs long term.
- Opt-in is a local boolean / local config file. Nothing authenticates
  the operator, records consent anywhere durable, or stops an operator
  from flipping the flag; "by their choice" is demonstrated as a
  mechanism, not as a consent system.
- The node serves verified policy bytes only. It does not execute or
  sandbox policy code, does not check revocation (see above), and
  keeps payloads in a plain in-memory dict.
- No performance numbers from the source proposals were measured or
  are claimed.

## Process captured

Reusable process: build the next gate as a new package that consumes
the previous gate's *record format* (files and hashes), never its
live objects, so the offline claim is true by construction (the new
package's API cannot contact the old one) instead of by test
discipline alone; and when a gate exposes a predecessor's gap
(revocation), write the predecessor's missing test case in the same
pass while explicitly not building the fix. Opportunity Stack entry:
"gate-outcome test suite" (from experiment 002, applied again, plus
the record-format-not-live-object rule as a new note on that entry).
What would differ next time: Gate 3 should replace the explicit
`shutil.copyfile` distribution step in these tests with the real sync
mechanism and re-run this same Gate 2 suite against it, so sync is
judged by whether decentralized serving still holds when copies move
on their own.

## Next, per the build order

Gate 2 passing is the signal to proceed to Gate 3 (synchronization
mesh: concurrent mutations converge with no central lock, partitions
rejoin or rollback fires), under the standing authorization. Not done
in this experiment.
