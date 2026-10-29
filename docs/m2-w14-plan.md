# Marathon 2.0 W14 — model intelligence and evaluation (M2-W14)

Follows the 2026-10-29 W13 outcome (control plane shipped, execution leg open;
see `docs/m2-w13-plan.md`). The roadmap row in one line: *per-step model routing,
an eval harness for "which model is actually better on our repos", cost/latency
dashboards, and a rate card so unpriced models stop billing zero.* The marathon
row adds the invariants: *reproducible model comparisons; routing requires
opt-in; failure never forces a downgrade.*

## Purpose

W14 has four pieces, and only the first is deterministic enough to gate today:

1. **Corpus contract** — fixed expected outputs, a real held-out split, drivers
   that exist. A comparison is worthless if any of those are optional.
2. **Reproducible comparisons** — the same task, model and budget must produce
   comparable evidence; runs must name the deployed revision they were judged
   against (`revisionCheck`), and live tasks must be runnable twice without
   changing the pass rule.
3. **Opt-in routing** — a cheap model may be chosen for mechanical steps and a
   frontier model for reasoning, but only when a team turns it on; a routing or
   provider failure must never silently downgrade the model (the existing law:
   *no forced model downgrade*).
4. **Metering** — every priced model has a rate; an unpriced model must be
   visible as unpriced, not silently billed at zero. Cost/latency rollups must be
   queryable per run/team.

## Audit (2026-10-29)

| Area | Observation | Where |
|---|---|---|
| Eval runner | executes a corpus task's driver, records exit code + independent verifier output + `revisionCheck`; deferred ≠ pass | `scripts/eval/run_eval.py` |
| Corpus | 38 tasks, 5 held out, 28 implemented; `expected`/`verifier` are prose; **nothing validated the structure** — a task could omit its expected output, point at a missing driver, or lose `heldOut` in an edit | `docs/eval/corpus.json` |
| Model routing | per-role primary/reviewer model preferences only; no per-step routing, no opt-in switch | `section3/.../prefs.py`, `service.py` |
| Pricing | `MODEL_PRICING_CATALOG` is declarative; zero-priced `local/*` entries are intentional; a model id **absent** from the catalog is the open question | `section3/.../config.py`, `pricing.py` |
| Metering | cost ledger + daily rollup + per-run metering exist | `section3/.../ledger.py`, `section4/.../run_metering.py` |
| Dashboards | Prometheus counters for router requests and spend exist; no cost/latency dashboard panels | `section3/.../api.py`, `deploy/` |

## Shipped in this session (first slice)

**Corpus contract gate.** `python3 scripts/eval/run_eval.py --check` validates:

* every task declares a non-empty `title`, `verifier`, `expected` and `evidence`;
* `id` is unique; `category`/`kind`/`cost`/`status` come from the fixed sets;
* `heldOut` is a boolean, and at least **five** tasks are held out;
* a `script` driver's path exists in the repository (`builtin`/`http`/`none` are
  allowed and need no path);
* `budget.timeoutSeconds` is a positive integer;
* a `live` task names its model, and a held-out task cannot also record tuning
  use (the `not used for tuning` disclaimer is required if "tuning" appears).

It is wired into `make static-gates`, so a corpus edit that drops a fixed
expected output now fails CI rather than disappearing into a comparison.

Current output: `{"ok": true, "tasks": 38, "heldOut": 5, "implemented": 28}`.

**Operator rate card.** `section3/src/harness_router/rate_card.py` merges the
frozen catalog with `HARNESS_RATE_CARD_JSON` (inline JSON or a container path),
validated at load: a negative, missing, non-numeric, non-finite or empty-id entry
raises `RateCardError` instead of turning into free inference. Every lookup now
reports its source — `catalog`, `operator` or `unpriced` — and money math is
pinned to `PricingEngine.calculate_cost` by a parity test over the whole catalog.

`/estimate-cost` also fixed a real bug: naming a model that had no rate used to
**silently answer the team's default model's price under the requested model
name**. It now answers the requested model with `estimatedCostUsd: 0`,
`priced: false` and `pricingSource: "unpriced"`, and reports `model`/`priced`/
`pricingSource` for every call. `/catalog`'s per-provider `priced` list uses the
same card. Compose passes the env var to the models service and `.env.example`
documents the shape.

Tests: `section3/tests/unit/test_rate_card.py` (16) — overlay override and
addition, catalog-vs-unpriced distinction, zero-priced `local/*` staying
`catalog`, six malformed-card refusals, env loading, `priced_cost` through the
active card, and the three `/estimate-cost` behaviours above. Section 3 suite:
144 passed.

Live check on the rebuilt models image (2026-10-29): role default →
`deepseek/deepseek-coder`, `$0.42`, `priced: true`, `catalog`; named
`brandnew-lab/model-v9` → `$0.0`, `priced: false`, `unpriced` and the **requested
id echoed** (no substitution); a one-off container with
`HARNESS_RATE_CARD_JSON={"models":{"lab/container-priced":{"input":2,"output":4}}}`
reports `operator` and `$2.000000` for 1 M input tokens.

## Next slices (in order)

1. **Per-step routing, opt-in.** A team setting (default off) enabling
   mechanical/reasoning step classes; the engine resolves each step's model from
   that policy; any resolution failure falls back to the **team's configured
   primary**, never to a cheaper model, and the fallback is recorded on the run.
2. **Comparison runner.** `run_eval.py --model A --model B --task …` with the
   same budget and fixture, writing one comparison evidence file; live tasks
   require `--include-live` and a named model, and the pass rule is the task's
   own verifier, not a relative win.
3. **Cost/latency dashboard.** Panels over the existing ledgers (per team, per
   model, p50/p95 latency, spend vs cap) with a runbook note; no new metric
   names unless a panel needs them.
4. **Held-out hygiene.** The W11 vision task's prompt was adjusted at W12 and
   its purity is disclosed; W14 should add a fresh, never-tuned mockup set so at
   least one design task is genuinely held out.

## Gate position

| W14 exit claim | State |
|---|---|
| Corpus has fixed expected outputs and a real hold-out split, enforced | **pass** — `--check` in static gates (38 / 5 / 28) |
| Reproducible model comparisons | **open** — no comparison runner yet |
| Routing requires opt-in | **open** — not implemented |
| Failure never forces a downgrade | **existing law**, must be re-proven for per-step routing |
| Unpriced models stop billing zero silently | **pass (visibility + overlay)** — a named unpriced model reports `priced: false`, `pricingSource: "unpriced"`, cost 0; operators price it via `HARNESS_RATE_CARD_JSON`; malformed cards fail loudly |
| Cost/latency dashboards | **open** — metrics exist, panels do not |

## Limitations

* The corpus gate validates structure, never truth: a well-formed task can still
  have a weak verifier. The verifier is the task author's claim.
* `revisionCheck` covers three runtime files (engine, image gate, images plugin);
  it is not a whole-image digest.
* Held-out count (5) is a floor, not a quality bar; two of the five are infra
  boundary tasks, not model-judged.
* No model call is made by any gate added here.
