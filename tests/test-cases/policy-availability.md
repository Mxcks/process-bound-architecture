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
6. Revocation delivery to a placed policy (added 2026-11-06,
   experiment 021; finding F1 in experiment 020): a policy is
   placed at a replication floor on opted-in holders (the case
   2/4 machinery), and its author then revokes it (registration
   case 6). The updated record is delivered the way this layer
   delivers every record: the grown registry is copied over the
   placement's shared snapshot file, following the anchor
   lifecycle the earlier experiments stated when an anchor is
   configured (a reload of the honestly grown snapshot before
   re-anchoring is refused as anchor_stale, and after the grown
   registry is re-anchored by the SAME signing identity the
   reload can stand). Expected: the placed policy object gains
   a reload path in the same style as a standalone node's
   `reload_snapshot` (experiments 008/015): calling it
   re-verifies the delivered snapshot and re-consults the
   anchor BEFORE any holder's view changes, then rebuilds
   every holder's view from it, so `serve()` on the placed
   policy raises the revocation refusal and no holder serves
   the revoked policy. Negative controls, in the style of
   case 1: a reload over a tampered snapshot is refused and
   leaves the previous serving state untouched (the placed
   policy still serves exactly as before the attempt), exactly
   as a standalone node behaves; a policy that was NOT revoked
   keeps serving after the same reload; and with NO reload
   called, the placed object keeps serving from its
   placement-time view - that remaining fact is stated, not
   hidden: delivery is an explicit reload call, not a push,
   and nothing here bounds how long a placed policy that is
   never reloaded stays stale. Status: implemented at
   prototype scale in experiment 021 (explicit reload call on
   in-process holders over one shared snapshot file; no push,
   broadcast, or automatic delivery is claimed).
7. Automatic snapshot delivery to a placed policy (added
   2026-11-06, experiment 022): case 6 gave a placed policy a
   reload path and named its own remaining fact - nothing calls
   `reload_snapshot()`, so a placed policy learns of a revocation
   only when someone reloads it by hand, and the liveness watcher
   of case 5 does not trigger reloads. A delivery watcher in the
   style of case 5's liveness watcher (deterministic modeled
   clock, no wall-clock sleeps) watches one placed policy and, on
   each of its checks, compares two cheap facts of the registry
   snapshot the placed policy was built over - its current chain
   head and its entry count - against the head and count the
   placed policy last loaded. Expected: when the snapshot has
   changed and the delivery is clean (the delivered snapshot
   verifies, and when the placed policy was built with anchor
   consultation as in case 6 the anchor lifecycle has been
   followed, the grown registry already re-anchored by the same
   signer), the watcher calls `reload_snapshot()` automatically
   in that same check and the change takes effect: a revoked
   policy stops being served by the placed object, a non-revoked
   policy placed over the same snapshot keeps serving after its
   own automatic delivery, and the delivery is recorded in the
   watcher's report / log with the before and after heads and
   entry counts. Negative controls, in the style of case 1: when
   the snapshot changed but the anchor lifecycle was NOT followed
   (the honestly grown snapshot is anchor_stale) or the delivered
   snapshot is tampered with or rewritten, the watcher refuses
   the delivery in that check, the placed policy keeps serving
   its previous view, the refusal is recorded with its reason,
   and later checks keep re-reporting the same refusal instead of
   going silent; and when nothing changed, the watcher does
   nothing and reports no delivery, so holder views are not
   rebuilt on unchanged ticks. Healing interaction, stated
   before the build: case 6 noted that a heal between a
   revocation and a reload recruits a replacement over the stale
   record; after an automatic delivery has landed, a later heal
   (including one a holder loss triggers while delivery is in
   flight) recruits over the UPDATED record, so the recruit
   refuses the revoked policy exactly like the holders that were
   reloaded. Status: implemented at prototype scale in
   experiment 022 (a watcher whose checks the caller drives on a
   modeled clock, in-process holders over one shared snapshot
   file; no unattended scheduling, no network delivery, and no
   push or broadcast is claimed).
