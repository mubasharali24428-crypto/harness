# Architecture map (Master prompt part 13)

Traced from code on 2026-10-18; every line cites a file. Items marked **UNVERIFIED** were not run in Session 1.

## Services and state owners
| Section | Role | Authoritative state |
|---|---|---|
| `contracts/` | Frozen shared package (events, brief, plan, roles, signatures); TS mirror in `contracts/ts/types.d.ts` | none (types) |
| `section1` | Identity, auth, audit, billing, SSO, event bus + WS replay, migrations 0001–0035 | Postgres: users, teams, sessions, audit_log, agent_events, cost, outbox |
| `section3` / `section6` / `section8` / `section10` / `section11` | Auxiliary services (each has `openapi.json` except 11) | per-service; see their `src/` |
| `section4` | Orchestrator + engine: plan gate, runs, collaboration (`collab.py`), staging (`staging.py`), retention, pilot metrics | Postgres: agent_runs, run_participants, run_handoffs, run_collab_events, high_impact_approvals; run workspace + engine session dirs on disk |
| `section5` | Sandbox (command allow-list, egress pinning, Landlock on Linux only) | per-run workspace (disk) |
| `section7` | Next.js UI; calls services directly on host ports (no proxy) | browser session token |
| `deploy/pilot` | Disposable pilot stack (`stack.py`, `run_smoke.py`), stand-in worker (`worker.py`, test target only) | scratch Postgres |

Collaboration state owner: **section4** (`collab.py`) — leader, participants, handoffs, redirects, approvals are rows in
the shared Postgres; section1 owns the migration that creates them (`0035_run_collaboration.sql`).

## Traces
- **Auth, UI → DB:** `section7/src/lib/api.ts` + `session.ts` attach the bearer → service `Principal` (section1 `auth.py`) →
  orchestrator `authz.py` `require_role` / ownership check (denials write `authz.denied` to `audit_log`) → collab routes use
  `collab.pilot_allowed(team)` (flag `HARNESS_PILOT_TEAMS`) → per-run `_participant` / `_require_leader` in `collab.py`.
- **Events, producer → subscriber:** engine/collab emit `agent_events` rows (`collab._event`, partitioned table 0028) →
  `section1/eventbus.py` (pg_notify, 0003/0029) → WebSocket (`ws_tickets` auth, bounded replay by seq cursor) and REST cursor
  endpoint; UI also polls (3 s) in `useEventStream.ts` / `collabApi.ts`.
- **Approval:** UI "Approve this change" → orchestrator collab approval route → `collab.decide_approval` (leader-only; digest from
  `proposal_digest`) → `staging.py` applies only a digest-matching approved group (`apply_group`); a changed proposal is
  invalidated (`collab._invalidate`) and must be re-requested; `claim_approval`/`complete_approval` serialize execution.
- **Cancel:** stop route → worker kill → `cancel-requested` / `cancel-confirmed` audit events (section4 `engine_worker.py`).

## Shared-state assumptions
- All services share one Postgres (`HARNESS_DATABASE_URL`) in the pilot stack; collab tables are FK-bound to `agent_runs`.
- The orchestrator and the container worker share workspace + session roots on one host filesystem (`P1_WORKSPACE`,
  `P1_SESSION_ROOT`, `DSH_*`). Multi-host needs shared storage — not designed (part 54).
- Realtime is DB-notify + polling; there is no presence service, no Redis (UNVERIFIED: see lane C ops evidence).

## Patterns to extend, not duplicate
Frozen contracts; `require_role`; the `_collab` wrapper + `audit_denial`; the notification outbox; the retention sweeps; per-run staging.

## Debt that affects pilot safety/delivery
No presence/discussion; polling not push; no tamper-evident audit; `agent_runs.state` not maintained (retention note);
`KNOWN_UNSPECCED` is empty but spec_drift covers only the orchestrator (lane B extends); synthetic sign-in in the
two-user proof (Session 1 deliverable 4).
