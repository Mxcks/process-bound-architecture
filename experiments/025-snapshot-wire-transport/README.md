# 025. Snapshot wire transport: a running server receives the updated record over the wire

Date: 2026-11-06
Authorization: Max's standing authorization for the entire
Process-Bound Architecture (2026-11-06); the gates are Maren's
internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`.
Code: `packages/synchronization/` (`node_server.py` gains a
peer-pushed snapshot delivery - `NetworkNode.deliver_snapshot()`,
a `POST /deliver-snapshot` endpoint, a `deliver_snapshot`
client helper, and one `/health` field; new
`test_snapshot_wire_transport.py`, new
`demo_snapshot_wire_transport.py`). No mechanism was rebuilt:
the applied delivery runs through experiment 024's reload
path (the `PolicyNode`'s own reload, experiments 015/021),
the candidate is judged by a full `PolicyNode` construction
with the server's own anchor configuration (the experiment
015/019 consultation, signature included where configured),
and the refusal naming is experiment 022's `_refusal_reason`,
reused exactly. One judgement is new and is named in the
case itself: the backward delivery refusal,
`snapshot_regression`. `packages/synchronization/README.md`
gained the two table rows, and experiment 024's README
carries a superseded-in-part note for its no-transport
limit. This experiment does not touch
`proposals/PBA-FIX-001.md` or implement any part of it, and
it does not work on deliberate removal (availability case 3,
experiment 011's blocked item).
Test case: `tests/test-cases/sync-mesh.md`, case 14. The
case did not exist before this experiment; it was added to
that file, in the detailed format of cases 12/13, before the
code was built, with every outcome below - including the
unchanged startup gate, the `no_snapshot` report, and the
backward refusal's name and its prefix-only scope - stated
in the case itself. The new suite was run against the
unbuilt code first (six errors on exactly the absences the
case names; the seventh test, the startup gate, passing as
the pre-existing property it must remain), so the case is
proven to be what the code had to satisfy; that failing
output is recorded verbatim at the top of `test-output.txt`.
Raw output: `test-output.txt` in this folder (the
tests-first failure, the pre-build baseline, the full
regression across all four tested packages, the new suite
by direct file run, the new demo, and the leftover-process
checks).

## Question

Experiment 024 closed the reload half of server delivery
and stated the remaining half in its own limits: the check
notices that the server's snapshot FILE changed, but "how
the updated record gets into that file is still an explicit
copy performed outside the process, as in every earlier
experiment: there is no push, no broadcast, and no network
transport of the record to a running server". The mesh side
has moved its state between processes since experiment 007;
the registry record itself has only ever moved as a file
copy or a restart. Can the prototype now move the record
over the wire - a peer pushing the snapshot entries
themselves to a running server - while keeping the
strictness every earlier delivery surface proved: the
record verified as a chain and consulted against the
server's anchor (and signature) BEFORE anything changes,
refusals named and leaving no trace, and the server's file
and serving view never split apart?

## The design choices this experiment sets, stated plainly

**Delivery surface.** The delivery rides the server's
existing POST surface as `POST /deliver-snapshot`, driven by
a new client helper (`node_server.deliver_snapshot`, in the
style of `mutate`, `request_sync`, and `check_snapshot`).
The payload is `{"entries": [...]}`: the Gate 1 log entries
as JSON dicts, the record itself as data - never a file
path, because a receiver handed a path has been handed
nothing it can verify. The response is the delivery's
report, refusals included: HTTP 200 with status
`delivery_refused` and the refusal's own name as `reason`,
exactly as a refused check is a check outcome and a
divergence is a `/entries` outcome, not a transport error.
A body that is not a list of entry objects is a 400, in the
`/entries` handler's bad-encoding shape.

**Candidate verification, not a new judgement.** A delivery
that is neither identical to the standing chain nor its
earlier prefix is serialized (Gate 1's own log format:
`json.dumps(entry, sort_keys=True)`, one entry per line,
exactly as `registration.py` writes its log) to a candidate
file beside the snapshot, and judged by constructing a full
`PolicyNode` over it with this process's own anchor
configuration and no payloads. Construction is the
judgement every earlier surface already trusts: standalone
chain verification, then the anchor consultation -
`anchor_stale`, `anchor_mismatch`,
`anchor_signature_invalid`, `tampered_record`, each raised
before any view exists. The server invents no second
verification for the wire; the transport changes how the
record arrives, not how it is judged.

**File and view move together.** Only a candidate that
stands is applied: `os.replace` moves the verified
candidate over the server's snapshot file, and the running
`PolicyNode` reloads from that file (experiment 024's
reload path, re-verifying the same bytes from the real
path). The file is rewritten from the delivery on purpose,
and the reason is stated: the server's snapshot file is its
one durable record - the startup gate, case 13's check, and
GET /snapshot all read it - so a wire delivery that lived
only in the process's memory would split the record from
the view, and a restart would silently resurrect the old
record. The proof is in the suite: a case 13 check driven
right after a stood delivery reports `no_change`, and the
server's file is byte-for-byte the channel log's own
serialization of the delivered entries.

**The backward refusal, and its name.** Two judgements run
on the delivered hashes before candidate verification. A
delivery identical to the standing chain is `no_change`
(nothing written, nothing reloaded). A delivery that goes
backward - fewer entries, and every delivered entry hash
equal to the loaded hash at its index, i.e. exactly an
earlier prefix of the very chain the server stands on - is
refused as `snapshot_regression`, the name case 14 sets.
The existing vocabulary could not say this: the anchor
names judge the anchor against the chain, and
`tampered_record` judges the record against itself; none of
them says "this is our own chain, only older". The
prefix-only scope is the load-bearing part: a shorter chain
that is NOT the standing chain's prefix never reaches this
refusal - it falls through to candidate verification, where
a rewrite is refused as `anchor_mismatch` - so the backward
name cannot be used to launder a rewrite into a lesser
finding. The suite asserts both directions (the genuine
prefix refused as `snapshot_regression`; mallory's shorter
rewrite refused as `anchor_mismatch`).

**Health.** `/health` gains one field,
`last_snapshot_delivery` (the last delivery's report, or
null before the first push), beside `last_snapshot_check` -
so a refusal is readable on the health surface without
pushing anything, as case 13 made the last check readable.

## Result: all case 14 outcomes observed, 7 new tests pass, full regression green

Run on Python 3.12.3, standard library only:

```
packages/registration     Ran 20 tests  OK   (unchanged)
packages/policy-library   Ran 39 tests  OK   (unchanged)
packages/synchronization  Ran 57 tests  OK   (50 baseline + 7 new wire transport)
packages/validation       Ran 18 tests  OK   (unchanged)
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly
```

134 tests total (baseline before this experiment was 127),
every suite on `unittest discover`, the new suite also by
direct file run, the new demo run end to end, and post-run
process checks confirmed zero leftover `node_server`
processes after every run. All of it is quoted verbatim in
`test-output.txt`. No test in this suite sleeps to wait for
a delivery: the push is synchronous and its report is the
response. (The suite's process-spawning helpers poll for
server readiness with the same short sleeps experiments
007-024 use.)

| # | Case 14 outcome | Test | Result |
|---|---|---|---|
| 1 | No-change discipline over plaintext: a push of exactly the standing entries reports `no_change` with `loaded_entries` null, and the server's snapshot file is byte-for-byte unwritten - no reload churn, in case 13's discipline | `test_no_change_stale_then_clean_wire_delivery` (first part) | PASS |
| 2 | Anchor lifecycle not followed, over the wire: the honestly grown record (revocation appended) is pushed WITHOUT the re-anchor; the running server refuses as `anchor_stale`, the snapshot file is byte-for-byte unchanged, the previous view still serves alice's policy (the stale window, asserted), and `/health` carries the refusal in `last_snapshot_delivery` with `snapshot_entries` still 2 | `test_no_change_stale_then_clean_wire_delivery` (second part) | PASS |
| 3 | Clean wire delivery, plaintext: after the same-signer lifecycle (unsigned anchor here) the same entries are pushed again and report `delivered` (counts 2 -> 3, head moved, loaded 3); the server's file is byte-for-byte the channel log's serialization of the delivered record; the revoked policy returns case 12's clean refusal over the wire (410, `policy_revoked`, no bytes), bob's non-revoked policy serves 200 with its exact bytes, `/health` reports `snapshot_entries` 3 and the `delivered` last delivery, and a case 13 check right after reports `no_change` - file and view moved together, no restart, no manual file copy anywhere in the flow | `test_no_change_stale_then_clean_wire_delivery` (third part) | PASS |
| 4 | Negative control, tampered in transit: one entry of the grown record is edited in the pushed list (body changed, `entry_hash` left standing, experiment 021's tamper exactly); the delivery is refused as `tampered_record`, file and view unchanged, and the genuine delivery then lands and flips the wire to 410 | `test_tampered_entry_in_transit_refused_then_clean_lands` | PASS |
| 5 | Negative control, rewritten chain: experiment 012's rewritten-from-genesis chain (one entry, internally consistent, SHORTER than the standing chain but not its prefix) is refused as `anchor_mismatch` - explicitly not `snapshot_regression` - with file and view unchanged; a clean delivery afterward still lands | `test_rewritten_chain_refused_anchor_mismatch` | PASS |
| 6 | The backward delivery: once the server stands on the 3-entry record, the old 2-entry prefix of that very chain is pushed and refused as `snapshot_regression` (before 3, presented 2), the standing file is byte-for-byte unchanged, the revocation still stands over the wire (alice 410, bob 200), `/health` names the refusal - and a further clean growth (bob's second registration, re-anchored, 4 entries) still lands afterward, loaded 4 | `test_backward_delivery_refused_snapshot_regression` | PASS |
| 7 | No snapshot configured: a mesh-only server reports the delivery as `no_snapshot`, `/health` shows it as the last delivery with `snapshot_entries` null, and its mesh endpoints behave exactly as in experiment 007 | `test_mesh_only_server_reports_no_snapshot` | PASS |
| 8 | Startup gate unchanged: a server STARTED over the rewritten snapshot with an anchor still refuses to start as `anchor_mismatch` (log carries `AnchorMismatch`), asserted here and by experiments 018/019/024's unmodified suites in the same full run | `test_restart_over_rewritten_snapshot_still_refused` | PASS |
| 9 | The full flow over the protected link, with identity binding and a signed anchor: a mutual-TLS server started with `--tls-bind-identity`, `--anchor-signer`, and `--anchor-secret-file` answers `no_change` to a push of the standing record; the revocation record pushed without the re-anchor is refused as `anchor_stale` (the untouched anchor's signature still verifies, so the refusal is the head judgement under its own name); the grown head anchored by a DIFFERENT enrolled identity (bob) is refused as `anchor_signature_invalid` - the anchor the server consults arrived signed by an identity it is not configured to trust (staged at a separate path, because experiment 019's same-identity rule, correctly, refuses to let bob re-anchor over alice's anchor in place, and copied over the anchor file as an out-of-band anchor delivery would arrive); after the same-signer anchor is in place ONE push delivers to the running TLS server, its file is the delivered record, the revoked policy refuses 410 over HTTPS, bob's serves 200, and `/health` reports 3 entries and the `delivered` last delivery | `test_signed_tls_wire_delivery_full_flow` | PASS |

The demo (`python3 packages/synchronization/demo_snapshot_wire_transport.py`)
also runs end to end and prints the outcomes in the same
shapes (see `test-output.txt`): one anchored server process,
never restarted (same pid throughout) and its snapshot file
never copied by hand - no-change push, revocation pushed
without the re-anchor refused as `anchor_stale` with file
and stale view untouched, re-anchor, one push delivers
2 -> 3 and the file is the delivered record, the revoked
policy refuses 410 over the wire, a case 13 check after
reports `no_change`, and the old record pushed again is
refused as `snapshot_regression`.

## How the mechanism works, in plain words

`NetworkNode.deliver_snapshot(entries)` compares the
delivered entry hashes with the hashes of the chain the
running `PolicyNode` stands on. Equal: a `no_change`
report. An exact earlier prefix: a `delivery_refused`
report named `snapshot_regression`. Anything else is
serialized to a candidate file beside the snapshot and
judged by a throwaway `PolicyNode` construction with this
process's own anchor path, signer, and secret - the full
chain verification and anchor consultation of experiments
015/019, run over the candidate before the server's file or
view is touched. A refusal deletes the candidate, leaves
both untouched, and is reported under the refusal's own
name through experiment 022's `_refusal_reason`, the same
naming `check_snapshot` uses. A candidate that stands is
moved over the snapshot file with `os.replace`, the running
node reloads from that file, the last-loaded facts move to
the delivered facts, and the report says `delivered` with
the before/after heads and counts and the loaded entry
count. The handler runs the delivery under the node's one
lock, like every other node access, validates only the
payload's shape (a list of entry objects), and stores the
report as the node's last delivery for `/health`.

Design choice worth naming: the candidate is judged by the
SAME object that will serve from it - a `PolicyNode`
construction - rather than by calling the anchor check and
the chain verifier separately and re-assembling their
verdicts in the synchronization package. That is the
composition lesson of experiments 020-024 applied to a new
arrival path: the wire adds a candidate step and a file
replacement to the delivery pipeline, and zero new
judgements to it. The two hash-only judgements that do run
first (`no_change`, `snapshot_regression`) are not trust
judgements - they compare the delivery against what this
process already verified and stands on - and everything
that extends or replaces the standing chain goes through
the full construction.

## What failed

The tests-first run failed as designed: before the endpoint,
helper, and health field existed, the new suite errored six
times on exactly those absences and passed the one test that
asserts a pre-existing property (the startup gate), recorded
verbatim in `test-output.txt`. After the build, nothing
failed: the new suite (7/7 on its first post-build run and
in the recorded full run), the full synchronization
discovery (all of experiments 007-024's server and mesh
suites unmodified, including case 13's check suite and the
startup gates), and the other three packages.

## Honest limits (this is a prototype, not production)

- Delivery is pushed, never solicited or scheduled: no
  timer, scheduler, gossip, or discovery inside the server
  asks a peer for the record, and a server nobody pushes to
  (or drives a case 13 check on) stays exactly as stale as
  experiment 007 left it. This experiment adds the transport
  a driver can use; it does not add the driver.
- The anchor still arrives out of band. The anchor file is
  the local stand-in of experiments 012-019, and this
  experiment transports the RECORD, not the anchor: in the
  tests, the deliverer's re-anchor writes the anchor file
  the server consults (and the differently-signed anchor of
  row 9 is copied over it), because anchor distribution is
  the witnessed-anchor problem, which remains open. A wire
  delivery is consulted against whatever anchor the server
  already holds; a deployment that cannot move anchors
  cannot complete the lifecycle this case assumes.
  [Superseded in part by experiment 028 (sync-mesh case
  17): the registry anchor now travels over the wire -
  `POST /deliver-anchor` carries the anchor object's own
  JSON, alone or paired with the record it blesses in one
  call, judged before acceptance (succession
  `different_signer` audit-logged, the backward fence
  `anchor_regression`, signature and kept history
  `anchor_signature_invalid`, pairing `anchor_unpaired`)
  and judged TOGETHER with the record when paired, under
  case 14's own discipline with the delivered anchor - so
  the anchor lifecycle this case assumed a hand performs
  at the server (write the advanced anchor over the
  server's file, as this experiment's tests did) can now
  be completed by one paired delivery, in which a
  revocation takes effect over the wire with neither half
  applied unless both stand. What this experiment's own
  statement above still carries untouched: `POST
  /deliver-snapshot` itself still transports the record
  only, no server pushes its anchor (there is no
  `/push-anchor`, and case 16's `POST /push-snapshot`
  still transports the record only), and the WITNESS
  problem - an anchor file anyone holding the secret can
  re-sign over a rewrite, experiment 019's recorded
  symmetric-model limit, which case 17's finding shows
  the wire neither adds to nor closes - remains open.]
- The push surface is unauthenticated wherever the server's
  transport is: over plaintext (experiment 007 mode) anyone
  who can reach the port can push a delivery, as they
  already could mutate, sync, and drive checks; over mutual
  TLS with identity binding (experiments 014/017, row 9)
  only a credential-holding peer whose claim matches its
  certificate can. A push is not harmless by construction -
  it is bounded by the verification itself (nothing
  untrusted can be made to serve by pushing; the worst a
  refused delivery achieves is a named refusal on the health
  surface) - but there is no per-endpoint authorization
  beyond the transport, as everywhere in this prototype.
  One asymmetry is stated plainly: a push REPLACES the
  server's snapshot file when it stands, where case 13's
  check never writes the file; the file remains the record
  the startup gate judges, so a pushed record is exactly as
  durable - and exactly as exposed to whoever can write the
  directory - as a copied one.
- The pushing peer in the tests is a credential-holding node
  client driving the same client helpers every experiment
  uses; no server-to-server snapshot push endpoint exists (a
  server initiates mesh `/sync` only), and no experiment has
  yet made one server process deliver its record to another
  unprompted.

  [Superseded in part, 2026-11-06, experiment 027: the
  second sentence no longer holds. In experiment 027
  (`experiments/027-server-to-server-delivery/`), under
  sync-mesh case 16, a server gains a driven
  `POST /push-snapshot` endpoint in `/sync`'s style: it
  sends the entries its PolicyNode stands on to a peer's
  `/deliver-snapshot` - this experiment's surface,
  unmodified - and reports the peer's delivery report as
  the push's result, so one running server now delivers its
  record to another (the end-to-end story, the cross-server
  refusals `snapshot_regression` / `anchor_stale` /
  `anchor_mismatch` reported in the push result, the full
  flow over mutual TLS with identity binding and a signed
  anchor, and a false-claiming pusher refused at the
  binding peer's transport as `identity_mismatch`). The
  rest of the limit stands unchanged: the push is still
  driven by a client call, as `/sync` is - no server
  delivers its record to another UNPROMPTED, and no timer,
  scheduler, gossip, or discovery fires one.]
- The baseline is startup-time, as in 022/024: the
  `snapshot_regression` and `no_change` judgements compare
  against the view the running `PolicyNode` loaded, and
  constructing the server is a statement that the process is
  current with its file; nothing audits that statement.
- Detection and delivery costs: the candidate is verified in
  full (chain recomputation plus anchor consultation) and
  then the reload verifies the same bytes again from the
  real path - two full verifications per stood delivery, the
  prototype's price for reusing both mechanisms unmodified.
  No performance, scale, or latency number is measured or
  claimed here.
- The mesh side of a server process is untouched: it still
  holds a plain `SyncNode`, consults no mesh anchor, and
  learns of mesh state only when a client or `/sync` call
  asks, as experiments 007/015/018 stated. Liveness in the
  server processes, discovery, persistence, and restart
  recovery for a server's mesh state remain unbuilt.

  [Superseded in part, 2026-11-06, experiment 026: the
  first sentence above no longer holds where a mesh anchor
  is configured. In experiment 026
  (`experiments/026-mesh-anchor-wire/`), under sync-mesh
  case 15, a server started with `--mesh-anchor` holds
  experiment 015's AnchoredSyncNode for its mesh side and
  consults the mesh anchor on every mesh receive over the
  wire (a rewritten peer flagged `anchor_mismatch`, honest
  growth `anchor_stale`, a bad anchor signature
  `anchor_signature_invalid`; /health reports
  `mesh_anchor_status`). The rest of the limit stands
  unchanged: a server started without `--mesh-anchor`
  still holds a plain `SyncNode` and consults no mesh
  anchor, the mesh side still learns of state only when a
  client or `/sync` call asks, and liveness, discovery,
  persistence, and restart recovery for a server's mesh
  state remain unbuilt.]
- Inherited limits stand unchanged: localhost processes on
  one machine, HMAC prototype signing, signed-but-unwitnessed
  anchors, no consensus, no discovery, no identity proofing
  beyond a certificate's node_id, no certificate lifecycle.
- Deliberate removal with notification (availability case 3,
  experiment 011's blocked item) remains unbuilt and
  untouched, and `PBA-FIX-001` remains talk-first and
  untouched by this experiment.

## Process captured

Reusable process: when a record gains a new arrival path,
the path is done when the record is judged by the SAME
object that will serve from it, through the same
construction, not when a parallel verifier written for the
path agrees with it - the candidate-construction pattern
here (serialize the arrival into the incumbent format, judge
it with the incumbent mechanism, then move it into place)
costs one extra full verification and buys the guarantee
that the new path cannot drift from the old one's
strictness. Second lesson, from the backward refusal: a new
refusal name earns its place only when its scope is fenced
so it cannot rename an existing finding - `snapshot_`
`regression` is defined on the standing chain's own prefix,
and the suite asserts the shorter rewrite still lands on
`anchor_mismatch`, because a gentler name for a worse act
would be a laundering path, not a vocabulary improvement.
Opportunity Stack entry: extends experiment 024's
surface-parity clause with an arrival-parity clause: a
delivery discipline is a property of how a record ARRIVES
only when every arrival path - file copy, driven check,
wire push - runs the same verification object, the same
refusal vocabulary, and the same file-and-view discipline;
parity of arrival, not merely of destination, is what makes
the paths one system. What would differ next time: record,
in the case itself, the byte-level file expectations (case
14 did - the file is the delivered record byte-for-byte -
and the tests-first failures landed on exactly the named
helper, endpoint, and health field; keep doing that).

## Next, per the build order

Sync-mesh case 14 closes the transport item experiment 024
left open, at prototype scale: the updated record now
reaches a running server over the wire, judged exactly as
strictly as a file delivery, with file and view moving
together. The no-transport limit statement in experiment
024's record carries a superseded-in-part note pointing
here; `node_server.py`'s own limits are restated with the
push surface named. The honest distance to utilization is
unchanged in kind and shorter by that item: deliberate
removal (availability case 3, blocked on Max in experiment
011), anchor distribution and a witnessed anchor,
unattended delivery scheduling with a real clock (a driver
for the pushes and checks this case and case 13 expose),
server-to-server record push, liveness in the server
processes, production crypto and identity proofing,
consensus, persistence, and Gate 5's open production
benchmark. `PBA-FIX-001` remains talk-first and untouched
by this experiment. Nothing here is deployed beyond the
localhost test processes the tests themselves spawned and
cleaned up, and nothing in this experiment changes that.
