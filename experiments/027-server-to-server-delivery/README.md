# 027. Server-to-server snapshot delivery: one server pushes its standing record to another

Date: 2026-11-06
Authorization: Max's standing authorization for the entire
Process-Bound Architecture (2026-11-06); the gates are Maren's
internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`.
Code: `packages/synchronization/` (`node_server.py` gains a
server-initiated snapshot push - a `POST /push-snapshot`
endpoint in `/sync`'s style, a `push_snapshot` client helper,
and one `/health` field; new
`test_server_to_server_delivery.py`, new
`demo_server_to_server_delivery.py`). The receiving side is
experiment 025's `deliver_snapshot`, unmodified: no mechanism
was rebuilt, no new verification exists on either side, and
the sender re-derives no verdict - it reports the peer's
delivery report as the push's result. `packages/
synchronization/README.md` gained the node_server row's push
clause and the two file rows, and experiment 025's README
carries a superseded-in-part note for its
no-server-to-server-push limit. This experiment does not
touch `proposals/PBA-FIX-001.md` or implement any part of
it, and it does not work on deliberate removal (availability
case 3, experiment 011's blocked item).
Test case: `tests/test-cases/sync-mesh.md`, case 16. The
case did not exist before this experiment; it was added to
that file, in the detailed format of cases 12-15, before the
code was built, with every outcome below - including the
surface's shape, the health field, the 502 refusal shape,
and the initiating-side check - stated in the case itself.
The new suite was run against the unbuilt code first (seven
errors, every one on exactly the absences the case names),
so the case is proven to be what the code had to satisfy;
that failing output is recorded verbatim at the top of
`test-output.txt`.
Raw output: `test-output.txt` in this folder (the
tests-first failure, the pre-build baseline, the full
regression across all four tested packages - including one
loaded run's port-file race in an earlier suite's own
helper, recorded verbatim and superseded by a clean re-run -
the new suite by direct file run, the new demo, and the
leftover-process checks).

## Question

Experiment 025 gave a running server a wire delivery surface
and stated its remaining limit in the same breath: the
pushing peer in every case 14 test is a credential-holding
node CLIENT driving the client helpers; no server process
ever pushes its standing record to another server, so two
running servers cannot bring each other current on the
registry side even though their mesh sides have synced since
experiment 007. Can the prototype now move the record server
to server - the sender contributing only what it stands on,
the receiver judging it exactly as it judges any delivery -
with the refusals a cross-server push can meet named in the
result the sender sees?

## The design choices this experiment sets, stated plainly

**The sender surface.** The push rides the pushing server's
existing POST surface as `POST /push-snapshot` with payload
`{"peer": "<base URL>"}` - `/sync`'s exact shape, because the
push is `/sync`'s registry-side sibling: one server
initiating contact with a peer process at a configured
address. It is driven by a new client helper
(`node_server.push_snapshot`, returning the (status, body)
pair as `fetch_policy` does, because a push's refusal can
ride a non-200 status and the caller must see its body).

**What is pushed.** The entries the pushing server's
PolicyNode STANDS ON - its loaded, verified entries, what it
serves from - read from the node under its lock, never the
snapshot file read around the view. Cases 13/14 keep the
file and the view in step, so the two agree in every flow
the earlier experiments built; where they could disagree (a
file copied over a running server whose check was never
driven), the view is the honest thing to push: a server
pushes the record it has itself verified and serves, not
bytes it has not loaded. The pushed entries go to the peer's
`POST /deliver-snapshot` (experiment 025, unmodified), over
the same transport the pushing process serves on: its client
helpers present its own credential and claim its own node
id, as its `/sync` initiation already does.

**The peer judges; the sender reports.** On HTTP 200 the
push's response is a report carrying the pushing server's
node id, the peer URL, `status` equal to the peer's delivery
status (`delivered`, `delivery_refused`, `no_change`), and
the peer's delivery report itself under `delivery`. The
sender adds no judgement of its own: the composition lesson
of experiments 020-025 applied to initiation - the delivery
discipline lives in exactly one place (the receiver), and a
second verdict on the sender would be a second place for the
two to drift apart. A pushing server with no PolicyNode
answers on 200 with status `no_snapshot` and `delivery`
null: it holds no record to push, and its mesh behavior is
undisturbed.

**Refusals that never reach judgement.** Two refusal shapes
mirror `/sync`'s, on HTTP 502. With identity binding on, the
push first runs experiment 017's initiating-side check on
the peer (its certificate id against the id it claims in its
health response), exactly as `_sync_with_peer` does; a
mismatch refuses the push outright with `identity_mismatch`
naming claimed and certified. And when the peer's transport
refuses the SENDER before the delivery endpoint runs -
experiment 017's accepting-side check answering 403
`identity_mismatch` to a false-claiming pusher - the push
reports that refusal on 502 with the claimed and certified
ids carried through. The handler therefore posts through
`post_json` directly rather than the `deliver_snapshot`
helper (which asserts a 200): a refused push is an outcome
with a body, and the body is the result the sender sees.
An unreachable peer is the third 502, in `/sync`'s
"peer unreachable" / "peer push failed" wording.

**Health.** `/health` gains one field, `last_snapshot_push`
(the last push's response body - a 200 report or a 502
refusal alike - or null before the first push is driven),
beside `last_snapshot_check` and `last_snapshot_delivery` -
so a push's outcome is readable on the sender's health
surface without driving another one, as cases 13/14 made
the last check and the last delivery readable.

## Result: all case 16 outcomes observed, 7 new tests pass, full regression green

Run on Python 3.12.3, standard library only:

```
packages/registration     Ran 20 tests  OK   (unchanged)
packages/policy-library   Ran 39 tests  OK   (unchanged)
packages/synchronization  Ran 71 tests  OK   (64 baseline + 7 new server push)
packages/validation       Ran 18 tests  OK   (unchanged)
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly
```

148 tests total (baseline before this experiment was 141),
every suite on `unittest discover`, the new suite also by
direct file run, the new demo run end to end, and post-run
process checks confirmed zero leftover `node_server`
processes after every run. All of it is quoted in
`test-output.txt`. One loaded full-regression run errored
once - in experiment 026's unmodified suite, on the
port-file race in that suite's own `wait_ready` helper
(the file momentarily empty when read) that experiment 026
also recorded; no assertion failed anywhere, the error is
recorded verbatim in `test-output.txt`, and the clean
re-run of the synchronization discovery (71/71) supersedes
it. No test in this suite sleeps to wait for a push: the
push is synchronous and its report is the response. (The
suites' process-spawning helpers poll for server readiness
with the same short sleeps experiments 007-026 use.)

| # | Case 16 outcome | Test | Result |
|---|---|---|---|
| 1 | The end-to-end story over plaintext: both servers start over the same 2-entry record, each consulting its own anchor; alice revokes, the lifecycle is followed for both, the grown record reaches A through A's own accepted case 14 delivery; ONE driven push A -> B reports `delivered` with the peer's report (counts 2 -> 3, loaded 3); B - never restarted, its file never copied by hand - holds a snapshot file byte-for-byte the delivered record, answers the revoked policy with case 12's clean 410 `policy_revoked`, keeps bob's at 200 with exact bytes, `/health` reports 3 loaded and the `delivered` last delivery, and A's `/health` carries the push as its last push; the pair's mesh surface still converges over `/sync` | `test_server_push_delivers_revocation_end_to_end` | PASS |
| 2 | No-change across servers: a second push to B, already standing on the identical record, reports `no_change` with the peer report's `loaded_entries` null, and B's snapshot file is byte-for-byte unwritten - not an error, not a rewrite | `test_server_push_delivers_revocation_end_to_end` (second part) | PASS |
| 3 | Backward across servers: B is brought ahead of A (bob's second registration delivered to B alone through B's own case 14 surface, lifecycle followed for B); A's push of its 3-entry record - exactly an earlier prefix of the 4-entry chain B stands on - reports `delivery_refused` / `snapshot_regression` in the push result; B's file, standing (4 loaded), and wire are unchanged, and A is unchanged | `test_push_backward_to_ahead_peer_refused_snapshot_regression` | PASS |
| 4 | The lifecycle the peer cannot accept: the grown record reaches A with A's anchor advanced, but B's anchor still stands over the old head; A's push reports `delivery_refused` / `anchor_stale`, B's file is untouched and B still serves alice's policy (the peer's stale window, asserted); once the lifecycle is completed for B, the same push delivers and B flips to 410 | `test_push_with_peer_anchor_behind_refused_anchor_stale` | PASS |
| 5 | A rewrite does not spread: a server started WITHOUT `--anchor` accepts experiment 012's rewritten record through its own case 14 delivery (no consultation, and it serves mallory's policy - the anchorless acceptance, stated plainly as in cases 5/8), then pushes it to an anchored peer; the push reports `delivery_refused` / `anchor_mismatch`, and the anchored peer's file, view, and standing are unchanged | `test_unanchored_sender_cannot_spread_rewrite_to_anchored_peer` | PASS |
| 6 | A mesh-only sender: the push reports `no_snapshot` on HTTP 200 with `delivery` null, the sender's `/health` records it as its last push, and its mesh behavior is undisturbed | `test_mesh_only_sender_reports_no_snapshot` | PASS |
| 7 | The full flow over the protected link, with identity binding and a signed anchor: A accepts the revocation delivery with the same-signer lifecycle followed for both servers' signed anchors, then pushes over HTTPS; the push reports `delivered` (loaded 3), B's file is the delivered record, the revoked policy refuses 410 over the protected link, bob's serves, and both health surfaces report the new standing | `test_signed_tls_server_push_full_flow` | PASS |
| 8 | The identity refusal across servers, at the transport, exactly as in experiment 017: a pushing server in experiment 014 mode (mutual TLS, binding OFF) claiming `push-false` while presenting the certificate issued for `push-tls-other` is driven to push to a binding peer; the peer's accepting-side check refuses before case 14's endpoint runs, the push is reported on HTTP 502 with error `identity_mismatch` naming claimed `push-false` and certified `push-tls-other`, the peer's log carries the refusal as in case 11 - and the peer never even records a delivery (`last_snapshot_delivery` still null), its file, view, and standing unchanged | `test_false_claiming_pusher_refused_identity_mismatch` | PASS |

The demo (`python3 packages/synchronization/demo_server_to_server_delivery.py`)
also runs end to end and prints the outcomes in the same
shapes (see `test-output.txt`): two anchored servers on the
same record, neither restarted, B's file never copied by
hand - the revocation delivered to A (case 14), one driven
push A -> B delivers (B's file is the delivered record,
alice refuses 410 from B, bob serves, both health surfaces
report the standing), and the same push again reports
`no_change` and rewrites nothing.

## How the mechanism works, in plain words

`POST /push-snapshot` on the sender runs `verify_peer_
identity` first when binding is on (experiment 017's
initiating-side check, shared with `/sync`), reads the
standing entries from the sender's PolicyNode under the
node's lock, and posts them to the peer's
`/deliver-snapshot` through the module's client helpers -
which present the sender process's own certificate and
claim its node id, exactly as its `/sync` initiation does.
The peer's `deliver_snapshot` runs unmodified: identical
record `no_change`, earlier prefix `snapshot_regression`,
anything else judged by a full candidate PolicyNode
construction (chain verification plus the anchor
consultation, signature included where configured) before
anything changes, a stood delivery rewriting the peer's
snapshot file and reloading through experiment 024's path.
The sender then wraps the peer's report - `{"node_id",
"peer", "status": <the peer's delivery status>,
"delivery": <the peer's report>}` - records it as its own
last push for `/health`, and returns it. Non-200 answers
from the peer (a transport refusal before the endpoint ran)
and unreachable peers become 502 bodies in `/sync`'s refusal
shape, recorded the same way.

Design choice worth naming: the sender contributes data and
reports the verdict; it never renders one. Everything strict
about a cross-server record move was already built, once, on
the receiving side in experiments 024/025 - the candidate
construction, the anchor consultation, the refusal
vocabulary, the file-and-view discipline. This experiment's
whole sender is an initiation surface in `/sync`'s style
plus honest reporting (including carrying a transport
refusal's own body through instead of flattening it into a
generic error), and that smallness is the point: server-to-
server delivery is case 14's delivery with a server holding
the pushing end of the client helpers.

## What failed

The tests-first run failed as designed: before the endpoint,
helper, and health field existed, the new suite errored
seven times on exactly those absences, recorded verbatim in
`test-output.txt`. One test-side defect was found and fixed
before the build, also recorded there in substance: the
rewrite-spread test first asserted the anchorless sender
serves mallory's policy while its payloads file held no
mallory payload - a Gate 2 node serves only payloads it
holds (`PolicyNotFound`), so the test now gives the sender a
payloads file that holds mallory's code, and the tests-first
failure then lands on the push surface's absence alone.
After the build: the new suite passed 7/7 on its first
post-build run, and the full regression is green on the
clean re-run; the one loaded run's single error is the
known port-file race in experiment 026's unmodified
`wait_ready` helper (an empty port file read mid-write),
recorded verbatim in `test-output.txt` and superseded by
the clean re-run, exactly as experiment 026 recorded the
same race in its own first loaded discovery. No other suite
was modified to make any of this pass.

## Honest limits (this is a prototype, not production)

- The push is driven, never unattended: a client call drives
  `POST /push-snapshot`, as a client call drives `/sync`.
  No timer, scheduler, gossip, discovery, or peer list
  inside either server fires one, solicits one, or retries
  one; two servers nobody drives stay exactly as current as
  the last drive left them. This experiment adds the
  server-held end of the transport; it does not add the
  driver, as experiment 025 said of its own surface.
- The push transports the record, not the anchor. Each
  peer's anchor still arrives out of band (its file is the
  local stand-in of experiments 012-019), and the lifecycle
  steps in the tests are performed per server - row 4 is
  the direct consequence: a peer whose anchor was not
  advanced refuses the push `anchor_stale`, and completing
  the lifecycle is an out-of-band act no push performs.
  Anchor distribution and a witnessed anchor remain the
  open problem they were in 012-026.
- The push surface inherits the transport's authorization,
  no more: over plaintext (experiment 007 mode) anyone who
  can reach the sender's port can drive a push, as they
  already could drive `/sync`, checks, and deliveries;
  over mutual TLS with identity binding (experiments
  014/017, rows 7-8) only a credential-holding peer whose
  claim matches its certificate can, and a false claim is
  refused before the delivery endpoint runs. What a driven
  push can make a peer do remains bounded by the peer's own
  verification - nothing untrusted can be made to serve by
  pushing - exactly as in experiment 025. There is no
  per-endpoint authorization beyond the transport, as
  everywhere in this prototype.
- The sender pushes the record it stands on at the moment
  the push is driven; nothing versions or sequences pushes
  between two servers (a peer that is ahead refuses the
  backward push, row 3 - that fence is the receiver's
  case 14 prefix judgement, not a sender-side ordering).
  Two servers pushing to each other concurrently are not
  tested and no claim is made about them; the node's one
  lock serializes each process's own accesses, as
  everywhere in this prototype.
- Baseline honesty is inherited unchanged: the sender's
  statement "these are the entries I stand on" is its
  loaded view, whose construction at startup trusted its
  snapshot file exactly as in 022/024/025; the peer's
  judgement of the pushed entries does not depend on that
  trust (it verifies the chain and consults its own anchor),
  which is why an anchorless sender's rewrite (row 5) is a
  finding about the sender, not a hole in the peer.
- Inherited limits stand unchanged: localhost processes on
  one machine, HMAC prototype signing, signed-but-
  unwitnessed anchors, no consensus, no discovery, no
  identity proofing beyond a certificate's node_id, no
  certificate lifecycle, mesh state in process memory with
  no persistence, and no performance, scale, or latency
  number measured or claimed.
- Deliberate removal with notification (availability case 3,
  experiment 011's blocked item) remains unbuilt and
  untouched, and `PBA-FIX-001` remains talk-first and
  untouched by this experiment.

## Process captured

Reusable process: when a capability exists at a boundary
with a client holding one end (case 14's delivery), the
server-held version is done when the receiving side needs
ZERO changes and the sender's whole job is initiation plus
honest reporting - if the build starts modifying the
receiver to accommodate the sender, the composition is
wrong. Second lesson, from the one non-200 path: a refusal
that arrives as a transport status is still a verdict with
a body; carrying the body's own fields (claimed/certified)
through to the caller, instead of flattening them into a
generic failure, is what makes the sender's report evidence
rather than summary - the helper that asserts a 200 is the
wrong tool exactly where the refusal is the outcome.
Opportunity Stack entry: extends experiment 025's
arrival-parity clause with an initiation-parity clause: a
delivery discipline is one system when every INITIATOR -
credential-holding client, or a server pushing its own
standing record - arrives at the same receiving surface and
is judged identically, and when the initiator's report is
the receiver's report, not a re-derivation. What would
differ next time: state the payload-holding precondition in
the case itself when a test asserts serving after an
acceptance (the rewrite-spread leg's first draft asserted a
serve the fixture could not support - a Gate 2 node serves
only payloads it holds - and finding that cost one
tests-first cycle; case 16 now names acceptance and serving
as the separate facts they are).

## Next, per the build order

Sync-mesh case 16 closes the server-to-server item
experiment 025 left open, at prototype scale: two running
servers can now bring each other current on the registry
side, judged exactly as strictly as any delivery, with the
refusals a cross-server push can meet named in the result
the sender sees. The no-server-to-server-push limit
statement in experiment 025's record carries a
superseded-in-part note pointing here; `node_server.py`'s
own limits are restated with the push named as driven, like
every other delivery surface. The honest distance to
utilization is unchanged in kind and shorter by that item:
deliberate removal (availability case 3, blocked on Max in
experiment 011), anchor distribution and a witnessed
anchor, unattended delivery scheduling with a real clock (a
driver for the pushes, checks, and deliveries cases 13/14/16
expose), liveness in the server processes, production
crypto and identity proofing, consensus, persistence, and
Gate 5's open production benchmark. `PBA-FIX-001` remains
talk-first and untouched by this experiment. Nothing here is
deployed beyond the localhost test processes the tests
themselves spawned and cleaned up, and nothing in this
experiment changes that.
