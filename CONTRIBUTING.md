# Contributing

This repo is the workspace for Process-Bound Architecture. Three lanes, keep them separate:

1. **Ideas** go in `ideas/inbox/` using `ideas/TEMPLATE.md`. Raw is fine. Do not polish an idea into a spec in the inbox.
2. **Proposals / changes to a layer** go as a dated file in `docs/clarifications/` or as an edit to the matching file in `docs/architecture/`, with the source named.
3. **Tests** go in `tests/test-cases/<layer>.md` first, then as a numbered folder in `experiments/` when run.

Rules that keep this clean:

- One layer per file, one experiment per folder.
- Name the failure mode in the same file that claims the behavior.
- Unverified ideas are marked UNVERIFIED. Nothing is called production without its verification gate in `docs/architecture/06-verification-and-thresholds.md`.
- Source proposals in `docs/original-proposals/` are read-only history. Clarifications supersede summaries, never the source.
