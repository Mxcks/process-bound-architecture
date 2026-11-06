# 08. Outcomes That Unlock the Next Build

Plain rule: a layer unlocks the next build only when its outcome is
**observed in a test**, not when its spec is written.

Per `AUTHORIZATION.md` (2026-11-06), these gates are Maren's own internal
build order for the entire architecture, which Max authorized as a whole.
They are NOT per-step approvals to ask Max for. Max authorized the whole
architecture; Maren works the gates herself and reports results.

## Gate 0 - Workspace exists (DONE 2026-11-06)
Outcome: this clean monorepo is public, holds every layer, its tests, its
instructions, and an idea dump lane.
Unlocks: Gate 1 spec and prototype work.

## Gate 1 - Registration works (THE blocker)
Outcomes, all from `tests/test-cases/registration.md`:
1. An authenticated registration channel exists *before* any user needs it.
2. Any user can register a policy/process library they authored.
3. A bad signature is rejected, and the rejection is logged.
4. Unregistered bytecode is blocked at the execution boundary, every time.
5. A good registration writes one deterministic, tamper-evident record
   (policy hash, inputs, identity context) that anyone can verify later.
6. A validation channel is bound on registration, and only on registration.
Unlocks: **the validation layer build.** Nothing in validation is built
or claimed before all six are observed.

## Gate 2 - Policy has no central home, in practice
Outcomes:
1. One registered policy is served from a second, independent node with
   the central registration point offline at runtime.
2. A user's own computer can act as that policy source, by their choice.
Unlocks: multi-node runtime work.

## Gate 3 - Synchronization mesh converges
Outcomes, from `tests/test-cases/sync-mesh.md`:
1. Concurrent mutations across nodes converge with no central lock.
2. A partitioned node rejoins and converges, or rollback fires.
3. Split-brain / divergence is detected and triggers automated rollback.
Unlocks: any claim that state is safe across nodes.

## Gate 4 - A policy survives its host going offline
Outcomes, from `tests/test-cases/policy-availability.md`:
1. Today, honestly: sole-host offline = policy drops. That failure is recorded.
2. Unlock outcome: the same test passes because the policy was replicated
   to a floor of independent registered nodes at registration time.
Unlocks: production hosting of other people's policies. Before this gate,
hosting is experiment-only.

## Gate 5 - Verification gate is real
Outcomes:
1. A canary harness measures lineage time per transformation under load.
2. A forced Byzantine / divergence fault halts rollout and rolls back,
   observed, not assumed.
Unlocks: any production claim. The sub-millisecond number is a benchmark
to measure at this gate, not a promise before it.

## The build order, in one line
Gate 1 first, then 2 to 5 in order, all under the standing authorization
in `AUTHORIZATION.md`. Maren proceeds gate to gate without asking.
