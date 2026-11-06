# Fix Proposals

When a test outcome needs a fix that changes the approach (not a plain
retry or an obvious correction inside the standing authorization),
Maren may create a **fix proposal**.

Rules (Max, 2026-11-06):

1. Every fix proposal gets an ID at creation: `PBA-FIX-001`, `PBA-FIX-002`,
   in order, never reused, never renumbered. Spoken form: "fix one".
2. The ID is registered in `proposals/registry.md` in the same turn the
   proposal is created, with its title, the test outcome that spawned it,
   and its status.
3. The Telegram outcome post that spawns a fix proposal names its ID, so
   the outcome and the proposal are linked both ways.
4. Fix proposals are **talk-first**: Maren and Max talk about a proposal
   by its ID before Maren acts on it. Creating one is not approving one.
5. A proposal file lives here as `PBA-FIX-NNN-short-name.md` and states:
   the outcome it fixes (with its test/experiment link), the proposed
   change, what it costs, and what happens if we do nothing.
