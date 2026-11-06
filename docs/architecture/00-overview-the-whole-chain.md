# 00. Overview: the whole chain

Dependency order is the build order. A later layer does not get validated
before the layer it depends on exists.

1. Pure payloads at rest (02)
2. Execution-bound identity (01)
3. Policy libraries as polymorphic payloads (03)
4. **Registration (04) - the blocker. Build this first.**
5. Continuous Synchronization Mesh (05)
6. Verification and thresholds (06) - gates 1-5, never a substitute for 4
7. Availability when a policy host goes offline (07) - open, unsolved

Full narrative: root `README.md`.
