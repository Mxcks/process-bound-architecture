# 002. Gate 1 Registration Prototype

Date: 2026-11-06
Authorization: `AUTHORIZATION.md` (Max authorized the entire architecture;
Gate 1 is Maren's internal build order, gate 1 in
`docs/architecture/08-outcomes-that-unlock-the-next-build.md`).
Code: `packages/registration/` (`registration.py`, `test_registration.py`, `demo.py`).
Test cases: `tests/test-cases/registration.md`.
Raw output: `test-output.txt` in this folder.

## Question

Can the six Gate 1 outcomes be observed in a small, runnable prototype,
before any validation layer is built (per experiment 001: no validation
before registration exists)?

## Result: all six outcomes observed, 6 of 6 automated tests pass

Run on Python 3.12.3, standard library only:

```
Ran 6 tests in 0.012s
OK
```

| # | Gate 1 outcome | Test | Result |
|---|---|---|---|
| 1 | Authenticated registration channel exists before use | `test_channel_exists_before_use` | PASS: channel constructs its registry/audit logs and answers `is_ready()` with zero users enrolled; an unenrolled author's attempt is refused and audit-logged as `unknown_author`; the empty chain verifies. |
| 2 | Any user can register a policy library they authored | `test_any_user_can_register_own_library` | PASS: two different users (alice, bob) each enroll, sign, register, and are admitted at the boundary. |
| 3 | Bad signature rejected, rejection logged | `test_bad_signature_rejected_and_logged` | PASS: a one-character-altered signature raises `RegistrationRejected`, exactly one `rejected / bad_signature` audit event is written, and nothing is registered. |
| 4 | Unregistered code blocked at the execution boundary | `test_unregistered_blocked_at_execution_boundary` | PASS: a never-registered policy hash is blocked; a registered hash presented with tampered code is also blocked. |
| 5 | One deterministic, tamper-evident record, verifiable later | `test_one_deterministic_tamper_evident_record` | PASS: registering the same library twice returns the identical record, writes exactly one hash-chained log line; a fresh channel reloaded from the log alone verifies the chain and returns the same record; editing the stored entry's name makes chain verification return False. |
| 6 | Validation channel bound only on registration | `test_validation_channel_bound_only_on_registration` | PASS: no channel before registration, no channel after a rejected attempt, a deterministic `val-...` channel after a good registration, identical when reloaded from the log. |

The demo (`python3 packages/registration/demo.py`) also runs end to end
and prints all six outcomes as True (see `test-output.txt`).

All five cases in `tests/test-cases/registration.md` are covered:
unregistered blocked (case 1, outcome 4), bad signature rejected and
logged (case 2, outcome 3), correctly signed library from any user
registers and becomes loadable with its policy hash in the record
(case 3, outcome 2), duplicate registration yields one deterministic
record (case 4, outcome 5), validation channel bound on registration
and on nothing else (case 5, outcome 6).

## What failed

Nothing in the test run. There were no failing tests to fix; the suite
passed on its first full run and on both invocation styles
(`unittest discover` and direct file run).

## Honest limits (this is a prototype, not production crypto)

- Signatures are HMAC-SHA256 with a shared secret issued by the channel
  itself at enrollment. Symmetric: the channel can forge any user's
  signature, enrollment has no identity proofing, and secrets live in
  process memory only (a restart loses them; the on-disk chain stays
  verifiable, but nobody can sign new registrations for existing users
  until they re-enroll). Production needs asymmetric keys (e.g.
  Ed25519), identity proofing, and protected key storage.
- The hash-chained JSON-lines log is tamper-*evident*, not
  tamper-*proof*: anyone with file write access can rewrite the whole
  chain from scratch and verification would pass against the rewritten
  file. Detecting that requires an external anchor (a witnessed head
  hash, replication, a real ledger), which is later-gate work. There is
  no consensus and no sync mesh here.
- The execution boundary admits or blocks; it does not sandbox or run
  policy code. Safe execution belongs to the runtime package.
- No server, no network, no concurrency beyond single-process use, no
  key rotation, no revocation. Revocation in particular is unmodeled:
  once registered, a record currently stands forever in this prototype.
- "Sub-millisecond" and other performance numbers from the source
  proposals were not measured and are not claimed. The suite runs in
  about 0.01s locally, which says nothing about production load.

## Process captured

Reusable process: one automated test per gate outcome, named for the
outcome, plus a demo that prints the same outcomes for a human reader;
the experiment record quotes the test run verbatim (`test-output.txt`)
instead of summarizing it. Opportunity Stack entry: "gate-outcome test
suite" (new, from this experiment). What would differ next time: add a
revocation case to `tests/test-cases/registration.md` before building
Gate 2, because this prototype exposed that registration currently has
no way to be undone.

## Next, per the build order

Gate 1 passing is the signal to proceed to Gate 2 (policy served from a
second, independent node with the registration point offline at
runtime), under the standing authorization. Not done in this experiment.
