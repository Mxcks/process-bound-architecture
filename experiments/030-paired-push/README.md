# 030. Paired server-to-server push: one server brings a peer fully current, record and anchor together

Experiment 027 gave a running server a push for the record
alone (`POST /push-snapshot`), and experiment 028 gave a
running server a surface for receiving its registry anchor
over the wire (`POST /deliver-anchor`), alone or paired
with the record it blesses. Between them one gap stayed
open, stated in both experiments' limits in the same
words: no server could SEND its own anchor onward. The two
halves of a peer's currency still could not travel together
from a server. A peer whose anchor had not advanced refused
a record-only push `anchor_stale`, correctly, and the only
way to complete that peer's lifecycle was a client driving
028's surface by hand, or a hand at the machine placing the
anchor file. The sending half of the anchor's wire story
was the last piece missing.

This experiment closes that half, at the same prototype
scale and on the same terms. A running `node_server` gains
a driven `POST /push-anchor` endpoint in experiment 027's
style: payload `{"peer": url}`, a client helper
`push_anchor(base_url, peer_url)`, and the outcome recorded
on the sender's `/health` as `last_anchor_push` beside
`last_snapshot_push`. Driven, the sender posts the entries
its `PolicyNode` stands on AND the anchor object its own
anchor file holds to the peer's `POST /deliver-anchor` in
its paired form (`{"anchor": ..., "entries": [...]}`) -
experiment 028's receiving surface, unmodified, judging the
pair under case 17's whole order, experiment 029's witness
judgement included where the peer is witness-configured.
The peer's paired delivery report is the push's result,
returned in `/push-snapshot`'s report shape. A sender with
no anchor configured sends the record alone to the peer's
`/deliver-snapshot`: experiment 027's push exactly, its
outcome recorded as the last anchor push. The case this
closes is case 19 of `tests/test-cases/sync-mesh.md`,
written before the build, in the detailed format of cases
12-18.

## Question

Can one running server bring a peer fully current in a
single driven push - record and anchor together, judged at
the peer by the peer's own unmodified rules - so that after
a revocation lands at the sender through the sender's own
accepted paired delivery, ONE push makes the peer serve the
revocation (the clean 410), keeps its other policy serving,
and leaves both of the peer's files byte-for-byte what the
sender sent, with no restart, no client driving the peer,
and no manual step anywhere? And can the same setup prove
the old path still cannot do it: the record-only push of
experiment 027, in the identical state, still refusing
`anchor_stale` at that peer?

## The design choices this experiment sets, stated plainly

The brief left the purely technical shape to the
experiment; these are the choices, each in the style of
what already exists:

- The surface is `POST /push-anchor` with payload
  `{"peer": url}` in `POST /push-snapshot`'s exact shape
  (a malformed call refuses on 400 with `push-anchor needs
  a peer base URL`), a client helper
  `push_anchor(base_url, peer_url)` returning the (status,
  body) pair like `push_snapshot`, and `/health` gains
  `last_anchor_push` beside `last_snapshot_push`. On 200
  the report is `{"node_id", "peer", "status": <the peer's
  paired delivery status>, "delivery": <the peer's paired
  delivery report>}`; on a peer that cannot be reached, a
  transport refusal, or the sender's own anchor unreadable,
  the answer is 502 in `/sync`'s refusal shape, carrying
  `claimed` / `certified` / `detail` through where the
  transport supplies them, exactly as 027's push carries
  them.
- What is sent is what the sender stands on, read under
  the node's lock: the record half is the `PolicyNode`'s
  loaded, verified entries (never a file read around the
  view), exactly as `_push_snapshot_to_peer` reads them;
  the anchor half is the parsed object from the sender's
  own anchor file. For the anchor the file IS the standing,
  because the `PolicyNode` consults that very file at
  startup and on every reload, so file and standing agree
  by construction.
- The receiving side is not touched. The push performs no
  lifecycle step for the peer and judges nothing itself:
  succession, the backward fence, signature and history,
  the witness judgement, and pairing are the peer's, run by
  028's code on 028's surface. A witness countersignature
  rides inside the anchor object itself (experiment 029),
  so a countersigned anchor travels whole, and completing
  the lifecycle is the sender's own work at its own anchor
  file before it pushes (row 4).
- The sender's degenerate shapes are stated, not implied:
  no `PolicyNode` (a mesh-only process), the push answers
  200 with `status: "no_snapshot"` and `delivery: null`,
  in `/push-snapshot`'s own convention; an anchor file
  configured but unreadable, or holding something that is
  not a JSON object, at push time, the push refuses on 502
  `own anchor unreadable: ...`, recorded as the last anchor
  push, and the peer is never contacted - a sender that
  cannot read its own anchor has nothing honest to send;
  no anchor configured, the record alone travels, to the
  peer's `/deliver-snapshot`, and the new surface adds
  nothing.
- The initiating-side identity check of experiment 017
  runs first when binding is on, exactly as in 027's push:
  the peer's certificate identity is verified against the
  node id the peer claims before anything is pushed, and a
  mismatch refuses the push outright on 502
  `identity_mismatch`.

## Result: all case 19 outcomes observed, 9 new tests pass, full regression green

| # | Outcome (case 19) | Test | Result |
|---|---|---|---|
| 1 | The end-to-end story over two running plaintext servers with signed anchors: both start on the same record and anchor; the revocation reaches A by A's own accepted case 17 paired delivery (same-signer re-anchor, sequence 0 -> 1) while B still serves the revoked policy (the stale window asserted); the record-only push then refuses `anchor_stale` at B as the control, B's files unchanged; ONE paired push from A reports `delivered` and brings B fully current - B's snapshot file byte-for-byte the delivered record, B's anchor file byte-for-byte the anchor A sent, the revoked policy answering the clean 410 `policy_revoked` over B's wire, the other policy still serving, no restart (same process throughout), A's `/health` carrying the push in `last_anchor_push` and B's carrying the delivery in `last_anchor_delivery`; a second identical paired push reports the pair's `no_change` and rewrites NEITHER of B's files | `test_paired_push_brings_peer_fully_current` | PASS |
| 2 | Succession across servers: a sender whose anchor is signed by a DIFFERENT identity pushes its pair; the push result reports `delivery_refused` with reason `different_signer` exactly as the peer judged it, the refusal is audit-logged at the peer in case 10's shape, and the peer's snapshot file, anchor file, and serving are unchanged | `test_paired_push_different_signer_refused` | PASS |
| 3 | The backward fence across servers: the peer holds a LATER version of the same anchor lineage (a refreshed anchor over the standing record, delivered to the peer alone under case 17 and accepted) while the sender still holds the earlier version; the sender's paired push reports `delivery_refused` with reason `anchor_regression`, and the peer's anchor file (still the later version), snapshot file, and serving are unchanged | `test_paired_push_anchor_regression_refused` | PASS |
| 4 | The witness fence across servers (case 18's discipline, carried): the peer is witness-configured, the sender is not, and the sender's signer-alone pair is refused in the push result as `anchor_witness_invalid`, the peer unchanged; once the sender's anchor over the same grown head is countersigned by the configured witness at the SENDER (the lifecycle completed where it belongs, a write at the sender's own anchor file), the same paired push reports `delivered` and the peer flips to the 410 - the push transports a completed lifecycle, it performs none at the peer | `test_paired_push_witness_refused_then_delivered` | PASS |
| 5 | No anchor configured on the sender: the paired push sends the record alone to the peer's `/deliver-snapshot` and behaves exactly as experiment 027's push, its outcome recorded as the sender's last anchor push | `test_push_anchor_without_anchor_pushes_record_only` | PASS |
| 6 | A mesh-only sender (no `PolicyNode`): the push answers 200 `no_snapshot` with `delivery: null`, and nothing is sent anywhere | `test_push_anchor_mesh_only_sender_no_snapshot` | PASS |
| 7 | The sender's own anchor file unreadable at push time: the push refuses on 502 `own anchor unreadable`, recorded as the last anchor push, and the peer is never contacted (its health records no anchor delivery) | `test_push_anchor_own_anchor_unreadable` | PASS |
| 8 | The full paired flow over mutual TLS with identity binding and a signed anchor: A's pair delivered to B over HTTPS, both of B's files moved to the sent pair, the revoked policy refusing 410 over the protected link, both servers' `/health` surfaces reporting the new standing | `test_signed_tls_paired_push_full_flow` | PASS |
| 9 | The identity refusal at the transport, exactly as in cases 11 and 16: a pushing server in experiment 014 mode (mutual TLS, binding OFF) claiming a node id its certificate was not issued for, holding an anchor of its own so the paired form is what it attempts, is refused by the binding peer before the paired endpoint runs - the push reports 502 `identity_mismatch` naming claimed and certified ids, and the peer records no anchor delivery, its files and standing unchanged | `test_false_claiming_paired_pusher_refused` | PASS |

Suite counts, all run in this session and recorded in
`test-output.txt`: the new suite 9/9 by direct file run and
inside discovery; the full regression after the build is
registration 25, policy-library 42, synchronization 100
(91 baseline plus the 9 new), validation 22 - 189/189 -
plus the EAI handoff verification PASS 3/3, against the
pre-build baseline of 180/180 plus the same handoff PASS.
No earlier suite was modified. No `node_server` process was
left running after any run. The demo
(`demo_paired_push.py`) walks the same story outside the
test harness: revocation pair delivered to A, control push
refused `anchor_stale`, one paired push delivered with both
of B's files byte-for-byte the sent pair and alice at 410
while bob serves, second paired push `no_change`.

## How the mechanism works, in plain words

Until this experiment, a peer's currency had two halves
that travelled by different roads: the record could be
pushed server to server (027), but the anchor could only be
carried to a server by a client driving 028's surface, or
placed by a hand. This experiment adds the one missing
road, and adds nothing else. When a driver calls
`/push-anchor` on server A naming server B, A reads the two
things it stands on - the verified entries its serving view
holds, and the anchor object in its own anchor file - and
posts them together to B's `/deliver-anchor`, the surface
028 already built, in the paired form 028 already judges.
B runs case 17's whole judgement on the pair exactly as if
a client had driven the delivery: succession, the backward
fence, signature and kept history, the witness judgement
where B is witness-configured, and pairing, before either
of B's files moves; when the pair stands, both files are
replaced from the verified pair and B's serving view
reloads once, so the revocation A carries is live at B in
the same call. B's paired delivery report travels back as
the push's result, word for word the peer's own judgement,
and A records it on its health surface. The control in row
1 is the proof the road was missing: in the identical
state, 027's record-only push is refused `anchor_stale` at
B, because the record arrived asking to be consulted
against an anchor that had not moved, and nothing in that
push could move it. The paired push moves both, so the
consultation has something true to consult.

## What failed

The tests-first run failed as designed, and one part of it
failed honestly in a way worth keeping in the record.
Before the build, the new suite ran FAILED (failures=1,
errors=8): the 8 errors were the named absence, the
`push_anchor` helper and the surface it drives not existing
(`AttributeError: module 'node_server' has no attribute
'push_anchor'`). The 1 failure was a TEST-side defect,
found and fixed before any implementation was written: the
anchor-regression setup wrote the refreshed anchor directly
into the peer's anchor file, so the sender delivering its
earlier version correctly reported `no_change` (an
identical anchor is no change) instead of the expected
`anchor_regression`. The defect was in the test's staging,
not in the design: the refresh now reaches the peer by
delivery alone, from a staging copy outside the peer,
extending the lineage the peer holds, and the sender's
earlier version then refuses under the backward fence as
the case requires. Experiments 027 and 028 recorded the
same species of pre-build test defect in the same place;
the discipline is unchanged: fix the test's staging, never
bend the expectation. A capture note, stated plainly: the
original tests-first and baseline capture files were held
in /tmp and lost when the machine's /tmp cleared
mid-session. The tests-first evidence was reproduced after
the build against the exact pre-build `node_server.py` as
pushed for experiment 029 (GitHub blob
0cd09598c75df501e78ad23efb7f8913d2530c40): Ran 9 tests,
FAILED (errors=9) - 8 on the missing `push_anchor`, 1 on
the missing `last_anchor_push` health field, every error
the named absence and no other failure mode. That
reproduction is the verbatim tests-first evidence in
`test-output.txt`, labelled as what it is. The first full
regression pass after the build was captured through a
tail filter that did not retain the per-package Ran lines;
it was re-run with full per-package captures, and the
re-run is the regression of record (189/189, no transient
error in it). No suite was modified to make any of this
pass.

## Honest limits (this is a prototype, not production)

- The paired push is driven, never unattended or
  solicited: a client call fires it, as with `/push-snapshot`
  and `/sync`. No timer, scheduler, gossip, or discovery
  inside either server fires one, nothing retries one, and
  a peer nobody pushes to stays exactly as current as the
  last drive left it. This experiment adds the sending
  server as a possible driver of 028's surface; it does not
  add an unattended driver.
- The push transports the sender's REGISTRY anchor only.
  Case 15's mesh anchor (experiment 026) still arrives out
  of band; the mesh side's consultation inputs are
  unchanged, and no push surface carries it.
  (Superseded in part by experiment 031: the mesh anchor
  now arrives over the wire too, at its own surface -
  `POST /deliver-mesh-anchor` under sync-mesh case 20,
  judged against the mesh state the receiving server
  holds. What stands of this bullet: the PAIRED PUSH still
  transports the registry anchor only, and no push surface
  carries the mesh anchor even now; experiment 031's mesh
  delivery is client-driven, as case 17's was before this
  experiment built its sender.)
  (Superseded in part by experiment 032: a push surface
  now carries the mesh anchor - `POST /push-mesh-anchor`
  under sync-mesh case 21 sends the anchor object the
  sender's own mesh anchor file holds to a peer's
  `POST /deliver-mesh-anchor`, judged there by experiment
  031's judgement unmodified. What stands of this bullet:
  the PAIRED PUSH still transports the registry anchor
  only.)
- There is still no anchor-ALONE server push: a server
  sends its anchor paired with the record it blesses, or
  (with no anchor configured) sends the record alone exactly
  as 027. An anchor whose record has not changed travels
  only as half of a pair whose record half the peer already
  holds, where the pair's judgement reduces to the
  anchor-alone judgement on the peer's side.
- The push performs no lifecycle step for the peer and
  manufactures nothing: succession, freshness, and
  countersignature are the sender's completed facts, judged
  at the peer. A sender whose anchor lacks a countersignature
  a witness-configured peer requires is refused (row 4)
  until the lifecycle is completed at the sender; the
  transport cannot countersign, re-anchor, or refresh
  anything, on either side.
- The push inherits the transport's authorization, no
  more: over plaintext (experiment 007 mode) anyone who can
  reach the sender's port can drive a push, and anyone who
  can reach the peer's port could already deliver the same
  pair as a client; over mutual TLS with identity binding
  (experiments 014/017, rows 8-9) only a credential-holding
  peer reaches either surface. There is no per-endpoint
  authorization beyond the transport, as with every surface
  since 007.
- What the push sends is the sender's standing AS READ AT
  PUSH TIME under the node lock: the verified entries the
  sender serves from and the anchor object its anchor file
  holds at that moment. It is a snapshot of a standing, not
  a subscription; a sender that advances after the push
  leaves the peer behind again until the next drive, exactly
  as every driven surface in this prototype leaves things.
- The signature model is unchanged, and 028's finding
  stands exactly where 029 left it: signing is symmetric
  HMAC, a secret that verifies can also sign, and at a
  point with no witness configured the custody of the
  signer's secret is the whole fence - a sender legitimately
  holding that secret can push a pair blessing a rewritten
  record and the peer will accept it, over this push exactly
  as over 028's surface driven by a client. The witnessed
  half of the closure (029) is respected wherever the peer
  is witness-configured (row 4); the asymmetric half remains
  unbuilt, because Gate 1 enrollment has no asymmetric key
  model to build it on.
- One machine, localhost, runtime-generated throwaway keys
  and certificates, hand-started processes: no real
  deployment, no PKI, no certificate revocation, no
  discovery beyond the address a driver is handed, no
  production cryptography. The anchor secrets in the tests
  are enrollment secrets generated at runtime into test
  temp dirs; nothing about key custody in a real deployment
  is designed here.

## Process captured

Reusable process: a missing wire capability is closed by
making the existing receiving surface reachable from a new
sender, never by building a second judgement - the paired
push is 027's push shape carrying 028's payload to 028's
unmodified surface, so the wire still has exactly one
paired judgement in the codebase and every strengthening of
it (as 029's witness judgement strengthened it) applies to
client-driven and server-driven delivery at once. The
control discipline is the other half of the process: the
new path is proved against the old path IN THE SAME SETUP,
the record-only push refusing `anchor_stale` in the state
the paired push then closes, so the claim "the gap is
closed" rests on a contrast, not on the new path merely
working. Opportunity Stack entry: the wire lifecycle of the
prototype is now complete in both directions for both
halves of a peer's currency (record and registry anchor:
deliver, push, consult), leaving the mesh anchor's
transport and unattended drivers as the named open wire
work. (The mesh anchor's transport has since landed as
experiment 031's client-driven receiving surface; the
unattended drivers remain open, and no server push
carries the mesh anchor.) (Experiment 032 has since built
the mesh anchor's sender - `POST /push-mesh-anchor` under
sync-mesh case 21; the unattended drivers remain open.)
What would differ next time: keep run captures under
the workspace, not /tmp - this session lost its tests-first
and baseline captures to a /tmp clear and had to reproduce
the tests-first evidence against the pushed pre-build code,
which worked only because the pre-build code was itself
pushed and retrievable.

## Next, per the build order

Both halves of a peer's currency now travel both
directions, so the remaining wire work is the mesh anchor's
transport (case 15's anchor still arrives out of band) and
unattended drivers for surfaces that are all still driven.
(The mesh anchor's transport has since landed, as
experiment 031's `POST /deliver-mesh-anchor` under
sync-mesh case 20 - client-driven, judged against the
state the receiving server holds; a server-driven mesh
anchor push and the unattended drivers remain open.)
(The server-driven mesh anchor push has since landed as
experiment 032's `POST /push-mesh-anchor` under sync-mesh
case 21; the unattended drivers remain open.)
The build order otherwise continues to the items the
earlier experiments recorded: real multi-machine
deployment, certificate revocation and lifecycle, node
discovery beyond set addresses, the Gate 5
production-scale timing benchmark, the asymmetric half of
experiment 028's closure, and availability case 3's
deliberate-removal semantics, which remain blocked on the
scope decision recorded in experiment 011.
