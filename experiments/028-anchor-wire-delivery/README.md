# 028. Anchor wire delivery: a running server receives its registry anchor over the wire, judged together with the record it blesses

Experiments 025 and 027 closed the record's side of the
wire: a running server could be handed the updated registry
record (`POST /deliver-snapshot`), and one server could push
its standing record to another (`POST /push-snapshot`). Both
left the same half of the lifecycle in a pair of hands at
the server: the registry anchor (experiments 012, 015,
018, 019) - the signed pin outside the record against
which every wire delivery is consulted - still arrived out
of band, a file placed by hand. Every anchor experiment
since 012 said so, in the same words. The direct
consequence ran through 025 and 027: a record delivery
whose anchor lifecycle had not been performed by hand at
that server was refused `anchor_stale`, correctly, and
nothing either server could do over the wire completed
that lifecycle. The anchor was the last piece of the
serving state that only a hand at the machine could move.

This experiment closes that half, at the same prototype
scale and on the same terms as the halves before it. A
running `node_server` gains a driven `POST /deliver-anchor`
endpoint, in experiment 025's surface shape, carrying the
anchor object's own JSON - the content itself, never a
path - alone, or paired with the entries of the record it
blesses in the same call. The delivery is judged before
anything is accepted, in the order the case sets:
configuration, identity of content, succession under
experiment 019's same-identity rule (a different signer's
anchor refused and audit-logged beside the anchor, in case
10's own event shape), the backward fence (an anchor older
than the one the server holds refused as
`anchor_regression`, for where it points, whichever of its
two shapes it arrives in), signature and kept history
under experiment 019's verification code - the same code,
loaded and called, not a copy - and finally pairing: an
anchor alone is accepted only for the record the server
already stands on, and an anchor whose head pairs with
nothing the server holds is refused `anchor_unpaired`; a
pair is judged together, the record half through
experiment 025's whole discipline (case 14's refusals,
unchanged) with the DELIVERED anchor as the consultation,
neither half applied unless both stand, both of the
server's files replaced from the verified pair when they
do. A first anchor where the server holds none, or over an
unsigned anchor, establishes the signer, exactly as case 10
set. The anchor case this closes is case 17 of
`tests/test-cases/sync-mesh.md`, written before the build,
in the detailed format of cases 12-16.

## Question

Can a running server be brought its anchor over the wire
it already speaks - judged before acceptance under every
rule the anchor already carries on disk (signature, kept
history, same-identity succession), refused when it is
older than the anchor the server holds, refused when it
blesses a chain the server cannot check it against - and,
when the anchor and the record it blesses travel together,
judged together, so the server ends serving the delivered
record under the delivered anchor with no restart and no
manual lifecycle step at that server? And the question the
case insists be answered with evidence rather than
comfort: when the deliverer legitimately holds the current
signer's secret - as every enrolled verifier in experiment
019's symmetric model does - what, exactly, stops a
rewritten record from being blessed over this wire?

## The design choices this experiment sets, stated plainly

The brief left the purely technical shape to the
experiment; these are the choices, each in the style of
what already exists:

- The surface is `POST /deliver-anchor` with a client
  helper `deliver_anchor(base_url, anchor, entries=None)`,
  beside case 14's `POST /deliver-snapshot` and case 16's
  `POST /push-snapshot`, and `/health` gains
  `last_anchor_delivery` beside `last_snapshot_delivery`
  and `last_snapshot_push`. The payload is `{"anchor":
  {...}}` or `{"anchor": {...}, "entries": [...]}`: the
  pairing is one call carrying both halves, never two calls
  whose halves could land apart. Malformed payloads refuse
  on 400 in the `/deliver-snapshot` handler's shape.
- A report mirrors the snapshot delivery report:
  `{"status": "delivered" | "no_change" |
  "delivery_refused", ...}`, failures carrying the reason
  under its refusal name; every attempt, accepted or
  refused, lands in `/health`'s `last_anchor_delivery`,
  and an accepted delivery reports its record half's
  judgement in an embedded `snapshot` object and the
  anchor's own movement as `before_seq` / `after_seq`,
  reusing case 10's sequence name.
- The judgement order is fixed and stated in the case:
  configuration (`no_anchor`, then `no_snapshot`), a
  delivered anchor that cannot be read as an anchor
  (refused under experiment 012's parsing names), the
  server's own unreadable anchor (`anchor_unreadable`, in
  case 12's naming family), the paired record's pre-checks
  in case 14's order (identical entries = the anchor-alone
  judgement; an exact earlier prefix =
  `snapshot_regression`, judged before the anchor half, so
  a backward record is named for the record), byte-identical
  anchor = `no_change`, succession (`different_signer`,
  refused and audit-logged through case 10's own audit
  write path, event `reanchor_rejected`, before any
  signature judgement - a stranger's anchor is refused for
  whose it is, not for how it verifies), the backward fence
  (`anchor_regression`, in both its shapes: a delivered
  anchor whose kept history is a proper prefix of the
  current one - an earlier version of the server's own
  anchor file - and a delivered anchor pinning an earlier
  prefix of the standing chain, each recognised wherever it
  appears in the order), signature and kept history
  (`anchor_signature_invalid`: verified under the server's
  configured signer where one is configured; where none is,
  a signed delivered anchor's history is still verified for
  internal truth and a head-only current anchor leaves the
  succession refusal as the only identity fence, case 10's
  standing on an unsigned lifecycle, stated not repaired),
  and pairing last.
- Pairing is proved, never assumed from shape: an anchor
  alone is judged by experiment 015's registry
  consultation - `check_registry_against_anchor` over the
  server's snapshot file - and that consultation's
  `anchor_mismatch` is renamed `anchor_unpaired` in this
  delivery's report, because what it proves here is that
  the anchor pairs with nothing the server holds; its
  `anchor_stale` passes through under its own name,
  because an anchor behind a grown record is a different
  fact from an anchor over a foreign chain. A pair is
  judged by building experiment 015's `PolicyNode` over
  candidate files (the delivered record and the delivered
  anchor, staged beside the live ones) under the server
  process's own anchor configuration - the same
  construction the server's startup and case 13's checks
  use - and any refusal it raises is reported under that
  construction's own names.
- Nothing is duplicated: the delivered anchor is parsed by
  experiment 012's reader, its succession judged through
  experiment 019's own audit function, its signature and
  history verified by experiment 019's own functions, its
  pairing consulted through experiment 015's functions and
  `PolicyNode`, and a stood pair lands by experiment 024's
  reload, exactly as case 14's delivery lands. The one
  wiring fact worth naming: `node_server` loads the
  registration package's `anchor.py` by file path under a
  private module name, the same move `policy_node.py` made
  in experiment 012, because two packages ship a file named
  `anchor.py` and an import would take the wrong one.

## Result: all case 17 outcomes observed, 10 new tests pass, full regression green

| # | Outcome (case 17) | Test | Result |
|---|---|---|---|
| 1 | Anchor alone, plaintext, signer configured: the identical anchor reports `no_change` (file byte-for-byte, no audit append); bob grows the record and alice's refreshed anchor over the SAME head is delivered - the count moves by registration's own entries, not re-anchoring - and accepted `delivered` under succession and pairing (file byte-for-byte, standing view untouched, bob serves, health reports both); a case 13 check after reports `no_change`, so the move broke no standing | `test_anchor_alone_no_change_then_refresh_delivered` | PASS |
| 2 | Anchor alone, unpaired: mallory's genuine signed anchor over a real foreign chain is refused `anchor_unpaired`; anchor file and view byte-for-byte and semantically unchanged, alice still serves | `test_anchor_alone_unpaired_refused` | PASS |
| 3 | The pair judged together: the grown record with the OLD anchor refuses `anchor_stale` with NEITHER half applied (both files byte-for-byte, the stale window asserted, health names the refusal); the new anchor alone refuses `anchor_unpaired`; the pair then reports `delivered` with the embedded record judgement `delivered` (loaded 3) and the anchor's sequence moved, BOTH files byte-for-byte the delivered pair's serializations, alice 410 and bob 200 over the wire, the process never restarted, health `anchored` with the pair's delivery in `last_anchor_delivery`; a stale check after reports `no_change`, and a malformed call (a list where the anchor belongs) refuses on 400 and moves nothing | `test_paired_delivery_judged_together` | PASS |
| 4 | A tampered kept history: an earlier entry's head edited inside the delivered object, heads and signatures otherwise the genuine current anchor's, refuses `anchor_signature_invalid` before any head judgement; the anchor file byte-for-byte unchanged, no audit event written | `test_tampered_history_refused_signature_invalid` | PASS |
| 5 | Backward anchors, both shapes: the earlier VERSION of the server's own anchor (a proper prefix of its kept history, pinning the earlier head it was written for) refuses `anchor_regression` and is audit-logged in case 10's shape; an original-state anchor with NO history (the unsigned-lifecycle shape) pinning the same earlier head refuses `anchor_regression` by the head fence - the naming never depends on history's presence; the current anchor byte-for-byte unchanged, the standing view untouched, and a clean delivery after still lands | `test_backward_anchor_refused_anchor_regression` | PASS |
| 6 | THE FINDING, in three legs: (i) the rewritten record paired with the genuine CURRENT anchor refuses `anchor_mismatch` - the anchor over the true chain is the fence, and it holds; (ii) the same pair with the anchor over the rewrite signed by BOB refuses `different_signer` and is logged - the identity fence holds against a stranger with a genuine enrollment secret of his own; (iii) the same pair with the anchor over the rewrite signed by ALICE - the current signer, its kept history honestly extended by case 10's own write path, the deliverer legitimately holding alice's enrollment secret as every verifier in this symmetric model does - is ACCEPTED: succession passes (same identity), the backward fence does not fire (the history extends), the signature verifies (it is genuine), and the pair stands together under the same consultation that refused legs (i) and (ii); the server's snapshot file is the rewritten record's serialization and it serves mallory's policy with her exact bytes while alice's genuine policy returns 404 | `test_rewrite_anchor_finding` | PASS - the finding is the result: ACCEPTED, stated as such |
| 7 | Succession on a head-only server (no signer configured): bob's signed anchor over the SAME chain refuses `different_signer` and is audit-logged in case 10's shape (identity is judged from the delivered objects before any signature judgement); alice's own refreshed anchor over the grown head then pairs `delivered`, so the fence is the signer's identity, not signatures as such | `test_different_signer_refused_and_logged` | PASS |
| 8 | Configuration edges: a server started without `--anchor` answers `delivery_refused` / `no_anchor` with `last_anchor_delivery` recorded and the standing view untouched; a `NetworkNode` built with an anchor path and no snapshot node answers `no_snapshot` in-process, no files read or written | `ConfigEdgeTests` (2 tests) | PASS |
| 9 | The pair over the protected link: a mutual-TLS server with identity binding and a signed anchor (alice, secret file) receives the identical anchor `no_change` over HTTPS, then the pair `delivered` - succession, pairing, and the record's anchor consultation all passing over the protected link - alice refusing 410 over HTTPS and bob serving, `/health` reporting 3 entries under the anchor; the wire adds nothing the protected transport does not already carry | `test_paired_delivery_over_tls` | PASS |

Suite counts, all run in this session and recorded in
`test-output.txt`: the new suite 10/10 by direct file run
and inside discovery; the full regression after the build
is registration 20, policy-library 39, synchronization 81
(71 baseline plus the 10 new), validation 18 - 158/158 -
plus the EAI handoff verification PASS 3/3, against the
pre-build baseline of 148/148 plus the same handoff PASS.
No earlier suite was modified. No `node_server` process
was left running after any run.

## How the mechanism works, in plain words

A delivery arrives carrying the anchor itself - the whole
anchor object: the head it pins, the count, who signed it,
its signature, and the kept history of every version
before it - and, when the record travels with it, the
record's entries in the same call. The server writes the
delivered anchor to a candidate file BESIDE its own anchor
and judges everything against candidates; its live files
move only at the end, and only together. Succession is
judged first among the identity questions, because it is
the cheapest and the most decisive: if the anchor the
server holds is signed and the delivered one names a
different signer, the delivery is refused and the refusal
is written into the audit log that lives beside the anchor
- the same log, the same event shape, that experiment
019's own refused re-anchors use - before the delivered
anchor's signature is ever looked at. Then the backward
fence: an anchor whose kept history is the current
anchor's history cut short is an earlier version of the
server's own anchor file arriving late, and an anchor
pinning a head the standing record has already grown past
is an anchor over an earlier state, and both refuse under
one name, `anchor_regression`, whether or not the
delivered anchor carries history at all. Then the
signature and the whole kept history are verified by
experiment 019's own code, under the server's configured
signer where it has one. Only then pairing: alone, the
anchor is consulted against the record the server stands
on by experiment 015's own consultation, and a head that
matches nothing the server holds is `anchor_unpaired`;
paired, a full `PolicyNode` is built over the two
candidate files under the server's own anchor
configuration, and the pair stands or refuses as one
thing, under the names that construction already speaks.
When both stand, the server's snapshot file and anchor
file are replaced from the verified candidates and the
serving view reloads once, the way experiment 024's live
reload lands a record delivery - so the revocation the
record carries is live over the wire in the same call,
with no restart and no hand at the machine.

## The rewrite-anchor finding, stated exactly as evidenced

The case required the rewrite question to be answered by a
test, not by a hope, and the test's third leg is the
answer. What stops a rewrite from being blessed over this
wire: the genuine anchor over the true chain (leg (i)
refuses `anchor_mismatch`), and the signer's identity
(leg (ii) refuses `different_signer` against a stranger
holding a genuine enrollment secret of his own). What
does NOT stop it: anything else. A deliverer holding the
CURRENT signer's secret - which, under experiment 019's
symmetric HMAC model, is every enrolled identity the
server trusts to verify, the deliverer here included,
legitimately - can extend the anchor's kept history over
a rewritten record by case 10's own write path, and the
resulting pair passes every judgement this experiment
built: succession (same identity), the backward fence
(the history extends, never shortens), signature and
history verification (the signature is genuine), and
pairing (the pair is internally true). The server accepts
it and serves the rewritten record. This is recorded as a
finding, not patched over: no check was invented that
pretends to distinguish a blessed rewrite from an honest
growth, because under this signature model there is none
to invent - the two are the same object. The wire adds no
new weakness: the identical pair handed to the server as
files would land identically, because the anchor lifecycle
on disk is the same code with the same model. The weakness
is the symmetric model itself, already recorded as
experiment 019's central limit (a secret that verifies can
also sign), and its closure is the one experiment 019
already named: a witnessed anchor, or an asymmetric
signature the verifier cannot mint. Until one of those
exists, the custody of the signer's secret IS the fence,
over the wire exactly as on disk - and case 17's third leg
is the standing proof of that sentence.

## What failed

The tests-first run failed as designed: before the
endpoint, helper, method, and health field existed, the
new suite errored ten times on exactly those absences -
eight on the `deliver_anchor` client helper, one on
`NetworkNode.deliver_anchor`, one on the missing
`last_anchor_delivery` health field - recorded verbatim in
`test-output.txt`. After the build, one test failed once,
on a test-side miscount and nothing else: the backward
test asserted the report's anchor sequences as 1 -> 0, but
its own class setup writes the anchor once before the test
body runs (sequence 0 over the two-entry chain), so the
earlier version the test saves is sequence 1 and the live
anchor sequence 2; the refusal itself - `anchor_regression`,
the audit event, the byte-for-byte checks - all fired as
designed. The assertions were corrected to the true
sequences (2 -> 1) with the setup's write named in a
comment, and the suite then passed 10/10. The first full
regression pass carried one error in experiment 017's
unmodified cert-binding suite; that pass's output was
captured through a summary filter, so the error's
traceback was not retained - a gap in this record, stated
as one. The failing suite reads its servers' ports through
the same `wait_ready` port-file helper whose read race
experiments 026 and 027 recorded under load, and the clean
re-run of the identical synchronization discovery
immediately after passed 81/81; both runs are recorded in
`test-output.txt`. No suite was modified to make any of
this pass.

## Honest limits (this is a prototype, not production)

- The anchor witness problem is untouched, and this
  experiment's finding is its sharpest statement yet: the
  anchor is a local file even when signed and even when
  delivered over the wire, its signature is experiment
  019's symmetric HMAC (a secret that verifies can also
  sign), nothing outside the server attests the anchor's
  head, and a signed anchor file can still be deleted or
  replaced wholesale outside any delivery - the wire
  judges what it is handed; it does not guard the file
  between deliveries. Deleting the anchor remains what
  experiment 019 said: locally indistinguishable from
  never having anchored. A witnessed or external anchor
  service, and asymmetric anchor signatures, remain the
  open work.
- Anchor delivery is driven, never solicited or scheduled:
  no timer, scheduler, gossip, or discovery inside the
  server asks anyone for an anchor, and a server nobody
  delivers to stays exactly as anchored as the last drive
  left it. This experiment adds the receiving surface a
  driver can use; it does not add the driver.
- There is no server-to-server anchor push: no
  `/push-anchor` endpoint exists, and experiment 027's
  `POST /push-snapshot` still transports the record only -
  a server can RECEIVE its anchor over the wire when a
  client (or a peer acting as a client of
  `/deliver-anchor`) drives one, but no server sends its
  own anchor onward, and a record push to a peer whose
  anchor has not advanced still refuses `anchor_stale` at
  that peer exactly as experiment 027 recorded.
- The mesh anchor does not travel: case 15's mesh anchor
  (experiment 026) still arrives out of band. This
  experiment delivers the REGISTRY anchor only; the mesh
  side's consultation inputs are unchanged.
- Succession has one soft edge, inherited from case 10
  and stated rather than widened: on a server with NO
  signer configured whose current anchor is head-only
  (unsigned, no kept history), succession has no signed
  current anchor to judge against, so a first SIGNED
  anchor over a head the server can check establishes its
  signer by landing - the unsigned lifecycle's standing
  from experiment 019, at its weakest exactly where
  experiment 019 said it was. Where the current anchor
  carries a kept history, succession binds even without a
  configured signer (row 7), and a tampered history still
  refuses `anchor_signature_invalid` (row 4).
- The delivery surface inherits the transport's
  authorization, no more: over plaintext (experiment 007
  mode) anyone who can reach the port can deliver an
  anchor, as they already could deliver a record, mutate,
  sync, and drive checks; the judgements above are what
  such a delivery must pass, not an identity check on the
  deliverer. Over mutual TLS with identity binding
  (experiments 014/017, row 9) only a credential-holding
  peer reaches the surface at all. There is no per-endpoint
  authorization beyond the transport, as with every surface
  since 007.
- The pairing judgement builds one full `PolicyNode` over
  candidate files per paired delivery, re-running the
  whole record's verification, as cases 13-16's deliveries
  do: correct at prototype scale, and the same whole-chain
  cost experiment 009 measured. Nothing here changes that
  cost profile, for or against.
- One machine, localhost, runtime-generated throwaway
  keys and certificates, hand-started processes: no real
  deployment, no PKI, no certificate revocation, no
  discovery beyond the addresses a driver is handed, no
  production cryptography. The anchor secrets in the tests
  are enrollment secrets held in test-local files; nothing
  about key custody in a real deployment is designed here.

## Process captured

Reusable process: an out-of-band lifecycle step becomes a
wire delivery by reusing the on-disk lifecycle's own code
as the wire's judge - parsing, succession audit, signature
and history verification, and pairing consultation are
experiment 012/015/019's functions called, not rewritten -
so the wire can never be more lenient than the disk, and
any future strengthening of the anchor model lands on both
at once. The paired judgement extends the pattern: judge
two artefacts as one transaction by building the existing
verifying construction over candidate files staged beside
the live ones, and commit by file replacement plus the
existing reload - neither half moves unless the whole
construction stands. The finding's process is worth as
much as the mechanism's: when a proposed check cannot
distinguish the attack from the honest case because the
underlying model makes them the same object, build the
test that shows the fence that DOES hold (custody), record
the acceptance as the result, and name the model change
that would close it - never invent a check that performs
safety it does not have. Opportunity Stack entry: the
anchor-delivery surface completes the wire lifecycle
record of the prototype (record, push, mesh anchor
consultation, anchor), leaving the witnessed anchor as the
single named gap between this prototype and a deployable
anchor story. What would differ next time: capture full
regression output unfiltered on the FIRST pass, so a
transient error's traceback is in the record rather than
described around.

## Next, per the build order

The anchor's transport is closed; its witness is the
remaining anchor gap, and it is a model change (asymmetric
signatures or an external witness), not another endpoint.
The build order otherwise continues to the items the
earlier experiments recorded: real multi-machine
deployment, certificate revocation and lifecycle, node
discovery beyond set addresses, unattended drivers for the
surfaces that are all still driven (checks, deliveries,
pushes, and now anchor deliveries), the Gate 5
production-scale timing benchmark, and availability case
3's deliberate-removal semantics, which remain blocked on
the scope decision recorded in experiment 011.