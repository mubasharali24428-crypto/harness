# WAVE 9 — TASK DEPENDENCY GRAPH (orchestrated by ox-alpha)

Method: subagents execute tasks in parallel; ox-alpha manages, verifies every
delivery independently, and merges at graph completion. Status updates go in
this file (the graph is the tracking artifact).

```
                 ┌───────────────────────────────────────────────────┐
                 │ BASELINE 4a6c381 (wave8: obs + TLS + backup, ✅)   │
                 └───────────────────────┬───────────────────────────┘
                                         │ fan-out (no interdependencies)
   ┌──────────────┬──────────────┬───────┴──────┬────────────────┐
   ▼              ▼              ▼              ▼                ▼
┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌─────────────┐
│ A. COST   │ │ B. BUDGET │ │ C. ORGS   │ │ D. NOTIF  │ │ E. SECRETS  │
│ METERING  │ │ CAPS      │ │ INVITES + │ │ EMAIL     │ │ HARDENING   │
│ cost_ledger│ │ pre-call  │ │ ROLES UI  │ │ templates │ │ + CSP       │
│ writers   │ │ spend API │ │ S7 team   │ │ notifier  │ │ headers +   │
│           │ │           │ │ page      │ │ hook      │ │ scanning    │
└─────┬─────┘ └─────┬─────┘ └─────┬─────┘ └─────┬─────┘ └──────┬──────┘
      └─────────────┴─────┬───────┴──────┬──────┘              │
                          │ A feeds B's aggregation read-only       │
                          ▼                                         │
                    ┌─────────────────────────────────┐             │
                    │ MERGE GATE (ox-alpha)           │             │
                    │ 1. footprint audit per node     │             │
                    │ 2. rerun its tests myself       │             │
                    │ 3. exercise deliverable once    │             │
                    │ 4. full sweep → single commit   │             │
                    └─────────────────────────────────┘◄────────────┘
```

## Node briefs

### A — Cost metering (`cost_ledger` gets real writers)
- `harness_router/ledger.py` NEW: `record_usage(agent_id, role, model,
  input_tokens, output_tokens, latency_ms, run_id=None) -> LedgerRow` —
  computes cost via existing `pricing.py`, INSERTs into `cost_ledger`
  (asyncpg pool), returns the row. Fire-and-forget semantics: NEVER raises
  into the caller on ledger failure (log + continue).
- Wire into `call_model` path in `api.py`: after a successful provider call,
  record usage with tokens from the provider result.
- New routes: `GET /api/v1/models/spend?teamId=&projectId=&sinceDays=` →
  `{totals:{inputTokens,outputTokens,costUsd},byModel:[{model,costUsd,tokens}],
  byDay:[{day,costUsd}]}` (SQL GROUP BY, parameterized).
- Tests: unit (pricing math, insert shape via aiosqlite, failure-swallow),
  route test through real app. Ruff clean.

### B — Budget caps (pre-call enforcement)
- `budget.py` NEW in section4/src/harness_orchestrator/: `async check_budget(
  pool, team_id, budget_usd) -> BudgetStatus` — SUM(cost_usd)+est next call vs
  cap; `BudgetStatus(allowed: bool, spentUsd, capUsd, reason)` wire model
  camelCase per api-conventions.md.
- Enforce in orchestrator before engine spawn AND inside reviewer's
  model_router call path: over-budget ⇒ no model call, honest error event.
- Config: `HARNESS_TEAM_BUDGET_USD` env default (None = unlimited); per-team
  override column via NEW migration 0007 (`ALTER TABLE teams ADD COLUMN
  budget_usd NUMERIC(12,2) NULL`) — additive only, never edit 0001.
- Reads `cost_ledger` READ-ONLY (raw SQL against frozen table names).
- Tests: under/at/over cap matrix, unlimited default, migration applies.

### C — Org invites + roles UI (S7 team page)
- Backend (section1): `POST /api/v1/teams/{teamId}/invites` (admin-only via
  roles.can('admin')), `GET .../invites` list, `DELETE .../invites/{id}`
  revoke, `POST /api/v1/invites/redeem` (authed user joins team). Invites
  table via NEW migration 0008 (`team_invites`: id, team_id, email, role,
  token_hash UNIQUE, expires_at TIMESTAMPTZ, redeemed_at, created_at;
  index on token_hash). Token = secrets.token_urlsafe(32), stored SHA-256
  hashed; expiry 7d; redemption atomic (single-statement UPDATE...WHERE
  token_hash= AND redeemed_at IS NULL RETURNING).
- Frontend (section7): `/settings/team` page — members list w/ roles,
  invite form (email+role select), pending-invites table with revoke.
  Uses real fetch to live API, no mocks. Dark-header/light-card theme kept.
- Tests: backend invite lifecycle (mint→list→redeem→revoke, wrong-role
  rejected, expired rejected, double-redeem rejected) + vitest for the page.

### D — Email notifications (notifier hook)
- Extend `section1/src/harness_identity/notifier.py`: template registry
  {run_finished, review_failed, pr_opened} with subject+HTML+plain bodies;
  async `send_template(to, template, context)`; outbox table via NEW
  migration 0009 (`notification_outbox`: id, team_id, to_email, template,
  payload JSONB, status queued/sent/failed, attempts, last_error, created_at,
  sent_at; index on status).
- Delivery worker: drains queued rows with backoff (3 tries), marks failed
  honestly; dev mode (no SMTP env) logs and marks sent so tests pass offline.
- Emit hooks: S4 publishes run_finished/review_failed events already —
  add a subscriber in deploy/runners/services.py::app_orchestrator that
  enqueues outbox rows (fire-and-forget task).
- Tests: template rendering, outbox enqueue→drain→sent, retry→failed path,
  offline dev mode. No real SMTP in CI.

### E — Secrets hardening + security headers + scanning
- KeyVault write-path hardening in section1 (find it via grep; encrypt-at-
  rest verify + key_hint derivation audit). Audit that no secret value ever
  appears in logs/audit rows (grep-driven sweep + regression test asserting
  redaction).
- Security headers middleware on identity app: X-Content-Type-Options,
  X-Frame-Options DENY, Referrer-Policy strict-origin-when-cross-origin,
  HSTS behind flag (off on http dev), CSP report-style default for HTML
  responses. Apply same middleware import in S4/S5/S6/S8 apps (import-level
  change only, one line each, APPEND pattern).
- CI: add pip-audit job (continue-on-error WARN first wave) + gitleaks scan
  step to .github/workflows/ci.yml (APPEND job, don't restructure).
- Tests: header assertions through TestClient on identity app, redaction
  regression test. Compose config still valid.

## FILE OWNERSHIP (hard boundaries — agents must not cross)

| Node | Owns exclusively |
|------|------------------|
| A | `section3/**` (new `ledger.py`, `api.py` append), new `section3/tests/test_ledger.py` |
| B | `section4/src/harness_orchestrator/{budget.py,engine.py}`, `section4/api/authz-adjacent files ONLY where enforcing`, `section1/migrations/0007_budget.sql`, `section4/tests/unit/test_budget.py` |
| C | `section1/src/harness_identity/{invites.py,api.py}`, `section1/migrations/0008_team_invites.sql`, `section1/tests/test_invites.py`, `section7/app/settings/team/page.tsx` (+ its small fetch helper if needed) |
| D | `section1/src/harness_identity/notifier.py`, `section1/migrations/0009_notification_outbox.sql`, `section1/tests/test_notifications.py`, `deploy/runners/services.py` (APPEND block in app_orchestrator only) |
| E | `section1/src/harness_identity/security_headers.py` (new), `section1/src/harness_identity/app.py` (middleware line), one-line imports in S4/S6/S8/S5 app factories, `.github/workflows/ci.yml` (append jobs), KeyVault file in section1 |

**Hotspot rules:**
- `section1/migrations/` — numbered files are node-private (0007=B, 0008=C,
  0009=D); NOBODY touches 0001–0006 or migrate.py ordering logic.
- `section1/tests/` — each node adds its own test FILE only; never edit
  another node's file; conftest.py is FROZEN this wave (if you need a
  fixture, define it locally in your test file).
- `deploy/runners/services.py` — ONLY node D appends (one block,
  app_orchestrator); nobody else touches it this wave.
- `section1/src/harness_identity/app.py` — ONLY node E (include_router lines
  for invites are added by C INSIDE ITS OWN invites router module registration
  — C coordinates by exposing `router`; ox-alpha wires registration at merge
  if both touch app.py).

**Merge order at gate:** E (infra/middleware) → A → B → C → D (D last since
it appends to services.py which depends on nothing else changing there).

## Node status (updated by ox-alpha)

| Node | Agent | State | Verification | Merged |
|------|-------|-------|--------------|--------|
| A cost-metering | — | PENDING | | |
| B budget-caps | — | PENDING | | |
| C orgs-invites-ui | — | PENDING | | |
| D notifications | — | PENDING | | |
| E secrets-headers | — | PENDING | | |

## Merge checklist (gate exit criteria)

- [ ] A: S3 suite green + spend endpoint returns aggregated JSON against live PG
- [ ] B: S4 suite green + over-budget run rejected with honest error event
- [ ] C: S1 suite green + invite mint→redeem proven against live PG; team page renders
- [ ] D: S1 suite green + outbox drain produces 'sent' rows offline
- [ ] E: header middleware verified via TestClient; pip-audit/gitleaks jobs appear in CI yaml
- [ ] Full sweep green across all sections + ruff clean everywhere
- [ ] GAP Part 10 ledger rows + STATUS.md wave entry
- [ ] Single wave commit pushed; CI all jobs green
