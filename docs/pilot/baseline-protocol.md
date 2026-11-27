# Pilot baseline protocol (G-L part 03) — 2026-11-21 (P9)

> Status: PROTOCOL (agent-prepared, P9). The first measured week needs a real
> team (owner part). This document is the method; the tool is
> `section4/src/harness_orchestrator/pilot_metrics.py`; the facilitator guide
> is `docs/briefs/pilot-baseline-guide.md`. Consistent with it; this file adds
> the pilot-side operational protocol.

## What is measured (the five rubric metrics, from existing tables)

| Metric | Source | Definition |
|---|---|---|
| Acceptance rate | audit rows `review.approved` per finished run | distinct runs approved / finished runs (proxy; the human sheet remains the truth) |
| Time-to-first-output | `agent_events` run start → first tool-call artifact/`run.started` delta | run creation → first qualifying completion/failure/PR event or `run.stopped` |
| Cost per task | `cost_ledger` rows per run | sum(microUSD)/1000000 per completed run |
| Intervention rate | `review.approved`/`review.rejected` + plan-edit audit rows / runs | human touches per run |
| Denials per run | `agent_events` denial events (`denied` outcomes, W27 shape) per run | gate/loop-guard denials per run |

## How (dry run on RC, labelled synthetic)

1. Seed the RC stack with the demo seed (zero provider keys).
2. Run the metrics CLI: `HARNESS_DATABASE_URL=<rc-dsn> section4/.venv/bin/python -m harness_orchestrator.pilot_metrics <teamId> --since <d1> --until <d2>`.
3. Every number from a dry run is labelled **synthetic** in the dashboard
   panel and in any doc; it proves the plumbing, never a result.
4. Dashboard: import `deploy/observability/dashboards/harness-cost-latency.json`;
   the five metrics live in the cost panel (Grafana datasource bound at import
   — step3-operations-checklist §1 documents the two gotchas).

## Baseline week (the real one, owner-gated)

- Week 0 (no harness): the manual sheet from the facilitator guide; ≥5 tasks.
- Week 1 (pilot): ≥5 comparable tasks through the harness; recorder +
  reviewer named; selection rule written BEFORE results; exclusions recorded.
- Acceptance: `claimable: true` requires baselineN ≥5 AND pilotN ≥5; even
  then descriptive only (median/acceptance), never causation/ROI claims.
