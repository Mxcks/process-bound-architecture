# Standing Authorization: the Entire Architecture

Given by Max Stern, voice call, 2026-11-06, in his words in substance:

> I'm not trying to authorize the next build. I'm trying to authorize the
> entire architecture so that you can go do this yourself instead of having
> me design this, because I have no clue what's going on, to be frank,
> technically.

## What this authorizes

Maren owns the technical design and execution of the **entire**
Process-Bound Architecture inside this repository:

- Design every layer, make the technical choices, and record them in the
  layer files. Max is not the technical designer and is not a gate.
- Build in dependency order, using the gates in
  `docs/architecture/08-outcomes-that-unlock-the-next-build.md` as
  **Maren's own internal build order**, not as approvals to ask Max for.
  A gate passing is Maren's signal to proceed to the next layer herself.
- Run tests as needed, create experiments, fail honestly, and iterate
  recursively. Come back to Max with results, tradeoffs that change the
  idea itself, and blockers Maren cannot clear, not with step approvals.
- Keep everything open source in this repo. Dump ideas, tests, and
  results here as the work goes.

## What this does not authorize

- Deploying PBA into any production system outside this repo.
- Spending money, or putting PBA in front of clients, without Max's word.
- Changing the core idea itself (pure payloads, execution-bound identity,
  centralized registration to decentralize policy). That idea is Max's;
  implementing it is Maren's.

## Why this exists

Max wants the ability to authorize a lot of work at once so Maren can
work continuously without Max having to guide the technical steps, and
test whether this system would actually be beneficial.
