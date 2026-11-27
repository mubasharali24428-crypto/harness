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
team already has — `CLAUDE.md`, `AGENTS.md`, `.cursor/rules/*.mdc`, and
`.codex/rules/*.md` — and writes one fenced block in the existing project rules
document through the **public API**. It does not import Codex `config.toml` or
write project memory:

* the default preview sends nothing and assumes an empty existing document;
  `--apply` fetches the current document and writes the merged result;
* each source is identified by a heading in the merged block;
* common secret-shaped lines and private-key blocks are filtered and counted,
  but the filter is not exhaustive; remove secrets and review the preview;
* whole sources that do not fit are dropped and reported; an individually
  oversized source is refused;
* re-running replaces the fenced block. The GET/PUT sequence has no conditional
  write, so an intervening concurrent edit can be overwritten.

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

## Slice 5 — the pages, in the product (shipped)

`/pricing` and `/docs` are now routes in the app (section7), not only Markdown:

* `/pricing` renders from `section7/src/lib/pricing.json`, and the backend test
  compares that file to `tier_caps()` and the door constants — the same pin as
  PRICING.md, so the page a customer actually reads cannot drift. Rows are
  labelled `enforced` / `declared`, amounts are `$0 forever` / `Contact us` (a
  test refuses any other dollar figure), and the Team-only surfaces name their
  refusal (`403 PLAN_REQUIRED`).
* `/docs` carries the four steps the quickstart was executed with (no fifth,
  invented step), labels `/demo` a recorded replay, and points at the sample
  repos and the migration command.
* The landing nav links both pages; a signed-out visitor can reach them without
  guessing a URL.

Evidence: 10 vitest tests over the two pages (rendered with `renderToStaticMarkup`,
the repo's Node-env convention), 6 more backend pins for the JSON, `make
frontend` 1013 passed + tsc clean, and both routes served live after rebuilding
the frontend image (`/pricing` 200 with `>20<`, `2000`, `Unlimited`, `enforced`,
`declared`; `/docs` 200 with the four steps and both links).

## Slice 6 — a demo that runs live (shipped, in the honest sense)

`make e2e-compose` against the deployed stack: **13/13 checks green in 3.1s** —
21 containers healthy, readiness, passwordless login, demo seed with
`replay=true` and idempotent re-seed, run aggregate with 10 steps, engine truth
`replayed`, 10 marked events, the audit row, sandbox create/exec/destroy with a
post-destroy 404, the cross-tenant no-oracle answer, 23 metrics families, and the
frontend serving HTML. That is a demo that RUNS rather than a recording — and the
seeded run inside it still says `replayed`, because the honesty rule does not get
suspended for a demo.

## Not this week, or not here (stated)

* **A live *agent* demo (real provider spend).** The journey above runs the
  product live but replays the agent, by design. A real provider run is a W17
  concern (engine saturation, cost) and belongs in an isolated environment.
* **Publishing to PyPI/npm.** An external release step with credentials this
  environment does not hold; the packages are built and import-checked instead.
* **Seat and agent-seat limits.** `PRICING.md` will stop claiming them as
  enforced because no door enforces them. Adding a cap is a product decision
  (and a W17-adjacent quota change), not a launch-page edit.
* **Pricing amounts.** Still `[PLACEHOLDER: counsel review]`; unchanged.
* **Any W17 file**: `section1/src/harness_identity/eventbus.py`,
  `docs/m2-w17-plan.md`, `docs/runbooks/w17-reliability.md`,
  `section1/tests/test_w17_event_backpressure.py`, and the live stack.
