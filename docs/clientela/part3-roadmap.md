# CLIENTELA Part 3 — Strategic Roadmap

**Sources:** raw-09-product-gaps.md (15 product gaps + priority matrix), raw-19-comp-coding.md (vs Cursor/Windsurf/Copilot Workspace/Devin/Aider), raw-20-comp-meeting.md (vs Zoom/Meet/Teams/Slack huddles/Granola/Fireflies), raw-21-comp-verdict.md (two-sided verdict + 10 adoption killers), raw-11-business-legal-process.md (pricing/billing/legal audit), raw-01b-security-auth-v2.md + raw-22-hardening.md (auth/config fixes).
**ID conventions:** `G#` = product-gap number in raw-09; `C#/H#/M#` = security-audit findings in raw-01b; coding/meeting capabilities cited by name. **Waves:** NOW = wave 0 (launch-blocking), NEXT = wave 1 (launch week–month), LATER = wave 2 (month 2–3), FUTURE = wave 3 (post-quarter). Sizes: S ≤ 3 days, M ≤ 2 weeks, L > 2 weeks.

---

## 1. Competitive position summary

**What harness is:** a *meeting-driven AI coding agent with governance*. A human approves a generated plan before any agent execution (plan gate in the Section 4 orchestrator state machine); every gate decision, pause, resume, human edit, and command lands as an immutable event on a replayable, seq-gap-healing event log (`agent_events`); agent code executes only inside an OS-enforced allow-list sandbox with `forbidden_paths` as hard constraints; every invocation is meant to hit a cost ledger. Tenancy is team-scoped out of the box, and self-host + BYO-model is the design center. No coding incumbent (Cursor, Windsurf, Copilot Workspace, Devin, Aider) ships this combination, and no meeting incumbent (Zoom, Meet, Teams, Slack huddles, Granola, Fireflies) even attempts it.

**Two-sided verdict:**
- **vs. coding tools — parity-plus on transparency, structurally behind on context and curation.** The live shell stream with mid-run pause/resume/human-edit arguably beats Devin's passive stream and Aider's command printouts. But there is no codebase indexing/@refs/RAG, no editor or file tree, no inline hunk accept/reject, no checkpoints, no Tab completion, and the PR loop dead-ends — so quality collapses and trust erodes on real repos regardless of how good the control plane is.
- **vs. meeting tools — differentiated on governance, absent from the category.** Plan-gate approval with an event-sourced trail has zero equivalent among all six incumbents; but the product is text-only chat with a regex echo-bot — no voice, video, screen share, recording, transcript, calendar, threads, or presence — meaning it cannot yet enter the meeting-category conversation at all.

**Where harness genuinely wins today (from raw-21 §A):**
1. **Human plan-gate governance** — an auditable human sign-off before autonomous action; a procurement requirement for regulated buyers (finance, health, gov).
2. **Event-sourced audit trail** — replayable history of *human decisions* interleaved with machine actions; rarely drives the initial pick, decisive post-incident and in compliance reviews.
3. **OS-level sandbox allow-list** — kernel-enforced capability boundaries, not prompt instructions; genuinely differentiated as prompt-injection exfiltration fears grow, and the easiest new buyer door (security).
4. **Team-scoped multi-tenancy** — workspaces, briefs, approvals, and audit scoped per team natively; table stakes for enterprise, differentiator vs. single-player-rooted indie tools.
5. **Self-host + BYO-model** — true self-host plus arbitrary model endpoints, which none of the six allow; a narrow-but-valuable sovereignty pitch (defense, EU data residency, IP-paranoid R&D) pairing naturally with #3.
6. **Terminal transparency + a multiplayer-ready event bus** — near-parity streaming today, and shared runs/collaboration will be dramatically cheaper to build on the replay log than for incumbents retrofitting it.

**Blunt read:** the moat candidates are all back-office. Every front-office surface a user touches in minute one — browse code, ask a question, watch a diff, undo a mistake, know what it costs — is missing, broken, or fabricated. Governance wins sell to governance/security buyers; mainstream adoption requires closing the front-office stack below, in waves.

---

## 2. The 10 adoption killers, ranked

From raw-21 §B (ranked order preserved), cross-referenced with raw-09 gap numbers. "Covers" = which incumbent already ships this and sets the buyer's baseline expectation. Wave = remediation horizon in §3 below.

| # | Killer | Covers it (baseline) | Severity | Build | Wave |
|---|--------|----------------------|----------|-------|------|
| K1 | No voice/video/anything live — text-only chat can't enter the meeting-category conversation; Zoom/Meet/Teams *are* that product | Zoom, Meet, Teams | BLOCKER | L | FUTURE (decision gate in NEXT) |
| K2 | No codebase indexing / @refs / RAG — echo-bot knows only pasted text; output collapses on real codebases | Cursor, Windsurf, Copilot Workspace, Aider (G#1, G#8) | BLOCKER | M–L | LATER |
| K3 | No code editor surface / file tree — devs won't live in a chat pane with no way to see or navigate their project | All five coding incumbents (G#7) | BLOCKER | S–M | NOW |
| K4 | No Tab completion — highest-frequency interaction in AI coding absent; no daily-habit hook | Cursor, Copilot | HIGH | L | FUTURE |
| K5 | Read-only diff panel, no inline accept/reject — review happens away from where edits land | Windsurf per-hunk flow is baseline; also Cursor, Copilot Workspace stages (G#2) | HIGH | M | NEXT |
| K6 | No checkpoints / git snapshots — a bad run is unrecoverable; trust requires undo | Cursor, Aider (G#4) | HIGH | S–M | NEXT |
| K7 | PR flow orphaned — approved work dead-ends instead of landing as a reviewed pull request | Copilot Workspace, Devin, Aider (raw-19 footnote) | HIGH | M | NOW |
| K8 | No rules files / persistent repo conventions — corrections don't stick across sessions | .cursorrules, Windsurf rules, copilot-instructions.md, CONVENTIONS.md (G#9) | MED-HIGH | S | NEXT |
| K9 | Per-step model selection missing + cost table unwired — no model choice, no spend visibility; dead UI erodes trust | Cursor (G#6) | HIGH | M | NOW (metering) → NEXT (selection) |
| K10 | No threads, presence, calendar, or persistent rooms — zero social fabric; product hosts transactions, not collaboration | Slack huddles, Teams, Granola | MEDIUM | M each | NEXT/LATER |

**Compounding launch damage not in the top-10 list but feeding it:** fabricated placeholder project/run IDs at login (G#10, BLOCKER, S), unwired demo fixtures (G#13, BLOCKER for evals, S), and silent `cost_ledger` bypass on direct dsh runs (G#6a, BLOCKER-adjacent, S). These are wave-NOW honesty fixes: nothing recovers a demo that starts with fake data.

<!-- APPEND -->

---

## 3. Feature roadmap — four horizons

Format per item: **what to build** · size S/M/L · dependencies · finding IDs resolved (`G#` = raw-09 gap; `C#/H#/M#` = raw-01b; capability names from raw-19/20).

### 3a. NOW — launch-blocking

Nothing else matters if these ship broken; they are the difference between "audits like a control plane" and "demos like a terminal with extra steps."

| # | Item | What to build | Size | Dependencies | Resolves |
|---|------|---------------|------|--------------|----------|
| N1 | **Honest empty state at login** | Stop minting placeholder project/run IDs on login; route new users to a real empty state that walks repo-connect → brief → first run. Never show objects that don't exist. | S | none | G#10 (BLOCKER), §2 compounding damage |
| N2 | **Plan-gate UI completion** | The gate is the differentiator — finish the surface: plan rendering with step-level detail, approve/reject with reason capture, gate decisions written as audit events via `write_audit` (currently zero production call sites). Binary approve/reject stays for launch; mid-flight editing is LATER. | M | N5 (audit events wired) | G#5 partial, raw-21 §A-1 credibility |
| N3 | **PR flow wiring** | Close the orphaned loop: approved run → branch/commit on the S2 working branch → opened PR with summary of the event trail. No auto-merge anywhere (house rule) — the PR is the handoff to human review. | M | working git integration from S2 | K7, raw-19 footnote |
| N4 | **Real LLM behind @ai** | Swap (or sibling-route) the meeting-page regex echo-bot for an actual LLM call through the dsh/provider path, scoped to pasted text + brief context. Full repo grounding waits for indexing (LATER); honesty now: label what the AI can and cannot see. | M | N6 (engine config sane) | G#8 partial, K2 partial |
| N5 | **Auth fixes** | Default `HARNESS_REQUIRE_OWNERSHIP=true` (OTP-proven login); delete or flag-off the legacy no-proof invite endpoint; fix rate-limiter route table to real paths incl. `/auth/verify-otp`; wire `write_audit` into every mutating handler; remove dev master-key compose defaults. | M–L | none | C1, C2, H2, H3, M1 partial, M2 partial |
| N6 | **Engine config hardening** | Stop mutating process-global `os.environ` with live API keys around `DeepSeekHarness.run()` (interleaved runs swap each other's keys; error text can leak config) — pass credentials explicitly to the SDK constructor and redact exception text before it reaches the UI. Fail-fast boot validation: required settings non-empty-or-absent; refuse prod boot on default master key; bearer-auth the entire Section 3 router incl. `POST /models/config`. | M | none | M3, raw-22 §A/§E |
| N7 | **Meter all dsh calls** | Wrap the dsh call site so *every* engine invocation writes usage rows to `cost_ledger` — close the direct-run bypass first so spend can never go dark silently. | S | none | G#6a, raw-11 dead-man's-switch |
| N8 | **Demo seeding** | `POST /demo/seed` (or CLI) materializing the existing `demo_repo_manifest.json` fixture as real project + repo + one completed run replayed from recorded events. Two-click, fully-real demo for every eval. | S | N1 | G#13 |
| N9 | **Repo browser / file tree** | Read-only tree + file viewer fed by S2's existing repo access (`git ls-tree` / blob endpoints), lazy-loaded directories. The cheap unblocker that gives users a mental model of the codebase and starves indexing/diffs of their navigation surface. | S–M | project connected | G#7 (BLOCKER), K3 |

**NOW exit criteria:** a stranger can sign up honestly (N1/N5), connect a real repo, see its tree (N9), watch a governed run execute with visible cost rows (N7), approve or reject at the gate (N2), and receive the result as a reviewable PR (N3) — with an @ai that answers from real model calls (N4) and a two-click seeded demo (N8).

### 3b. NEXT — launch week → month 1

Turns the trustworthy core into a daily-habit product.

| # | Item | What to build | Size | Dependencies | Resolves |
|---|------|---------------|------|--------------|----------|
| X1 | **Checkpoints / git snapshots** | Git-native auto-commit (or stash-ref) per accepted step on the S2 working branch, tagged with the `agent_events` seq so the replay log doubles as a restore timeline. Restore-any-point UI on the run page. | S–M | N3 branch flow exists | G#4, K6 |
| X2 | **Rules files** | `.cursorrules`-equivalent precedence chain team > repo > run; loaded into system context at plan-generation time; hard rules enforced through S5 alongside `forbidden_paths`. Parse AGENTS.md/conventions.md first — users already expect the convention (this repo's own AGENTS.md proves it). | S | none | G#9, K8 |
| X3 | **Cost metering → per-step model selection** | With the ledger bypass closed (N7): light up per-team/per-run spend views (the dead cost table goes live), then add a model field on plan steps / run config resolved through the provider registry instead of env globals. | M | N7 | G#6b, K9 |
| X4 | **Notifications** | Hook a notifier service on orchestrator terminal states (run complete/fail/pause) — email + webhook/Slack payload, per-user prefs in settings. Event bus makes this a consumer, not a feature fork. Include unsubscribe/opt-out footer (CAN-SPAM/GDPR). | S–M | event bus; notification_outbox exists | G#14, raw-11 opt-out gap |
| X5 | **Inline diff editing** | Per-file/per-hunk accept/reject components with keep/discard state persisted to the orchestrator as new contract event types, keyboard-first navigation, "apply selected hunks" re-driving S2 commit logic. | M | N3; benefits from N9 | G#2, K5 |
| X6 | **Live command-approval interstitial** | When the sandbox sees an out-of-plan command, surface "approve this command" instead of failing it silently. Small orchestrator state addition; completes the transparency story started by the terminal stream. | S | none | G#3 |
| X7 | **Video presence decision** | Decide (don't build) the meeting-side bet: full media stack (K1) vs. async-first positioning. Gate on early-customer signal: does the governance wedge sell without live media? Output is a written decision + pricing implication, not code. | S (decision) | early-customer signal | K1 framing, raw-20 BLOCKER |
| X8 | **Threads on run events** | First social fabric: comment threads attached to run events/steps, reusing the event log; presence indicators (S) ride along cheaply. | M | none | K10 partial, raw-20 threads/presence |

**NEXT exit criteria:** mistakes are undoable (X1), conventions stick (X2), spend is visible and models switchable per step (X3), users stop babysitting runs (X4), edits are curatable not just inspectable (X5), and the meeting-side strategic bet has a written verdict (X7).

<!-- APPEND -->


