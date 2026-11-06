# Process-Bound Architecture (PBA)

Identity, policy, and classification bound to **execution**, not storage.

Data stays pure at rest. When data moves through a process, that process gives it
its identity, its security rules, and its routing in that moment.

> Status: open research monorepo. Ideas, specs, tests, and experiments land here
> first. Nothing in this repo is production until its verification gate passes.

## The whole chain, in plain words

1. **Data is pure at rest.** No metadata tags, no ACL bloat on the payload.
2. **Execution gives identity.** Execution-Attached Identity (EAI) =
   `Hash(ProcessID, PipelineStage, CryptographicEpoch, IngressVector)`.
3. **Policy comes from policy libraries.** A policy is itself a polymorphic
   payload. Anyone can author a policy/process, turn it into a library, and
   register it.
4. **Registration is the blocker, and it comes first.** There is one secure,
   centralized registration process (authenticated channels, signature checks,
   a tamper-evident / blockchain-style ledger) whose whole job is to
   decentralize policy after verification. Centralize to verify, then
   decentralize to execute.
5. **After registration, policy has no central home.** Registered policies live
   on any node in the network. Any registered computer can serve as a policy
   source, turning a user's computer into a server if they want.
6. **A Continuous Synchronization Mesh wraps the runtime.** Sync is decoupled
   from execution: an ambient, always-on layer propagates state across nodes
   with no central locks, inside an immutable identity boundary.
7. **Verification gates everything.** Edge canaries, sub-millisecond
   cryptographic lineage, automatic consensus convergence. Any Byzantine
   fault, split-brain, or state divergence = hard failure + automated rollback.

### The open risk Max named on 2026-11-06

If a policy lives on someone's machine and they turn that machine off, that
policy source drops. Replication across registered nodes is the intended
answer (see `docs/architecture/07-availability-risk-when-a-policy-host-goes-offline.md`),
and it is **not solved yet**. It is a test case, not a footnote.

## Monorepo layout

| Folder | What lives there | Principle |
|---|---|---|
| `docs/original-proposals/` | The two source proposals, verbatim | Source first, never paraphrase the source away |
| `docs/architecture/` | One file per layer, in dependency order | A layer is only claimed once its file states inputs, outputs, and failure mode |
| `docs/clarifications/` | Dated clarifications from Max, in his chain | Later clarifications supersede earlier summaries |
| `docs/communication/` | How people communicate, propose, upload, and test in this repo | Communication is a layer of the system, not an afterthought |
| `packages/` | One package per runtime layer (registration, sync-mesh, runtime, policy-library) | Packages mirror the architecture layers, nothing else |
| `tests/test-cases/` | Test cases per layer, in Markdown first | A test case exists before code claims the layer works |
| `experiments/` | Numbered, self-contained experiments | An experiment can fail; its record stays |
| `ideas/` | Idea dump lane: template + inbox | Any supporting idea lands raw, then gets filed, never lost |

## Where to start

- New here: `docs/architecture/00-overview-the-whole-chain.md`
- Want to contribute a policy/process: `docs/communication/how-to-register-a-policy-library.md`
- Want to dump an idea: `ideas/TEMPLATE.md`, drop it in `ideas/inbox/`
- Want to run/check a test: `tests/README.md`

## License

MIT. See `LICENSE`.
