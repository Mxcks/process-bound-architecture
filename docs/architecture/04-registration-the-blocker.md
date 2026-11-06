# 04. Registration: the Blocker (build this first)

## The paradox, in Max's words (2026-11-06)

You are creating a centralized process for policy registration in order to
decentralize the policy as soon as it is centralized.

## What registration must do

1. Authenticated channel, created in advance, that any user can use to
   register a policy library / process library.
2. Verify code signature and behavioral policy before the library can run.
3. Write the registration to a tamper-evident, append-only record
   (the blockchain-style blocker Max named): inputs, EAI context, policy
   hash, and output are logged to an immutable audit fabric.
4. Bind a validation channel dynamically on registration.
5. Block unverified or unregistered bytecode at the execution boundary.
   No exceptions.

## Why it blocks the validation layer

Policy validation needs this registration process for the synchronization
layer, and that process needs to be secure and designed first. Do not build
or claim the validation layer before this layer's spec and test cases pass.

## Registry home

The ambient synchronization layer (05) functions as the decentralized
registry and policy engine after registration. Registration is the door;
the mesh is the room. Do not confuse the door with a central runtime server:
at runtime, policy is decentralized.
