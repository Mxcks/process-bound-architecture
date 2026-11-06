# 012. External chain anchor: a fully rewritten record fails an outside check

Date: 2026-11-06
Authorization: `AUTHORIZATION.md` (Max authorized the entire
architecture 2026-11-06; the gates are Maren's internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`).
Code: `packages/registration/` (new `anchor.py`,
`test_anchor.py`, `demo_anchor.py`) and
`packages/synchronization/` (new `anchor.py`, `test_anchor.py`).
No earlier module's behavior was modified: the only earlier code
files touched are two docstring limit notes (`policy_node.py`,
`sync_mesh.py`) updated to point at this experiment, and the
registration and mesh anchor modules are additive. This experiment
does not touch `proposals/PBA-FIX-001.md` or implement any part of
it, and it does not work on deliberate removal (availability
case 3, experiment 011's blocked item).
Test case: `tests/test-cases/registration.md`, case 7, added to
that file in the detailed format of its case 6 before the code was
built, with a cross-reference as case 5 in
`tests/test-cases/sync-mesh.md`. The new tests were run against the
unbuilt code first (both files failed at import,
`ModuleNotFoundError: No module named 'anchor'`) so the case is
proven to be what the code had to satisfy; that failing output is
recorded verbatim at the top of `test-output.txt`.
Raw output: `test-output.txt` in this folder (the tests-first
failure, the full regression across all four tested packages, both
new suites by direct file run, the handoff check, the new demo,
and the leftover-process check).

## Question

Every experiment so far trusted a hash chain exactly as far as the
chain could judge itself, and every one named the same hole in its
honest limits: editing one entry is caught, but a record rewritten
from genesis with different content, recomputed entry by entry,
verifies cleanly - there was nothing outside the record to compare
it against. Rewriting the registry would silently replace who
registered what; rewriting one sync-mesh origin's chain would do
the same to that origin's history. Can the prototype now close
that, at prototype scale: write the chain head to a place outside
the record's own files, and have a verifier check a presented
record against that outside anchor, so the full rewrite is caught
even though its internal verification passes?

## Result: all case 7 outcomes observed, 6 new tests pass, full regression green

Run on Python 3.12.3, standard library only:

```
packages/registration     Ran 14 tests  OK   (6 Gate 1 + 4 revocation + 4 new)
packages/policy-library   Ran 12 tests  OK   (3 Gate 2 + 3 Gate 4 + 3 revocation serving + 3 self-healing)
packages/synchronization  Ran  9 tests  OK   (3 Gate 3 + 4 network + 2 new)
packages/validation       Ran  9 tests  OK   (4 Gate 5 + 5 scale)
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly
```

44 tests total (baseline before this experiment was 38), every
suite on `unittest discover`, both new suites also by direct file
run, the new demo run end to end, and a post-run process check
confirmed zero leftover `node_server` processes from the network
suite. All of it is quoted verbatim in `test-output.txt`.

| # | Case 7 outcome | Test | Result |
|---|---|---|---|
| 1 | The chain head (latest entry hash) and entry count are written to an anchor file outside the registry's own directory, and the genuine registry verifies against that outside anchor | `test_anchor_captures_head_outside_registry_and_genuine_registry_verifies` | PASS: after two registrations the anchor holds count 2 and head equal to the second record's hash; the anchor file's directory is not the registry's; a Gate 2 snapshot copy of the same chain also verifies against the anchor; and `write_anchor` refuses a path inside the registry directory with `AnchorError`. |
| 2 | Negative control: a registry rewritten from genesis, internally consistent so its own chain verification passes, fails the anchor check | `test_negative_control_full_rewrite_passes_internal_but_fails_anchor` | PASS: a second channel registers different content (different author and code) in its own directory, its file presented as the registry passes `verify_registry_chain()` on a fresh channel, and the anchor check returns `internal_verifies True, head_matches False, ok False, status anchor_mismatch`. |
| 3 | A single edited entry is caught, and the check says by which layer: internal verification first | `test_single_entry_tamper_fails_internal_and_anchor_check` | PASS: one entry's policy name edited in place; the check returns `internal_verifies False, status internal_verification_failed`, distinct from the rewrite's `anchor_mismatch`. |
| 4 | Boundaries: the anchor pins head AND length. A truncated presentation (genuine prefix, internally valid) and an extended one (one more real registration after anchoring) both fail; re-anchoring restores the extended registry; a missing anchor is an error, never a silent pass | `test_anchor_pins_length_truncation_and_extension_fail_until_reanchored` | PASS: truncation fails on `count_matches`; extension fails on `head_matches` while its chain still verifies internally; after `write_anchor` runs again the extended registry verifies; `verify_registry_against_anchor` against a nonexistent anchor path raises `AnchorError`. |
| 5 | The second chain the docs name: the sync mesh's per-origin chain heads, anchored outside the node, with converged peers verifying | `test_mesh_anchor_captures_origin_heads_and_converged_nodes_verify` (synchronization) | PASS: the anchor holds both origins' heads (node-a top seq 1, node-b top seq 0) and the state hash; both converged nodes verify; after one more honest mutation and sync, the old anchor reports `anchor_mismatch` - the anchor pins a state, it does not bless a history. |
| 6 | Mesh negative control: an origin's whole chain rewritten from genesis, internally consistent and acceptable to a fresh node, fails the anchor check | `test_negative_control_rewritten_origin_chain_passes_internal_but_fails_anchor` | PASS: an attacker node with the same origin id and different values has every entry hash recompute and is accepted by a fresh victim node with no divergence detected; the anchor check returns `heads_match False, ok False, status anchor_mismatch`. |

The demo (`python3 packages/registration/demo_anchor.py`) also
runs end to end and prints the same shape: head anchored outside
the registry directory, genuine registry verifies, rewritten
registry passes its own verification and fails the anchor, a
legitimate new registration fails the old anchor until it is
re-anchored (see `test-output.txt`).

## How the mechanism works, in plain words

An anchor is a small JSON file holding what a chain claims at one
moment, kept somewhere the chain's own files are not. For the
registry, `write_anchor` first runs the chain's own internal
verification (a chain that does not verify on its own is refused,
not anchored), then records the head - the latest entry's hash -
and the entry count. The outside-ness is enforced, not advised:
the anchor path's directory must differ from the registry file's
directory, because an anchor file that gets rewritten together
with the registry proves nothing. Checking is two judgements kept
separate in the result: the chain judging itself (indices, links,
recomputation) and the anchor judging the chain (head and count
equality). A full rewrite passes the first judgement and fails the
second, which is exactly the gap this experiment closes; a
single-entry edit fails the first, and the status vocabulary
(`anchored`, `internal_verification_failed`, `anchor_mismatch`)
keeps the two findings from being blurred.

The mesh anchor is the same shape against the second chain the
docs name. `packages/synchronization/anchor.py` reads a node's
per-origin heads (top sequence per origin and that entry's hash)
plus its state hash from the node's public view and writes them
out; any presented node - the same one, or a peer that converged
to the identical entry set - is checked against them. Anchoring
both chains matters because they fail the same way independently:
a rewritten registry and a rewritten origin chain are different
attacks on different records, and either one passing alone would
leave the other's hole open.

Design choice worth naming: the anchor pins head AND count
together. The head alone already binds the whole history of a
hash chain, but recording the count makes truncation a separately
named failure in the check result, and gives a reader of the
anchor file the chain's size without opening the registry.

## What failed

The tests-first run failed as designed: before either `anchor.py`
existed, both new test files failed at import
(`ModuleNotFoundError`), and that failure is recorded verbatim in
`test-output.txt`, not only summarized here. After the modules
were built, nothing failed: both new suites, the full regression
across all four tested packages, the handoff check, and the demo
passed on their runs quoted in `test-output.txt`. No earlier test
was modified.

## Honest limits (this is a prototype, not production)

- The anchor location is a stand-in, named as one: at prototype
  scale the outside place is a separate file outside the
  registry's directory, on the same machine, writable by the same
  operator. It proves the comparison logic and nothing about the
  anchor itself being safe. An attacker who can rewrite the
  registry AND the anchor file defeats this completely; production
  needs the head written somewhere the registry's writer cannot
  reach (a witnessed or distributed ledger, a second operator's
  countersignature, a public append-only log). The original
  proposals' phrase "anchored to tamper-evident distributed
  ledgers" names that destination; nothing here is a ledger.
- The anchor is not signed and not witnessed. Anyone who can write
  the anchor file can re-anchor a rewritten registry and it will
  verify. No countersignature, witness set, timestamping service,
  or append-only anchor history is built; each `write_anchor`
  overwrites the previous anchor, so anchor history itself is not
  tamper-evident here. SUPERSEDED IN PART by experiment 019
  (2026-11-06): anchors can now be written signed by an enrolled
  identity (Gate 1's HMAC scheme), re-anchoring a signed anchor
  requires that same identity (a different identity's attempt is
  refused and logged), accepted signed re-anchors append to a
  signed, hash-chained history kept in the anchor file, and
  every consultation point verifies the signature when a signing
  identity is configured, under the name
  anchor_signature_invalid. Unsigned anchors behave exactly as
  this experiment built them, and the anchor is still
  unwitnessed: no countersignature or witness set exists.
- Nothing consults an anchor automatically. Gate 2's `PolicyNode`
  still serves from the chain alone, Gate 3 sync does not check
  anchors, experiment 007's server processes do not carry one, and
  Gate 5's verdict does not include an anchor check. The anchor is
  a verifier-side check a caller runs; wiring it into serving,
  sync, or the gate verdict is future work, not claimed here.
- A mismatch is a finding, not a verdict about intent. The anchor
  pins one head at one moment; a registry that honestly grew after
  anchoring mismatches the old anchor exactly as a rewrite does.
  The check separates "does not verify on its own" from "not the
  anchored head", and no further: deciding why a head moved is
  the operator's, and the lifecycle that follows (re-anchor when
  a new head is accepted) is a manual step here.
- Mesh anchors inherit the in-process limit: `SyncNode` keeps
  nothing on disk, so "outside the node" means a file the node
  does not own or read, held by whoever runs the check. There is
  no cross-node head comparison at scale, no anchor exchange
  protocol between nodes, and no performance, scale, or latency
  numbers were measured or are claimed for anchoring.
- Deliberate removal with notification (availability case 3,
  experiment 011's blocked item) remains unbuilt and untouched, as
  do coordinated rollback and consensus.

## Process captured

Reusable process: when a check has two judgements inside it (a
record judging itself, and an outside fact judging the record),
return them as separate named fields with a status vocabulary
that says which judgement failed, instead of collapsing them into
one boolean - the boolean stays available as a convenience, but
the evidence keeps its shape. Second lesson, from the docs sweep
that opened this experiment: the phrase "the chain" hid two
chains (the registry record and the mesh's per-origin chains),
and anchoring only the first would have closed the named gap in
name only; searching every "still unbuilt" statement before
building is what surfaced the second one.
Opportunity Stack entry: extends Gate 1's tamper-evidence entry
with an outside-comparison clause (a chain's self-verification is
never the whole verification story; every chain in the system
needs a head held somewhere the chain does not control, and the
check result names which side failed). What would differ next
time: anchor the Gate 5 snapshot path as a third presentation of
the registry chain in the tests, so the canary's record is named
in the evidence rather than covered by the snapshot-copy argument.

## Next, per the build order

The external chain anchor closes the item experiments 002-010
listed as open, at prototype scale, for both chains the docs
name. The honest distance to utilization is unchanged in kind and
shorter by one item: deliberate-removal notification
(availability case 3, blocked on Max's answers in experiment 011),
liveness detection and automatic healing triggers, transport
security, discovery and automatic sync, persistence and restart
recovery, production crypto and identity proofing, coordinated
rollback and consensus, revocation delivery to placed policies,
anchor consultation wired into serving/sync/the Gate 5 verdict
(done, experiments 015 and 018), a signed anchor (done at
prototype scale, experiment 019; a WITNESSED anchor is still
open), and Gate 5's open
production benchmark. `PBA-FIX-001` remains talk-first and
untouched by this experiment. Nothing here is deployed beyond
the local working copy, and nothing in this experiment changes
that.
