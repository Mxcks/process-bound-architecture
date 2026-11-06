# Test Cases: The Full Chain, End to End (experiment 020)

1. Full-chain integration (added 2026-11-06, experiment 020):
   every earlier experiment in this repo proved its layers singly
   or in pairs (registration 002, placement 005, serving 003,
   mesh sync 004, revocation 008, self-healing 010, protected
   links 014, anchor consultation 015, liveness 016, certificate
   binding 017, anchors in servers and in the verdict 018,
   signed anchors 019). This case runs Max's whole original
   chain in ONE scripted scenario, in order, over ONE policy,
   asserting each stage's outcome with the mechanisms already
   built, changing no existing mechanism's behavior:
   (a) A user registers a policy through the authenticated
       channel (Gate 1): the channel exists and is ready before
       use, the registration is admitted at the execution
       boundary, a validation channel is bound, and the record
       is the one deterministic hash-chained record.
   (b) A signed external anchor (019) is written over that
       registry by the author's enrolled identity, outside the
       registry directory, and verifies with its signature
       checked.
   (c) The policy is placed at a replication floor of 3
       (Gate 4): the placement report names 3 opted-in holders,
       floor met, status available, and the holders consult the
       signed anchor at construction (015/019 consultation,
       carried into placement by a pure wiring addition: new
       optional parameters on place_policy / heal, defaulting
       to no anchor, i.e. exactly the pre-020 behavior).
   (d) The policy is served from a SECOND holder node, in
       process (Gate 2): the served bytes, author, and record
       hash match the Gate 1 record exactly.
   (e) The policy is served from a second PROCESS over a
       protected, identity-bound link: two node-server processes
       run over mutual TLS with certificate-to-node-id binding
       (014/017) and with the signed registry anchor configured
       at startup (018/019); health on the serving process
       reports anchor_status anchored; the policy fetched over
       HTTPS from the second process carries the registered
       code and served_by naming that process; and the two
       processes sync mesh state over the same protected link
       and converge to one state hash (007/014/017).
   (f) Mesh sync with anchor consultation (Gate 3 + 015/019):
       in-process nodes mutate, exchange, and converge to one
       state hash with no divergence (004); a signed mesh
       anchor is written over the converged state; an
       AnchoredSyncNode configured with that anchor and its
       signing identity accepts a merge whose merged state is
       exactly the anchored state, and reports anchored.
   (g) Liveness and automatic healing (016 over 010/005): with
       the policy placed at floor 3 in (c), one holder stops
       beating on the modeled clock; for threshold-1 missed
       beats it is silent-short, still a holder, and no healing
       fires; on the next tick it is declared lost and healing
       triggers automatically in the same check, recruiting
       one eligible replacement (which is itself constructed
       with the same signed-anchor consultation, per (c)'s
       wiring), the floor is restored, and the policy still
       serves. Coordinated state after healing: every current
       holder serves the same verified policy (same code, same
       record hash) and the mesh state from (f) is still one
       converged hash on every mesh node.
   (h) Author revocation stops serving (008): the author, and
       only the author, revokes through the same channel; the
       revocation is a new entry in the same chain, the
       execution boundary refuses the policy as revoked, and
       the validation channel is no longer honored. Delivery
       of the updated record follows the anchor lifecycle the
       earlier experiments stated: a holder built with the
       anchor first refuses the honestly grown (revocation)
       snapshot as anchor_stale; after the grown registry is
       re-anchored by the SAME signing identity (019's rule),
       the holder reloads the updated record and refuses the
       policy as PolicyRevoked. Final state, stated per
       surface in (i): no holder serves the policy.
   (i) Final state per serving surface, each asserted, none
       glossed: (1) a PolicyNode constructed fresh over the
       updated, re-anchored snapshot refuses PolicyRevoked;
       (2) a node-server process RESTARTED over the updated,
       re-anchored snapshot, over the protected identity-bound
       link with the signed anchor configured, starts and
       reports anchored, and a policy request over HTTPS does
       not return the policy (the exact shape of that refusal
       is whatever the server actually does today - see the
       finding clause below); (3) the placed ReplicatedPolicy
       from (c)/(g) is asserted for exactly what its public
       API can and cannot do today - see the finding clause.
   (j) The Gate 5 verdict over the scenario's evidence (018/019
       pattern): a Gate 5 harness scenario whose registry and
       mesh chains are anchored with SIGNED anchors by an
       enrolled identity passes its verdict with both anchor
       checks additional and passing as anchored; the same
       verdict over a registry chain rewritten so it passes
       every internal check fails, with the registry anchor
       check the only failing check, named anchor_mismatch.
   Finding clause, stated before the build, in the style of
   case 1's honest failure: where two mechanisms genuinely
   cannot compose today, the stage STOPS, the incompatibility
   is recorded exactly as found (in the experiment README and
   in the test output), and the stages that do compose finish.
   A found incompatibility is a successful outcome of this
   experiment, not a failure to hide. Two composition surfaces
   are named in advance as the ones this case is most likely
   to expose, because no earlier experiment ever exercised
   them together: revocation (008) delivered to PLACED holders
   (005/010/016 - placement's own docstring already states its
   holders are constructed once and never reload), and
   revocation (008) served over the NETWORK transport
   (007/014 - the server's policy endpoint has never been
   asked for a revoked policy in any earlier suite).
   Runtime discipline: floor of 3 where the floor matters, 2
   processes where transport matters, modeled clock for
   liveness, no wall-clock sleeps beyond what the existing
   server suites already use. Status: implemented at
   prototype scale in experiment 020
   (`experiments/020-full-chain-integration/`); its findings,
   including any stage that stopped, are recorded there and
   in `test-output.txt`, not in this file.
