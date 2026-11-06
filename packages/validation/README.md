# packages/validation - Validation Layer

Separate layer 3 of the operational pipeline. Validation does exactly
one job: enforce behavioral boundaries on payloads and pipelines, using
validation channels bound at registration (layer 04). It runs only on
registered libraries. Unregistered code never reaches validation; it is
blocked at the execution boundary first.

Inputs: payload + EAI + bound validation channel.
Outputs: pass, or drop/isolate in real time, with the result logged to
the immutable execution log.
Failure mode: gate bypass or unverified channel execution = hard failure.
Blocked by: Gate 1 registration (see AUTHORIZATION.md build order).
