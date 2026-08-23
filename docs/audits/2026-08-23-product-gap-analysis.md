# Harness vs. the AI-Coding-Agent Market: Brutal Product-Gap Analysis

**Scope:** `/Users/user/untitled folder 2/harness` (Section 1–10 monorepo) benchmarked against Cursor, GitHub Copilot Workspace, Devin (Cognition), and Windsurf-class tools.
**Method:** Write-from-map against a pre-verified reality audit; repo spot-checks for citations only. Two prior analyst runs died to provider outages; this run writes first.
**Severity scale:** BLOCKER = kills adoption for the target user; HIGH = deal-loser vs. incumbents in head-to-head evals; MEDIUM = table-stakes polish gap.

---

## Executive summary

Harness has an unusually strong *trust spine* — a full orchestrator state machine with plan gate (`section4/src/harness_orchestrator/engine.py`), event-sourced audit log with seq-gap replay, sandbox allow-list with `forbidden_paths` hard constraints (`section5/src/harness_sandbox/policy.py`), and a cost ledger. That's more governance than most incumbents ship.

It is missing nearly everything users actually *touch*: no codebase indexing, no repo browser, no inline diff UX, no per-step model choice, no rules files, no RAG Q&A, fabricated demo IDs at login, and an orphaned review page. The result: a product that audits like an enterprise control plane but demos like a terminal emulator with extra steps. 15 gaps below; 5 are blockers.

---

## Core gaps (12 dimensions)

### 1. Codebase indexing & context retrieval
- **Incumbents:** Cursor builds per-repo embeddings (Merkle-tree incremental sync) plus @file/@symbol/@docs retrieval; Windsurf's Cascade does repo-wide memory; Copilot Workspace ships workspace-level context assembly. This is the single most-copied feature in the category.
- **Harness today:** No indexing, no embeddings, no RAG anywhere in the map. The dsh engine (`section4/src/harness_orchestrator/engine.py`) drives DeepSeek via `deepseek_harness` SDK with whatever context the plan text carries — no retrieval layer between repo and model.
- **Severity:** **BLOCKER.** Without retrieval, every prompt is either context-starved or manually pasted; long-run quality collapses on any real repo.
- **Build:** M. Ship a per-project index job (tree-sitter symbol extraction → embeddings → pgvector in the existing Postgres), expose `@file`/`@symbol` mentions in the meeting/chat composer, inject top-k chunks into plan context. Postgres is already there; no new infra.

### 2. Inline diff editing UX (hunk accept/reject, multi-file nav)
- **Incumbents:** Cursor/Windsurf made hunk-level accept/reject the core loop — green/red inline edits, per-hunk keep/discard, jump-between-files, "accept all." Copilot Workspace presents spec→plan→implementation diffs with stage-by-stage accept. Nobody makes users read raw unified diffs.
- **Harness today:** A diff panel inside `projects/[projectId]/runs/[runId]` working area with pause/resume/human-edit controls — read-oriented, not accept/reject-oriented. Approval happens at the plan gate, not at the edit level.
- **Severity:** **HIGH.** Users won't adopt an agent whose output they can only inspect, not curate; review friction directly caps trust and reuse.
- **Build:** M. Render per-file/per-hunk components with keep/discard state persisted to the orchestrator (new event types in `harness_contracts`), keyboard-first navigation, "apply selected hunks" action that re-drives S2 commit logic.

### 3. Terminal transparency & mid-run intervention
- **Incumbents:** Devin streams a live shell+browser view; Windsurf shows cascade terminal commands with approve-to-run; Cursor runs terminals inline with visible output and interrupt.
- **Harness today:** Genuinely competitive — live terminal panel in the working area, WS streaming over the event-sourced `agent_events` log with seq-gap replay, pause/resume and human-edit controls. This is one of harness's real strengths.
- **Severity:** LOW-MEDIUM (parity-ish). Remaining gap: no command-level allow-list prompts mid-run (S5 confinement is pre-declared via plans, not negotiated live).
- **Build:** S. Surface an "approve this command" interstitial when the sandbox sees an out-of-plan command, instead of failing it silently. Small orchestrator state addition.

### 4. Checkpointing / undo of agent edits
- **Incumbents:** Cursor checkpoints every agent edit (restore-any-point timeline); Devin snapshots workspaces per step; Copilot Workspace leans on branch-per-task git semantics.
- **Harness today:** Nothing in the map. Human-edit controls imply manual rescue; no snapshot/rollback primitive exists.
- **Severity:** **HIGH.** One bad autonomous edit session that can't be undone ends pilot programs.
- **Build:** S–M. Git-native: auto-commit (or stash-ref) per accepted step on the S2 working branch, tag each checkpoint with the `agent_events` seq so the replay log doubles as a restore timeline. Mostly orchestration glue.

### 5. Editable multi-file planning mid-flight
- **Incumbents:** Copilot Workspace's whole model is editable spec→plan→build stages; Devin accepts mid-run steering; Cursor plans are chat-editable before and during execution.
- **Harness today:** Strong plan gate in the S4 state machine — but approval is binary (approve/reject at gate). No evidence of editing plan steps after the run starts; human-edit controls exist but are not plan-aware.
- **Severity:** **HIGH.** Long runs drift; without plan mutation, the choice is "let it finish wrong" or "restart from zero."
- **Build:** M. Make plan steps first-class mutable records (versioned plan doc keyed to run), add PATCH endpoint + editor in the plan panel, emit `plan_revised` events so the audit trail stays honest.

### 6. Per-step model selection + real-time token/cost visibility
- **Incumbents:** Cursor offers per-feature/per-request model pickers with live usage counts; Copilot shows premium-request meters; Devin reports compute credits per session. Cost transparency is now a selling page, not a settings page.
- **Harness today:** Model = whatever `DEEPSEEK_*` env vars say (`section4/src/harness_orchestrator/engine.py`, import at ~line 115) — global, operator-set, invisible to users. `cost_ledger` table exists, but direct dsh runs bypass metering; dashboard/cost page reads a partial ledger.
- **Severity:** **HIGH** (cost visibility half is BLOCKER-adjacent for team buyers). Un-metered spend + no per-step choice means no cost control and no model experimentation.
- **Build:** S for metering (wrap the dsh call site so *every* engine invocation writes usage rows to `cost_ledger` — close the bypass first), then M for per-step selection (model field on plan steps/run config, resolved through the provider registry instead of env globals).

### 7. Repo browser / file tree
- **Incumbents:** Universal. Every incumbent pairs the agent pane with a navigable tree; Cursor's is the anchor for @file and diff navigation.
- **Harness today:** Absent from the entire UI map — no tree, no file viewer, nothing. The working area has terminal/diff/plan panels only.
- **Severity:** **BLOCKER.** Users cannot form a mental model of what the agent is operating on. It also starves gaps #1/#2/#8 of their navigation surface.
- **Build:** S–M. Read-only tree + file viewer fed by S2's existing repo access (git ls-tree / blob endpoints); lazy-load directories. Do this before indexing (it's the cheap unblocker).

### 8. Chat-with-codebase Q&A (RAG)
- **Incumbents:** Cursor chat, Copilot Chat, Windsurf Cascade Q&A — grounded answers with citations into files/symbols. Category-defining feature; often the *first* thing evaluators try.
- **Harness today:** The meeting page's "@ai" is a regex rules engine, not an LLM — pattern-match responses, no grounding, no repo access. Nothing else answers questions about code.
- **Severity:** **HIGH.** It's the lowest-friction value moment in every competitor's funnel; harness's equivalent literally cannot answer questions.
- **Build:** M. Depends on #1's index; swap the meeting @ai path (or add a sibling route) to an LLM-backed retriever with file citations. Keep the regex engine as offline fallback if desired.

### 9. User rules files (.cursorrules equivalent)
- **Incumbents:** `.cursorrules`/`.cursor/rules`, `.windsurfrules`, `copilot-instructions.md`, AGENTS.md support across tools. Users expect to encode conventions once and have agents obey.
- **Harness today:** Only `forbidden_paths` constraints embedded in plans (`section5/src/harness_sandbox/policy.py`). No user-authored, repo-committed, or team-level rules surface. Ironically this repo's own AGENTS.md proves the authors know the convention.
- **Severity:** **MEDIUM-HIGH.** Not a day-one blocker, but its absence surfaces as "the agent doesn't follow our conventions" within the first week of any real pilot.
- **Build:** S. Define precedence (team > repo > run), load rules files at plan-generation time into system context, enforce hard rules through S5 alongside forbidden_paths. Pure plumbing; contracts already carry per-run config.

### 10. Time-to-first-value / onboarding friction
- **Incumbents:** Copilot Workspace: zero-install inside github.com; Cursor: open folder → working in minutes; Devin: connect repo → assign task. Everyone compresses signup→first-artifact aggressively.
- **Harness today:** Onboarding checklist page exists, but login fabricates placeholder project/run IDs — new users land on screens pointing at objects that don't exist. First real value requires operator-side env setup (DEEPSEEK_*, Postgres, sandbox).
- **Severity:** **BLOCKER.** Fabricated IDs actively destroy trust in the first five minutes; nothing recovers a demo that starts with fake data.
- **Build:** S for honesty (login routes to a real empty-state that walks repo-connect → first run; never mint placeholder ids), M for the guided first-run flow (repo connect → brief → plan gate → small PR).

### 11. Realtime collaboration (shared sessions/runs)
- **Incumbents:** Copilot Workspace inherits GitHub's share-by-link review model; Devin sessions are shareable/async-handoff friendly; Cursor is single-player (its known weakness — an opening for harness).
- **Harness today:** Multi-seat primitives exist (settings/team, api keys, roles in contracts) and the event bus is multiplayer-ready (WS + replay), but sessions aren't shared: one viewer per run, no presence, no comments on events/steps.
- **Severity:** MEDIUM (but strategic — it's harness's best differentiation wedge).
- **Build:** M. Run-share links (read-only first) reusing the WS replay path; presence + step comments later. The event-sourced log makes this dramatically cheaper here than for incumbents retrofitting it.

### 12. Self-host vs. cloud packaging story
- **Incumbents:** Cursor/Copilot/Windsurf = cloud/desktop SaaS only; Devin = cloud with enterprise VPC talk. Self-hostable agent platforms (e.g., OpenHands) win dev-infra and security-sensitive buyers precisely because incumbents won't self-host.
- **Harness today:** Monorepo with per-section venvs, Postgres 18 local, Docker available, `deploy/` assembly point + `deploy/runners/services.py` — self-host is clearly the design center. But packaging is developer-manual: no Helm/compose one-liner, no managed-cloud option, no versioned release artifacts.
- **Severity:** MEDIUM (positioning risk both ways: no cloud trial funnel, no polished self-host install).
- **Build:** M. Compose file + pinned image set + health-checked bootstrap (sections already have conformance fixtures to smoke-test against); decide the cloud-trial story second.

---

## Additional gaps (13–15)

### 13. Test/demo-data seeding for instant demos
- **Incumbents:** Devin's public leaderboard tasks, Copilot's sample repos — evaluators always get a paved road with known-good content.
- **Harness today:** Seeds exist in tests only — `section10/tests/fixtures/demo_repo_manifest.json` and `guided_demo.json` prove the shape was designed, but nothing wires them into a runtime "load demo project" path; combined with login's placeholder IDs, first-run experience is fake-or-empty.
- **Severity:** **BLOCKER** for sales/eval motions (HIGH for organic adoption).
- **Build:** S. A `POST /demo/seed` endpoint (or CLI) that materializes the manifest fixture as a real project + repo + one completed run replayed from recorded events. Demo becomes a two-click, fully-real-data affair.

### 14. Notifications (run done → email/push)
- **Incumbents:** Devin emails/posts on completion (Slack integrations standard); Copilot notifies via GitHub notifications; monitoring-style products treat completion pings as mandatory because runs take 10+ minutes.
- **Harness today:** Nothing in the map. Users must babysit the runs history table or poll. For a product whose whole pitch is delegated autonomy, silence-on-completion is self-defeating.
- **Severity:** HIGH.
- **Build:** S–M. Orchestrator terminal states already exist in the state machine — hook a notifier service on run-complete/fail/pause events (email via SMTP, webhook/Slack payload), per-user prefs in settings. Event bus makes this a consumer, not a feature fork.

### 15. Mobile/responsive monitoring
- **Incumbents:** Weak across the board — Devin/Cursor are desktop-bound; GitHub mobile shows check status. Genuine whitespace.
- **Harness today:** Section 7 app (`section7/src/components/AppShell.tsx`) is desktop-shaped; no responsive pass in the map. Monitoring runs from a phone (status, approve plan gate, get notified, tap through to PR) is a realistic executive/lead workflow.
- **Severity:** MEDIUM today, rising with #14 (notifications are useless if the link they send dead-ends on mobile).
- **Build:** S–M. Responsive pass on Command Center + run detail + plan-approve flow only — not the full IDE-like working area. Sequence after #14.

---

## Priority matrix

| # | Gap | Severity | Size | Order |
|---|-----|----------|------|-------|
| 10b | Stop fabricating login IDs → honest empty state | BLOCKER | S | **0 (do immediately)** |
| 13 | Demo seeding endpoint from existing fixtures | BLOCKER | S | 1 |
| 6a | Close cost_ledger bypass (meter all dsh calls) | BLOCKER | S | 2 |
| 7 | Repo browser/file tree (read-only) | BLOCKER | S–M | 3 |
| 4 | Git-native checkpoints | HIGH | S–M | 4 |
| 9 | Rules files (.cursorrules equiv.) | MED-HIGH | S | 5 |
| 14 | Completion notifications | HIGH | S–M | 6 |
| 1 | Codebase indexing (pgvector) | BLOCKER | M | 7 |
| 2 | Hunk-level diff accept/reject | HIGH | M | 8 |
| 8 | RAG Q&A replacing/augmenting regex @ai | HIGH | M | 9 (needs 1) |
| 5 | Mid-flight plan editing | HIGH | M | 10 |
| 6b | Per-step model selection | HIGH | M | 11 |
| 3 | Live command-approval interstitial | LOW-MED | S | 12 |
| 11 | Shared runs / collaboration | MEDIUM | M | 13 (wedge) |
| 12 | Compose/Helm self-host packaging | MEDIUM | M | 14 |
| 15 | Mobile monitoring pass | MEDIUM | S–M | 15 |

**The blunt thesis:** harness's moat candidates (governance, audit, sandbox, replayable event log, collaboration-ready bus) are all back-office. Every front-office surface a user touches in minute one — browse code, ask a question, watch a diff, undo a mistake, know what it costs — is missing or broken. Five sub-week fixes (10b, 13, 6a, 9, 3) would remove the active trust damage; the M-size context stack (#1→#8) is what makes it a competitor rather than a compliance shell.
