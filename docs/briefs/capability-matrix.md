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
| 05 | Known-gap checklist | all | verified | docs/briefs/evidence/s2/part05-gap-checklist.md (9 pass, 1 partial: live presence, closed in Session 4 by part 41) |
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
| 37 | Organizations & identity | s1 | verified (cap 8) | orgs, roles, invites, guests, service identities, revocation: test_orgs.py (22) + live-PG test_session3_pg.py; SSO login now records session assurance; step-up and emergency access via part 38; live SSO/Stripe unproven (external). docs/organizations.md, evidence/s3/lane-o.md |
| 38 | Permissions & policy model | contracts `policy_engine.py`/`policy_enforce.py`, s1 `policy.py` | verified | engine, custom roles, preview, decision log, step-up, emergency access (test_policy_engine.py, test_policy_api.py); shared gate classifies every identity and orchestrator route and enforces org policy for org-linked teams (test_policy_gate.py, test_policy_gate_orchestrator.py, live PG test_session3_pg.py). Limits: additive to legacy checks, WebSocket and `/orgs/*` not engine-gated. docs/permissions.md, evidence/s3/lane-p.md, evidence/s3/part38-enforcement.md |
| 39 | Workspace & project model | s1/s4 | verified | workspaces, members, settings versions, templates, allowlisted bindings, pause/archive/restore/delete; run-start gate in s4 (test_workspaces_*.py 28, test_workspace_gate.py 10, live PG). Known gaps (no purge job, running run not stopped on archive, quota race, residency is a label): evidence/s3/lane-w.md |
| 40 | Session & run domain model | s1 `sessions.py`, s4 `session_gate.py` | verified | session separate from run, members/roles/agents, templates (versioned), resume, parent/child links, soft delete + retention + purge; run start and queue claim refuse non-active sessions (test_sessions_*.py 47, test_session_gate.py 13, live PG test_session4_pg.py). Gaps: no purge scheduler, stop/fail not distinguishable in the event log, running runs not stopped on pause/archive, no owner transfer. docs/sessions.md, evidence/s4/lane-s.md |
| 41 | Presence & participation | s4 `presence.py`/`participation.py` | verified | bucketed presence, per-user privacy, team off/limited, multi-device merge, fake-clock expiry, participant cap `SESSION_FULL` (advisory lock; concurrent joins tested on real PG), observe/participate/leave/mute/rejoin, announcement text, UI roster with aria-live (test_presence.py 18, test_participation.py 15, controlPanels.test.tsx). Gaps: per-team setting only, no heartbeat rate limit beyond device cap, expiry announced on next poll. docs/presence.md, evidence/s4/lane-p.md, lane-u.md |
| 42 | Simultaneous control & conflicts | s4 `control.py`, `collab.py` | verified | exclusive timed lease (acquire/renew/release/transfer/revoke/expiry/disconnect), request queue, non-holder suggestions queued for the holder, versioned intent with `expectedVersion`, conflict view, resolution voids approvals; races tested incl. real PG (test_control.py 44, live PG test_session4_live_pg.py); UI accept/reject + control + conflict views, smoke passes. Gaps: no lease sweeper, intent is recorded state not engine-enforced, no intent editor in the UI. docs/control.md, evidence/s4/lane-c.md |
| 43 | Collaborative discussion | s4 `discussion.py`, s7 | verified | comments on runs/sessions, bounded replies, mentions limited to people who can see the scope, closed reactions, re-authorized references, promote-to-guidance (steer) via the control queue, history, edit window, tombstones, moderation, search, export, retention (test_discussion*.py 53 on sqlite, live PG test_session5_live_pg.py; UI discussion.test.tsx 52). Gaps: purge job not scheduled; search is an unindexed LIKE scan over discussion only; no write rate limits; UI has no reference creation, settings screen, moderation queue or @name autocomplete; no session page in section7 |
| 44 | Tasks, plans, execution | s4 `plan_gate.py` | partial | `test_plan_gate*.py`; no versioned plans (S6) |
| 45 | Agent management | s1 `agents.py`, s4 `agent_tree.py` | verified | versioned registry with approval workflow, selection, spawn limits (depth/children/budget; concurrency tested on real PG), delegation bounded by the user's effective permissions, scoped assignments, tool-call ledger without hidden reasoning, pause/cancel/reassign, recorded fallback, UI subtask tree (test_agents.py 28, test_agent_tree*.py 43, live PG). Gaps: pause/cancel/reassign change records not processes, the engine does not call spawn yet, scope not enforced in the sandbox. docs/agents.md, evidence/s4/lane-a.md |
| 46 | Real-time delivery | s1 `eventbus.py`, s4 `delivery.py`, s7 `collabStream.ts` | verified | decision recorded (table = log, WS = push, polling fallback); `prevSeq` chain proves gaps; `delivery.Reducer` + TS port; schema-version gating; close codes 4408/4410/4429/1013; socket rate limits; 15 s access revalidation; closed-label realtime metrics (test_realtime.py 19, test_delivery*.py 52, realtimeCore.test.ts 67, collabStream.test.ts 25). Gaps: no route emits `STALE_CLIENT_STATE` yet (older stale codes cover control/approvals); orchestrator route rate-limit hook not installed in deploy so `MUTATION_ROUTE_LIMITS` is inert; revocation takes ~17 s and other devices' sockets stay open; project stream has no `prevSeq`; hooked UI containers covered by tsc only |
| 47 | Notifications & user control | s1 `notifications.py`, s4 `notify.py`, s7 | verified | inbox (7 closed kinds), email opt-in only, scope precedence, quiet hours in user tz, digests, dedupe/grouping, link re-authorization, delivery state, mute/snooze/unsubscribe audited (test_notifications_*.py 66, test_notify*.py 58, live PG test_session5_pg.py, notifications.test.tsx 50). Gaps: nothing prunes inbox/dedupe rows; no Prometheus metrics; two simultaneous first events of a burst can make two rows; email is the only extra channel, previews English only; `control_request`/`suggestion` kinds have no call site; needs `tzdata` in slim images and `HARNESS_APP_URL` |
| 48 | Files, artifacts, versions | s4 `diffstore.py` | partial | no artifact store/lineage (S6) |
| 49 | Integrations & extensibility | s1/mcp | partial | GitHub, Stripe, SSO; no signed outbound webhooks (S7) |
| 50 | Search, history, export | s7 | partial | repo-tree search only; no authorized search/export (S6) |
| 51 | Administration & governance | s7 | partial | settings pages; no admin console (S7) |
| 52 | Security & privacy program | docs | blocked | threat model done; pentest external |
| 53 | Reliability & failure handling | deploy | blocked | PITR never run on real object store; migrations rollback = `pg_restore` only |
| 54 | Scalability & performance | s4 | partial | `docs/benchmarks`; claim cap now enforced (lane C); multi-host not designed |
| 55 | Observability & support | s1/s4 | partial | request id in logs; metrics gauges added; no SLOs/dashboards |
| 56 | Accessibility, localization, clients | s7 | partial | collaboration UI done (S5): one throttled announcer, keyboard operation tests at handler level, reduced motion, Intl dates/numbers, windowed long history, `docs/accessibility.md`; no real-browser keyboard/axe/screen-reader run, English only, `html lang` fixed; rest of part 56 is S10 |
| 57 | Billing, quotas, cost controls | s1 | blocked | code exists; live Stripe never driven; tier caps unenforced |
| 58 | Compliance & data lifecycle | docs | blocked | no legal hold/residency/deletion; counsel review external |
| 59 | Rollout, migration, compatibility | deploy | partial | flag + rollback runbook; staged rollout S10 |
| 60 | Full product roadmap | docs | verified | `docs/roadmap.md` |
| 61 | Full product acceptance | all | blocked | ledger = this file; acceptance run is S10 |

Counts (Session 2 interim, lane A pending): verified 9 · implemented 15 · partial 29 · missing 2 · blocked 6. Session 2 changed rows 09, 12, 24, 28, 32 only; rows 01, 02, 05–08, 10, 11, 22, 23, 27 wait on lane A and integration.
plus the fresh run; 09 re-run fresh. Nothing else is claimed verified.
