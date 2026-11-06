# packages/runtime-filters - WebAssembly Runtime Filters (core module)

Initialized 2026-11-06. Core modules for the Wasm runtime filters that
carry classification logic at edge gateways and in micro-execution units.

## Filter contract (v1, draft)

A filter is a Wasm module that:
1. Is registered (layer 04) and signature-verified before it can load.
2. Receives: payload bytes + EAI + ingress vector. Nothing else.
3. Returns exactly one verdict: PASS, DROP, or ISOLATE, plus a lineage
   record (inputs hash, EAI, policy hash, output hash).
4. Holds no state between calls. State belongs to the synchronization
   layer, never to a filter.
5. Runs inside the sandbox with declared memory and time limits.
   Exceeding either is a DROP, not a crash of the host.

## Module layout (to be built under Gate 1+)

- `contract/` - the host/guest interface definition (this contract, in code).
- `filters/` - one folder per filter, each with its test cases.
- `host/` - the sandboxed loader that refuses unregistered modules.

Status: CONTRACT INITIALIZED, code not yet built. The loader's refusal of
unregistered modules is Gate 1 outcome 4 and is tested there first.
