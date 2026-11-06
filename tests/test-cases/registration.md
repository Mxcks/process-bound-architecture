# Test Cases: Registration (layer 04)

1. Unregistered bytecode is blocked at the execution boundary.
2. A library with a bad signature is rejected and the rejection is logged.
3. A correctly signed library from any user registers through the
   authenticated channel and becomes loadable, with its policy hash in the record.
4. Registration of the same library twice yields one deterministic record, not two conflicting ones.
5. A validation channel is bound on registration and on nothing else.
6. Revocation (gap flagged in experiment 002; test case only, revocation
   is NOT built yet): a registered library that is later revoked by its
   author is blocked at the execution boundary after revocation, its
   validation channel is no longer honored, and the revocation itself is
   recorded as a new entry in the tamper-evident record. The original
   registration entry is not deleted or rewritten, and a node holding
   only the pre-revocation record is a known stale state this case must
   name, not hide.
