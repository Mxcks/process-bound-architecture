# 01. Execution-Bound Identity

Classification and schema are compiled into the runtime (Wasm / eBPF), not
attached to stored objects. Data traversing a channel receives an ephemeral,
verifiable Execution-Attached Identity:

`EAI = Hash(ProcessID, PipelineStage, CryptographicEpoch, IngressVector)`

Payload at rest: zero static metadata overhead.
Failure mode: non-deterministic identity derivation under out-of-order or
concurrent execution. That failure is owned by layer 05, not hidden here.
Source: both files in `docs/original-proposals/`.
