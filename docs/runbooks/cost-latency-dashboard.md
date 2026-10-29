# Cost and latency dashboard (W14)

Dashboard JSON: `deploy/observability/dashboards/harness-cost-latency.json`
(uid `harness-cost-latency`). It is loaded the same way as the operations
overview — import it in Grafana and bind the `DS_PROMETHEUS` template variable
at import time.

**Verification status:** structurally checked in
`section1/tests/test_deploy_observability.py`
(`test_cost_latency_dashboard_covers_metering`), with series covered by a SQLite
test and a PostgreSQL-gated test. Visually verified on 2026-10-29 in a throwaway
Grafana 11.2.0 wired to this stack's Prometheus: the dashboard imported, every
panel query returned data, and the screenshot is
`docs/briefs/evidence/eval/w14-dashboard-grafana.png`. That run also exposed two
panel bugs, now fixed: zero-cap teams were counted as "at or over cap", and the
utilisation panel divided by a clamped zero cap (fake 10000%).

## Series it reads

Six gauges are refreshed by the orchestrator's DB poller
(`HARNESS_METRICS_DB_POLL_SECONDS`, default 15s) from the frozen
`cost_ledger`:

| Series | Meaning |
|---|---|
| `harness_team_spend_usd_month{team_id}` | Month-to-date spend, DB-clock month boundary |
| `harness_team_budget_usd{team_id}` | Resolved cap: `teams.budget_usd` > `billing_plans.monthly_budget_usd` > `HARNESS_TEAM_BUDGET_USD`; absent = unlimited |
| `harness_model_cost_usd{team_id,model}` | Whole-ledger spend per team and model |
| `harness_model_calls{team_id,model,role}` | Metered calls per team, model and agent role |
| `harness_model_latency_p50_seconds{team_id,model}` | Rolling-window p50 (PostgreSQL only) |
| `harness_model_latency_p95_seconds{team_id,model}` | Rolling-window p95 (PostgreSQL only) |

Attribution is the frozen chain `cost_ledger.run_id -> agent_runs ->
projects -> teams`. A ledger row whose run id does not resolve is reported as
`team_id="unattributed"` rather than dropped — if that series is non-zero, the
metering writer is not resolving a run and needs attention.

The two cost/call series are **gauges, not counters**: releasing a budget
reservation deletes reserved ledger rows, so a decrease is a real event. Do not
wrap them in `rate()` and expect reset semantics.

Cardinality: one series per `(team_id, model)` pair, plus one per team for
spend/cap. A local database seeded by hundreds of drills produces hundreds of
series; before pointing a shared Prometheus at it, either drop the stale drill
teams or aggregate with `sum without (team_id)`.

## Knobs

- `HARNESS_METRICS_DB_POLL_SECONDS` — poll interval; `<=0` disables polling
  (series stay at their last values / empty).
- `HARNESS_COST_METRICS_WINDOW_HOURS` — latency-quantile window, default 24.
  Non-numeric and non-positive values fall back to 24 instead of blanking the
  series.

## Reading the panels

- **Spend vs cap** only shows teams that have a resolved cap. A team missing
  from the cap series is unlimited, not free.
- **Budget utilisation** only plots teams with a POSITIVE cap; a zero cap is a
  blocking gate, not a percentage, so those teams show in the "at or over cap"
  count instead. In a dev database where every cap is zero, the panel honestly
  reads "No data".
- **Spend vs cap** shows the top 10 spenders; idle teams are omitted from the
  legend but still counted by the cap panels.
- **Latency p50/p95** absent on a non-PostgreSQL deployment is expected: the
  rollup uses `percentile_cont`.
- **Spend by model** includes unpriced models at their ledger cost (zero).
  Unpriced visibility lives in the router's `/estimate-cost` response
  (`priced: false`, `pricingSource: "unpriced"`), not in these panels.

## Cost is not a billing statement

The panels read the same ledger the budget gate enforces against. A mismatch
between the panel and an invoice means the rate card (`HARNESS_RATE_CARD_JSON`)
or the catalog is wrong — fix the rate, backfill, and re-check; do not
reconcile by editing the dashboard.
