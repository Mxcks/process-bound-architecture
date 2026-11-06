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
