# 07. Availability Risk: a Policy Host Goes Offline (OPEN)

Named by Max, 2026-11-06, in his own chain:

Policies become polymorphic payloads and can live on any server with network
access. If a policy is removed, or someone turns off their computer without
knowing that policy is hosted there, that could drop policies for systems
that depend on them.

Status: **OPEN, unsolved in the source proposals.**

Candidate direction to test (UNVERIFIED, not a decision):
replication of a registered policy to N independent registered nodes at
registration time, with the mesh refusing to mark a policy "available" until
its replication floor is met, and a liveness check in the verification gate.

Test cases: `tests/test-cases/policy-availability.md`.
