# A5-S5 runbook — operating `harness-ops`

Scope: the S5 ops surface (evidence export, alerts, usage webhooks, CI intake,
chat approvals, schedules). Written to be runnable by an operator who has
never seen this repo, using only the commands below.

## 1. Service topology

- One Starlette ASGI app: `harness_ops.app:app_factory` (see
  `packages/ops/README.md` for routes).
- Two SQLite files: `OPS_DB` (webhooks/deliveries/schedules/alerts/actions)
  and `OPS_ARCHIVE_DB` (telemetry runs used by usage + evidence).
- No background threads. Two external drivers you must run:
  1. **Scheduling sweep** (cron, e.g. every 5 min) — proposes due work:
     ```bash
     OPS_DB=ops.sqlite ./packages/extensions/.venv/bin/python -c "
     import asyncio
     from datetime import datetime, UTC
     from harness_ops.schedule import run_due_schedules
     from harness_ops.store import OpsStore
     from harness_ops.app import make_ops_app  # to read gate config
     asyncio.run(run_due_schedules(store=OpsStore('ops.sqlite'),
                                   gate=GATE, now=datetime.now(UTC)))"
     ```
     where `GATE` is the same `ApprovePlanGate`/`ProposePlanGate` config wired
     in the app. Proposals land in the orchestrator's approval queue — the
     sweep never approves anything.
  2. **Alert evaluation** (cron, e.g. every 15 min) — `POST
     /api/v1/ops/alerts/evaluate` with an admin bearer token.

## 2. Config

| Env | Meaning | Default |
| --- | --- | --- |
| `OPS_DB` | ops state db | `ops.sqlite` |
| `OPS_ARCHIVE_DB` | telemetry archive db | `ops-archive.sqlite` |
| `OPS_FIXTURES` | `1` ingests demo fixtures — **never set in a pilot** | unset |

Auth is injected by assembly (`get_principal_dep`); if it is not wired every
route returns 503 `AUTH_NOT_CONFIGURED` — that is the safe state, not a bug.

## 3. Health check

```bash
curl -s http://127.0.0.1:8484/healthz          # {"status":"ok"}
curl -s -H "authorization: Bearer $TOKEN" \
  http://127.0.0.1:8484/api/v1/ops/usage | head -c 400
```

## 4. Upgrade

1. `git fetch` + checkout the target revision; rebuild/install wheels in the
   service venv (`verify_distribution.py` output is the supported combo).
2. Back up `OPS_DB` and `OPS_ARCHIVE_DB` (`sqlite3 <db> ".backup <db>.bak"`).
3. Restart the ASGI process and the cron entries.
4. Smoke: `/healthz`, one `/api/v1/ops/usage` read, one schedule list.

## 5. Rollback

1. Stop cron entries first (prevents proposals from the wrong version).
2. Restore both `.bak` files, reinstall the previous revision, restart, smoke.
3. Deliveries already sent cannot be unsent; check
   `/api/v1/ops/usage/webhooks` history and re-deliver manually only if the
   event's `deliveryId` was never delivered (idempotency prevents doubles).

## 6. Incident procedures

### 6.1 Webhook endpoint failing (customer receiver down)
- Symptom: `deliveries` rows `status=retrying/failed`, `responseCode` 5xx.
- Action: nothing to fix server-side — deliveries are idempotent by
  `deliveryId`; the receiver's dedupe makes re-delivery safe. Use
  `POST`-style manual re-delivery via `usage.deliver_event` with the same
  `delivery_id` once the receiver recovers (`max_attempts=3`,
  backoff `(0, 2, 5)s` is intentionally bounded).

### 6.2 Alert storm (thousands of opens)
- Alerts dedupe by `dedupe_key` (rule + runId); a storm means many distinct
  runs triggered a rule. Triage: `GET /api/v1/ops/alerts` (admin) to list;
  mute is **not** implemented — lower `expensive_run` threshold inputs by
  fixing spend, or disable the webhook (delete/disable row) if delivery
  itself is the problem. Do not delete alert state to hide the condition.

### 6.3 Evidence export failing verification
- `verify_evidence_zip` errors are explicit: `EVIDENCE_TAMPERED` (bytes
  changed after packaging), `EVIDENCE_INCOMPLETE` (manifest/counts mismatch),
  `EVIDENCE_CORRUPT` (unreadable zip). Re-export from the archive; if it
  recurs, check disk (`OPS_ARCHIVE_DB` integrity: `PRAGMA integrity_check`).

### 6.4 Schedule stuck / duplicates
- Claiming is `INSERT OR IGNORE` + a 5-minute stale-claim takeover, so a
  crashed sweep self-heals on the next tick. Duplicate proposals should be
  impossible for the same `(scheduleId, occurrence)`; if observed, capture
  `schedule_runs` rows and file a bug — do not delete rows first.

### 6.5 Orchestrator unreachable
- CI/chat/schedule proposals fail closed (`PLAN_GATE_REJECTED`, 502);
  chat approvals return an error and **no** state changes. Schedules release
  their claim so the next sweep retries. Wait for orchestrator recovery; no
  operator action required.

## 7. Support / data lifecycle

- Logs: the app logs to stderr; assembly is responsible for shipping them.
- Retention: evidence packages embed `retentionUntil` per run from telemetry.
  Deleting `OPS_DB` loses webhook/dedupe history — back it up as in §4.
- Escalation: repo `AGENTS.md` owners; this runbook + `release-blockers.md`
  are the support baseline until VR040 (support/onboarding) is addressed.
