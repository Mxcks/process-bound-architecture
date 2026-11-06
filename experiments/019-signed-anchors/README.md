# 019. Signed anchors with a kept history

Date: 2026-11-06
Authorization: Max's standing authorization for the entire
architecture 2026-11-06; the gates are Maren's internal build order in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`.
Code: `packages/registration/anchor.py` and
`packages/synchronization/anchor.py` (both gain signing, the
same-identity re-anchor rule, the kept history, and the anchor
audit log), `packages/policy-library/policy_node.py` (new
`AnchorSignatureInvalid` refusal; `PolicyNode` gains
`anchor_signer` / `anchor_secret_hex`),
`packages/synchronization/anchored_sync.py` (`AnchoredSyncNode`
gains the same identity arguments and the
`anchor_signature_invalid` divergence reason),
`packages/synchronization/node_server.py` (`NetworkNode` gains
the identity, passed to its PolicyNode; new `--anchor-signer` /
`--anchor-secret-file` arguments), and
`packages/validation/gate5_harness.py` (`run_gate` gains
per-chain signing-identity arguments, passed to the same probes
experiment 018 built). Signing reuses Gate 1's machinery
exactly: the signer's enrollment secret, HMAC-SHA256, a
domain-separated canonical encoding in the style of
`sign_revocation` - no new crypto, standard library only.
Secrets are generated inside the tests at runtime (real Gate 1
enrollments); no secret, key, or enrollment material exists in
this repository. No earlier test was modified, and with no
signing identity configured every earlier behavior is unchanged
(asserted by new tests and by the unmodified earlier suites
passing in the same run). This experiment does not touch
`proposals/PBA-FIX-001.md` or implement any part of it, and it
does not work on deliberate removal (availability case 3,
experiment 011's blocked item).
Test cases: `tests/test-cases/registration.md`, case 10, and
`tests/test-cases/sync-mesh.md`, case 11, both added to those
files before the code was built, in the detailed format of
policy-availability case 4. The new tests were run against the
unbuilt code first (import errors for the signing/history/audit
names and for `AnchorSignatureInvalid`, and `write_anchor() got
an unexpected keyword argument 'signer'`), so the cases are
proven to be what the code had to satisfy; that failing output
is recorded verbatim at the top of `test-output.txt`.
Raw output: `test-output.txt` in this folder (the tests-first
failure, the pre-build baseline, the full regression across all
four tested packages, all five new suites by direct file run,
the handoff check, the new demo, and the leftover-process
check).

## The re-anchor rule, stated plainly

Re-anchoring a SIGNED anchor requires a valid signature from
the SAME identity that signed the current anchor. An attempt
by a different enrolled identity - signing correctly as
themselves, with their own secret - is refused, the anchor
file is left unchanged, and the refusal is appended to an
anchor audit log beside the anchor file
(`<anchor>.audit.jsonl`, Gate 1's audit style). A re-anchor
over a signed anchor whose existing signature or history does
not verify under the presented secret is refused the same way
and logged with reason `existing_signature_invalid`. One
stated exception: a first signed write over an unsigned anchor
(or over no anchor) establishes the signer and starts the
history, because an unsigned anchor has no signer to match.
The rule is expressible in the existing code as-is (the current
anchor records its signer, and attempts present a signer plus
a secret), so no governance question had to be decided by this
build; WHO should be an anchor signer in the first place
remains an operator choice this prototype does not make.

## Question

Experiments 012, 015, and 018 each recorded the same two
limits: the external anchor is unsigned and unwitnessed, so
whoever can write the anchor file can re-anchor a rewrite and
pass every consultation built since; and re-anchoring
overwrites, so there is no anchor history to audit. Can the
prototype now close both, at prototype scale: an anchor signed
at write time by an enrolled identity, its signature verified
at every consultation point that exists today when a signing
identity is configured (a missing, wrong, or tampered
signature failing under a name distinct from anchor_mismatch
and anchor_stale), re-anchoring bound to the signer's identity
with a kept, verifiable history - while unsigned anchors from
experiments 012-018 keep working exactly as before?

## Result: all case 10/11 outcomes observed, 22 new tests pass, full regression green

Run on Python 3.12.3, standard library only:

```
packages/registration     Ran 20 tests  OK   (6 Gate 1 + 4 revocation + 4 anchor + 6 new signed)
packages/policy-library   Ran 24 tests  OK   (3 Gate 2 + 3 Gate 4 + 3 revocation serving + 3 self-healing + 5 anchor consultation + 4 liveness + 3 new signed consultation)
packages/synchronization  Ran 37 tests  OK   (3 Gate 3 + 4 network + 2 anchor + 4 anchor consultation + 3 coordinated rollback + 4 protected + 4 cert binding + 4 anchor in servers + 6 new signed mesh + 3 new signed in servers)
packages/validation       Ran 18 tests  OK   (4 Gate 5 + 5 scale + 5 anchor evidence + 4 new signed evidence)
scripts/verify-handoffs.py  PASS: 3 EAI vectors recompute exactly
```

99 tests total (baseline before this experiment was 77), every
suite on `unittest discover`, all five new suites also by
direct file run, the new demo run end to end, and a post-run
process check confirmed zero leftover `node_server` processes.
All of it is quoted verbatim in `test-output.txt`.

| # | Case 10/11 outcome | Test | Result |
|---|---|---|---|
| 1 | Signed registry anchor: records signer and signature, the genuine registry verifies with the signature checked, and the kept history starts at sequence 0 naming head, count, and signer | `test_signed_anchor_records_signer_and_verifies_with_history` (registration) | PASS: check `anchored` with `signature_checked` and `signature_valid` both true; history verifies with and without the secret. |
| 2 | Missing signature (unsigned anchor consulted with an identity), wrong secret, wrong claimed signer, tampered signature, and a head edited after signing are each `anchor_signature_invalid` - never valid, never a mismatch | `test_missing_wrong_and_tampered_signature_is_signature_invalid` (registration) | PASS: all five shapes fail with that status; the head-edit case is asserted not `anchor_mismatch`. |
| 3 | Same-identity re-anchor after honest growth appends to the kept history (sequences 0 and 1, both the signer), the history verifies, and the grown registry is anchored again | `test_reanchor_same_identity_appends_to_kept_history` (registration) | PASS. |
| 4 | Different-identity re-anchor refused and logged: bob, enrolled and signing correctly as himself, is refused; the anchor file is byte-for-byte unchanged; the anchor audit log records `reanchor_rejected` / `different_signer` naming both identities; the same-identity re-anchor still works after | `test_reanchor_different_identity_refused_and_logged` (registration) | PASS. |
| 5 | Tampered history entry caught: editing an old entry's head breaks history verification (chain and signature), and an identity-configured check treats it as `anchor_signature_invalid` | `test_tampered_history_entry_is_caught` (registration) | PASS. |
| 6 | Backward compatibility at the check: unsigned anchors behave exactly as experiment 012 (including the overwrite re-anchor lifecycle), and a signed anchor checked with no identity is judged by its head | `test_unsigned_anchors_unchanged_when_no_identity_configured` (registration) | PASS: `signature_checked` false, statuses exactly the pre-019 ones. |
| 7 | Gate 2: an identity-configured PolicyNode serves a correctly signed anchor (status anchored, signature verified in the check) | `test_signed_anchor_with_identity_serves` (policy-library) | PASS. |
| 8 | Gate 2: a tampered signature and a missing signature each refuse the load as `AnchorSignatureInvalid`, named `anchor_signature_invalid`, not mismatch and not stale | `test_tampered_and_missing_signature_refused_at_load` (policy-library) | PASS. |
| 9 | Gate 2 backward compatibility: a node with the anchor but no signing identity judges the signed anchor by its head exactly as experiment 015 built | `test_no_identity_configured_judges_signed_anchor_by_head` (policy-library) | PASS. |
| 10 | Signed mesh anchor: records signer, a converged node verifies with the signature checked, kept history starts at sequence 0 with heads, state, and signer | `test_signed_mesh_anchor_verifies_and_history_starts` (synchronization) | PASS. |
| 11 | Mesh: missing and tampered signatures are `anchor_signature_invalid` when an identity is configured | `test_missing_and_tampered_signature_is_signature_invalid` (synchronization) | PASS. |
| 12 | Mesh re-anchor rule: same identity appends history; different identity refused, logged in the anchor audit log, anchor unchanged | `test_reanchor_same_identity_appends_different_refused_logged` (synchronization) | PASS. |
| 13 | Mesh kept history: an edited old history entry is caught by history verification and by the identity-configured check | `test_tampered_history_entry_is_caught` (synchronization) | PASS. |
| 14 | Gate 3 consultation: an identity-configured AnchoredSyncNode accepts a genuine peer exactly as in case 8; against a tampered-signature anchor it refuses the merge as a divergence named `anchor_signature_invalid` (rolled back, nothing accepted, `anchor_status()` reports the same name) | `test_anchored_sync_node_verifies_signature` (synchronization) | PASS. |
| 15 | Gate 3 backward compatibility: without an identity, a signed mesh anchor is judged by its heads exactly as in cases 5/8 | `test_no_identity_configured_judges_signed_anchor_by_heads` (synchronization) | PASS. |
| 16 | Server startup: a server started with `--anchor-signer` / `--anchor-secret-file` over a genuine snapshot with a correctly signed anchor starts and serves, health reporting anchored | `test_signed_anchor_server_starts_and_serves` (synchronization) | PASS: health `anchor_configured True`, `anchor_status == "anchored"`, policy served with code matching the Gate 1 record. |
| 17 | Server startup: the same server over an anchor whose signature was tampered with after signing refuses to start, its log naming `AnchorSignatureInvalid` / `anchor_signature_invalid` and not `AnchorMismatch` | `test_tampered_signature_server_refuses_to_start` (synchronization) | PASS: process exits nonzero on its own. |
| 18 | Server backward compatibility: the same signed anchor with no signing identity on the command behaves exactly as experiment 018 | `test_signed_anchor_without_identity_behaves_as_018` (synchronization) | PASS. |
| 19 | Gate 5 verdict: genuine chains with correctly signed anchors and their signing identities keep the verdict passing, both anchor checks additional and passing as anchored | `test_genuine_signed_anchors_keep_verdict_passing` (validation) | PASS: verdict passed, all checks ok, no `anchor_signature_invalid` in any anchor detail. |
| 20 | Gate 5 verdict, registry: a tampered registry anchor signature fails the verdict, the registry anchor check is the ONLY failing check, named `anchor_signature_invalid` | `test_tampered_registry_signature_fails_verdict_on_signature_only` (validation) | PASS: failing list is exactly `["anchor_evidence_registry"]`; the mesh anchor check still passes. |
| 21 | Gate 5 verdict, mesh: a tampered mesh anchor signature fails the verdict under the same name, for the mesh check only | `test_tampered_mesh_signature_fails_verdict_on_signature_only` (validation) | PASS: failing list is exactly `["anchor_evidence_mesh"]`. |
| 22 | Gate 5 backward compatibility: the same signed anchors with no signing identity passed are judged exactly as experiment 018 and the verdict passes | `test_signed_anchors_without_identity_behave_as_018` (validation) | PASS. |

The demo also runs end to end (see `test-output.txt`):
`demo_signed_anchor.py` shows the signing, the
signature-checked verification, the history growing from 1 to
2 entries on the same-identity re-anchor, bob's refused and
logged attempt with alice's anchor untouched, and the tampered
signature reported as `anchor_signature_invalid`.

## How the mechanism works, in plain words

Signing: `write_anchor(..., signer=..., secret_hex=...)` (and
`write_mesh_anchor` the same) signs the anchor with Gate 1's
scheme - HMAC-SHA256 under the signer's enrollment secret over
a canonical encoding with its own prefix
(`PBA-ANCHOR-SIGN-v1`), domain-separated from library and
revocation signatures exactly as `sign_revocation` is separated
from `sign_library`. The canonical bytes cover the head (or,
for the mesh, the per-origin heads and state hash), the count,
the signer, the history sequence, and the previous history
entry's hash, so a signature binds one head, by one identity,
at one point in the anchor's own history.

History: a signed anchor file carries its history inside
itself. Each entry is built like a registry entry - a body
(head, count, signer, sequence, prev-hash, signature) hashed
into an `entry_hash` that links it to the previous entry - so
verifying the history is the same recomputation the repo
already trusts for its other records, plus a signature check
per entry when a secret is presented. The anchor's top-level
fields are required to BE the history's last entry; an anchor
whose top and history disagree does not verify.

Consultation: the identity is opt-in per consultation point,
in the same places the anchor path already is - a PolicyNode's
constructor, an AnchoredSyncNode's constructor, the server's
command line (`--anchor-signer` plus `--anchor-secret-file`, a
file so the secret does not ride in the process command line),
and `run_gate`'s keyword arguments. Where an identity is
configured, the check first verifies the signature and history
and only then judges the head, because a head edited after
signing is a signature failure, not a head finding; the failure
travels under one name everywhere -
`anchor_signature_invalid` as a check status, an
`AnchorSignatureInvalid` exception at Gate 2 and at server
startup, a divergence reason at Gate 3, and the failing check's
detail in the Gate 5 verdict. Where no identity is configured,
nothing about the judgement changes: the anchor is read by its
head exactly as experiments 012-018 built, signed or not.

## What failed

The tests-first run failed as designed (recorded at the top of
`test-output.txt`). Three real failures during the build, each
worth recording:

- The first full discovery run FAILED in the synchronization
  package with one loader error: the new mesh suite was named
  `test_signed_anchor.py`, the same filename as the new
  registration suite, and in the shared discovery process a
  plain `__import__('test_signed_anchor')` resolved to the
  registration package's file (an earlier suite had put that
  directory at the front of `sys.path`), which then failed
  against the synchronization `anchor` module already cached.
  This is experiment 018's same-named-module lesson one level
  up: test filenames are module names too, and across packages
  they share one namespace in a discovery process. The suite is
  renamed `test_signed_mesh_anchor.py`; the discovery run is
  green with that fix, and the direct file run had passed
  throughout, which is exactly why the discovery run is the
  evidence that matters.
- The registration suite's first run failed in one test for a
  reason that turned out to be the new rule working: after
  tampering with a signed anchor's signature in the file, the
  test tried to write a fresh signed anchor over it to set up
  its next case, and the write was refused because the existing
  signature does not verify - re-anchoring over a tampered
  anchor is exactly what the rule refuses. The test now removes
  the tampered file first, and says why in a comment.
- An in-pipeline leftover-process count read 2 because the
  combined command's own command line contained the plain
  string `node_server` (the known failure of the `ps` bracket
  trick when the target string also appears elsewhere in one's
  own command); the standalone check confirms 0 leftover
  processes, and `test-output.txt` records both readings.

After those fixes, nothing failed: all five new suites, the
full regression across all four tested packages, the handoff
check, and the demo passed on the runs quoted in
`test-output.txt`, and no earlier test was modified.

## Honest limits (this is a prototype, not production)

- The anchor is still NOT WITNESSED. Signing binds an anchor to
  one enrolled identity; nothing countersigns it, no witness
  set or public append-only log holds it, and no other party
  attests that this head was the head at a given time.
  Production still needs the head held where the record's
  writer cannot reach it.
- Signing inherits Gate 1's HMAC limit in full: the signature
  is symmetric, verification REQUIRES the signer's secret, so
  every verifier configured with an identity holds a secret
  that could also forge that signer's anchors - and the
  registration channel, which issued the secrets, could forge
  any of them. There is no asymmetric key, no identity proofing
  of who should be a signer, and no key storage beyond the
  server's secret file read at startup.
- A signed anchor file can be DELETED or ROLLED BACK: replacing
  the current anchor with an older, genuinely signed anchor
  (and its shorter history) verifies as a signature - it then
  fails as a head mismatch against any chain that has moved on,
  but against a chain still AT that older head it is
  indistinguishable from the current anchor. The kept history
  is tamper-evident going forward, not rollback-proof.
- Signature consultation is per-point and opt-in, like the
  anchor itself: a node, server, or gate run configured with an
  anchor but no signing identity judges the anchor by its head
  alone, under which a re-anchored rewrite signed by anyone -
  or an unsigned re-anchor - passes exactly as in experiments
  012-018. Nothing here makes a signing identity mandatory,
  distributes one, or notices a consultation point that should
  have one and does not.
- The same-identity rule governs re-anchoring, not succession:
  there is no signer rotation, no transfer of anchor authority,
  and no recovery when a signer's secret is lost - a lost
  secret means that anchor can never be legitimately
  re-anchored, only replaced by a first signed write over a
  deleted or unsigned anchor, which the rule permits and which
  is therefore also an operator-level attack the prototype
  cannot distinguish from recovery. Who may establish a signer
  in the first place is ungoverned here, as case 10 states.
- The kept history lives inside the anchor file it audits, on
  the same stand-in terms as the anchor itself: one local file,
  one machine, writable by the same operator. Its chain makes
  edits detectable in the same way the registry's does, and
  inherits the same limit - detectable, not impossible.
- Inherited limits stand unchanged: localhost processes on one
  machine, no Byzantine consensus or quorum, the server's mesh
  side still consults no mesh anchor (its `--anchor` family
  covers the registry snapshot, now including its signature),
  no anchor lifecycle automation or distribution, and no
  performance, scale, or latency numbers were measured or are
  claimed for signing or history verification.
- Deliberate removal with notification (availability case 3,
  experiment 011's blocked item) remains unbuilt and untouched.

## Process captured

Reusable process: closing a "the outside record is forgeable"
gap takes three separate proofs, and each needs its own
negative control at the surface that consumes it - the
signature scheme itself (tamper the signature, the head, and
the history), the WRITE path's rule (a different identity,
signing correctly as themselves, refused and logged, anchor
byte-for-byte unchanged), and every CONSULTATION point (the
same tampered file refused under the same new name at serving,
at sync, at process startup, and in the verdict), plus the
backward-compatibility proof that an unconfigured point cannot
tell a signed anchor from an unsigned one. Second lesson, the
third occurrence of the same root cause (015: dataclass module
registration; 018: shared import name bet; 019: shared TEST
filename bet): in this repo any name that enters a shared
process - module, package file, or test file - is a global
name, and uniqueness has to be checked across packages, not
within one. Opportunity Stack entry: extends the
outside-comparison clause with a signature clause (an outside
fact earns its standing from WHO signed it under a rule for who
may re-sign it, and its past states stay readable as a
verifiable history; a check that can be silently overwritten
by whoever it judges is a witness stand the defendant owns).
What would differ next time: when adding suites to several
packages in one experiment, run ONE cross-package filename
uniqueness check before the first test run - it is one command
and it is exactly the failure the full discovery run later
spent its longest cycle finding.

## Next, per the build order

The anchor gaps experiments 012, 015, and 018 recorded are now
closed at prototype scale: signed anchors, the same-identity
re-anchor rule, and a kept verifiable history, consulted at
every surface the anchor itself is consulted. The honest
distance to utilization is unchanged in kind and shorter by
that item: deliberate-removal notification (availability case
3, blocked on Max's answers in experiment 011), a WITNESSED
anchor for production (countersignature or an append-only log,
plus signer succession and rollback protection), mesh-anchor
consultation inside the networked processes' sync (their mesh
side is still a plain SyncNode), liveness in the server
processes (016's watcher is in-process), discovery and
automatic sync, persistence and restart recovery, production
crypto and identity proofing, consensus, revocation delivery
to placed policies, and Gate 5's open production benchmark.
`PBA-FIX-001` remains talk-first and untouched by this
experiment. Nothing here is deployed beyond the localhost test
processes the tests themselves spawned and cleaned up, and
nothing in this experiment changes that.
