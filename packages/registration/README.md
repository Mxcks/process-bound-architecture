# packages/registration

The blocker package: authenticated channels, signature checks, tamper-evident registration record, validation-channel binding.

Status: **Gate 1 prototype built and passing** (2026-11-06, experiment
`experiments/002-gate1-registration-prototype/`), and **revocation
built and passing** on top of it (2026-11-06, experiment
`experiments/008-revocation/`; test case 6 in
`tests/test-cases/registration.md`). Prototype only, not production.
See "Honest limits" below and the module docstring in
`registration.py`, which is the authoritative list.

## What is here

| File | What it is |
|---|---|
| `registration.py` | The prototype: `RegistrationChannel`, `ExecutionBoundary`, signing and hash-chain verification, and revocation (`revoke`, `sign_revocation`). Python standard library only. |
| `test_registration.py` | Automated tests, one per Gate 1 outcome, also covering cases 1-5 in `tests/test-cases/registration.md`. |
| `demo.py` | Runnable walkthrough of all six Gate 1 outcomes. |
| `test_revocation.py` | Automated tests for case 6: author-only revocation, boundary and validation-channel refusal, new chain entry with the original untouched, non-author and bad-signature rejections. |
| `demo_revocation.py` | Runnable walkthrough of revocation, including the refused non-author attempt. |

## How to run it

From the repo root:

```sh
# Tests (either command works)
python3 -m unittest discover -s packages/registration -v
python3 packages/registration/test_registration.py

# Demo (creates its registry/audit logs in a temp dir and prints the path)
python3 packages/registration/demo.py

# Revocation tests and demo (case 6)
python3 packages/registration/test_revocation.py
python3 packages/registration/demo_revocation.py
```

Requires Python 3.10+. No dependencies, no network, nothing is deployed.

## How it works, in plain words

1. Construct a `RegistrationChannel` first. It creates the registry log
   and audit log and loads any existing hash chain. The channel existing
   and answering `is_ready()` before any user shows up is outcome 1.
2. `enroll_user(user_id)` provisions an identity and returns a secret
   once. Any user can enroll (outcome 2 starts here).
3. An author signs their library with `sign_library(secret, ...)` and
   calls `register(...)`. A wrong signature is rejected and the rejection
   is written to the audit log (outcome 3).
4. A successful first registration appends exactly one entry to an
   append-only, hash-chained registry log (outcome 5). Registering the
   same library again returns the same record and writes nothing new.
   `verify_registry_chain()` recomputes the chain from the file alone,
   so anyone holding the log can verify it later, with no secrets.
5. That successful registration, and only that, binds a deterministic
   validation channel id (`val-...`) to the policy (outcome 6).
6. `ExecutionBoundary.admit(policy_hash, code)` returns the record only
   if the policy is registered, not revoked, and the presented code
   hashes to the registered code hash. Everything else raises
   `BlockedUnregistered` (outcome 4).

## Revocation, in plain words (case 6)

7. The author of a policy can take it back: `revoke(author,
   policy_hash, signature)` with a revocation signature made by
   `sign_revocation(secret, policy_hash)` under the author's own
   enrollment secret. Only the author: an enrolled non-author signing
   correctly as themselves is rejected as `not_author`, an unenrolled
   caller as `unknown_author`, a wrong signature as `bad_signature`,
   and every rejection is written to the audit log. Nothing is
   appended to the registry chain on a failed attempt.
8. A successful revocation appends one NEW entry (`event: "revoked"`)
   to the same hash chain. The original registration entry is never
   deleted or rewritten, the chain still verifies end to end, and
   revoking twice returns the same revocation record without writing
   another entry. The historical record stays readable via
   `get_record(...)`; `is_revoked(...)` and `get_revocation(...)`
   report the policy's current standing.
9. From that moment, in this channel's view, the policy is dead:
   `admit(...)` raises `BlockedRevoked` (a `BlockedUnregistered`
   subclass) even for the exact registered code, and
   `validation_channel_for(...)` returns None, so the validation
   channel is no longer honored.
10. Taking it back everywhere else is a delivery question, not a
    channel question: nodes serving the policy from their own
    snapshot copies stop when the updated record reaches them. See
    "the stale window" in `packages/policy-library/README.md`.

## Honest limits

- "Signatures" are HMAC-SHA256 with a shared secret the channel itself
  issues. Symmetric crypto: the channel could forge a signature, secrets
  live in memory only, and identity proofing at enrollment is out of
  scope. Production needs asymmetric keys and protected key storage.
- The hash chain makes log edits *detectable*, not impossible. It is a
  single local file: no consensus, no replication, no blockchain, no
  sync mesh (those are later gates).
- The execution boundary admits or blocks based on registration and code
  hash. It does not sandbox or execute arbitrary code; that is the
  runtime package's job.
- Single-process, single-threaded assumptions; no server, no HTTP, no
  persistence for enrollment secrets.
- Revocation inherits the signing limit above (the channel issues the
  secrets, so the channel itself could forge a revocation). It is
  also final: there is no un-revoke entry type, and re-registering
  the same library (same author, name, version, and code, so the
  same policy hash) returns the existing record and does not
  reinstate it; a new version is a new policy hash and a separate
  registration. And revocation is recorded, not broadcast: nobody is
  notified, and a node holding a pre-revocation snapshot keeps
  serving until the updated record is delivered to it (the stale
  window, named in case 6).
