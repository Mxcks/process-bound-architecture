# 07. Availability Risk: a Policy Host Goes Offline (OPEN)

Named by Max, 2026-11-06, in his own chain:

Policies become polymorphic payloads and can live on any server with network
access. If a policy is removed, or someone turns off their computer without
knowing that policy is hosted there, that could drop policies for systems
that depend on them.

Status: **MITIGATED AT PROTOTYPE SCALE, still OPEN at real scale**
(updated 2026-11-06, Gate 4, experiment
`experiments/005-gate4-availability/`).

The candidate direction below was tested and it works in the
prototype (`packages/policy-library/replication.py`, 3 of 3 Gate 4
tests pass, Gate 1/2/3 regressions green):

- The honest failure is real and shown: a policy held by exactly one
  node is unavailable after that node goes offline (negative control,
  asserted as a test result, not hidden).
- Replication to a floor of independent, opted-in holder nodes at
  registration time works at prototype scale: with floor 3, the
  policy is still served after the original host goes offline, and
  still served by the last holder after two of three go offline.
  Non-opted-in nodes and duplicate node ids are never counted toward
  the floor.
- The floor is reported honestly: a placement that cannot meet its
  floor reports the actual holder count vs the floor with status
  `below_floor`, and is never marked "available". A policy is marked
  "available" only when its replication floor is met, as the
  candidate direction proposed.

What the prototype proves, and nothing more: the placement, floor
accounting, and failover logic are correct against Gate 2's verified
serving, with "offline" modeled as a flag on in-process nodes on one
machine.

What remains open at real scale:

- Real holders: network transport, discovery, and a placement
  protocol. Prototype holders are in-process objects over one shared
  snapshot file.
- Liveness: TESTED at prototype scale (2026-11-06, experiment
  `experiments/016-liveness-auto-healing/`, availability case 5).
  Holders beat into a shared liveness view on a deterministic
  modeled clock; a watcher declares a holder lost only after 3
  missed beats (fewer is silent-short, the temporary case: still
  a holder, no healing), and that declaration triggers experiment
  010's heal automatically. Still open at real scale: the
  heartbeat is a method call and the clock is a counter - no real
  heartbeat transport, timer, or machine-death detection exists,
  and the Gate 5 verification gate does not consult liveness.
- Self-healing: TESTED at prototype scale (2026-11-06, experiment
  `experiments/010-self-healing/`, availability case 4). A
  permanently lost holder drops the count below the floor, the
  shortfall is detected and reported, an explicit heal step
  recruits a replacement from other opted-in nodes, copies the
  policy to it (verified there like any holder), and restores the
  floor; with no eligible replacement it reports below_floor
  honestly. Still open at real scale: healing here is an explicit
  call against a caller-supplied candidate list, in-process, and
  no copy moves over a network. (The liveness detection and the
  automatic trigger that were open here are now tested at
  prototype scale, experiment 016, availability case 5 - on a
  modeled clock, with candidate discovery still open, and a
  returned lost holder rejoining as a candidate only.)
- Real independence: the prototype counts distinct node ids. It
  cannot verify different machines, operators, or failure domains.
- Deliberate removal: `tests/test-cases/policy-availability.md`
  case 3 (owner removes a policy, dependents are notified before it
  stops being served) is not implemented.
- Inherited limits: no revocation (a holder cannot learn a policy
  was revoked after its snapshot), and snapshot trust is
  tamper-evidence without an external anchor. (The anchor half of
  that sentence has since been built at prototype scale,
  2026-11-06, experiment `experiments/012-external-chain-anchor/`:
  the registry head, and the sync mesh's per-origin heads, are
  anchored to a file outside the record and a fully rewritten
  record fails that check in tests. The anchor file is a local
  stand-in, not a witnessed external service, and serving does not
  consult it; revocation delivery remains as stated.)

Candidate direction as originally recorded (now TESTED at prototype
scale, see above):
replication of a registered policy to N independent registered nodes at
registration time, with the mesh refusing to mark a policy "available" until
its replication floor is met, and a liveness check in the verification gate.

Test cases: `tests/test-cases/policy-availability.md`.
