# MISSION 10 — TASK GRAPH (ATLAS · ALEX_TEAM protocol)

Method: ALEX_TEAM three-tier org. ATLAS (this session) authors the graph,
dispatches one delegation per node, gates every delivery personally (footprint
audit → own suite rerun → live probe), runs the lead review ladder, wires
cross-node items, merges. Baseline: abcab51.

```
                    ┌─────────────────────────────────────────────────┐
                    │ BASELINE abcab51 (wave9 merged, CI all-green ✅) │
                    └───────────────────────┬─────────────────────────┘
                                            │ fan-out (10 lanes, 2 squads)
        ┌───────────────────────────────────┼───────────────────────────────────┐
        │ SQUAD ALPHA (LEAD-ALPHA reviews)  │ SQUAD BETA (LEAD-BETA reviews)    │
        │                                   │                                   │
        │ A1 BYO-keys API ─┐ PAIR×2         │ B1 BILLING API ─┐ PAIR×2           │
        │                  ├ (contract M10-C1)│                ├ (contract M10-C3)│
        │ A2 KEYS PAGE ────┘                │ B2 BILLING PAGE ┘                  │
        │ A3 COST DASHBOARD (eats /spend)   │ B3 ONBOARDING REVIVAL              │
        │ A4 RUNS INDEX API ─┐ PAIR×2       │ B4 WAL-G PITR                      │
        │                    ├(contract M10-C2)│            │                     │
        │ A5 RUNS PAGE ──────┘              │ B5 LOGIN RATE LIMIT                │
        └───────────────────────────────────┴───────────────────────────────────┘
                                            ▼
                    ┌─────────────────────────────────────────────────┐
                    │ GATES (ATLAS): footprint → rerun → live-probe    │
                    │ LEAD LADDER: spec PASS → quality APPROVED        │
                    │ ATLAS WIRINGS: router registrations, nav links   │
                    │ MERGE: full sweep → ledger/STATUS → 1 commit     │
                    │ ROLLBACK: git revert <merge-sha> (single commit) │
                    └─────────────────────────────────────────────────┘
```

## Interface contracts (authored by ATLAS — pairs implement BOTH sides)

**M10-C1 (A1 ↔ A2)** — `/api/v1/keys` (bearer auth):
- `POST /api/v1/keys` body `{label, provider, key}` → 201 `{id, label, provider, keyHint, createdAt}` (raw key NEVER returned again)
- `GET /api/v1/keys?teamId=` → `{items:[{id,label,provider,keyHint,createdAt,revokedAt}]}`
- `DELETE /api/v1/keys/{id}` → 204 (soft-revoke sets revoked_at)
- Errors use contract `error_body()` envelope; admin/engineer can write, viewer read-only (roles.can).

**M10-C2 (A4 ↔ A5)** — `/api/v1/orchestrator/runs` (bearer auth):
- `GET /api/v1/orchestrator/runs?projectId=&status=&limit=&offset=` (limit≤100 default 25)
- → `{items:[{id, projectId, status, createdAt, startedAt, finishedAt}], total, limit, offset}`
- Team-scoped via existing `_authz_dep`-style ownership join; read role required.

**M10-C3 (B1 ↔ B2)** — `/api/v1/billing` (bearer auth):
- `GET /api/v1/billing/state` → `{enabled, plan, quota:{monthlyBudgetUsd, usedUsd}, source}`
  (`enabled=false` + `source:"flag-off"` when HARNESS_BILLING unset — page renders upsell, no errors)
- `PUT /api/v1/billing/plan` body `{plan}` admin-only → updated state (dev/local fallback stores plan row; Stripe adapter behind `HARNESS_BILLING=stripe` + key present, never imported unless enabled)

## Node briefs

- **A1 BYO-keys API (S1)**: CRUD over frozen `api_keys` table (0001): encrypt with existing KeyVault crypto, key_hint per convention, soft revoke. Append `ApiKey` ORM class to models.py ONLY if missing (append-only). NEW router module `section1/src/harness_identity/api_keys.py`; exposes `router`. Tests `test_api_keys_routes.py` (mint→list→revoke, hint never leaks full key, role gates).
- **A2 Keys page (S7)**: `section7/app/settings/keys/{page.tsx,keysApi.ts}` per M10-C1. Form (label/provider/key), list w/ hints + revoke, inline errors, a11y labels, theme-consistent.
- **A3 Cost dashboard (S7)**: `section7/app/dashboard/cost/{page.tsx,costApi.ts}` consuming LIVE `GET /api/v1/models/spend` (verified wave9): totals cards, byModel bar list, byDay sparkline/table. Graceful empty state.
- **A4 Runs index API (S4)**: NEW `section4/src/harness_orchestrator/runs_index.py` (own APIRouter, M10-C2 shape); team-scope via projects/teams join; parameterized SQL; tests `tests/unit/test_runs_index.py`. APPEND one include line in `deploy/runners/services.py::app_orchestrator` (sole owner of services.py this mission).
- **A5 Runs page (S7)**: `section7/app/runs/{page.tsx,runsApi.ts}` per M10-C2: filter selects (project/status), paginated table, status pills, relative times.
- **B1 Billing API (S1)**: NEW `billing.py` + migration `0010_billing_plans.sql` (plans/quotas additive) + `test_billing.py`. Flag `HARNESS_BILLING` (unset=off → enabled:false). Local fallback persists plan; Stripe adapter = thin injectable client, ZERO new package installs, never imported unless flag on. Quota read joins teams.budget_usd (0007) + cost_ledger SUM.
- **B2 Billing page (S7)**: `section7/app/settings/billing/{page.tsx,billingApi.ts}` per M10-C3: plan display/change (admin), quota meter vs budget, disabled-state upsell.
- **B3 Onboarding revival (S10+S7)**: inspect `section10/src/harness_onboarding/steps.py`; NEW `section10/src/harness_onboarding/api.py` exposing `make_app()` (simple_app pattern, own router `/api/v1/onboarding/checklist`) + `section7/app/onboarding/{page.tsx,onboardingApi.ts}` interactive checklist. Section10 has no test venv: pure-function checks via section1's interpreter are FORBIDDEN (no cross-imports) — instead ship `section10/tests/test_steps.py` runnable with any pytest on PYTHONPATH=src, and say so honestly.
- **B4 WAL-G PITR (deploy)**: NEW `deploy/walg/`: `backup-walg.sh` + `restore-walg.sh` (env-driven: WALG_S3_PREFIX, PGDSN; pg-backup vs wal push split; PITR restore to target time) + compose `walg` PROFILE service appended to `deploy/docker-compose.yml` (sole owner this mission) + APPEND "WAL-G operation" section to `docs/runbooks/backup-and-pitr.md`. Honest limits: no S3 creds locally ⇒ validate with WALG availability check (`wal-g --version` guard) + shell syntax + dry-run config dump; document exactly what a credentialed operator must run.
- **B5 Login rate limit (S1)**: NEW `ratelimit.py` (fixed-window counters in Postgres, migration `0011_rate_buckets.sql`) guarding auth/login + OTP request routes; factory `install_rate_limit(app)`; ATLAS wires at merge. Tests: burst blocked → window reset → allowed; per-IP+route keying; DB-down fails OPEN (availability > strictness) with warning.

## FILE OWNERSHIP (exactly one owner per file)

| Owner | Paths |
|---|---|
| A1 | section1/src/harness_identity/api_keys.py (NEW), section1/tests/test_api_keys_routes.py (NEW), models.py append-if-missing |
| A2 | section7/app/settings/keys/** (NEW) |
| A3 | section7/app/dashboard/cost/** (NEW) |
| A4 | section4/src/harness_orchestrator/runs_index.py (NEW), section4/tests/unit/test_runs_index.py (NEW), deploy/runners/services.py app_orchestrator block ONLY |
| A5 | section7/app/runs/** (NEW) |
| B1 | section1/src/harness_identity/billing.py (NEW), section1/migrations/0010_billing_plans.sql (NEW), section1/tests/test_billing.py (NEW) |
| B2 | section7/app/settings/billing/** (NEW) |
| B3 | section10/src/harness_onboarding/api.py (NEW), section10/tests/test_steps.py (NEW), section7/app/onboarding/** (NEW) |
| B4 | deploy/walg/** (NEW), deploy/docker-compose.yml (append walg profile), docs/runbooks/backup-and-pitr.md (append section) |
| B5 | section1/src/harness_identity/ratelimit.py (NEW), section1/migrations/0011_rate_buckets.sql (NEW), section1/tests/test_ratelimit.py (NEW) |
| ATLAS at merge | section1 app.py (router registrations A1/B1/B5), services.py onboarding factory block (B3), S7 nav links, STATUS/GAP ledger |

HOT RULES: migrations numbered per-node (0010=B1, 0011=B5); conftest.py FROZEN;
contracts/ FROZEN; no section imports outside harness_contracts; siblings' dirty
files are expected — ignore them.

## Node status (updated by ATLAS)

| Node | Agent | State | Gate verdict |
|---|---|---|---|
| A1 keys-api | sa-0-84846778 | DONE | ✅ ATLAS rerun: 9/9 + ruff clean on owned files; footprint = 2 new files (models.py untouched — no append needed); encrypted-at-rest + hint-leak assertions present |
| A2 keys-page | sa-0-5cd7487d | DONE | ✅ shared S7 gate: tsc OK, vitest 65/65; M10-C1 client shapes + a11y patterns verified in report |
| A3 cost-dashboard | sa-0-72c44aa7 | DONE | ✅ shared S7 gate: tsc OK, vitest 65/65; client shapes verified against real summarize_spend wire format |
| A4 runs-index-api | sa-0-7f9addd5 (re-dispatch x1: first attempt 43m explore then provider timeout, ZERO writes) | DONE | ✅ ATLAS rerun: test_runs_index 11/11, ruff clean, full S4 green except known LIVEPROBE flake; team-scope via join filter, startedAt/finishedAt derived from agent_events (frozen table lacks columns), expanding bindparams throughout |
| A5 runs-page | sa-0-abc6708e (+fix-loop sa-0-17bc15ea) | REVIEW LOOP | lead verdict: quality CHANGES — status filter enum fabricated vs real AgentState vocabulary (idle/planning/awaiting_plan_approval/executing/…), headline filter functionally broken; ~5-line fix dispatched; paging/a11y praised |
| B1 billing-api | sa-0-2cf37a67 | DONE | ✅ ATLAS rerun: test_billing 11/11 (full S1 sweep green at gate), ruff clean, migration 0010 style matches siblings w/ explanatory header; flag-off/enabled/quota-math/month-scoping proven via suite's ASGI transport; disclosed design notes for lead review (local row stays system-of-record in stripe mode) |
| B2 billing-page | sa-0-96814f1c (+fix-loop sa-0-0dd18604) | REVIEW LOOP | lead verdict: spec FAIL (null-quota crash on contract's flag-off payload, page.tsx:82) — fix dispatched; ATLAS gate had covered tsc/vitest but not render-vs-payload | |
| B3 onboarding | sa-0-e1ee3262 | DONE | ✅ ATLAS rerun: test_steps 15/15 (PYTHONPATH=src recipe, no section venv — honest), S7 gate 73/73 incl. new page; api.py was baseline-owned file EXTENDED per brief; done-derivation honest (no fabricated completion signals) |
| B4 walg-pitr | sa-0-461180a7 (+fix-loop sa-0-dbd7f13c) | REVIEW LOOP | lead verdict: quality CHANGES — dead WALG_MODE/WALG_LOOP env knobs (compose/runbook document what script ignores) + Dockerfile checksum can never pass (rename-before-verify); fixes dispatched; honesty of validated/not-validated split praised | |
| B5 rate-limit | sa-0-2bce4bb9 | DONE | ✅ ATLAS rerun: 9/9 + ruff clean; footprint = 3 owned files; 429+Retry-After verified in middleware source, single-statement ON CONFLICT upsert w/ window reset, DB-failure fail-open exception-guarded per spec; migration 0011 style matches siblings |

## Merge checklist (gate exit criteria)

- [ ] All 10 footprints == owned paths
- [ ] Each suite rerun green by ATLAS (S1, S4, S7 vitest/tsc, section10)
- [ ] Live probes: keys mint/revoke via TestClient; spend page fetch shape; runs endpoint pagination; billing flag-off state; rate-limit burst block
- [ ] Lead ladder: ALPHA squad spec-PASS + quality-APPROVED; BETA ditto
- [ ] Full sweep green + ruff clean + compose config valid (incl. walg profile mode)
- [ ] GAP Part 10 rows + STATUS.md entry + evolution ledgers audited
- [ ] Single mission commit pushed; CI all jobs green
