# Test Cases: Policy Availability (layer 07, open risk)

1. The sole host of a policy goes offline: dependent systems must not
   silently run without the policy. Expected today: FAILURE is the honest
   result until replication exists. Record it, do not hide it.
2. With replication (UNVERIFIED candidate): one host goes offline and the
   policy remains available from another registered node.
3. A policy is deliberately removed by its owner: dependents are notified
   through the mesh before the policy stops being served.
