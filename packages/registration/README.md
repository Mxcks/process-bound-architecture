# packages/registration

The blocker package: authenticated channels, signature checks, tamper-evident registration record, validation-channel binding.

Status: **Gate 1 prototype built and passing** (2026-11-06, experiment
`experiments/002-gate1-registration-prototype/`). Prototype only, not
production. See "Honest limits" below and the module docstring in
`registration.py`, which is the authoritative list.

## What is here

| File | What it is |
|---|---|
| `registration.py` | The prototype: `RegistrationChannel`, `ExecutionBoundary`, signing and hash-chain verification. Python standard library only. |
| `test_registration.py` | Automated tests, one per Gate 1 outcome, also covering all five cases in `tests/test-cases/registration.md`. |
| `demo.py` | Runnable walkthrough of all six outcomes. |

## How to run it

From the repo root:

```sh
# Tests (either command works)
python3 -m unittest discover -s packages/registration -v
python3 packages/registration/test_registration.py

# Demo (creates its registry/audit logs in a temp dir and prints the path)
python3 packages/registration/demo.py
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
   if the policy is registered and the presented code hashes to the
   registered code hash. Everything else raises `BlockedUnregistered`
   (outcome 4).

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
