# Test Cases: Polymorphic Payloads (layer 02)

1. The same pipeline accepts binary, JSON, and protobuf payloads with no schema registration.
2. A payload breaking a behavioral boundary is dropped/isolated in real time.
3. A legacy-format payload and a new-format payload interoperate on one pipeline with no migration.
