# How to Register a Policy Library (draft, layer 04 is not built yet)

1. Author your policy/process as a library with a stable name and version.
2. Sign the library bytecode.
3. Submit it through an authenticated registration channel.
4. Registration verifies the signature and behavioral policy, logs the
   policy hash to the tamper-evident record, and binds a validation channel.
5. Only then may any node load and serve the policy as a polymorphic payload.

Status: INSTRUCTION DRAFT. The registration process itself is the blocker
spec in `docs/architecture/04-registration-the-blocker.md`.
