#!/usr/bin/env python3
"""Zero-trust handoff verification (prototype scope, honest limits).

Checks, in order:
1. Every EAI test vector in schemas/eai-test-vectors.json recomputes
   exactly from its canonical string (deterministic hashing holds).
2. No vector field contains the separator character.

This is continuous cryptographic verification at the schema handoff.
Pipeline-stage proof verification (zk / Merkle) is Gate 5 work and is
NOT claimed here.
"""
import hashlib, json, pathlib, sys

root = pathlib.Path(__file__).resolve().parent.parent
data = json.loads((root / "schemas" / "eai-test-vectors.json").read_text())
failures = []
for i, v in enumerate(data["vectors"]):
    for field in ("process_id", "pipeline_stage", "ingress_vector"):
        if "|" in str(v[field]):
            failures.append(f"vector {i}: separator in {field}")
    canon = (
        f"PBA-EAI-v1|{v['process_id']}|{v['pipeline_stage']}|"
        f"{v['cryptographic_epoch']}|{v['ingress_vector']}"
    )
    if canon != v["canonical_string"]:
        failures.append(f"vector {i}: canonical string mismatch")
    digest = hashlib.sha256(canon.encode()).hexdigest()
    if digest != v["eai_sha256"]:
        failures.append(f"vector {i}: hash mismatch {digest} != {v['eai_sha256']}")
if failures:
    print("FAIL:"); [print(" -", f) for f in failures]; sys.exit(1)
print(f"PASS: {len(data['vectors'])} EAI vectors recompute exactly.")
