# packages/synchronization - Synchronization Layer

Separate layer 2 of the operational pipeline. Synchronization does
exactly one job: propagate state across nodes continuously, with no
central locks, through the Continuous Synchronization Mesh
(`packages/sync-mesh/` is the mesh mechanism; this package is the layer
contract every node implements).

Inputs: state transitions from the runtime, each carrying its EAI.
Outputs: converged state, or a divergence signal that triggers rollback.
Failure mode: split-brain or diverging state views. Detection is this
layer's job; hiding divergence is a hard failure (see Gate 3 and Gate 5).
