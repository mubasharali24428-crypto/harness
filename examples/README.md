# Examples

Two small repositories that show what a governed run looks like end to end. Both
are meant to be used as the target of a real project, not read as documentation.

| Example | What it demonstrates | Start here |
|---|---|---|
| [`fix-a-failing-test/`](fix-a-failing-test/) | The core loop: plan → approve → run → review. The test fails on purpose so there is something real to fix. | `fix-a-failing-test/README.md` |
| [`review-a-pr/`](review-a-pr/) | The differential review gate: a small change, a human-approved plan, a review that must pass before merge. | `review-a-pr/README.md` |

Both carry an `AGENTS.md`. Import it into the project's rules with
`scripts/migrate_agent_config.py` (see
[`docs/migrating-from-claude-code-and-codex.md`](../docs/migrating-from-claude-code-and-codex.md))
instead of copying it by hand — that is the supported path and it keeps the
repository as the source of truth.

Every example is deliberately tiny: the point is the workflow around the code,
not the code.
