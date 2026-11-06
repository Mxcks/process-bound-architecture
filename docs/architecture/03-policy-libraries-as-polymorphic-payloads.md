# 03. Policy Libraries as Polymorphic Payloads

- A policy library is a polymorphic payload: it can live on any server/node
  that has access to the network, once registered.
- **Any user** can create their own policies and processes, turn them into a
  library, and register that library. Registration must be easy and must go
  through authenticated channels that exist in the system *before* the user
  reaches the point of needing to register.
- After registration there is no central policy location and none is needed:
  policy is uploaded into state through the verification locations, so there
  are multiple access points for policy.
- A registered user's computer can act as a server / policy source if they want.

Open risk: removal or shutdown of a hosting node. See 07.
