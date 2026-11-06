# packages/policy-library

Authoring, signing, versioning, and serving of policy libraries as polymorphic payloads.

Status: **Gate 2 prototype built and passing** (2026-11-06, experiment
`experiments/003-gate2-decentralized-policy/`). Prototype only, not
production. The module docstring in `policy_node.py` is the
authoritative list of honest limits.

Authoring, signing, and registering a library is Gate 1's job
(`packages/registration/`). This package is the other half: once a
policy is registered, it does not need the registration channel, or any
central home, ever again in order to be served.

## What is here

| File | What it is |
|---|---|
| `policy_node.py` | The prototype: `PolicyNode`, standalone snapshot verification. Python standard library only. Builds on Gate 1's registration record format; does not modify or re-implement Gate 1. |
| `test_policy_node.py` | Automated tests, one per Gate 2 outcome, plus the verification refusals. |
| `demo.py` | Runnable walkthrough of both Gate 2 outcomes. |

## How to run it

From the repo root:

```sh
# Tests (either command works)
python3 -m unittest discover -s packages/policy-library -v
python3 packages/policy-library/test_policy_node.py

# Demo (registers a policy, copies it to a second node, takes the
# central channel offline, and serves from the node)
python3 packages/policy-library/demo.py
```

Requires Python 3.10+. No dependencies, no network, nothing is deployed.

## How it works, in plain words

1. A policy is registered through Gate 1. That writes one hash-chained
   entry into the registry log: that entry is the *registration record*.
2. A node that wants to serve policies holds two things of its own: a
   **copy** of that registry log (its snapshot) and the **policy payload
   bytes**. How the copies arrive is Gate 3's problem (sync); this
   prototype copies files explicitly in the tests/demo.
3. The node verifies for itself, contacting nothing: it recomputes the
   snapshot's hash chain, checks the payload it holds hashes to the
   registered `code_hash`, and checks the `policy_hash` recomputes from
   (author, name, version, payload). Any mismatch is a refusal, never a
   serve.
4. Serving is **opt-in**. `PolicyNode(..., serve_policies=True)`, or the
   operator's own config file via `PolicyNode.from_config(...)` with
   `{"serve_policies": true}`. The default is False: a node that did
   not opt in can hold a verified policy and still will not serve it.
   That flag is how a user's own computer becomes a policy source *by
   their choice* (Gate 2, outcome 2).

## Honest limits

- "Second node" is a second `PolicyNode` over copied files on one
  machine. No network, no peer discovery, no replication protocol, no
  scale claim. Moving copies automatically is the synchronization
  layer's work (Gate 3).
- Snapshot trust inherits Gate 1's limit: the chain detects a single
  edited entry, but a whole-file rewrite from genesis would verify.
  An external anchor for the chain head is still unbuilt.
- No revocation. Once registered, a record stands in this prototype,
  and a node serving from an older snapshot cannot learn that a policy
  was later revoked. The revocation test case is written
  (`tests/test-cases/registration.md`, case 6, added with this gate);
  revocation itself is not built.
- The node serves verified bytes; it does not sandbox or execute
  policy code (runtime package's job).
