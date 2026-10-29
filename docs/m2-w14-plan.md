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

## Shipped in this session (second slice)

**Opt-in model routing (off by default).** Policy lives at
`team_settings.key='model_routing'` with three fields
(`enabled`, `mechanicalModel`, `reasoningModel`). `GET/PUT/DELETE
/api/v1/teams/{team_id}/settings/model-routing`; PUT/DELETE are admin-only,
unknown keys and `enabled` with no models are rejected with 422, delete restores
defaults by absence. `section4/.../model_routing.py` classifies a plan
(≤3 steps AND ≤3 files = mechanical; everything else, including an empty or
partial plan, is reasoning) and resolves the run's model. The decision recorded
on the run has `enabled`, `class`, `model`, `source ∈ primary|policy|
policy-fallback` and, for a fallback, a `reason`. No branch can select the other
class's model, so a missing reasoning model never downgrades to the cheap one.

Live smoke on the rebuilt image (2026-10-29): GET defaults → 200
`routingSource: "defaults"`, `enabled: false`; PUT
`{"routing":{...}}` → 200 `routingSource: "team"` with `updatedAt/updatedBy`;
a typo'd key → 422 `VALIDATION_ERROR`; `{"routing":{"enabled":true}}` → 422;
DELETE → 204 and the next GET is back to defaults. The admin-only 403 is proven
offline (`test_settings_api.py`); the dev team-bound login route refused a
second principal live, so that leg is not re-proven over HTTP here.

**Comparison runner.** `run_eval.py --compare-model A --compare-model B` runs
each selected task once per model with the same budget/fixture and writes one
`docs/briefs/evidence/eval/comparison-<stamp>.json` with
`passRule: "each task's own verifier, judged per model; never a relative win"`.
It refuses to start without `--include-live`, with fewer than two distinct
models, or with an empty selection. **No live comparison has been run** (no
model spend was authorized), so the file is a capability, not a result.

**Cost/latency dashboard.** Six new gauges are refreshed by the orchestrator's
existing DB poller from the frozen `cost_ledger`:
`harness_model_cost_usd{team_id,model}`, `harness_model_calls{team_id,model,role}`,
`harness_team_spend_usd_month{team_id}`,
`harness_team_budget_usd{team_id}` (cap resolution: team > plan > env; absent =
unlimited), and `harness_model_latency_p50_seconds` /
`harness_model_latency_p95_seconds{team_id,model}` (rolling window,
`HARNESS_COST_METRICS_WINDOW_HOURS`, default 24; PostgreSQL only). Rows whose run
cannot be attributed surface as `team_id="unattributed"` instead of vanishing.
They are gauges, not counters: releasing a reservation deletes ledger rows.
`deploy/observability/dashboards/harness-cost-latency.json` has eight panels
(spend vs cap, utilisation, spend by model, calls, p50/p95, cost per call, notes);
`docs/runbooks/cost-latency-dashboard.md` carries the reading guide. All six
series were confirmed live on the orchestrator `/metrics`
(125 cost, 347 spend, 6 cap, 25+25 latency samples at the time of the check).
The dashboard itself is structurally checked, **not visually verified**.

**Fresh held-out design set.** `w14-design-heldout` (heldOut, 960x640,
sidebar-led, never tuned) with `scripts/e2e/w14_design_heldout_drill.py`. Its
deterministic `--fixture-only` leg renders the mockup with the worker image's
Chromium and proves size, seven exact flat colours, drawn title text, zero
self-diff and a deterministic re-render — **PASS**
(`docs/briefs/evidence/eval/w14-design-fixture-1793271851.json`). The live leg
is **owed**: no vision provider key is configured and live spend was not
authorized. Thresholds were pre-registered before any run of this task and are
recorded below; they must not be relaxed after a result.

```json
{"ratioMax": 0.03, "colourTolerance": 12, "answerRatioTolerance": 0.005,
 "fixtureColourTolerance": 0}
```

## Remaining

1. Run the W14 live design leg once a vision key and spend authorization exist;
   record PASS/FAIL as-is.
2. Run a real `--compare-model` comparison on two priced models; until then
   "reproducible comparisons" is implemented but has n=0 evidence.
3. Open the dashboard in a live Grafana and confirm the panels render.

## Gate position

| W14 exit claim | State |
|---|---|
| Corpus has fixed expected outputs and a real hold-out split, enforced | **pass** — `--check` in static gates (39 / 6 / 28) |
| Reproducible model comparisons | **implemented, n=0** — runner + guards + pass rule; no live comparison run yet |
| Routing requires opt-in | **pass** — default off, policy read from `team_settings`, live-verified GET/PUT/DELETE |
| Failure never forces a downgrade | **pass (offline)** — resolver never crosses classes; missing class model keeps the primary; partial plans keep the primary |
| Unpriced models stop billing zero silently | **pass (visibility + overlay)** — a named unpriced model reports `priced: false`, `pricingSource: "unpriced"`, cost 0; operators price it via `HARNESS_RATE_CARD_JSON`; malformed cards fail loudly |
| Cost/latency dashboards | **pass (series + panels + tests), visually unverified** — six live gauges, eight panels, SQLite and PostgreSQL-gated tests; no live Grafana render |

## Limitations

* The corpus gate validates structure, never truth: a well-formed task can still
  have a weak verifier. The verifier is the task author's claim.
* `revisionCheck` covers three runtime files (engine, image gate, images plugin);
  it is not a whole-image digest.
* Held-out count (6) is a floor, not a quality bar; two of the six are infra
  boundary tasks, not model-judged, and the new design task's live leg is owed.
* No model call is made by any gate added here.
* Routing is per-RUN, not per-step: the engine takes one model per session, so a
  mixed plan gets one class. The decision is recorded on the run but not yet
  surfaced in the runs API.
* The cost gauges emit one series per (team, model); a dev database with hundreds
  of drill teams produces hundreds of series. Bound retention or aggregate before
  pointing a shared Prometheus at it.
