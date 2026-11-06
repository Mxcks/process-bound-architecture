# 024. Server live reload: a running server learns of a revocation without a restart

Date: 2026-11-06
Authorization: Max's standing authorization for the entire
Process-Bound Architecture (2026-11-06); the gates are Maren's
internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`.
Code: `packages/synchronization/` (`node_server.py` gains a
caller-driven snapshot check - `NetworkNode.check_snapshot()`,
a `POST /check-snapshot` endpoint, a `check_snapshot` client
helper, and two `/health` fields; new
`test_server_live_reload.py`, new
`demo_server_live_reload.py`). No mechanism was rebuilt: the
reload is `PolicyNode.reload_snapshot()` (experiments
015/021), the change fact is experiment 022's
`snapshot_delivery.snapshot_facts` (entry count, chain head,
file fingerprint), and the refusal naming is experiment 022's
`_refusal_reason`, both imported from the policy-library
package and reused exactly. `snapshot_delivery.py` changed
only in one docstring sentence recording that the server
surface now exists. This experiment does not touch
`proposals/PBA-FIX-001.md` or implement any part of it, and
it does not work on deliberate removal (availability case 3,
experiment 011's blocked item).
Test case: `tests/test-cases/sync-mesh.md`, case 13. The
case did not exist before this experiment; it was added to
that file, in the detailed format of case 12, before the
code was built, with every outcome below - including the
unchanged startup gate and the mesh-only `no_snapshot`
report - stated in the case itself. The new suite was run
against the unbuilt code first (six errors on exactly the
absences the case names; the seventh test, the startup
gate, passing as the pre-existing property it must remain),
so the case is proven to be what the code had to satisfy;
that failing output is recorded verbatim at the top of
`test-output.txt`.
Raw output: `test-output.txt` in this folder (the
tests-first failure, the pre-build baseline, the full
regression across all four tested packages, the new suite
by direct file run, the new demo, and the leftover-process
checks).

## Question

Every server experiment since 007 has stated the same limit:
a node server process builds its `PolicyNode` once at
startup and never reloads, so a revocation (registration
case 6) reaches a network client only after the process is
restarted over an updated snapshot - the shape experiments
008, 018, 019, and 021 all used, and the shape 021's clean
410 refusal still depended on. Experiments 022/023 closed
the trigger half of that gap for in-process placed policies
(a watcher notices the shared snapshot file changed and
calls the reload itself, refusals re-reported until their
cause is fixed), and both stated plainly that the server
processes were left out. Can the prototype now close the
same gap at the process surface: a running server that, when
its snapshot check is driven, notices its own snapshot file
changed and reloads in that same check - keeping 015's
verify-and-consult-before-any-view-changes discipline,
022's state discipline (the last-loaded facts move only on
a reload that stood, so a refusal re-reports), and 018/019's
startup gate exactly as it was?

## The design choices this experiment sets, stated plainly

**Check surface.** The check rides the server's existing
POST surface as `POST /check-snapshot`, driven by a new
client helper (`node_server.check_snapshot`, in the style of
`mutate` and `request_sync`). No new protocol meaning is
invented: the endpoint is an operator action on one's own
process, like `/mutate`, and its response is the check's
report, refusals included - a refused reload is a check
outcome with HTTP 200, exactly as a divergence is a
`/entries` outcome, not a transport error. No timer,
scheduler, thread, or file watcher inside the server fires
the check: "automatic" here claims precisely experiment
022's meaning (triggered by a check, without a separate
reload call), at the process surface.

**State.** `NetworkNode` records, at startup, the presented
facts of the snapshot its `PolicyNode` was built over
(experiment 022's `SnapshotFacts`, reused), plus the last
check's report. The facts move only when a reload stands.
`/health` gains two fields: `snapshot_entries` (the entry
count of the snapshot the running node currently serves
from) and `last_snapshot_check` (the last check's report, or
null before the first check is driven) - so the running
process's current anchor/record standing is readable without
driving a check.

**Refusal vocabulary.** A refused check is reported under
the reload's own names, via experiment 022's naming reused
verbatim: `anchor_stale`, `anchor_mismatch`,
`anchor_signature_invalid`, `tampered_record`. The server
check invents no refusal of its own on the reload path (one
inherited exception, as in 022: a snapshot file that cannot
even be read or parsed is reported refused under its
exception's class name, and the previous view keeps
serving).

## Result: all case 13 outcomes observed, 7 new tests pass, full regression green

Run on Python 3.12.3, standard library only:

```
packages/registration     Ran 20 tests  OK   (unchanged)
packages/policy-library   Ran 39 tests  OK   (unchanged)
packages/synchronization  Ran 50 tests  OK   (43 baseline + 7 new server live reload)
packages/validation       Ran 18 tests  OK   (unchanged)
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly
```

127 tests total (baseline before this experiment was 120),
every suite on `unittest discover`, the new suite also by
direct file run, the new demo run end to end, and post-run
process checks confirmed zero leftover `node_server`
processes after every run. All of it is quoted verbatim in
`test-output.txt`. No test in this suite sleeps to wait for
a reload: the check is driven synchronously through the
endpoint and its report is the response. (The suite's
process-spawning helpers poll for server readiness with the
same short sleeps experiments 007-021 use.)

| # | Case 13 outcome | Test | Result |
|---|---|---|---|
| 1 | No-change discipline over plaintext: an anchored running server's check over its unchanged snapshot reports `no_change` with before = after facts (2 entries) and `loaded_entries` null; the same policy serves the same bytes before and after the check - no reload churn, in experiment 022's discipline | `test_no_change_then_clean_delivery_reloads_plaintext` (first half) | PASS |
| 2 | Clean delivery, plaintext: the author revokes, the updated record is copied over the server's own snapshot file, the grown head is re-anchored; ONE check on the still-running process reports `reloaded` (counts 2 -> 3, head moved, loaded 3); the revoked policy then returns case 12's clean refusal over the wire (410, `policy_revoked`, no bytes), bob's non-revoked policy serves 200 with its exact bytes, `/health` reports `snapshot_entries` 3 and the `reloaded` last check, and a further check is `no_change` again | `test_no_change_then_clean_delivery_reloads_plaintext` (second half) | PASS |
| 3 | Negative control 1 (anchor lifecycle not followed): the honestly grown snapshot is delivered WITHOUT the re-anchor; the running server's check refuses as `anchor_stale` (presented count 3 recorded, nothing loaded), a second check re-reports the same refusal, `/health` carries the refusal's name, `snapshot_entries` stays 2, and the previous view still serves alice's policy - the stale window, asserted, not hidden. After the re-anchor, the next check delivers and the wire flips to 410 | `test_anchor_stale_refused_re_reported_then_delivered` | PASS |
| 4 | Negative control 2 (tampered delivery): one entry of the server's snapshot file is edited in place, experiment 021's tamper exactly (the fingerprint is what lets the check see an edit head+count cannot); two checks each refuse as `tampered_record` and the previous view serves exactly as before. Once the genuine updated record (with the revocation) is delivered and anchored, the next check reloads and the revoked policy refuses 410 | `test_tampered_delivery_refused_then_genuine_lands` | PASS |
| 5 | Negative control 3 (rewritten delivery): the server's snapshot file is replaced with experiment 012's rewritten-from-genesis chain (internally consistent, one entry); the check refuses as `anchor_mismatch`, the genuine policy keeps serving from the previous view, and restoring the genuine file reports `no_change` - the running view never moved | `test_rewritten_delivery_refused_anchor_mismatch` | PASS |
| 6 | The full flow over the protected link, with a signed anchor: a mutual-TLS server started with `--anchor-signer` + `--anchor-secret-file` reports `no_change`; the revocation delivered without the signed re-anchor is refused as `anchor_stale` (the untouched anchor's signature still verifies, so the refusal is the head judgement under its own name); after the same-signer re-anchor ONE check reloads the running TLS server, the revoked policy refuses 410 over HTTPS, bob's serves 200, and `/health` reports 3 entries and the `reloaded` last check | `test_signed_anchor_tls_live_reload` | PASS |
| 7 | Startup gate unchanged: a server STARTED over the rewritten snapshot with an anchor still refuses to start as `anchor_mismatch` (log carries `AnchorMismatch`), asserted here and by experiments 018/019's unmodified suites in the same full run | `test_restart_over_rewritten_snapshot_still_refused` | PASS |
| 8 | No snapshot configured: a mesh-only server's check reports `no_snapshot`, `/health` shows `snapshot_entries` null, and its mesh endpoints behave exactly as in experiment 007 | `test_mesh_only_server_reports_no_snapshot` | PASS |

The demo (`python3 packages/synchronization/demo_server_live_reload.py`)
also runs end to end and prints the outcomes in the same
shapes (see `test-output.txt`): one anchored server process,
never restarted (same pid throughout) - no-change check,
revocation delivered without the re-anchor refused as
`anchor_stale` with the stale view serving, re-anchor, one
check reloads 2 -> 3, the revoked policy refuses 410 over
the wire, and `/health` reports the new standing.

## How the mechanism works, in plain words

`NetworkNode.check_snapshot()` computes the snapshot file's
presented facts (`snapshot_delivery.snapshot_facts`:
parse for count and head, SHA-256 the raw bytes) and
compares them with the facts recorded when the running
`PolicyNode` last loaded - at startup, the snapshot it was
built over. Equal: a `no_change` report, nothing called.
Different: the check calls the `PolicyNode`'s own
`reload_snapshot()` (experiments 015/021, reused exactly -
chain verification and anchor consultation, signature
included when configured, run before the view changes).
Success moves the recorded facts to the presented facts and
the report says `reloaded` with the before/after heads and
counts and the loaded entry count. Any exception from the
reload becomes a `reload_refused` report under the refusal's
own name (experiment 022's `_refusal_reason`, reused); the
recorded facts do NOT move, which is precisely what makes
the next check re-attempt the same delivery and re-report
the same refusal - the re-reporting is a consequence of the
state discipline, not a retry loop, at the process surface
exactly as in-process. The handler runs the check under the
node's one lock, like every other node access, and stores
the report as the node's last check for `/health`.

Design choice worth naming: the server reuses the
policy-library's watcher FACTS rather than growing a second
change detector in the synchronization package, and it calls
the `PolicyNode` reload directly rather than embedding
022's `SnapshotDeliveryWatcher` - the watcher is built over
a `ReplicatedPolicy` (a placement), which a server process
does not hold; what the server holds is exactly one
`PolicyNode`, and 015 already put the verify-and-consult
reload on it. The composition the case actually needs -
facts, naming, state discipline - is shared; the object
each surface drives stays its own.

## What failed

The tests-first run failed as designed: before the endpoint,
helper, and health fields existed, the new suite errored six
times on exactly those absences and passed the one test that
asserts a pre-existing property (the startup gate), recorded
verbatim in `test-output.txt`. After the build, nothing
failed: the new suite (7/7 on its first post-build run and
in the recorded full run), the full synchronization
discovery (all of experiments 007-023's server and mesh
suites unmodified), and the other three packages. One
honest timing note, no claim made from it: the pre-build
baseline's synchronization discovery took 199.749s on this
shared machine (the same suite took 60.319s in the
post-build regression and ~40-60s in earlier experiments);
experiment 021 recorded the same variance once. Nothing in
this experiment depends on it.

## Honest limits (this is a prototype, not production)

- "Automatic" means precisely "triggered by a driven check
  without a separate reload call" - experiment 022's meaning,
  at the process surface. No timer, scheduler, OS file
  watcher, or background thread fires `POST /check-snapshot`
  unattended, and a server whose check is never driven stays
  stale indefinitely, exactly as before this experiment: the
  stale window is now bounded by the check's driver, not
  removed.
- The check notices that the server's snapshot FILE changed.
  How the updated record gets into that file is still an
  explicit copy performed outside the process, as in every
  earlier experiment: there is no push, no broadcast, and no
  network transport of the record to a running server, and
  the `/snapshot` endpoint's direction is unchanged (it
  serves this node's file out; nothing here pulls a peer's
  file in and reloads from it).
- The check surface is unauthenticated wherever the server's
  transport is: over plaintext (experiment 007 mode) anyone
  who can reach the port can drive a check, as they already
  could mutate and sync; over mutual TLS (experiment 014
  mode) only a credential-holding peer can. A driven check is
  not harmless by construction - it is bounded by the reload
  itself (nothing untrusted can be made to serve by driving
  checks; the worst a bad delivery achieves is a refused,
  re-reported reload) - but there is no per-endpoint
  authorization beyond the transport, as everywhere in this
  prototype.
- The baseline is startup-time: a server started over a
  snapshot file that was ALREADY changed (but never loaded by
  it) cannot start at all unless the file is the anchored
  chain (the startup gate), and a server started anchorless
  takes its startup file as its baseline - constructing the
  server is a statement that the process is current with its
  file, and nothing audits that statement, as in 022.
- The mesh side of a server process is untouched: it still
  holds a plain `SyncNode`, consults no mesh anchor, and
  learns of mesh state only when a client or `/sync` call
  asks, as experiments 007/015/018 stated. Liveness in the
  server processes (016's watcher over a server's holders)
  remains unbuilt, as do discovery, persistence, and restart
  recovery for a server's mesh state.
- Detection costs a full read of the snapshot file on every
  driven check, and delivery still costs the reload's full
  chain verification on every change; no performance, scale,
  or latency number is measured or claimed here.
- Inherited limits stand unchanged: localhost processes on
  one machine, HMAC prototype signing, signed-but-unwitnessed
  anchors, no consensus, no discovery, no identity proofing
  beyond a certificate's node_id, no certificate lifecycle.
- Deliberate removal with notification (availability case 3,
  experiment 011's blocked item) remains unbuilt and
  untouched, and `PBA-FIX-001` remains talk-first and
  untouched by this experiment.

## Process captured

Reusable process: closing a "reaches it only on restart" gap
is not done when the mechanism exists somewhere in the
system - the claim has to be re-proven at the surface that
serves, in the shape that surface actually receives it (a
file changed under a running process, a check driven over
the wire, the refusal readable on that process's own health
surface). Second lesson: when a discipline has already been
proven in one package (022's facts, naming, and unmoved
state), the next surface should import the discipline, not
re-derive it - the only new code here is the surface itself
(state on the node, one endpoint, one helper, two health
fields), and every judgement the check makes is a judgement
an earlier experiment already proved. Opportunity Stack
entry: extends experiment 021's delivery clause (a state
change is only as real as its path to every surface) with a
surface-parity clause: a delivery discipline proven
in-process is a claim about the prototype's objects, not
about its processes, until the process surface runs the same
facts, the same refusals, and the same re-reporting - parity
of discipline, not merely of outcome, is what makes the two
surfaces one system. What would differ next time: record, in
the case itself, which fields the health surface must carry
(case 13 did name them, and the tests-first failures landed
on exactly those fields - keep doing that).

## Next, per the build order

Sync-mesh case 13 closes the server half of the delivery
item experiments 021/022/023 each left open, at prototype
scale: a running server no longer needs a restart to learn
of a revocation, only a driven check. The restart-only limit
statements in the 018, 021, 022, and 023 records have been
replaced or annotated with what this experiment proved (018,
021, and 023 by appended superseded-in-part notes in the
style of 019-023; 022's limit bullet amended in place, as
023 amended it; `node_server.py`'s own limits restated;
`snapshot_delivery.py`'s one sentence annotated). The
honest distance to utilization is unchanged in kind and
shorter by that item: deliberate removal (availability
case 3, blocked on Max in experiment 011), a transport that
moves the updated record into snapshot files (sync
scheduling, discovery), unattended check scheduling with a
real clock, liveness in the server processes, a witnessed
anchor, production crypto and identity proofing, consensus,
persistence, and Gate 5's open production benchmark.
`PBA-FIX-001` remains talk-first and untouched by this
experiment. Nothing here is deployed beyond the localhost
test processes the tests themselves spawned and cleaned up,
and nothing in this experiment changes that.
