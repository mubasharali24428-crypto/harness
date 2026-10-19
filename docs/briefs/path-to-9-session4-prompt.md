# Session 4 — Sessions, presence, control, agents (parts 40, 41, 42, 45)

Prefix: `docs/briefs/stable-prompt-prefix.md`. Directives: `docs/master-prompts/harness-multiplayer-master-build-prompt.md` sections 40, 41, 42, 45
(read only your section, plus `docs/briefs/PROJECT-STATE.md` section 6 "Facts that bite"). Target: each part at 9/10 = every directive implemented or
explicitly out of scope with a reason, invariant tests, an evidence file, docs that match, honest gaps.

## What exists (verify, do not trust)
- A "run" (`agent_runs`) is today's only session. Collaboration is per run in section4: `collab.py` (participants, ONE leader enforced by a partial unique index,
  redirects, handoffs, high-impact approvals bound to a digest), `staging.py`, tables from `section1/migrations/0035_run_collaboration.sql`
  (`run_participants(run_id,team_id,user_id,role leader|participant|observer,joined_at)`, `run_collab_events`, `run_handoffs`, `high_impact_approvals`).
- Org policy gate (`contracts/.../policy_enforce.py`) already classifies routes by path; the lead adds rules for new routes (pre-added for the paths below).
- Sections other than section1 may import only `harness_contracts`, so section4 speaks raw SQL (`sqlalchemy.text`) to shared tables. Section1 may use Core `text()` too.

## Lane contract (the lead wires everything; lanes never touch shared files)
Lanes: **S** sessions (40), **P** presence and participation (41), **C** control leases and conflicts (42), **A** agents (45). Each lane owns ONLY:

- **S** (section1): migration `section1/migrations/0041_sessions.sql`, `section1/src/harness_identity/sessions.py` (exports `router`, prefix `/api/v1/sessions`),
  tests `section1/tests/test_sessions*.py`; section4 `section4/src/harness_orchestrator/session_gate.py` plus ONE call each in `service.py` `start_execution` and
  `_dispatch_claim` (copy the shape of `workspace_gate.py` calls, same file region); tests `section4/tests/unit/test_session_gate.py`; `docs/sessions.md`;
  `docs/briefs/evidence/s4/lane-s.md`.
- **P** (section4): migration `0042_presence.sql`, `section4/src/harness_orchestrator/presence.py` and `participation.py` (each exports `router` and
  `configure_<name>(service)`), tests `section4/tests/unit/test_presence*.py` / `test_participation*.py`; `docs/presence.md`; `evidence/s4/lane-p.md`.
- **C** (section4): migration `0043_control.sql`, `section4/src/harness_orchestrator/control.py` (`router`, `configure_control(service)`), edits to `collab.py` and
  `staging.py` (C is the ONLY lane that may edit those two), tests `section4/tests/unit/test_control*.py`; `docs/control.md`; `evidence/s4/lane-c.md`.
- **A** (section1 + section4): migration `0044_agents.sql`, `section1/src/harness_identity/agents.py` (registry; `router`, prefix `/api/v1/agents`),
  `section4/src/harness_orchestrator/agent_tree.py` (spawn/tracking; `router`, `configure_agent_tree(service)`), tests `section1/tests/test_agents*.py`,
  `section4/tests/unit/test_agent_tree*.py`; `docs/agents.md`; `evidence/s4/lane-a.md`.

Forbidden for all lanes: `models.py`, `app.py`, `audit.py` (Session 4 audit action names are already reserved, see below), `policy_enforce.py`,
`capability-matrix.md`, `Makefile`, `deploy/`, `openapi.json`/sdk files, anything under `section7/`. Do not run docker or `make pg-*` (the lead does).
Never `ruff check --fix`. Do not commit. Report paths, test counts, honest gaps. Use `text()` SQL, no new ORM models.

## Reserved audit actions (section1 audit writer rejects unregistered names)
session.{created,updated,state_changed,member_joined,member_left,member_removed,run_linked,run_unlinked,template_saved,template_applied,resumed,deleted,purged,run_refused}
participant.{muted,unmuted,observing,left,rejoined,capacity_refused}, presence.settings_changed
control.{requested,acquired,renewed,released,transferred,revoked,expired,conflict_detected,conflict_resolved,instruction_queued,instruction_rejected}
agent.{registered,updated,approved,retired,selected,spawned,spawn_refused,delegation_denied,paused,resumed,cancelled,reassigned,fallback_used,tool_called}
Section4 writes audit rows raw (`INSERT INTO audit_log (actor_member_id, actor_kind, action, target_type, target_id, detail, ts)`, see `workspace_gate._audit_refusal`);
keep detail free of secrets and hidden reasoning, and include `before`/`after` for consequential changes.

## Fixed interfaces between lanes (so they can be built in parallel)
- `presence_records(scope_kind 'run'|'session', scope_id, team_id, user_id, device_id, state 'online'|'idle', last_seen TIMESTAMPTZ, expires_at TIMESTAMPTZ, PRIMARY KEY(scope_kind,scope_id,user_id,device_id))` (P owns).
  C reads it (raw SQL) to decide disconnect behaviour: a lease holder with no unexpired presence row for the run is "disconnected". C's tests create this table
  with their own minimal DDL.
- `team_presence_settings(team_id PK, mode 'full'|'limited'|'off', stale_seconds, max_participants, updated_by, updated_at)` (P owns). Defaults when no row:
  full, 90 s, 20 participants.
- P exposes `async def capacity_ok(db, run_id, team_id, user_id) -> bool` in `participation.py`; C calls it from `collab.join` (one line, C edits collab.py).
  P leaving = DELETE of the `run_participants` row (history lives in `run_collab_events` and audit); a leader cannot leave (409 `LEADER_MUST_HAND_OFF`).
  Mute/notification preferences live in P's `participant_prefs(run_id,user_id,muted,notify,share_presence 'everyone'|'team'|'nobody',updated_at)`.
- `collab_sessions(id, team_id, workspace_id NULL, title, owner_user_id, leader_user_id NULL, state 'active'|'paused'|'completed'|'archived'|'deleted', ...)`,
  `session_runs(session_id, run_id UNIQUE, parent_run_id NULL, ...)` (S owns). Agents appear as `session_members.kind='agent'` with an `agent_id` string (no FK).
- `agent_definitions` / `agent_assignments` / `agent_tool_calls` (A owns); S, P, C do not read them.

## Paths each lane serves (the lead already classified these for the policy gate; use exactly these)
- S, prefix `/api/v1/sessions`: `POST ""`, `GET ""`, `GET /{id}`, `PATCH /{id}`, `POST /{id}/(pause|complete|archive|restore|delete|purge)`, `GET /{id}/resume`,
  `GET|POST /{id}/members`, `PUT|DELETE /{id}/members/{user_id}`, `GET|POST /{id}/runs`, `DELETE /{id}/runs/{run_id}`, `GET|POST /templates`, `POST /{id}/apply-template`.
- P, prefix `/api/v1/orchestrator`: `GET runs/{runId}/presence`, `POST runs/{runId}/presence/heartbeat`, `POST runs/{runId}/presence/leave`,
  `POST runs/{runId}/participation/(observe|participate|leave|mute|unmute|rejoin)`, `GET|PUT runs/{runId}/participation/prefs`, `GET|PUT presence-settings`.
- C, prefix `/api/v1/orchestrator`: `GET runs/{runId}/control`, `POST runs/{runId}/control/(request|acquire|renew|release|transfer|revoke|decide)`,
  `GET|PUT runs/{runId}/intent`, `GET runs/{runId}/intent/conflicts`, `POST runs/{runId}/intent/conflicts/{conflictId}/resolve`.
- A, prefix `/api/v1/agents`: `GET|POST ""`, `GET|PUT /{id}`, `POST /{id}/(approve|retire)`, `POST /select`; section4 `GET runs/{runId}/agents`,
  `POST runs/{runId}/agents/spawn`, `POST runs/{runId}/agents/{assignmentId}/(pause|resume|cancel|reassign|tool-calls)`.
If a directive needs a path outside these, tell the lead in the evidence file instead of inventing it silently.

## Requirements common to every lane
Deny by default; team-scoped reads; foreign or unknown resources answer 404 with the same envelope (no existence oracle); never trust client-supplied identity
(use the principal); idempotent retries where a client may repeat; closed-label counters only if cheap; every refusal that the user can hit is a stable error
code; docs in `docs/` describe behaviour as built (including limits). Tests run on sqlite with the real migration SQL (strip nothing the migration needs; avoid
`ADD COLUMN IF NOT EXISTS`). Add tests that FAIL when the invariant breaks (races, expiry with a fake clock, stale version, wrong team, wrong role).

## Per-lane acceptance (nine-out-of-ten bar)
- **S / part 40**: 001-010. Session vs run separation; session can hold many runs and members (owner, leader, participant, observer, agent identities);
  documented run state table and terminal rules; a completed/archived/deleted/paused session refuses new runs (`session_gate`, 409 `SESSION_NOT_ACTIVE`, queue-claim path too);
  templates (versioned); resume returns history, runs, members and the caller's current effective permissions; leaving or losing access keeps history (soft status, display
  name snapshot); parent/child run links; retention and deletion (soft delete, `purge_after`, documented purge semantics, runs preserved or detached as documented).
- **P / part 41**: 001-010. Approximate presence buckets (never exact "last seen" unless the user shares it), per-user privacy (`share_presence`), team setting can turn
  presence off or limit it, no presence for users lacking visibility (404), multi-device merge per user, stale expiry (fake-clock tests), participant limits with a stable
  `SESSION_FULL` refusal, join/leave/observe/mute/rejoin, role/control status in the roster payload, a documented accessibility announcement text field per change.
- **C / part 42**: 001-010. Documented control model (leader holds an exclusive control lease with expiry; shared steering = non-holders submit suggestions that are queued
  for the holder), lease acquire/renew/release/transfer/revoke/expiry/disconnect, request queue, who-holds-control view, documented rule for concurrent instructions
  (queue/reject), versioned run intent (goal, scope paths, permissions) with `expectedVersion`, conflict detection before apply, conflict view with current approved state,
  resolution that alters an approved proposal forces renewed review, audit of every event. Races tested.
- **A / part 45**: 001-010. Registry with versions, capabilities, tool permissions, approval workflow, selection of approved agents per task, spawn limits (depth, children,
  budget) enforced server-side, parent/child tracking, delegation can never exceed the delegating user's effective permissions (use
  `harness_contracts.policy_enforce.decide_request` or the pure engine), scoped access recorded per assignment, tool invocation summaries without hidden reasoning,
  pause/cancel/reassign, fallback model/tool when unavailable (recorded), concise progress for the shared workspace.
