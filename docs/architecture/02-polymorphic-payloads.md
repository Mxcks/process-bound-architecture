# 02. Polymorphic Payloads

Data in transit is a transient byte stream. It may be binary, JSON, protobuf,
or a custom encoding, with no upfront schema registration.

Validation is behavioral, not syntactic: the runtime enforces guardrails,
invariants, memory limits, and transition boundaries inside an isolated
sandbox. A payload that conforms to the safety constraints is processed;
a payload that breaks a boundary is dropped or isolated in real time.

Consequence: legacy and new systems can share one pipeline with no
coordinated schema migration. Policies are payloads too (see 03).
