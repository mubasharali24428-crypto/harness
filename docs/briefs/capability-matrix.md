# Capability matrix — single source of truth (Master prompt part 36.010)

Version 1 · 2026-10-18 · Session 1. Source: master prompt `docs/master-prompts/harness-multiplayer-master-build-prompt.md` (61 parts).
State: `verified` = every directive implemented or out-of-scope AND an invariant test/evidence file exists from a run in the cited session
(the 9/10 bar in `path-to-9-plan.md`) · `implemented` = built, not yet fully evidenced · `partial` · `missing` · `blocked` = needs external evidence.
States were assigned from code + this session's fresh runs (`evidence/s1/verification.md`); where a row says "not verified" nothing was run for it.
Row granularity is one row per part (directive groups are listed in the master prompt); later sessions split rows when they work a part.

| # | Requirement | Owner | State | Acceptance test / evidence |
|---|---|---|---|---|
| 01 | Role & outcome: shared-run pilot | all | partial | roadmap phase 1; residual gaps S2 |
| 02 | Product direction | docs | partial | `docs/product-boundary.md`; not verified against every directive |
| 03 | Pilot success metrics (≥5 tasks/side) | s4 `pilot_metrics.py` | blocked | `section4/tests/unit/test_pilot_metrics.py`; no real pilot data exists |
| 04 | Inherited baseline | all | verified | `evidence/s1/verification.md` (all suites fresh) |
| 05 | Known-gap checklist | all | verified | docs/briefs/evidence/s2/part05-gap-checklist.md (9 pass, 1 partial: live presence, deferred to Session 4) |
| 06 | Safety & trust (deny-by-default, cross-team) | s4 `collab.py`, `authz.py` | verified | test_collab_denial_audit.py, test_collab_rules_s2.py; docs/briefs/evidence/s2/lane-a.md |
| 07 | High-impact approval (digest-bound) | s4 `staging.py`, `collab.py` | verified | test_isolation_and_staging.py, test_collab_backend.py; smoke --otp 1 passed on integrated tree; docs/briefs/evidence/s2/acceptance-core-flow.md |
| 08 | Redirect & handoff | s4 `collab.py` | verified | test_collab_backend.py, test_collab_rules_s2.py (simultaneous guidance, reasons); docs/briefs/evidence/s2/acceptance-core-flow.md |
| 09 | Run isolation (container) | s4/s5 | verified | P0 18 passed fresh (`evidence/s1/verification.md`); `docs/briefs/p0-container-isolation.md`; S2: sandbox T15–T17 closed, T7b on Seatbelt (`evidence/s2/lane-b.md`, section5 230 passed). Linux Landlock unrun; local runner stays dev-grade |
| 10 | Cancellation & lifecycle | s4 engine | verified | test_lifecycle_{table,races,recovery}.py, docs/lifecycle.md; provider-call cancellation limit documented; docs/briefs/evidence/s2/lane-a.md |
| 11 | Events & audit | s1/s4 | verified | migration 0036 hash chain + test_audit_chain_pg.py, export/push tests, docs/audit-and-events.md; limits in its section 8; UI does not yet consume WS push |
| 12 | Shared experience (UI) | s7 | implemented | `collab.test.tsx` (vitest 540 passed); `multiplayer.e2e.ts` legs pass (`evidence/s2/lane-c.md`). Gaps: item 009 terminal details and responsive layout untested; no screen-reader check; polling data path until lane A WS push is wired |
| 13 | Architecture review | docs | verified | `docs/architecture-map.md` |
| 14 | Contract design | contracts, scripts | partial | `scripts/spec_drift.py` (6 services), `docs/contracts-and-limits.md`; limits NOT ENFORCED listed there; no per-event schema version |
| 15 | Implementation method | docs | implemented | `docs/briefs/stable-prompt-prefix.md` |
| 16 | Agent operating model | docs | implemented | `stable-prompt-prefix.md` (method) |
| 17 | Suggested agent lanes | docs | implemented | Session 1 used lanes A–D (`path-to-9-session1-prompt.md`) |
| 18 | Context budget | docs | implemented | `stable-prompt-prefix.md` |
| 19 | Prompt cache / token efficiency | docs | implemented | `stable-prompt-prefix.md` (stable prefix first) |
| 20 | Session plan overview | docs | verified | `path-to-9-plan.md` (carried from earlier ranking) |
| 21 | Session 1 discovery | docs | verified | `multiplayer-readiness.md` (carried) |
| 22 | Backend safety | s4 | verified | lifecycle + workspace lifecycle tests (test_workspace_lifecycle_s2.py); docs/briefs/evidence/s2/lane-a.md |
| 23 | Backend evidence | s4 | verified | test_lifecycle_recovery.py (crash/restart/reclaim), live-PG chaos 6 passed; docs/briefs/evidence/s2/lane-a.md |
| 24 | Shared workspace | s7 | implemented | `evidence/s2/lane-c.md`; gaps: no UI to file/amend proposals, no decision timestamp or amend event in API, plain-language error mapping missing |
| 25 | End-to-end flow | s7/deploy | implemented | two-user proof: see `evidence/s1/otp-e2e.md` |
| 26 | Integration | deploy | implemented | `deploy/pilot/run_smoke.py` (stand-in worker; real engine via `--real`) |
| 27 | Acceptance: core flow | all | verified | docs/briefs/evidence/s2/acceptance-core-flow.md (10 directives mapped; smoke --otp 1 passed on integrated tree) |
| 28 | Acceptance: operations | deploy, s1, s4 | partial | `evidence/s2/lane-d.md`: `alerts.yml` loads (promtool 15 rules); compose-up unverified; backup/WAL alerts inert (no emitter); no Alertmanager |
| 29 | Security review | docs | partial | `docs/briefs/threat-model.md`: 18 threats, 11 gaps (high: T15) |
| 30 | Quality & testing | all | verified | fresh suites, `evidence/s1/verification.md` (carried 9; one TS2344 and two section4 flakes noted) |
| 31 | Data & privacy | docs / s1 | partial | mapping done: `docs/data-map.md`; enforcement missing (S8) |
| 32 | Release strategy | deploy | implemented | `docs/runbooks/pilot.md`, `test_pilot_flag_rollback.py` (8 passed), `alerts.yml`. `HARNESS_PILOT_TEAMS` is an allowlist (empty = all teams open). Pilot-counter alerts wait on lane A metrics; staged rollout S10 |
| 33 | Checkpoint template | docs | verified | `docs/briefs/checkpoint-template.md` |
| 34 | Final deliverables | docs | implemented | `stable-prompt-prefix.md` |
| 35 | Final response format | docs | implemented | `stable-prompt-prefix.md` |
| 36 | Product boundary & maturity | docs | verified | `docs/product-boundary.md`, this file |
| 37 | Organizations & identity | s1 | partial | teams, OTP, SSO config exist; no orgs/step-up/emergency access; live SSO/Stripe gated |
| 38 | Permissions & policy model | s4 `authz.py` | partial | role checks only; no policy engine/inheritance/preview (S3) |
| 39 | Workspace & project model | s1/s4 | partial | projects exist; no templates/lifecycle (S3) |
| 40 | Session & run domain model | s4 | partial | run = session today (S4) |
| 41 | Presence & participation | — | missing | roster only (`collab.roster`); no presence (S4) |
| 42 | Simultaneous control & conflicts | s4 | partial | single leader; no control leases (S4) |
| 43 | Collaborative discussion | — | missing | guidance only; no comments/mentions (S5) |
| 44 | Tasks, plans, execution | s4 `plan_gate.py` | partial | `test_plan_gate*.py`; no versioned plans (S6) |
| 45 | Agent management | s4 | partial | no registry/spawn limits (S4) |
| 46 | Real-time delivery | s1 eventbus | partial | WS replay cursors tested in s1; collab uses polling (S5) |
| 47 | Notifications & user control | s1 | partial | outbox only; no prefs/digests UI (S5) |
| 48 | Files, artifacts, versions | s4 `diffstore.py` | partial | no artifact store/lineage (S6) |
| 49 | Integrations & extensibility | s1/mcp | partial | GitHub, Stripe, SSO; no signed outbound webhooks (S7) |
| 50 | Search, history, export | s7 | partial | repo-tree search only; no authorized search/export (S6) |
| 51 | Administration & governance | s7 | partial | settings pages; no admin console (S7) |
| 52 | Security & privacy program | docs | blocked | threat model done; pentest external |
| 53 | Reliability & failure handling | deploy | blocked | PITR never run on real object store; migrations rollback = `pg_restore` only |
| 54 | Scalability & performance | s4 | partial | `docs/benchmarks`; claim cap now enforced (lane C); multi-host not designed |
| 55 | Observability & support | s1/s4 | partial | request id in logs; metrics gauges added; no SLOs/dashboards |
| 56 | Accessibility, localization, clients | s7 | partial | no i18n, no reduced-motion handling found (S5/S10) |
| 57 | Billing, quotas, cost controls | s1 | blocked | code exists; live Stripe never driven; tier caps unenforced |
| 58 | Compliance & data lifecycle | docs | blocked | no legal hold/residency/deletion; counsel review external |
| 59 | Rollout, migration, compatibility | deploy | partial | flag + rollback runbook; staged rollout S10 |
| 60 | Full product roadmap | docs | verified | `docs/roadmap.md` |
| 61 | Full product acceptance | all | blocked | ledger = this file; acceptance run is S10 |

Counts (Session 2 interim, lane A pending): verified 9 · implemented 15 · partial 29 · missing 2 · blocked 6. Session 2 changed rows 09, 12, 24, 28, 32 only; rows 01, 02, 05–08, 10, 11, 22, 23, 27 wait on lane A and integration.
plus the fresh run; 09 re-run fresh. Nothing else is claimed verified.
