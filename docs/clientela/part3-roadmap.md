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

### 3c. LATER — month 2–3

The M-size context stack that makes harness a competitor rather than a compliance shell.

| # | Item | What to build | Size | Dependencies | Resolves |
|---|------|---------------|------|--------------|----------|
| L1 | **Codebase indexing / RAG** | Per-project index job: tree-sitter symbol extraction → embeddings → pgvector in the existing Postgres; `@file`/`@symbol` mentions in the meeting/chat composer; top-k chunks injected into plan context. No new infra — Postgres is already there. | M (raw-09) / L end-to-end (raw-19) | N9 tree ships first (cheap unblocker); N4 gives @ai a real backend to upgrade | G#1, G#8, K2 |
| L2 | **Mid-flight plan editing** | Make plan steps first-class mutable records (versioned plan doc keyed to run), PATCH endpoint + editor in the plan panel, `plan_revised` events so the audit trail stays honest. Extends N2's binary gate into editable governance. | M | N2 gate complete | G#5 |
| L3 | **AI notetaking / action items** | Meeting summaries + action items with owners — Granola/Fireflies-class output. M given any transcript source; without one it stays blocked on X7's media decision. | M | X7 decision; transcript source | raw-20 AI notetaking |
| L4 | **Calendar integration** | Google/Outlook calendar connect; join scheduled events and pre-load project/brief context before the human reaches the keyboard. | M | X7 positioning decided | raw-20 calendar |
| L5 | **Collaboration: shared runs** | Read-only run-share links reusing the WS replay path first; presence + step comments later. The event-sourced log makes this dramatically cheaper here than for incumbents retrofitting it — harness's best differentiation wedge. | M | event bus stable; benefits from X8 threads | G#11, K10 |
| L6 | **Multi-file change-set view** | Grouped per-file review of a coordinated change-set (Copilot Workspace spec→plan→build analog), building on X5's hunk UI. | S | X5 | raw-19 multi-file composer |
| L7 | **Mobile/responsive monitoring pass** | Responsive pass on Command Center + run detail + plan-approve flow only (not the full working area). Sequence after X4 — notifications are useless if their link dead-ends on mobile. | S–M | X4 shipped | G#15 |

**LATER exit criteria:** the agent answers questions from the actual repo with citations (L1), plans can be steered mid-flight without restarts (L2), and a second human can watch/review a run they didn't start (L5).

### 3d. FUTURE — post-quarter bets

Sequenced deliberately behind the trust/context stack; each is a company-scale bet, not a sprint item.

| # | Item | What to build | Size | Dependencies | Resolves |
|---|------|---------------|------|--------------|----------|
| F1 | **Voice/video presence stack** | The K1 BLOCKER itself: multi-party audio/video, screen share, recording + transcript pipeline. Proceeds only if X7's verdict says live media is load-bearing for the wedge; otherwise stay async-first and buy/integrate transcripts (L3). | L×several | X7 verdict; L3/L4 groundwork | K1, raw-20 video/screen-share/recording |
| F2 | **Tab completion** | The daily-habit hook Cursor/Copilot own: low-latency inline completion across files. Needs its own serving path and latency budget — meaningless until runs are trustworthy (NOW/NEXT) and grounded (L1). | L | L1 index infra; dedicated inference path | K4 |
| F3 | **Marketplace / plugins** | Third-party integration surface (Slack, Linear, CI notifiers, custom sandbox policies). Prereq: runtime feature-flag service and stable public API contracts. | L | contracts v0.2+ stabilized; flags service (raw-11 gap) | platform story |
| F4 | **Cloud offering** | Managed-cloud trial funnel alongside self-host. Self-host packaging ships first as the bridge (compose/Helm one-liner, pinned images, health-checked bootstrap against existing conformance fixtures) — design center stays sovereignty-compatible. | M then L | L-wave stability; billing real (§4) | G#12, raw-21 §A-5 pairing |
| F5 | **Enterprise SSO/SCIM + compliance pack** | SAML/OIDC SSO, SCIM provisioning/deprovisioning (with session revocation on offboarding — raw-11 privacy gap), SOC2 controls mapping of the existing governance claims, DPA/subprocessor list. The moment the audit trail becomes sellable to Fortune-500 procurement. | L | N5 auth foundation; cloud offering in flight | raw-11 regulatory/privacy cluster |

---

## 4. Pricing & packaging sketch

**Starting reality (raw-11):** free/pro/team exist as bare strings with zero behavioral differences anywhere in code; no pricing page or tier-comparison surface (`/settings/billing` renders `<option>` text); free-tier limits unenforced (unlimited everything on every plan); no feature gating middleware; quota enforcement is split-brain (`check_budget()` reads `teams.budget_usd` only, never `billing_plans.monthly_budget_usd`; fresh `BillingPlan` rows default to `$0` budget display); no seat licensing ("team" untied to `TeamMember` count); no checkout, webhooks, dunning, proration, refunds, invoices, or tax handling — `StripeClient` is an uninstantiated Protocol whose absence *silently degrades* to local mode. And the cross-cutting blocker: **no LICENSE + no pricing + no ToS means the product cannot yet be lawfully sold** — license/terms/pricing are launch blockers in their own right.

### Tier sketch

| | **Free** | **Pro** | **Team** |
|---|---|---|---|
| Price | $0 | ~$20–30/dev/mo | ~$40–60/seat/mo |
| Seats | 1 (+2 view-only) | 1–5 | 5–500 |
| Projects / runs | 2 repos, ~50 agent-runs/mo cap (enforced) | unlimited repos, generous run caps | pooled run budget + per-team budget controls |
| Governance | plan gate + basic audit trail (the wedge, free — it's the hook) | + checkpoints, rules files, notifications | + shared runs/collaboration, roles granularity, audit export |
| Cost visibility | own spend visible | + per-step model selection | + budgets, alerts, per-team rollups |
| Sandbox/security | standard allow-list | same | + custom policies, priority on interstitial approvals |
| Deployment | cloud | cloud | cloud **or self-host** (self-host gated here — it's the sovereignty SKU) |

**What gates what:** enforce free caps at the API layer (plan-gate dependency on project-create/run-start, honest 409s when billing is disabled — fix the currently-unguarded `PUT /billing/plan`); unify cap resolution so plan budgets actually gate runs (close the `check_budget` split-brain); tie "team" limits to real seat counts; map every tier boundary to a feature the roadmap ships (checkpoints/rules → Pro; collaboration/budgets/self-host → Team) so gating reads as value, not crippleware.

### Trial mechanics
Time-boxed (14-day) Team trial on signup — flag on the team row with expiry checked at request time, conversion tracked (raw-11 notes none of this exists). Trial gets full Team features including one seeded demo project (N8) so evaluation starts paved. When Stripe lands: checkout session creation, webhook ingest + reconciliation, dunning state machine, proration, invoices from `cost_ledger` — until then paid plans stay honor-system strings, so do not charge anyone before the billing wave closes. Seed demo/billing plan data in `seed_demo.py` so sales can show tiers. Self-host stays free-to-run under the chosen license (LICENSE decision is itself a NOW blocker) with commercial terms attached to support/update entitlements rather than runtime kill-switches.

---

## 5. Metrics to instrument from day one

Raw-11 verdict: zero product analytics today; the onboarding-funnel UI exists but step completion is never recorded server-side; the event bus already carries most of what we need. Instrumentation is therefore cheap — log milestones as events, don't build a parallel pipeline.

### The funnel (signup → project → first run → first PR)
1. **signup_created** → account exists (OTP-verified post-N5).
2. **repo_connected** → first real repo connected; time since signup.
3. **brief_submitted** → first brief/plan requested.
4. **plan_gate_decided** → first approve/reject (the governance moment).
5. **run_completed** → first agent run reaches terminal state.
6. **pr_opened** → first PR lands (N3 wired). *This is the activation event.*
Track step-to-step conversion + median time between steps; the onboarding checklist UI should read its state from these recorded milestones, not local guesses.

### Activation targets
- **Aha (< 10 min):** seeded demo opened (N8) or repo connected.
- **Activated (< 24 h):** plan gate approved AND run completed on a real repo.
- **Value confirmed (< 72 h):** PR opened and/or checkpoint restored after a bad run — the trust loop closing.
Funnel cliffs diagnose which wave is failing: drop-off at repo_connected → onboarding/N9 problem; at plan_gate → N2 friction; at run_completed → engine/config reliability (N6); at pr_opened → N3 gaps.

### Retention signals
- Weekly Active Teams / WAU-per-seat; repeat runs per repo (habit formation).
- Login recency + abandoned-run rate + invite-acceptance rate (all derivable from existing auth/audit tables per raw-11).
- Notification engagement: outbox open/click-through (the outbox is already "a ready-made engagement dataset with zero queries").
- Correction stickiness: do users stop re-explaining conventions after X2 ships?
- Spend trajectory per team (post-N7 ledger completeness ≥ 99% — alert on metering gaps via dead-man's switch).

### Guardrails
Cost per completed run vs. cost per merged PR; gate latency (the differentiator must stay fast — raw-21 warns slow gates read as friction to individual devs); sandbox rejection rate and false-positive approvals (X6 quality); audit-event write failures (C2 regression alarm).

