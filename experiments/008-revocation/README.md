# 008. Revocation: an author can take a policy back

Date: 2026-11-06
Authorization: `AUTHORIZATION.md` (Max authorized the entire
architecture 2026-11-06; the gates are Maren's internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`).
Code: `packages/registration/` (`registration.py` extended with
`revoke` / `sign_revocation`, new `test_revocation.py`,
`demo_revocation.py`) and `packages/policy-library/`
(`policy_node.py` extended with `PolicyRevoked`, `is_revoked`, and
`reload_snapshot`, new `test_revocation_serving.py`,
`demo_revocation.py`). Earlier packages were extended, not rewritten;
one docstring paragraph in `packages/policy-library/replication.py`
was corrected where it claimed revocation did not exist.
Test case: `tests/test-cases/registration.md`, case 6 (its status is
now "implemented"; cases 1-5 unchanged). That case was written during
Gate 2 from the gap experiment 002 flagged, and stood unimplemented
through experiments 003-007.
Raw output: `test-output.txt` in this folder (both new suites on both
invocation styles, the full regression, both new demos, both
original demos, and the leftover-process check).

## Question

Registration currently has no way to be undone: once a policy is
registered it stands forever at the boundary, on serving nodes, and
in its validation channel. Case 6 requires the undo, with the hard
parts kept honest: only the author can revoke, the revocation itself
must live in the same tamper-evident record without touching the
original registration entry, and a node holding only the
pre-revocation record is a stale state the case must name, not hide.
Can all of that be observed in the prototype stack as it stands?

## Result: all five case 6 outcomes observed, 7 new tests pass, full regression green

Run on Python 3.12.3, standard library only:

```
packages/registration     Ran 10 tests  OK   (6 Gate 1 + 4 new)
packages/policy-library   Ran  9 tests  OK   (3 Gate 2 + 3 Gate 4 + 3 new)
packages/synchronization  Ran  7 tests  OK   (3 Gate 3 + 4 network)
packages/validation       Ran  4 tests  OK   (Gate 5)
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly
```

30 tests total (baseline before this experiment was 23), every suite
on `unittest discover`, the new suites also by direct file run, both
new demos and both original demos run end to end, and a post-run
process check confirmed zero leftover `node_server` processes from
the network suite. All of it is quoted verbatim in `test-output.txt`.

| # | Case 6 outcome | Test(s) | Result |
|---|---|---|---|
| 1 | The policy's author (only the author, authenticated by their channel enrollment) can revoke a registered policy | `test_author_can_revoke_and_boundary_and_validation_refuse`, `test_revocation_requires_valid_signature_and_real_policy` (registration) | PASS: alice, enrolled, revokes with `sign_revocation(secret, policy_hash)` (HMAC over a domain-separated revocation string, so a registration signature replayed as a revocation fails). A bad revocation signature, an unenrolled caller, and revoking a never-registered hash are each rejected and audit-logged (`bad_signature` twice including the replay, `unknown_author`, `unknown_policy`), with no chain entry written for any failure. |
| 2 | After revocation: the ExecutionBoundary refuses the policy, PolicyNode stops serving it, and its validation channel is no longer honored | `test_author_can_revoke_and_boundary_and_validation_refuse` (registration); `test_node_with_updated_snapshot_stops_serving` (policy-library) | PASS: `admit` raises `BlockedRevoked` (a `BlockedUnregistered` subclass, so existing refusal handling keeps working) for the exact registered code; `validation_channel_for` returns None after revocation; both still hold on a fresh channel reloaded from the log alone, and an unrelated live policy (bob's) is admitted normally. On the serving side, a node whose snapshot holds both entries refuses with `PolicyRevoked` even though its payload verifies exactly against the registration record. |
| 3 | The revocation is a NEW entry in the hash-chained record; the original registration entry is never deleted or rewritten; the chain still verifies | `test_revocation_is_new_chain_entry_original_untouched` | PASS: the registry goes from 1 line to 2; line 1 is byte-identical before and after; line 2 is `event: "revoked"` at log index 1 with `prev_hash` equal to the registration entry's hash. `verify_registry_chain()` is True live and reloaded; `get_record` still returns the original record unchanged (history readable, standing changed). Revoking twice returns the same `RevocationRecord` and writes no third entry. |
| 4 | A node holding a stale pre-revocation snapshot is handled honestly: it keeps serving until it receives the updated record, then stops | `test_stale_node_serves_until_updated_record_arrives`, `test_reload_by_path_and_tampered_reload_leaves_view_untouched` (policy-library) | PASS, both directions asserted: after alice revokes centrally, the stale node (snapshot still 1 entry) **still serves the revoked policy** - that assertion is the honest part, and it is in the test on purpose. The updated record is then copied over the node's snapshot file (the delivery Gate 3 sync would perform) and `reload_snapshot()` re-verifies it standalone and rebuilds the node's view: 2 entries, `is_revoked` True, serve refused. Reload also works by explicit new path, and a tampered delivery raises `TamperedRecord` and leaves the node's previous view untouched. |
| 5 | A non-author cannot revoke someone else's policy (rejected and audit-logged) | `test_non_author_cannot_revoke` | PASS: bob, enrolled and signing correctly *as bob*, is rejected with audit reason `not_author` (ownership is checked before the signature, so the logged reason is the true one); bob signing *as alice* fails as `bad_signature`. After both attempts: not revoked, registry still 1 entry, boundary still admits, validation channel still honored. Rejections raise `RevocationRejected`, a `RegistrationRejected` subclass. |

## How the mechanism works, in plain words

Registration and revocation are both appends to one hash-chained
log; nothing in the log is ever edited. A registration entry says
"alice's policy exists, here is its code hash and validation
channel". A revocation entry, written only when the caller is
enrolled, is the record's author, and presents the author's
revocation signature for that policy hash, says "alice took this
policy back" and chains to whatever the previous entry was. Every
reader of the log - a reloaded channel, a serving node verifying a
snapshot - derives the policy's standing from the entry *set*: a
registration with no revocation stands; a registration with a later
revocation does not. The boundary, the validation-channel lookup,
and `PolicyNode.serve_policy` all consult that standing on every
call, which is why the same code paths that admitted and served the
policy refuse it the moment their copy of the record includes the
revocation - and why a copy that does not include it cannot refuse
it. That last part is the stale window, and it is a property of
decentralized copies, not a bug this experiment papers over: it
closes exactly when the record syncs, and nothing here bounds how
long that can take on a node that never syncs.

Design choices worth naming: revocation standing is kept in a
separate `RevocationRecord`, never by mutating the
`RegistrationRecord` (history vs standing); `BlockedRevoked` and
`RevocationRejected` subclass the Gate 1 exceptions so existing
callers keep working unmodified; and `PolicyNode` keeps registration
and revocation entries in separate maps, so a revocation entry can
never overwrite the registration record it revokes in a node's view.

## What failed

One real failure, found by the regression run and fixed in this
experiment: the first full run failed `unittest discover` for
`packages/policy-library` with `ImportError: 'test_revocation'
module incorrectly imported from '.../packages/registration'`. Both
packages had a `test_revocation.py`, and discovery imports test
modules by bare module name, while the policy-library tests put the
registration directory on `sys.path` first - so the name collided.
Fix: the serving-side suite is `test_revocation_serving.py` (the
reason is recorded in its docstring). The re-run after the rename is
the green run quoted in `test-output.txt`. No test content changed
in the fix. (A leftover-process check in the same first run also
appeared to show a surviving server process; a follow-up check with
a self-match-proof pattern showed none - the first check had
matched its own shell's command line. The final run's check, in
`test-output.txt`, uses the safe pattern and reports none.)

## Honest limits (this is a prototype, not production)

- Revocation signatures inherit Gate 1's HMAC prototype signing:
  secrets are issued by the channel and held in memory, so the
  channel itself could forge a revocation, and after a channel
  restart nobody can sign a revocation for an existing user until
  they re-enroll. Production needs asymmetric keys and protected
  storage, as it already did for registration.
- The stale window has no bound and no push: revocation is recorded,
  not broadcast. Nothing notifies nodes holding the policy, the
  synchronization layer does not yet carry registry snapshots on a
  schedule, and the networked server processes of experiment 007
  build their `PolicyNode` at startup and never call
  `reload_snapshot`, so a running server serves stale until it is
  restarted over an updated snapshot or gains a reload path.
  Deliberate-removal notification (availability test case 3) remains
  unbuilt, and Gate 4's `ReplicatedPolicy` holders are constructed
  once and never reload, so a placed policy does not learn of a
  later revocation.
- Revocation is final in this prototype: there is no un-revoke or
  reinstatement entry type, and re-registering identical bytes (same
  policy hash) returns the existing record without reinstating it.
  A new version is a new policy hash and registers separately, which
  is the prototype's only path back - stated, not hidden.
- The hash chain remains tamper-evident, not tamper-proof: a
  whole-chain rewrite (registration *and* revocation entries faked
  together from genesis) would verify against itself. The external
  anchor is still unbuilt, and revocation does not change that.
- Nothing about scale, timing, or network delivery of revocations
  was measured or is claimed. All nodes here are files and processes
  on one machine.

## Process captured

Reusable process: when a gap case (written earlier, built later)
gets implemented, map the new tests one-to-one onto the case's own
outcome list and quote the case number in every test, so the
experiment record is an audit of the case rather than a summary of
the work. Two sharper lessons ride along: (1) run discovery for
*every* package as regression even when only two were touched - the
only failure in this build was a cross-package interaction (a test
module name collision) that neither touched package's own direct
runs showed in isolation order; new test files in this repo must
have package-unique module names. (2) A process check whose pattern
appears in its own command line will match itself; use a bracketed
pattern that cannot self-match, and treat a surprising "leftover"
as a claim to verify, not a fact. Opportunity Stack entry:
"gap-case implementation audit" (new, from this experiment), plus
the test-module naming rule above added to the gate-outcome test
suite entry. What would differ next time: check new file names
against every package's existing test module names *before* the
first run, not after the collision.

## Next, per the build order

Case 6 closes the last test case in `tests/test-cases/registration.md`
that was written but unbuilt. The honest distance to utilization is
unchanged in kind and shorter by one item: revocation *delivery*
(push or scheduled snapshot sync, a reload path in the networked
server, notification to dependents), transport security, discovery
and automatic sync, persistence and restart recovery, production
crypto and identity proofing, coordinated rollback and consensus,
the external chain anchor, and Gate 5's open production benchmark.
Nothing here is deployed beyond the local working copy, and nothing
in this experiment changes that.
