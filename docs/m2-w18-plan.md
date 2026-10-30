# Marathon 2.0 W18 — launch (M2-W18)

The 2026-10-28 roadmap closes with launch: a docs site with a real quickstart, a
migration guide from Claude Code / Codex (config + rules import), a pricing page
mapped to **enforced** caps, a public SDK, sample repos, and a demo that runs
live rather than replaying a recording.

W17 (reliability and scale) is being executed concurrently in this tree by
another agent. This plan only takes work that does not touch W17's files, its
live drills, or the shared deployed stack; the skip list at the bottom is part of
the deliverable, not an omission.

## Starting audit (2026-10-30)

| Area | What exists | W18 gap |
|---|---|---|
| Pricing | `PRICING.md` (150 lines) claims it is "mapped to real code" | **It is not.** It advertises 50 runs/day and 5k chunks (code: 20 and 2000), "up to 5 seats" and "3/25 agent seats" (no seat cap is enforced anywhere), and per-tier concurrency 4/8/16 (code: one deploy-wide `HARNESS_RUN_CONCURRENCY`, default 4). Nothing pins the document, so it can drift again. |
| Public SDK | `clients/python` (`harness-sdk`, CLI `harness`) and `clients/ts` (`harness-ts-sdk`), both generated from the recorded specs, both with READMEs; `scripts/sdk-gen/smoke_sdk.py` | No end-to-end quickstart that a stranger can follow; no "these are the versions we publish" statement; publishing itself is an external step. |
| Sample repos | none | Nothing to point a new user at. |
| Migration | none | No path for a team already on `CLAUDE.md` / `AGENTS.md` / Codex config, which is the first question on every evaluation call. |
| Docs site | `docs/*.md` (80+ pages), no site build | No single entry point; the quickstart does not exist as a runnable document. |
| Demo | `scripts/e2e/compose_journey.py` (12 checks), drills per feature | No scripted demo that is *verifiable*, and running one now would contend with W17's load work on the shared stack. |

## Slice 1 — the pricing page tells the truth

`PRICING.md` is rewritten so every number is either a value the enforcement code
actually uses, or explicitly labelled "declared, not enforced". A test
(`section1/tests/test_pricing_page_matches_enforced_caps.py`) parses the tier
table and the platform table and compares them to
`harness_contracts.tiers.tier_caps()` and the real constants
(`MAX_SINKS_PER_TEAM`, `MAX_TOKENS_PER_TEAM`, `HARNESS_RUN_CONCURRENCY` default,
`ENGINE_MAX_CONCURRENCY` default, `HARNESS_MAX_WORKSPACES_PER_TEAM`). Changing a
cap in code without touching the page now fails `make test-section1`.

The tier env knobs (`HARNESS_TIER_RUNS_PER_DAY`, `HARNESS_TIER_CONTEXT_CHUNKS`)
were readable by the code but absent from `.env.example`; they are documented
there, because a pricing page that says "operator-tunable" is only true if the
operator can find the knob.

## Slice 2 — migrate a team that already uses Claude Code or Codex

A one-shot importer, `scripts/migrate_agent_config.py`, plus
`docs/migrating-from-claude-code-and-codex.md`. The importer reads the files a
team already has — `CLAUDE.md`, `AGENTS.md`, `.cursor/rules/*.mdc`, and a Codex
`config.toml`'s `instructions`/`rules` — and writes them into Harness through the
**public API only** (project rules + project memory), so it needs no schema or
route changes and cannot disagree with the product:

* `--dry-run` prints the exact calls it would make (the default; nothing is sent
  without `--apply`);
* each source becomes one rule with `source=<file>` in its provenance, so a
  later review can see where a rule came from;
* secrets-shaped lines (`sk-…`, `ghp_…`, bearer tokens, `*_KEY=`, private key
  blocks) are dropped and counted, never forwarded — a migration must not become
  an exfiltration channel;
* an oversized file is truncated at a rule boundary and reported, not silently
  split;
* idempotent: a re-run updates the same rule (matched on source + title) instead
  of duplicating it.

Tests run the parser and the request builder against fixtures; the HTTP leg is
covered by a `--dry-run` golden and (later) a live drill when the stack is not
being load-tested.

## Slice 3 — sample repos (shipped)

`examples/` gained two small repos: `examples/fix-a-failing-test/` (its test fails
on purpose; the README walks plan → approve → run → review) and
`examples/review-a-pr/` (a change whose review must name a behaviour, not the
diff). No `harness.yaml` was invented: the product has no such file, so the
examples use the surfaces that exist — an `AGENTS.md` imported through slice 2,
project settings, and the run/review APIs. `test_examples_are_runnable.py` runs
each example with pytest (the kata must FAIL and the review example must pass),
asserts both carry the conventions file, and pushes that file through the
importer with zero lines dropped — an example that trips the secret filter would
teach the wrong thing.

## Slice 4 — quickstart (documented and executed once)

`docs/quickstart.md` walks start → readiness → passwordless login → seeded run →
run aggregate → engine truth (`replayed`) → event replay → an audited action →
the audit page, and then the real workflow. Each step was run verbatim against
the compose stack on 2026-10-30: readiness 200/200/200, login token minted, seed
`pr_opened` with 10 steps, engine `replayed`, 10 replay events all marked, and the
audit page answering with the `invite.minted` row the page itself causes. Two
corrections came out of running it: the team id comes from the login response
(not `/me`), and a read is not audited — the page must show an action the reader
caused. The SDK README examples remain as they are; a generated checker for the
fenced blocks is NOT built (see "not this week").

## Not this week, or not here (stated)

* **A live demo run.** W17 owns the deployed stack for load and failure drills
  and requires an isolated environment; a demo that contends with it would make
  both sets of evidence weaker. The demo script lands with the quickstart check
  and is executed when the stack is free.
* **Publishing to PyPI/npm.** An external release step with credentials this
  environment does not hold; the packages are built and import-checked instead.
* **Seat and agent-seat limits.** `PRICING.md` will stop claiming them as
  enforced because no door enforces them. Adding a cap is a product decision
  (and a W17-adjacent quota change), not a launch-page edit.
* **Pricing amounts.** Still `[PLACEHOLDER: counsel review]`; unchanged.
* **Any W17 file**: `section1/src/harness_identity/eventbus.py`,
  `docs/m2-w17-plan.md`, `docs/runbooks/w17-reliability.md`,
  `section1/tests/test_w17_event_backpressure.py`, and the live stack.
