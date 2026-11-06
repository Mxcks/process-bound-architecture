# Test Cases: Policy Availability (layer 07, open risk)

1. The sole host of a policy goes offline: dependent systems must not
   silently run without the policy. Expected today: FAILURE is the honest
   result until replication exists. Record it, do not hide it.
2. With replication (UNVERIFIED candidate): one host goes offline and the
   policy remains available from another registered node.
3. A policy is deliberately removed by its owner: dependents are notified
   through the mesh before the policy stops being served.
4. Self-healing holder replacement (added 2026-11-06, experiment 010):
   a holder is lost permanently, so the holder count drops below the
   replication floor set at registration time. Expected: the shortfall
   is detected and reported (actual count vs floor, never hidden), a
   replacement is recruited from other registered, opted-in nodes that
   do not already hold the policy, the policy is copied to the
   replacement (and verifies there, the same as any holder), and the
   floor is restored, with the policy still served afterward.
   Negative control, in the style of case 1: when no eligible
   replacement node exists (none opted in, or only existing holders
   and duplicates are offered), the system reports below floor
   honestly instead of pretending it healed. Status: implemented at
   prototype scale in experiment 010 (explicit heal step; no liveness
   detection or automatic trigger is claimed).
5. Liveness detection with an automatic healing trigger (added
   2026-11-06, experiment 016): holders emit a heartbeat (a liveness
   signal) into a shared liveness view, modeled deterministically
   with beat counters the tests control (no wall-clock sleeps). A
   watcher distinguishes three states honestly: alive (beating),
   temporarily offline / silent-short (silent for fewer than the
   stated threshold of missed beats: the holder is still a holder,
   Gate 4 failover covers serving, and NO healing is triggered), and
   lost (silent past the stated threshold of missed beats: the
   holder count is treated as dropped). Expected: when a holder
   crosses into lost, healing triggers automatically from that
   detection, reusing experiment 010's heal machinery (not a second
   copy of it): a replacement is recruited under placement's own
   eligibility rules, the floor is restored, the policy still
   serves, and the detection, the trigger, and the healing outcome
   are all recorded in a report / log the tests can assert.
   Negative controls, in the style of case 1: a holder that goes
   silent briefly and returns before the threshold is never healed
   over and never loses its holder status; and when a holder is
   declared lost and no eligible replacement exists, the report
   says below floor honestly and keeps reporting the loss on later
   checks instead of going silent. Returning-holder rule, stated
   before the build: a holder that was declared lost and later
   comes back rejoins as a candidate only, never automatically as
   a holder, so the holder count never double-counts it; it can be
   recruited by a later automatic heal exactly like any other
   eligible candidate. Status: implemented at prototype scale in
   experiment 016 (modeled beats and a modeled clock only; no real
   heartbeat transport, timer, or machine-death detection is
   claimed).
