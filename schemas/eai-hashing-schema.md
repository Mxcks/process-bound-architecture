# EAI Hashing Schema (PBA-EAI-v1)

Execution-Attached Identity is deterministic: the same four inputs, in
the same order, with the same encoding, always produce the same identity,
on every node, with no lookup.

## Canonical form

```
canonical = "PBA-EAI-v1|" + ProcessID + "|" + PipelineStage + "|" +
            decimal(CryptographicEpoch) + "|" + IngressVector
EAI = SHA-256(canonical, UTF-8), lowercase hex
```

Rules:
1. Field order is fixed: ProcessID, PipelineStage, Epoch, IngressVector.
2. The version prefix `PBA-EAI-v1|` is part of the hashed string. A schema
   change is a new version prefix, never a silent change to v1.
3. Epoch is base-10, no leading zeros, no plus sign.
4. No field may contain `|`. A field that does is rejected at ingress.
5. Hash is SHA-256 for v1. The algorithm is named in the schema version,
   not negotiated at runtime.

## Test vectors

`eai-test-vectors.json` holds worked examples with their canonical
strings. `scripts/verify-handoffs.py` recomputes every vector; the
zero-trust CI pipeline runs that script on every handoff (push and PR).
A vector that does not recompute is a hard CI failure.
