# PBA-FIX-001: Registration gets slower as the registry grows

Status: PROPOSED, talk first. Creating this proposal is not approving it.

## The outcome that spawned it
Experiment 009 (scale run), posted to the PBA Test Outcomes Telegram topic on 2026-11-06. Registering one policy costs a median of 0.538 ms when the registry holds 200 entries, 2.447 ms at 1,000 entries, and 12.676 ms at 5,000 entries. Building a 5,000-entry registry took 74.5 seconds total. The trend says building 25,000 entries would take roughly 30 minutes. Every other measured operation stays fast at these sizes; registration writes are the first thing that breaks.

## What causes it
Each call to register re-reads and re-verifies the whole registry log from disk to find the end of the chain. The bigger the log, the more every new registration re-reads, so the total cost grows much faster than the registry itself.

## Proposed change
Keep the current chain head and entry count in memory while the channel is open, and verify the full chain once when the channel loads from disk instead of on every registration. The record format does not change, the chain still verifies from the file alone, and a fresh load still checks everything.

## Cost and risk
Small, local change inside packages/registration. Risk: in-memory state must never disagree with the file; the load-time full verification and the existing chain tests are the guard, plus a new test that kills and reloads the channel mid-sequence and compares.

## If nothing is done
The system stays usable at roughly 1,000 policies with interactive registration. Past a few thousand policies, registering becomes slow enough to feel broken, and test registries get expensive to build.
