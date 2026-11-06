# Clarifications from Max, voice call, 2026-11-06

Recorded in the order Max gave them. These supersede Maren's first summary,
which missed the tail of the main proposal.

1. The state synchronization solve **is** in the document: the Continuous
   Synchronization Mesh (see architecture/05). Maren missed it on a
   truncated first read, then corrected it after a full read.
2. Policy libraries need checking too. Policy validation needs a
   registration process for the synchronization layer. That registration
   must be a secure process, designed first. There is a blockchain-style
   blocker there that must be completed before the validation layer is executed.
3. Policy libraries can be registered **by any user**. A user can create
   their own policies and processes, turn them into a library, and register
   it easily, through authenticated channels created for registration,
   which exist in the system before the user has to approach that scenario.
4. That solves the central policy location problem: no central policy
   location is needed, because there are now multiple access points for
   policy. Policy uploads directly into state based on the verification
   locations, basically turning everybody's computer into a server if they
   want, through the registration layer.
5. Policies basically become a polymorphic payload. They can live on any
   server with network access. Risk: if a policy is removed, or someone
   turns off their computer, that could drop policies for systems.
6. Summary Max agreed to: a centralized process for policy registration
   to decentralize the policy as soon as it is centralized.
7. Build direction: build as much as possible, recursively come back to
   Max as work proceeds, leave it open source, in a clean monorepo on
   Max's GitHub that holds tests, test cases, instructions, and every layer
   needed, as a workspace where other people can communicate and upload
   or test, and where Max can dump supporting ideas.
