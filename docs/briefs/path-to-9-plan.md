# Path to 9/10 — all 61 master-prompt parts (plan, 2026-10-18)

Baseline: scores from the 2026-10-18 ranking (parts 04, 09, 20, 21, 30 already 9; the other 56 are below 9).
Source of truth for the parts: the "Harness Multiplayer AI — Full Product Master Build Prompt"
(61 sections; session e95e685c). Part numbers below = its section numbers.

## What "9/10" means (applies to every part)
A part is 9 only when ALL hold: (1) every directive in the part is implemented, or explicitly
marked out-of-scope with a reason in the capability matrix; (2) each security/state invariant has an
automated test that fails if the behavior breaks; (3) an evidence file (command + result, not
assertion) exists under `docs/briefs/evidence/`; (4) docs match shipped behavior; (5) the
capability matrix row says `verified`. A 10 additionally needs independent/external evidence.

## Externally gated — capped at 8 until outside evidence exists (code can't earn these)
- 03 Pilot success: needs real pilot runs + a measured baseline (>=5 tasks per side).
- 37/57: Stripe and OIDC SSO drilled against live providers.
- 53: S3 WAL-G PITR round trip on a real object store.
- 52/58: independent pentest / counsel-reviewed TERMS+PRIVACY / no compliance claims without audit.
- 61: only after every other row is verified.
Sessions deliver everything up to the gate (tooling, runbooks, drill scripts) and record the gap honestly.

## Independence rule
Each session starts from only: this plan, `docs/briefs/capability-matrix.md` (created in S1),
and the newest `docs/briefs/path-to-9-checkpoint.md`. No session relies on chat memory. Order is by
prerequisite: a session only uses things built in EARLIER sessions, and tests its own work alone.

## Sessions

| S | Theme | Parts raised | Builds (prerequisite for) |
|---|---|---|---|
| 1 | Truth ledger + verification baseline | 13,14,15,16,17,18,19,25,26,28,29,31(map),33,34,35,36,60,61(ledger) | capability matrix, contracts regen, real-sign-in E2E, ops acceptance, threat-model refresh — all later sessions track in the matrix |
| 2 | Pilot-tranche product residuals | 01,02,05,06,07,08,10,11,12,22,23,24,27,32 | per-action approval at file-op seam, instruction-ordering rules, WS push for collab events, tamper-evident audit, export of audit evidence, lifecycle edge cases |
| 3 | Identity, orgs, permissions, workspaces | 37,38,39 | org layer, policy engine with inheritance, permission preview, step-up auth, emergency access, workspace lifecycle |
| 4 | Session/run model, control, presence, agents | 40,41,42,45 | sessions containing runs, presence, control leases, agent registry + spawn limits |
| 5 | Discussion, notifications, realtime UX, a11y of collab UI | 43,46,47,56(collab UI) | comments/mentions, notification prefs/digests, reconnect UX, keyboard/screen-reader pass |
| 6 | Plans, artifacts, search, export | 44,48,50 | versioned plans, artifact store + lineage, authorized search, bulk export + audit |
| 7 | Integrations, admin, billing | 49,51,57 | signed outbound webhooks, integration health/rotation, admin console, policy simulation, quota UX |
| 8 | Security, privacy, compliance lifecycle | 52,58,31(enforce) | secrets rotation, tenant isolation tests, retention/legal hold/deletion/export, subprocessor docs |
| 9 | Reliability, scale, observability | 53,54,55 | SLOs, failure-injection, load scenarios, dashboards/alerts, support diagnostics |
| 10 | Rollout, a11y/i18n finish, roadmap, final acceptance | 56,59,60,03(tooling),61 | staged rollout/rollback, locale + reduced motion, full-product acceptance run, readiness decision |

Part 04/09/20/21/30 (already 9): each session must not regress them; S10 re-verifies.

## Per-session exit gate (all sessions)
1. Capability-matrix rows for the session's parts updated with evidence paths.
2. Targeted tests + lint + type checks for touched sections run; commands and results recorded
   (no claim for unrun checks). Existing suites stay green (S1 650, S4 643, S7 526 as of last run).
3. Compact checkpoint written to `docs/briefs/path-to-9-checkpoint.md` (<2 pages).
4. Commits by task with per-file pathspecs; no auto-merge; no push unless asked.

## Risks
- Parts 36–61 are large product surface; scope per session is deliberately narrow. If a session
  overruns, it records the unfinished parts in the checkpoint rather than shipping half-features.
- The working tree already has ~126 uncommitted changes: every session begins with `git status`
  and must not overwrite unrelated work.

## Plan amendments
- 2026-10-18 (after S1): threat T15 (sandbox taskId takeover, HIGH) and T16/T17/T7b are pulled forward from S8 into S2; S1 flake/tsc/pg-probe fixes also land in S2. Session 2 prompt: `docs/briefs/path-to-9-session2-prompt.md`.
