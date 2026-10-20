# harness-ops — operations surface (A5-S5)

Agent-agnostic operations package for the harness: evidence export, alerting,
usage webhooks, customer CI intake, chat approvals and scheduled-work
proposals. Like every A5 package it **proposes, never executes**: scheduled
and CI work goes through the same plan-approval gate as human-initiated runs.

## Install

```bash
./packages/extensions/.venv/bin/pip install -e ./packages/ops --no-deps
```

Depends on `harness-telemetry`, `harness-workflow-connectors` (plan gate,
signature schemes, idempotency), `starlette`, `httpx`, `tzdata`.

## Run

```bash
OPS_DB=ops.sqlite OPS_ARCHIVE_DB=ops-archive.sqlite OPS_FIXTURES=1 \
  ./packages/extensions/.venv/bin/python -m uvicorn harness_ops.app:app_factory --port 8484
```

`OPS_FIXTURES=1` ingests the demo fixture corpus into the archive (demos and
tests only; leave unset for pilot deployments and ingest real telemetry).
Scheduling is **not** a background loop: an operator cron calls
`harness_ops.schedule.run_due_schedules` (see `runbook.md`).

## Routes (all fail closed behind a principal)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/healthz` | liveness |
| GET | `/api/v1/ops/usage` | usage/cost snapshot (`?team=`, `?since=` window) |
| GET | `/api/v1/ops/usage/webhooks` | registered usage webhooks w/ delivery history |
| GET | `/api/v1/ops/alerts` | active alert list (member-scoped) |
| POST | `/api/v1/ops/alerts/evaluate` | run alert rules, dedupe + deliver |
| GET | `/api/v1/ops/evidence/export` | redacted evidence zip (manifest + sha256) |
| POST | `/api/v1/ops/ci/{provider}` | signed CI webhook → proposed plan |
| POST | `/api/v1/ops/chat/slack/actions` | signed approve/reject/inspect actions |
| GET/POST | `/api/v1/ops/schedules` | list / create schedules |
| POST | `/api/v1/ops/schedules/{id}/trigger` | propose due work now |
| POST | `/api/v1/ops/schedules/{id}/{action}` | enable / disable |

No `get_principal_dep` wired → 503 `AUTH_NOT_CONFIGURED`; cross-team reads →
403 `TEAM_FORBIDDEN`; errors use the HAP/1 envelope `{"error":{code,message}}`.

## Modules

- `store.py` — SQLite: webhooks, deliveries, schedules + run claims, alert
  state, one-shot approval-action bindings.
- `evidence.py` — F118 deterministic redacted zip (fixed mtimes, sorted,
  manifest, `verify_evidence_zip` → `EVIDENCE_TAMPERED/INCOMPLETE/CORRUPT`).
- `alerts.py` — F119 rules `stalled_run` (12h < 24h abandonment window),
  `blocked_run`, `expensive_run` (> $5.00), `claimed_but_rejected`; lifecycle
  `opened → reminder → resolved`, dedupe by key, delivered via F120.
- `usage.py` — F120 snapshot (streamed/settlement/overhead cost buckets,
  reworkRate as float, published definitions) + signed webhook delivery
  (HMAC `X-Harness-Signature`, 3 attempts, backoff `(0, 2, 5)s`, idempotent
  by `deliveryId`).
- `ci.py` — F128 GitHub / GitLab / CircleCI adapters (per-provider signature
  verification, normalized failure → proposed plan, replay-safe).
- `chat.py` — F129 Slack interactivity: scheme signature → one-shot token
  (TTL 900s, bound to actor/plan/action) → role gate → real orchestrator
  approve/reject; replay returns the original outcome.
- `schedule.py` — F130 hourly/daily/weekly schedules with IANA zones, DST
  `fold=0` semantics, misfire policy `skip` or `run_once` (exactly one
  catch-up), propose-only via the plan gate.

## Tests

```bash
cd packages/ops && ../../packages/extensions/.venv/bin/python -m pytest tests -q
# 77 passed; ruff: ./packages/extensions/.venv/bin/ruff check packages/ops
```

`tests/support.py` runs the app against the REAL orchestrator approve route
(in-process ASGI) and a REAL HTTP webhook receiver — no provider mocks except
the documented orchestrator test double for unknown-plan auto-vivification.

## Limits

Pilot-grade: SQLite storage, no queue, no HA. Evidence exports and usage
numbers inherit telemetry's event coverage (missing provider cost rows
understate, never overstate). See
`docs/agent-program/a5/session-5/release-blockers.md` before any rollout.
