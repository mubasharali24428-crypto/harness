# Harness — FUTURE PLAN 2.0

**Planning baseline:** 13 November 2026 · branch `w28-s1-delegate`, HEAD `8836dfa` (the older plans cite `7c1acf4` and `332d273`; Phase 0 pins the exact revision).
**Status:** Proposed. Nothing in this file is implemented, approved or dated by itself. Verdict today: **NOT READY** for production or a general pilot (`STATUS.md`).
**Replaces as the working plan:** `FUTURE PLAN.md` (11 Nov), `docs/plans/90-day-execution-plan-2026-11-11.md`, and `docs/roadmap-3.0.md` (31 Oct). Those three files are archived under `docs/archive/` (2026-11-22, X-05 docs diet) as historical references and are not edited. Every item below keeps its old ID in the **Src** column so nothing is lost.

## 0. How to read this plan

1. **One product, three lenses.** The three source plans describe the same product from different angles: Roadmap 3.0 = *trust and governance of agent work*; the 90-day plan = *design partners, first revenue, evidence-gated scale*; FUTURE PLAN = *the multiplayer workroom with role-based privacy*. This plan sequences all three so that each phase's output is an input to the next.
2. **Gates, not dates.** Phases end at a gate with written evidence. The only calendar date is the 90-day commercial gate (9–11 Feb 2027), because the commercial plan is time-boxed. Everything else is relative.
3. **Evidence states** (from `FUTURE PLAN.md` §2): `Not started` · `In progress` · `Implemented—unverified` · `Verified at <revision>` · `Blocked` · `Deferred by <decision>`. A task is done only when its evidence is linked in the repo at a named revision.
4. **Owner decides.** Items marked **[OWNER]** need a written decision record before dependent work starts. Defaults in §10 are planning assumptions, not approvals.
5. **Sprint 4 evidence note (2026-11-24).** Engineering evidence, not owner sign-off: the X-07 UI half is verified in Chromium against the real compose stack at `8a78c5dc` (`docs/briefs/evidence/sprint4/task-24-live-approval-e2e.md`, axe + 375 px included); `pg-probe` is green on compose `:5499` at `49445e4f`. The collab `viewed_detail` signal is measured but not yet wired into the section8 KPI (`docs/briefs/evidence/sprint4/task-27-collab-viewed-detail.md`). No gate moved; verdict stays **NOT READY**.

## 1. Product direction

Harness is a multiplayer AI engineering workspace. An admin brings people and agents together around a project. AI turns a goal and constraints into a reviewable plan with role-specific assignments. Each member sees only their authorized work. The admin sees the whole project: people, agents, plans, approvals, activity, cost and results. Agents execute only inside approved boundaries, through authorized and confined tools, and never merge or grant permissions themselves.

**Headline experience:** the shared, role-specific workroom. **Trust layer:** governance, confinement, evidence, cost and recovery, which is what lets client teams accept AI-written work. Neither multiplayer nor any single control is claimed as unique without a dated competitor comparison (E-10).

**Privacy invariant (owner requirement, non-negotiable):** members see their own assigned work and explicitly authorized project information; only project admins see everything. Visibility is enforced by the server in API, WebSocket events, search, exports, notifications, artifacts and agent context. Hiding UI elements is not enforcement.

## 2. Non-negotiable boundaries

Carried from `docs/archive/roadmap-3.0.md` and `docs/archive/FUTURE PLAN.md` §2; these apply to every phase.

- Frozen contracts change only with a version bump, regenerated TypeScript mirrors, mocks and conformance tests. Schemas use new numbered migrations; migrations are forward-only.
- Section ownership: Section 1 owns identity, secrets and event delivery; Section 4 owns event semantics; Section 5 confines execution and enforces the allow-list and forbidden paths. No undocumented cross-section imports.
- No auto-merge, no silent model downgrade, no unapproved execution, no per-keystroke analysis, no second workflow authority by default.
- Secrets never enter logs, events, model context or cache entries.
- External dependencies: pinned versions, licence review and security assessment before use.
- An optimizer or model can recommend; it never grants permissions, bypasses forbidden paths or merges work.
- Every section keeps its golden-fixture conformance tests.
- Human owners decide SLOs, legal terms, security review, provider budget, paid tests and release. Implementation does not invent those decisions.

## 3. Roles

Accountable roles from `FUTURE PLAN.md` §3. Assigning named people happens at kickoff (F-01), not in this file.

| Role | Accountable for |
|---|---|
| Owner / product lead | Decisions, priorities, budget, risk acceptance, release, sales and final review |
| Technical lead / integrator | Architecture, contracts, branch reconciliation, release candidate |
| Security / identity lead | Authorization, confinement, privacy, threat model |
| Backend / orchestration lead | APIs, planning, assignments, runs, events, queues, providers |
| Frontend / design lead | Admin and member workspaces, accessibility, browser acceptance |
| Quality / evaluation lead | Test strategy, journeys, benchmarks, negative tests, evidence ledger |
| Infrastructure / SRE lead | Deployment, backup, alerting, scale, drills |
| Pilot / customer lead | Design partners, onboarding, pilot measurement |
| Legal / privacy adviser | Terms, privacy, data processing, external legal review |
| Independent reviewer | Security and release review; never reviews its own implementation |

Fleet roles from the 90-day plan (S = build, B = backend, G = gates) map onto the backend, frontend and quality rows above. Only the owner (R in that plan) marks anything `Verified at <revision>`.

## 4. Phase map

| Phase | Name | Source plans | Gate that ends it |
|---|---|---|---|
| 0 | Baseline and decisions | FP F-01…F-08; R3 Stage 0 | Clean reviewed baseline; owner decision records D-01…D-09 started |
| 1 | Trust, recovery and confinement | R3 Stage 1 (W21–W22); FP A-01, A-02, A-06; 90D P-04, P-06 | Deployment-level drills at a named revision |
| 2 | Reliability and operations | R3 Stage 2 (W17); FP A-09, E-04, E-07; scale tiers | Measured capacity, restore and alert evidence, named SLO owner |
| 3 | Daily loop and first partner | R3 Stage 3 (W23–W24, W7); 90D Month 1 (P-01…P-08); FP A-10, A-11 | Partner completes one governed task unaided; counsel engaged |
| 4 | Governance and delegation | R3 Stages 5–6 (W27, W28, W13); FP A-03, A-04, A-05 | Live approval-bound task, two-agent cancel test, real remote-host run |
| 5 | Measured pilot and benchmark | R3 Stage 4 (W25–W26); 90D Month 2 (P-09…P-15); FP A-08 | Mid-pilot go/no-go; ≥1 written pay-intent |
| 6 | Multiplayer foundation | FP Milestone B (B-01…B-09) | Pilot-gate authorization test set passes (B-09) **[OWNER: P-D7]** |
| 7 | AI planning and assignment | FP Milestone C (C-01…C-08) | Admin-reviewed proposal workflow passes C-07 |
| 8 | Speed and cache | FP §9A (S-01…S-11, CD-01, CD-02) | Same-model, same-task, ≥5-trial comparison |
| 9 | Integration audit and ecosystem | R3 Stages 7, 8, 9, 10; FP E-01, E-02; R3 candidate list | Written adopt/pilot/defer/reject record per candidate |
| 10 | Collaboration depth, scale and release | FP Milestones D, E; 90D Day-90 gate (P-16…P-25) | Independent broad-release review and owner decision |
| UI | Dedicated UI week (§5A) | UI overhaul plan U0–U6; Kimi UI brief; FP A-10, D-06 | UI week exit checklist (§5A.6) |
| 10X | The 10X program (§20) | `docs/briefs/harness-10x-plan-2026-11-22.md` (landscape analysis + owner ideas, 22 Nov 2026) | Wave gates in §20; evidence per §16 |

Dependency order: `0 → 1 → (2 ∥ 3) → 4 → 5 → 6 → 7 → 10`, with 8 measured from Phase 1 onward and 9 running alongside 4–7. The UI week runs after Phase 0 and in parallel with Phases 1–3 (it does not need deployments). Phase 6 schema design (B-01, B-02) may start in Phase 1. The 10X program (§20) runs alongside: Wave A in Phases 0–1, Wave B in the UI week and Phases 3–4, Wave C in Phase 8, Wave D as a Phase-9-style candidate, Wave E in Phases 4–5.

## 5A. Dedicated UI week

Owner priority: the UI is the top priority (`docs/plans/ui-overhaul-plan-2026-11-13.md`). The UI plan and the Kimi brief were written as plans and prompts, and some of their work is now committed. This section records what is actually built and schedules one full week for everything that is not.

### 5A.1 Verified status (checked 13 Nov 2026 at HEAD `8836dfa`)

Evidence used: commit history on `w28-s1-delegate`; grep of `section7/`; `npx vitest run` in `section7` (106 files, 1,153 tests, all pass, working tree including uncommitted edits); `npx tsc --noEmit` (exit 0). Playwright e2e was **not** run (it needs the running stack).

| Plan item | Status | Evidence and gap |
|---|---|---|
| **U0** Foundation (tokens, shell, palette, shortcuts, states, 100 ms budget) | **Partly implemented** | Committed: `d491121` (Tailwind v4 on existing tokens, command palette, go-to shortcuts), `a664a65` (palette actions, keyboard journey with axe and 100 ms budget). Gap: "every existing page on the new shell" is not shown; the 9 settings pages and ~120 components are not audited. |
| **U1** Home (inbox, mission control, tab-icon status) | **Implemented, not live-verified** | Kimi T1–T2, `6d51e12` (U1 finish, demo loop fix); `HomeTriage.tsx`, `triage.ts`. Gap: the timed task (oldest approval in under 10 s, keyboard only) has not been run; no mission-control view of all running agents was found. |
| **U2** Run workspace (timeline, scrubber, Follow, receipts, editable plan) | **Partly implemented** | Kimi T3, `f7c740a`: run timeline with scrubber and Follow; secrets scrubbed from receipts. Gap: "plan as an editable object that creates a new version" is not built (`PlanPanel.tsx` has no versioning). Not tested at 40 steps. |
| **U3** Review (diff centre, inline comment → agent, per-chunk accept/revert, verdict card, risk tiers) | **Partly implemented** | Kimi T4, `d304f56`: verdict card using only the PR's own run. `hunkReview.ts` provides hunk selection. Gap: inline comment that becomes an agent instruction: not found. Per-chunk accept/revert wiring: not confirmed end to end. Risk-tiered approval: not built, correctly, because D-U4 is open. |
| **U4** Evidence, cost, trace waterfall, trust portal | **Not implemented** | No trace waterfall, cost page or portal found. Blocked on P-17 (scoped read-only role). |
| **U5** First run (demo, onboarding, key tour, accessibility sweep, phone triage) | **Partly implemented** | Kimi T5 phone-width checks and `e2e/phone.e2e.ts`; demo loop fix in `6d51e12`. Gap: 5-key tour, under-60-second first run, phone triage view (approve/deny only), recorded screen-reader journey: none found. |
| **U6** Presence (avatars, who is viewing, mute) | **Not started, gated** | Needs D-01 and P-D7. Do not build in this week. |
| Kimi brief tasks T1–T5 | **Committed** | `6d51e12`, `f7c740a`, `d304f56`. Each still needs the reviewer's live check. |

**Correction to earlier wording:** the Kimi prompts are not all unimplemented. Tasks T1–T5 are in committed code. What remains is the gap column above.

### 5A.2 Week goals

By Friday, every U0–U5 item is either **Verified at a revision** (with a Playwright journey, axe pass, before/after screenshots in `docs/briefs/evidence/ui/`, and a timed task) or has a written **Blocked** or **Deferred** state with its owner. U4 and U6 are scoped, not built, unless their gates have opened.

### 5A.3 Guardrails for the week

- No deploys, no `docker compose` changes, never `down -v`, never touch the owner's demo containers (`harness-*` without `-rc-`).
- No paid model calls. The journey that starts real runs (`e2e/journey.e2e.ts`) runs only with the reviewer present, against an approved budget.
- No auto-approval of anything. Risk tiers stay off until D-U4 is decided.
- Commit by explicit path only. About 20 files in `section7/` are uncommitted from other lanes: do not revert or format them.
- Each section's tests run from its own directory. Frontend: `cd section7 && npx vitest run && npx tsc --noEmit`.

### 5A.4 Day-by-day

| Day | Work | Owner role | Done when | Src |
|---|---|---|---|---|
| **Mon** | **Baseline and decisions.** Run `vitest`, `tsc`, then the e2e suites that do not start runs (`palette`, `home`, `a11y`, `phone`, `demo`). Record every skip with its reason. Take before-screenshots at 375, 768 and 1280 px in light and dark. Investigate the "Try the demo" loop reported in UI plan §1. Owner records D-U1 (component stack: Tailwind v4 + shadcn/ui + cmdk, recommended), D-U2 (dark-first, recommended), D-U3 (U0 before P-17, recommended) and D-U4 (risk tiers off). | Frontend + owner | Baseline ledger at one revision; four decision records; demo loop root cause written down | UI plan §6, Kimi T2 |
| **Tue** | **U0 completion.** Inventory every route and component still on the old stylesheet (`globals.css`, 1,178 lines). Move the app shell and the nine settings pages onto the new tokens and primitives. Keep old pages working during migration. Confirm: no single-key shortcut fires inside an input; focus order is correct; the 100 ms budget test covers every route, not only the palette. | Frontend | Every route renders on the new shell; palette reaches every route; axe clean; inventory file committed | U0, UI plan §5 risks |
| **Wed** | **U2 completion.** Plan as an editable object: edits create a new plan version with approver and timestamp (`PlanPanel.tsx`, plan-gate API). Receipts for each step: what changed, where, under which permission, diff and undo. Load a synthetic 40-step run and scrub to any step. Test Follow on a live-updating run without a model call (fixture stream). | Frontend + backend | 40-step scrub test passes; plan edit produces version 2 and keeps version 1 readable; receipt undo tested | U2, UI plan §5 |
| **Thu** | **U3 completion.** Inline comment on a diff line becomes an instruction to the agent (wire to the existing comment and instruction path; no new authority). Per-chunk accept and revert wired end to end. "Since last iteration" as the default view, using checkpoints. Negative test: a risky path (forbidden or outside scope) still requires explicit approval, even when all other checks pass. Keyboard-only review of one run using only the verdict card and diff. | Frontend + backend | Keyboard-only approval works; risky-path negative test passes; comment-to-instruction tested against a fixture | U3, UI plan §4 Review |
| **Fri** | **U5 first run and accessibility.** Five-key tour (taught in menus and tooltips, skippable). Demo that starts a finished run in under 60 s, without a loop. Phone triage view: approve or deny only, nothing else. Recorded manual screen-reader journey (one run from login to approval) with findings and owners. U1 timed task: oldest pending approval found and opened in under 10 s, keyboard only, recorded. | Frontend + quality | Timed tasks recorded; screen-reader findings resolved or owned; axe clean on the journey | U5, U1, FP A-10, FP D-06 |
| **Sat (buffer)** | Fix failures from Mon–Fri. Re-run the full suite and the e2e set. No new features. | Frontend | Green at the final revision, skips listed with reasons | 90D P-25 |
| **Sun** | Rest, or owner review of the evidence ledger. | Owner | Ledger entry for the week under `docs/briefs/evidence/ui/week-ui.md` | 90D Friday ledger rule |

**Scoped, not built this week:** U4 (trace waterfall, cost page, export, read-only portal) is scoped against the P-17 role design so it is ready when that gate opens. U6 (presence) waits for D-01 and P-D7.

### 5A.5 Who does what

- **Frontend lead:** Tue–Fri build and tests; owns the inventory and the screenshots.
- **Backend lead:** Wed–Thu plan versions, comment-to-instruction and risky-path test, reusing existing APIs.
- **Quality lead:** Mon baseline, Fri screen-reader journey, Sat regression.
- **Owner:** Mon decisions D-U1 to D-U4; Sun review.
- **Reviewer:** no paid run and no deploy; reruns the checks and decides what is committed.

### 5A.6 UI week exit checklist

1. U0 complete: every route on the new shell, palette reaches every route, axe clean, 100 ms budget on every route.
2. U1 timed task recorded under 10 s, keyboard only.
3. U2 40-step scrub and plan versioning tested; receipts show what changed, where, which permission, with undo.
4. U3 inline comment becomes an instruction; per-chunk accept/revert and "since last iteration" tested; risky-path negative test passes.
5. U5 five-key tour, under-60-second demo run, phone triage (approve/deny only), screen-reader journey recorded.
6. Every item in §5A.1 is `Verified at <revision>`, `Blocked` (with owner) or `Deferred` (with decision). Screenshots are in `docs/briefs/evidence/ui/`.
7. U4 scoped against P-17; U6 untouched and marked gated.

## 5. Phase 0 — Baseline and decisions (start now)

| ID | Work | Owner role | Done when | Src |
|---|---|---|---|---|
| 0.1 | Adopt this plan or amend it. Name owners, capacity, decision makers and a weekly review. | Owner + technical lead | Decision record; named roster; prioritized backlog | F-01 |
| 0.2 | Inventory `main`, remote PRs, unique commits, worktrees and untracked work (`docs/plans/branch-inventory-2026-11-11.md` is the start). Dispositions only; no destructive cleanup. | Integrator | Inventory with commit IDs per branch | F-02, 90D P-02 |
| 0.3 | Reconcile the uncommitted W22 changes and the staged login conflict resolution. Review local auto-sign-in before any deployment. | Integrator + security | Each change committed, dropped or dispositioned; no unresolved conflicts | R3 Stage 0, R3 Immediate 1 |
| 0.4 | Capture exact revision, running image digests, configuration and source mapping; produce a drift report. Use scratch infrastructure for tests. | SRE + integrator | Versioned environment manifest; `scripts/ops/stack_tags.py` clean | F-03, A-06 |
| 0.5 | Run suites with skip reasons per section directory (`cd sectionN && .venv/bin/python -m pytest tests`). Record each skip as environmental or defect. | Quality | Test/skip ledger at the pinned revision | R3 Stage 0, R3 Immediate 2 |
| 0.6 | Refresh the capability matrix and 61-part crosswalk; mark every stale count and claim. | Quality | Each row linked to a revision and result; sample independently reviewed | F-04 |
| 0.7 | Decide threat model, supported hosts, path-confidentiality semantics and treatment of shell-scope expected failures. | Owner + security | Written decision; testable requirements | F-05 |
| 0.8 | Start external dependencies: independent security review, counsel, on-call owner, design-partner recruitment, restore planning. | Owner + SRE + legal | Named contact, scope and expected date per dependency | F-07, 90D P-08 |
| 0.9 | Establish one reviewed integration baseline and release branch. | Integrator | Reproducible clean checkout; CI green; explicit dispositions | F-08 |
| 0.10 | Decide the revision the commercial plan uses (the 90-day plan cites `7c1acf4`; current HEAD is `8836dfa`). | Owner | Recorded in the decision ledger | Conflict C5 |

**Owner decisions opened here:** D-01 deployment order (self-host first is the default); D-02…D-09 (§11); P-D3 rename; P-D4 pilot price band; P-D5 open-source flip.

## 6. Phase 1 — Trust, recovery and confinement

Goal: prove the sandbox and recovery claims at deployment level before any partner runs real work.

| ID | Work | Owner role | Done when | Src |
|---|---|---|---|---|
| 1.1 | Shell gate: reject chained commands (`;`, `&&`, `||`, pipes, backticks, `$(...)`, newlines); argument-level path checks; one program per call. Part 52 left items. | Security + backend | Negative and adversarial tests on each supported host; independent review | A-01, R3 Stage 1, PROJECT-STATE §4.1 |
| 1.2 | Document what OS, container and network boundary each backend enforces. Every remaining A-01 exception has an owner and risk note. | Security | Document per backend; no claim beyond the drill evidence | A-01, 90D §0.4 |
| 1.3 | Dedicated worker/daemon review; Landlock and egress drill on the intended Linux host at the exact revision. | Security + SRE | Drill output; holes fixed or risk-accepted by name | A-02, 90D P-04 |
| 1.4 | Confinement coverage for each supported backend; child and worker cancellation; orphan cleanup. | Backend + SRE | No false cancellation confirmation; process disappearance recorded | R3 Stage 1 |
| 1.5 | Lifecycle truth, partial-write reconciliation, replay, restart and provider-recovery drills. Keep failures and unknowns. | Backend + SRE | Before/after deployment-level drill evidence; repeated where reliability is claimed | R3 Stage 1, A-05 part |
| 1.6 | One reviewed image set; digests and migration state recorded; rollback compatibility checked. | SRE | `stack_tags.py` no drift; rollback drill on scratch | A-06, 90D P-06 |
| 1.7 | Negative-test run on the deployed tag for every shell scope exception. | Quality | Results attached to the tag | 90D P-06 |
| 1.8 | Start B-01 and B-02 design (permission matrix and versioned schemas) without implementing them. | Product + security + backend | Reviewed matrix; contract review scheduled | FP B-01, B-02 |

**Gate 1:** drills at a named revision; no false cancellation; owner acceptance of remaining exceptions.

## 7. Phase 2 — Reliability and operations

| ID | Work | Owner role | Done when | Src |
|---|---|---|---|---|
| 2.1 | Terminal-event delivery (W17), cross-replica work (W19), remaining failure drills; reconcile work orders and live evidence. | Backend + SRE | Drill evidence per failure class | R3 Stage 2 |
| 2.2 | Dry and real-provider load on the target topology. Provider experiments only with an agreed budget. | SRE + quality | Measured capacity and failure outcomes | R3 Stage 2, E-04 |
| 2.3 | Scale tiers A1–A5: prove 5–20 concurrent runs per team first, including a real-model 20-run test and a second worker host. | SRE + quality | Published N, latency, resource use and failures | E-04, `docs/decisions/2026-11-06-scale-tiers.md` |
| 2.4 | Alerts delivered to an owned endpoint (email plus optional webhook today; pager integration is an open gap). Backup freshness monitored. | SRE | Tested alert and backup-freshness evidence | A-09, E-07 |
| 2.5 | Restore on the intended deployment (point-in-time restore is proven on file storage and self-hosted S3, not a cloud bucket). | SRE | Restore evidence on intended deployment | R3 Stage 2, PROJECT-STATE §4.7 |
| 2.6 | SLOs and error-budget rules adopted; on-call, escalation and public-status ownership assigned. **[OWNER]** | Owner + SRE | Named owners; SLO document | A-09, R3 Owner gates |
| 2.7 | Observability hardening: redaction, tracing, incident response exercise. | SRE | Exercise record; redaction tests | E-07 |
| 2.8 | Remove measured bottlenecks for ~100 concurrent runs only when a customer requires it: shared breaker, claim locking, pooling. | SRE | Load, failover and soak on multi-host topology | E-05 (deferred until demand) |

**Gate 2:** measured capacity, restore and alert evidence, named SLO owner.

## 8. Phase 3 — Daily loop and first partner

| ID | Work | Owner role | Done when | Src |
|---|---|---|---|---|
| 3.1 | First-run readiness checks (provider, model, repository, worker, permissions); stale-daemon detection; honest live status. | Frontend + backend | Fresh-setup run passes; stale daemon detected | R3 Stage 3 (W23) |
| 3.2 | Real stop and steering; steering states shown when they take effect. | Backend | Real stop evidence | R3 Stage 3 (W23), R3 detailed W23–W24 |
| 3.3 | Review summary: changed files, tests, failures, cost, elapsed time, uncertainty. Keyboard-first diff review. Reconcile `ui-ux-audit-2026-10-25.md` items. | Frontend | Review screen checked against each audit item | R3 Stage 3 (W24) |
| 3.4 | VS Code extension completes one real-window task through policy, approval and reviewed diff. Hermes H3 excluded (see C3). | Backend + frontend | Real-window end-to-end evidence (W7) | R3 Stage 3, E-01 |
| 3.5 | Accessibility sweep and manual screen-reader journey. | Frontend + quality | Findings resolved or accepted with owners | A-10, `docs/briefs/PROJECT-STATE.md` row 56 |
| 3.6 | Design-partner recruitment (20 outreach messages), 10 conversations, pilot SOW template and price band. Kill criterion: fewer than 2 signed partners after 20 real conversations → stop feature work and revisit positioning. | Pilot lead + owner | Ledger; SOW template; ≥2 verbal LOIs | 90D P-01, P-03 |
| 3.7 | Sign 2 design-partner SOWs with success criteria, consent and support contact. Decide D-01 (P-D1) and rename shortlist (P-D3). | Owner + pilot lead | Two signed SOWs in the legal pilot directory; decision records | 90D P-05 |
| 3.8 | Partner #1 boots the stack and completes one governed task unaided (tutorial journey). | Pilot lead + frontend | Recorded run | 90D P-07 |
| 3.9 | Payment rail decision (P-D2): verify seller eligibility for each candidate with primary evidence. No customer payment data before terms approval. | Owner + legal | Decision record with eligibility evidence | 90D P-07, P-D2, C2 |
| 3.10 | Counsel review of terms packet and `PRICING.md`; counsel engagement with named contact and date. | Legal + owner | Engagement record | A-11, 90D P-08 |

**Gate 3:** partner completes one governed task unaided; counsel engaged; rail decision recorded.

## 9. Phase 4 — Governance and delegation

| ID | Work | Owner role | Done when | Src |
|---|---|---|---|---|
| 4.1 | Version-bound plans, scope, risk and verification; policy denials explain the reason, offer a safe alternative and allow a scope request. Retry or model change only when the user selects it. | Backend | Denial and scope-request flow live | R3 Stage 5 (W27), A-03 |
| 4.2 | Actual versus estimated cost; the run read returns estimate and actual distinctly. Provider spend in `cost_ledger`; unknown spend identified. | Backend | No misleading cost claim | A-03, R3 Evidence |
| 4.3 | Repeated-denial circuit breaker and stop, reconciled with W27 slices 1–3. | Backend | Repeat-denial count tested | A-03, R3 W27 |
| 4.4 | Hx: decide which limits (no `break`, one workflow per run, cost not enforced, no revocation mid-run, no replay of failures) need work for product use. Do not claim isolation from Hx itself. | Owner + backend | Evidence-based decision per limit | R3 Hx scope |
| 4.5 | W28 subagents: scope binding, cancellation, conflict handling, visible ownership, reviewable synthesis with real model output. Child approvals; children cannot exceed parent grants. | Backend | Two-agent scenario with one cancelled, no orphan, reviewed synthesis | A-04, R3 Stage 6, C-06 |
| 4.6 | W13 remote BYO-runner agent loop: lease fencing, cancellation, re-dispatch, replay, transport hardening. Keep local proof separate from remote proof. | Backend + SRE | Real remote-host run and interruption drills; no false completion | A-05, R3 Stage 6 |
| 4.7 | Verification-first runs: done requires test evidence from the executor, never a model claim. | Backend | Negative test: model-supplied "passed" rejected | I-09, S-03 rule |
| 4.8 | Denial-learning loop: every human correction becomes a project rule plus a regression test. | Backend | Repeat-denial count → 0 on pilot tasks | I-08, P-23 |
| 4.9 | Rollback-native artifacts: each run ships a revert commit and migration-compatibility note. | Backend | Artifact check in run output | I-11 |
| 4.10 | Risk-tiered approval envelopes: **only if P-D6 approved**; risky paths still gated; manual merge preserved. | Owner + security + backend | Written policy; negative tests | I-05, P-21, FP D-05/D-06 |

**Gate 4:** approval-bound live task, denial and recovery demonstrations, two-agent cancel test, real remote-host run.

## 10. Phase 5 — Measured pilot and benchmark

| ID | Work | Owner role | Done when | Src |
|---|---|---|---|---|
| 5.1 | Both partners live and supervised; daily intervention log; weekly review call; target ≥10 real tasks per week per team. | Pilot lead | Task log with counts, interventions, denials and cost per task | 90D P-09 |
| 5.2 | Fix only the top-3 friction items from the logs. No speculative features. | Backend + frontend | Before/after evidence | 90D P-10 |
| 5.3 | Benchmark: held-out real-repository tasks, ≥5 independent trials per task per model, same-conditions comparator, p50/p95 latency, cost, interventions, recovery; every failure retained; cold/warm starts and concurrency. Rerun the fixed live set. | Quality | Versioned protocol; raw results; limits stated; no superiority claim from single trials | A-08, 90D P-13, R3 W25–W26 |
| 5.4 | Model decisions: keep Haiku as stack default only if the benchmark supports it; decide model or retirement for the six locally pinned tasks. | Quality + owner | Decision record | R3 W25–W26 |
| 5.5 | Mid-pilot go/no-go per partner; collect "would pay $X" in writing at a named price. | Owner | ≥1 written pay-intent | 90D P-14 |
| 5.6 | Pre-review digest: cheap reviewer summarises risk before human review; human review time measured before and after. | Backend | Digest on a real partner PR; time measured | I-10, P-11 |
| 5.7 | GitHub verdict bot: plan approver, sandbox boundary, tests, cost and risk flags on every agent PR; negative test: no secrets in comment. | Backend | Bot comment on a real partner PR | I-02, P-15 |
| 5.8 | Preview-only evidence summary for the verdict: unknown is shown as unknown, never as passed or $0. | Backend + frontend | Rule enforced by test | UI overhaul plan §4 and the W8 verdict comment, FP §1 |
| 5.9 | Public benchmark with raw data and methodology (leaderboard idea), only after 5.3 passes review. | Owner + quality | Published results link | I-04, P-18 |

**Gate 5:** ≥10 tasks per week per team sustained; intervention rate trending down; ≥1 written pay-intent.

## 11. Phase 6 — Multiplayer foundation (gated)

Goal: implement the admin/member visibility model safely. Design work (6.1–6.2) starts in Phase 1. Build work (6.3–6.9) starts only after the owner decides P-D7 (C1).

| ID | Work | Owner role | Done when | Src |
|---|---|---|---|---|
| 6.1 | Define team, project, project admin, member, agent, assignment, role; separate organisational admin from project admin. | Product + security | Reviewed permission matrix (create, invite, read, assign, approve, steer, export, revoke, delete) | B-01, D-02 |
| 6.2 | Versioned schemas: membership, role/capability profiles, task ownership, task visibility, agent ownership and relationships. | Backend + security | Contract review; migration plan; TS mirror; compatibility tests | B-02 |
| 6.3 | Admin-controlled invitation, acceptance, removal, role change and transfer. | Backend | Idempotent lifecycle; expired or revoked invite denied; removal revokes sessions per policy | B-03 |
| 6.4 | Server-side project and assignment authorization; scoped queries and event subscriptions. | Security + backend | Cross-tenant and cross-member negative tests across API, WebSocket, search, exports, notifications and artifacts | B-04 |
| 6.5 | Assignment lifecycle: proposed → approved → accepted → in progress → blocked or review → complete; rejection, reassignment, handoff. | Backend | Durable state, actor, timestamps, concurrency handling, audit events | B-05 |
| 6.6 | Admin workspace: project goals, plan, people, agents, ownership, dependencies, approvals, progress, cost, exceptions. | Frontend | Multi-member journey, accessible, permission-filtered | B-06 |
| 6.7 | Member workspace: only authorized tasks, instructions, agent work, status, requests and notifications. | Frontend | Two members cannot see each other's restricted data via UI or direct request | B-07 |
| 6.8 | Least-privilege context assembly for planner, member-facing agents, summaries, logs and model calls. | Security + backend | Canary tests: restricted content does not leak via outputs, retrieval, tools, events or errors | B-08 |
| 6.9 | Invite-to-revoke test with an admin and at least two differently skilled members, including concurrent updates and reconnect. | Quality | Browser and API evidence at one revision | B-09 |

**Gate 6 (pilot authorization):** B-09 passes; the multiplayer slice is threat-modelled; owner and independent reviewer approve. Until then, the pilot runs single-member with admin review.

## 12. Phase 7 — AI planning and role-specific assignment

| ID | Work | Owner role | Done when | Src |
|---|---|---|---|---|
| 7.1 | Structured project brief: goals, constraints, roles, capabilities, dependencies, priorities, outputs, acceptance criteria. | Product + backend | Schema reviewed; unknown skills and absent members represented honestly | C-01 |
| 7.2 | Generate a versioned plan and proposed human/agent assignments from authorized context only: rationale, uncertainty, dependencies, estimated cost, missing expertise. | Backend | No fabricated availability | C-02 |
| 7.3 | Admin planning review: edit, reassign, reject, approve. Each change creates a new plan version with approver, timestamp and scope. | Frontend + backend | Conflicts trigger renewed review | C-03, D-05 |
| 7.4 | Deliver each approved task and only its authorized context to the assignee; clarification, rejection, progress, evidence and handoff requests. | Backend | Security engineer gets security work; others cannot fetch restricted details | C-04 |
| 7.5 | Shared dependencies with minimum authorized information; admin-mediated requests for more context. | Backend | No restricted text in dependency status or notifications; grants and revocations propagate | C-05, D-03, D-04 |
| 7.6 | Agents attached to approved assignments with per-run authority, bounded workspace, cost ceiling, cancellation and output review. | Backend + security | Human and agent assignments distinguishable; no auto-merge | C-06, D-06 |
| 7.7 | Evaluate plan usefulness, role fit, omissions, unsafe assignments and human corrections on held-out scenarios and pilot feedback. Record disagreement, not only completion. | Quality + pilot lead | Results with failures recorded | C-07 |
| 7.8 | Scheduler neutral allocation as an optional proposal source only, independently validated. No dispatch, vendoring or expanded permissions. | Backend | Consistent with W31 decision; rights cleared before any bundling | C-08, R3 Stage 8, C4 |

Pilot posture: C runs first as an admin-reviewed proposal workflow. No autonomous assignment or execution beyond approved policy.

## 13. Phase 8 — Speed and cache track

**Principle:** harness speed comes from fewer model turns, parallel work, cheaper turns and skipped repeated work. Model time is the ceiling: if the model is 80% of wall time, a perfect harness gives about 1.25×. Measure first. Every speed claim needs a same-model, same-task, ≥5-trial comparison.

| ID | Work | Owner role | Done when | Src |
|---|---|---|---|---|
| 8.1 | Measure first: split wall time into model, tool, harness overhead and human wait; turns and cache-read tokens per run; add the split to `scripts/eval/bench_stats.py`. | Quality + backend | p50/p95 baseline on held-out and live tasks | S-01 |
| 8.2 | Stable prompt prefix (system prompt, tool schemas, memory snapshot at run start); volatile data last; test fails if volatile data enters the prefix. | Backend | Cache-read tokens and TTFT before/after | S-02 |
| 8.3 | Parallel independent tool calls and subagent fan-out measured against sequential runs. | Backend | Turns and wall time on multi-file tasks | S-09 |
| 8.4 | Content-addressed result cache, starting with test, lint and typecheck. Key: namespace, schema version, operation, canonical arguments, input content hash, environment fingerprint. Hermetic operations only; a flaky test is never cached as a pass; only the executor writes entries; per-tenant namespaces; never cache secrets or credentialed responses; store provenance. | Backend + security | Hit rate, time saved and false-hit rate with cache on and off | S-03 |
| 8.5 | Shadow verification: re-run a sample of cache hits; a mismatch disables that entry class until fixed. | Quality | Mismatch rate reported | S-04 |
| 8.6 | Cache governance: size cap with LRU, tenant purge for erasure, retention holds, manual purge. Immutable entries have no TTL; mutable external sources get a short TTL plus revalidation. Policy per CD-01. | SRE + security + legal | Written policy; purge and erasure tests | S-05, CD-01 |
| 8.7 | Model-call cache only for deterministic uses on immutable content; key includes model id and version, prompt hash and sampling settings. Not for general agent steps. | Backend | No silent reuse across model versions | S-06 |
| 8.8 | Warm sandbox pool, workspace snapshots keyed by lockfile hash, persistent shell. | SRE | Cold-start seconds before and after | S-07 |
| 8.9 | Cache warming and adaptive TTL, applied at next request or session, never mid-run in the prefix. | Backend | Hit-rate gain without prefix-cache breakage | S-08 |
| 8.10 | Learned memory and skills (ideas only, borrowed from public Hermes Agent docs, MIT): staged proposals for human approval, provenance, size caps, scrubbing, trust levels, repo-local skills not loaded until trusted. Promote a rule only if the benchmark improves. Scope per CD-02. | Backend + security | Staged-write flow; scrubber tests; benchmark evidence per promoted rule | S-10, I-08, C3 |
| 8.11 | Provider cache experiments: hosted open-model prompt caching and self-hosted prefix cache on AMD Developer Cloud (credits time-limited). Verify terms first. | Quality + owner | Same-conditions comparison; model id and version recorded | S-11 |

**Not to copy:** free-write agent memory by default; "same OS user plus a denylist" as a boundary; cross-tenant cache sharing; time limits as the correctness mechanism; caching general agent-step responses.

## 14. Phase 9 — Integration audit and ecosystem

Every candidate gets a written adopt, pilot, defer or reject decision with evidence. Popularity and README claims do not count.

| ID | Candidate | Decision rule | Src |
|---|---|---|---|
| 9.1 | Scheduler (OR-Tools) | Synthetic-workload prototype only, after recovery is proven; compare its timetable-specific contract with a fresh model; no real dispatch without approval; keep the existing queue and policy authoritative. | R3 Stage 8, W31, C-08 |
| 9.2 | Hx language | Use existing implementation; close limits only when a use case demands it; no isolation claim. (See 4.4.) | R3 Hx |
| 9.3 | Hermes | Separate project. Rights and distribution review before any integration. Ideas only (8.10); no code or bundling. | R3 Candidate, C3 |
| 9.4 | Drift Workbench | Optional permissioned diagnostics through the tool gateway; restricted network destinations; redacted evidence. | R3 Candidate |
| 9.5 | Headroom (context optimisation) | Benchmark quality, cost and privacy first; keep review-critical source access. | R3 Candidate |
| 9.6 | Agentic immunity | Compare attestation and revocation ideas with existing identity, policy and audit. Do not create a second authority. | R3 Candidate |
| 9.7 | Tree-sitter | Compare symbol indexing with existing LSP and repository maps. | R3 Candidate |
| 9.8 | OpenTelemetry | Evaluate instrumentation and redaction; telemetry cannot leak secrets or cross tenants. | R3 Stage 9, E-07 |
| 9.9 | Langfuse | Optional model-evaluation tool; component licence review. | R3 Candidate |
| 9.10 | Temporal | Comparison experiment only; no second workflow authority by default. | R3 Candidate |
| 9.11 | Odysseus and Dify | Study UX and features; licence implications; no wholesale merge. | R3 Candidate |
| 9.12 | book-to-skill | Optional import of untrusted documents, with review. | R3 Candidate |
| 9.13 | Provider proxies | Respect user-selected models and secret controls. | R3 Candidate |
| 9.14 | Third-party MCP | Assess with the release review (Phase 10). | R3 Stage 10 |
| 9.15 | Verify VS Code, CLI and tool-permission behaviour across clients; structured file/search/edit operations; diagnostics. | Same governed workflow from every supported client. | E-01 |
| 9.16 | Web, browser, visual inputs and computer use | Approved egress and isolation; reproducible visual task; no blanket parity claim. | E-02 |
| 9.17 | Provider compatibility, capability-aware routing, BYOK, health, explicit fallback, cost reconciliation | Unsupported-model refusal; estimate versus actual distinct; user-controlled changes only. | E-03, I-07 (cheap-model routing, recorded never silent) |
| 9.18 | Dated comparison with Claude Code, Codex, Muse, Grok Bot and others | Official sources and reproducible trials; scope and dates stated. | E-10, R3 W29/W33 |

## 15. Phase 10 — Collaboration depth, scale and release

| ID | Work | Owner role | Done when | Src |
|---|---|---|---|---|
| 10.1 | Real-time scoped updates, approximate presence, reconnect, replay and offline recovery. | Frontend + backend | Ordering and gap tests; no presence to unauthorized users | D-01 |
| 10.2 | Task-scoped discussion, mentions, handoffs, decisions and notifications with user controls. | Frontend + backend | Each audience permission-checked; no private text in previews | D-02 |
| 10.3 | Concurrency: version checks, conflicts, cross-member dependency resolution. | Backend | Simultaneous changes do not overwrite | D-03 |
| 10.4 | Artifacts, diffs, versions, GitHub integration, review and manual merge; access checks follow artifacts into previews, downloads and repo actions. | Backend + frontend | Review proves who approved what | D-04 |
| 10.5 | Permission-aware search, history, export, retention and deletion; legal holds. | Backend | Revoked content absent from all surfaces, including caches | D-05 |
| 10.6 | Localisation strategy, responsive and mobile needs, onboarding from pilot demand. | Frontend + pilot lead | Supported languages and clients stated accurately | D-06 |
| 10.7 | Optional agent management, skills, memory and scoped integrations, each with consent, least privilege, provenance and revocation. | Backend | Test evidence per extension | D-07 |
| 10.8 | Capacity for ~1,000 per team only when a customer and hardware justify it. | SRE + owner | Claim only tested capacity | E-06 |
| 10.9 | Threat modelling, independent penetration test, remediation, tenant-isolation review. | Security + independent reviewer | Findings fixed or risk-accepted | E-08, FP A-11 |
| 10.10 | Hosted versus self-hosted order, support model, terms, privacy, residency and pricing. **[OWNER]** | Owner + legal + SRE | Written operating model; no payment integration before decisions | E-09, C2 |
| 10.11 | Commercial Day-90 gate (9–11 Feb 2027): ≥2 signed SOWs by Day 28 and 3–5 teams by Day 83; ≥$1K MRR or ≥3 written pay-intents; payment rail live with a recorded test transaction; Linux sandbox evidence on the deployed tag; benchmark published; 13 weekly evidence entries; multiplayer B decision recorded. | Owner | Dated decision record per gate line | 90D §0, P-16…P-24 |
| 10.12 | Pricing v2 from real cost-per-task data; outcome-pricing meter from ledger actuals. | Owner | Pricing decision record | 90D P-20, I-12 |
| 10.13 | Open-source flip decision (P-D5). | Owner | Public repo, or recorded reasons to stay private | 90D P-20 |
| 10.14 | Independent broad-release review and owner go/no-go. Requires: reviewed security, privacy and legal posture; production deployment and restore evidence; support and SLO ownership; supported-host matrix; reliable authorization across surfaces; versioned evidence. | Independent reviewer + owner | Written release decision | FP §10, R3 Stage 10, W33 |
| 10.15 | Stabilisation: flake fixes, ledger completeness, status refresh at final revision. | Quality | Green gates at the final revision | 90D P-25 |

## 16. Recurring rules

- **Weekly ledger:** every week ends with one evidence entry per active task (state, revision, raw result link) under `docs/briefs/evidence/`. No entry means the task is not done.
- **Owner time cap:** ≤60% build and review. Month one: ≥40% of owner hours on conversations and distribution.
- **Fleet handbacks are inputs, not verification.** Only the owner marks `Verified at <revision>`.
- **Cost:** record provider spend in `cost_ledger`; unknown spend is labelled unknown. Paid provider tests need an agreed budget.
- **Evidence:** keep failures and skips; do not loosen preregistered thresholds; rerun vision evaluations when models change; record source and image revisions and the scope of each result.
- **Concurrent work:** before each deploy, check whether another session or lane shares the live stack; never `down -v`; commit by explicit path only; run each section's tests from its own directory.

## 17. Owner decision register

| ID | Decision | Default until decided | Needed before |
|---|---|---|---|
| D-01 | Self-hosted first, managed cloud first, or both | Self-host first; preserve both; no new cloud-only dependency | Phase 3 |
| D-02 | Who can be project admin; multiple admins; ownership transfer | Project admins fully visible; lifecycle needs owner approval | Phase 6 |
| D-03 | Member sees only assigned items, or shared goals and dependency status too | Assigned and explicitly authorized information only | Phase 6 |
| D-04 | Direct member communication, visibility requests, handoffs, invitations | Requests go to admin; no implicit access or invitation authority | Phase 6 |
| D-05 | Admin approval for every human task and agent plan | AI proposes; admin approves assignments; mandatory agent plan approval stays | Phase 7 |
| D-06 | Members using agents on their own tasks; agent access beyond owner | No wider access than explicit grants; existing approval and merge rules | Phase 7 |
| D-07 | Fixed roles, custom capabilities, or both; availability and workload | Admin-maintained attributes; no authority inferred from a title | Phase 7 |
| D-08 | Leaver, unavailable admin, ownership recovery | Revoke promptly; keep audit; authorized admin reassigns | Phase 6 |
| D-09 | Providers, jurisdictions, supported hosts, external reviewers for pilot | Existing documented pilot limits; no unsupported compliance claims | Phase 5 |
| P-D2 | Payment rail: Paddle or FastSpring (verify seller eligibility) vs US LLC with Stripe | Verify first; billing flag-off until decided | Phase 3 |
| P-D3 | Product name (collision with Harness Inc.) | Shortlist and trademark check; no public launch under the colliding name | Phase 3 |
| P-D4 | Pilot pricing band | $500–2K per month per partner; counsel review before any quote | Phase 3 |
| P-D5 | Open-source flip (MIT chosen; repo private) | Stays private until 10.13 | Phase 10 |
| P-D6 | Approval-envelope policy (4.10) | Not enabled | Phase 4 |
| P-D7 | Multiplayer build go/no-go (Gate 6) | Deferred; decided from pilot demand evidence, not momentum | Phase 6 |
| CD-01 | Cache retention, tenancy and sharing | No cross-tenant sharing; month-scale cap; purge on erasure | Phase 8 |
| CD-02 | Learned memory and skills in scope for pilot | Not in scope; proposals only, never auto-applied | Phase 8 |
| X-D1 | Approval rendering per member (Technical / Plain / Decision card): who sets it | Member self-selects own rendering; admin may set it for a member; rendering never changes disclosed facts or the policy-computed risk tier | Wave B (X-07) |
| X-D2 | Spend cap for 10X benchmark trials (X-14) | Dry corpus and local models first; paid trials only with an agreed budget (§16 cost rule) | Wave D (X-14) |

## 18. Conflicts between the source plans (resolved here, owner to confirm)

| # | Conflict | Resolution in this plan |
|---|---|---|
| C1 | FUTURE PLAN makes Milestone B (multiplayer) required before real-user rollout; the 90-day plan defers multiplayer to a Day-90 decision (P-D7). | B design runs early; B build waits for P-D7 (Gate 6). Until then the pilot is single-member with admin review. **[OWNER]** |
| C2 | 90-day plan needs a live payment rail by Day 90; FUTURE E-09 blocks payment integration until operating decisions; Roadmap 3.0 keeps Stripe flag-off. | Rail decided in Phase 3 (3.9); no payment before terms approval; Day-90 test transaction only if the owner approves that rail. **[OWNER]** |
| C3 | Roadmap 3.0 treats Hermes as a separate project needing rights clearance; FUTURE PLAN says out of scope; S-10 borrows ideas from its public docs. | Ideas only (8.10); no code, bundling or integration until rights are cleared (9.3). |
| C4 | Roadmap 3.0 stage 8 prototypes Scheduler; FUTURE C-08 allows proposal-only use and no dispatch. | Compatible: synthetic-workload prototype only (9.1); no real dispatch. |
| C5 | Baseline revision differs across plans (`7c1acf4`, `332d273`, current `8836dfa`). | Phase 0 item 0.10 pins one revision. |
| C6 | Dates: the 90-day plan has dates (Day 1 = 11 Nov 2026); Roadmap 3.0 and FUTURE PLAN have none. | Calendar only for the 90-day gate; all other phases gate on evidence. |
| C7 | Roadmap 3.0 lists W21–W33 work-order labels; the 90-day plan lists P-IDs; FUTURE PLAN lists A–E. | Every item here keeps its source ID in the Src column. |
| C8 | Stage 4 (benchmark) and 90-day benchmark both require ≥5 trials; FUTURE A-08 agrees. | Consistent: ≥5 trials per task per model everywhere. |

## 19. Source index

- Repo: `STATUS.md`, `README.md`, `AGENTS.md`, `contracts/`, `PRICING.md`, `PRIVACY.md`, `TERMS.md`.
- Plans superseded as working documents (kept as history under `docs/archive/` since 2026-11-22): `docs/archive/FUTURE PLAN.md`, `docs/archive/90-day-execution-plan-2026-11-11.md`, `docs/archive/roadmap-3.0.md`; plus the 24 `docs/archive/m2-w*-plan.md` legs and `docs/archive/STATUS-history-to-2026-11-06.md`.
- Still-current inputs: `docs/plans/two-week-release-candidate-plan-2026-11-06.md`, `docs/plans/ui-overhaul-plan-2026-11-13.md`, `docs/briefs/PROJECT-STATE.md`, `docs/briefs/capability-matrix.md`, `docs/briefs/evidence/s12/part52.md`, `docs/decisions/2026-11-04-w31-scheduler.md`, `docs/decisions/2026-11-05-w30-candidate-audit.md`, `docs/decisions/2026-11-06-scale-tiers.md`, `docs/hx-execution-architecture.md`, `docs/hx-language-spec.md`, `docs/legal/`, `docs/pilot-remediation/`, `docs/runbooks/`.
- `docs/briefs/harness-10x-plan-2026-11-22.md` — the 10X program detail: landscape analysis, owner product ideas (approval rendering by reader; geometric context store evolution), wave rationale, measurement and kill criteria.
- Old estimates and counts in any source are snapshots. Re-verify before relying on them.

## 20. The 10X program (added 2026-11-22)

Cross-cutting program from `docs/briefs/harness-10x-plan-2026-11-22.md`: close the table-stakes gaps competitors already have (compaction, caching, subagents, warm pools), productize the empty space this repo already owns (deterministic replay, governance plane, integrated evals, external security), and delete process waste. **Boundaries unchanged (§2).** All items follow §0 evidence states and the §16 recurring rules; evidence lands under `docs/briefs/evidence/10x/`. Standing KPIs from this program: blind-approval rate, cache-read ratio, fan-out token multiplier, intervention rate. Sequencing: Wave A is a prerequisite for every wave; Wave C's S-02/S-03 foundation is judged before X-14's store comparison.

### Wave A — Fix what is broken (Phases 0–1; starts immediately)

| ID | Work | Owner role | Done when | Src |
|---|---|---|---|---|
| X-01 | Repair CI startup (F-SEC-01): ci.yml jobs run again on master; gitleaks, pip-audit and the readiness-drift job execute as scheduled. | SRE | CI green on master; scanners' first executed runs archived under `docs/briefs/evidence/10x/` | 10X Wave A; scorecard "what moves the number soonest" #1 |
| X-02 | One `make test-all` target that auto-provisions services (compose db via `pg-up`, Docker P0 images) and runs the ~130 flag-gated tests; live-model/paid groups stay explicitly opt-in. | Quality | One command runs the PG + Docker P0 + promtool groups; skip ledger printed; paid/local-model groups still refuse without explicit flags | 10X Wave A |
| X-03 | Devloop frictions: compose journey frontend URL follows the host (`E2E_FRONTEND_URL` default), `make dev-otp` replaces log-grep for the dev OTP, and a fresh-clone → `make bootstrap` → `make gate` journey is documented and verified once. | SRE + frontend | e2e-compose passes on the reference host without manual override; journey recorded | 10X Wave A |
| X-04 | Execute the pending sandbox decisions: G1 route/container boundary, G3 root-equivalent daemon (rootless or risk-accepted by name), G2 Linux half re-drilled on the deployed tag. | Security + owner | Scorecard G-B ≥ 12/16 with named evidence at the deployed set | 10X Wave A; scorecard P2 decisions |
| X-05 | Docs diet: AGENTS.md becomes a ≤100-line table of contents pointing into structured docs (OpenAI pattern); superseded plans and `m2-wN` files move to `docs/archive/` (moves only, no content rewriting); STATUS.md splits into a ≤200-line current-readiness file plus history. | Integrator + quality | TOC live; archive in place with links updated; current STATUS file ≤200 lines | 10X Wave A |
| X-06 | Owner decision blitz: dated decision records for D-01…D-09, P-D2…P-D7, CD-01/02 (confirm the default or amend). **[OWNER]** | Owner | Every register row carries a dated record | 10X Wave A; §17 |

### Wave B — Approval ergonomics (UI week + Phases 3–4)

| ID | Work | Owner role | Done when | Src |
|---|---|---|---|---|
| X-07 | Approval rendering by reader: three renderings — Technical, Plain, and a structured Decision card — on the approval and verdict surfaces; profile-based default (member self-selects own rendering; admin may set it for a member, X-D1); deterministic templates first with an optional cached model line obeying 5.8 (unknown shown as unknown); technical detail always one tap away; risk badge = icon + color + text; keyboard-only and axe-clean journeys; blind-approval rate instrumented. Tone is translation, never replacement: the policy engine, never prose, decides what is disclosed. | Frontend + backend | All three renderings pass the keyboard journey and axe; template snapshot tests green; KPI dashboard shows blind-approval rate | 10X Wave B; UI U3; plan 4.1, 5.6, 5.8 |
| X-08 | Wire risk-tiered approval envelopes (4.10) with batch review and "since last iteration" as the default view once D-U4/P-D6 are decided; risky paths stay gated; manual merge preserved. | Backend + security | 4.10 negative tests pass; blind-approval KPI tracked before/after on pilot tasks | 10X Wave B; 4.10; D-U4/P-D6 |
| X-21 | **Decision card severity grid (owner suggestion, Mubashar Ali):** enhancement to the **Decision card** view in X-07 when the member profile is Decision card (X-D1). Render a visually prominent **severity grid matrix**: **rows** = policy-classified access/action families (file read, file write, network/egress, shell/execute, credentials/secrets, configuration change, merge/deploy, scope expansion); **columns** = severity bands low → moderate → elevated → critical; **each active cell** color-coded with a fixed accessible palette (icon + text per X-07 — never color alone). The **aggregate risk profile** (dominant band + highlighted cells) must be readable at a glance above Approve/Deny before the user acts; Plain and Technical renderings unchanged. Mapping comes only from the policy engine and run contract, not model prose; technical detail and paths stay one tap away. | Frontend + backend | Golden fixtures show correct grid cells per access family and severity; policy-only mapping unit tests green; axe-clean keyboard journey reads the grid before approve/deny | 10X Wave B; X-07; X-D1; UI U3; 4.1; P-D6 |

### Wave C — Context economics (Phase 8)

| ID | Work | Owner role | Done when | Src |
|---|---|---|---|---|
| X-09 | Stable prompt prefix (S-02): system prompt, tool schemas and the memory/skills snapshot frozen at run start; volatile data last; a CI test fails if volatile data enters the prefix. | Backend | Cache-read tokens and TTFT before/after on the eval corpus (S-01 split in `bench_stats.py`) | 10X Wave C; S-01/S-02 |
| X-10 | Content-addressed result cache MVP for tests/lint/typecheck (S-03) with shadow verification (S-04): hermetic operations only; executor-only writes; per-tenant namespaces; provenance stored. | Backend + security | Hit rate, time saved and false-hit rate measured with cache on and off | 10X Wave C; S-03/S-04 |
| X-11 | Cache economics as a first-class metric: Grafana panel for cache-read ratio per run/team next to cost; `cost_ledger` distinguishes cache reads from full-price reads. | SRE + backend | Panel live on RC with real run data; ledger distinguishes reads | 10X Wave C; A-03 |

### Wave D — Context store and curators (Phase-9-style candidate)

| ID | Work | Owner role | Done when | Src |
|---|---|---|---|---|
| X-12 | Context-graph store prototype (candidate): Postgres-backed simplicial store — vertices = entities with payload references; structural edges plus curator-maintained similarity edges; faces = three-way facts with provenance; attach/subdivide on overflow and collapse/coarsen for compaction and decay; operations event-sourced and replayable; caps mirroring the `memory_loader` limits; hash-ordered canonical serialization feeding the prefix. Geometry is logical (UI), never byte layout, and never enters prompts. | Backend + security | Prototype passes unit + property tests; serialization order-stability test green | 10X Wave D; S-02, CD-02 |
| X-13 | Curator subagents for store maintenance (sleep-time, off the request path) on W28 delegation: propose similarity links and coarsenings; the existing reviewed-status gate applies before injection; no new authority (§2). | Backend | Proposal → review → injection flow tested; canary tests confirm restricted content never leaks (6.8 pattern) | 10X Wave D; W28, B-08 |
| X-14 | Layout benchmark: plain graph vs hex lattice vs simplicial store on the eval corpus — retrieval quality (decision-provenance queries), latency, token cost and cache-read rate; prime-modulo hashing routed into the S-03 key design if it wins the hashing role. | Quality | Versioned protocol, raw results and an adopt/pilot/defer/reject record per the Phase 9 rules; kill criterion per the brief | 10X Wave D; A-08; §14 discipline |
| X-15 | Hex context map in the admin workspace: read-only visualization of the store rendering member-scoped subgraphs only. | Frontend | Canary test: restricted content absent from the map for unauthorized roles (6.8 pattern) | 10X Wave D; U4 |

### Wave E — 10X differentiators (Phases 4–5; X-20 in Phase 8)

| ID | Work | Owner role | Done when | Src |
|---|---|---|---|---|
| X-16 | Durable execution semantics: resume from the last completed effect without re-spend; selective checkpointing at effect boundaries (extends 1.5/4.6 using `run_checkpoints` + Hx recordings; blanket per-turn checkpointing is not a goal). | Backend + SRE | Kill-mid-run drill resumes a fixture with zero duplicated provider spend; the checkpoint policy documents the >75%-of-turns finding | 10X Wave E; 1.5/4.6 |
| X-17 | Subagents GA (finish W28 S1b): scope binding (children never exceed parent grants), cancellation, visible ownership, reviewable synthesis with real model output; parallel tool calls (S-09); token multiplier shown before fan-out with a per-session cap. | Backend | Gate-4 two-agent cancel test passes; fan-out cost guard test green | 10X Wave E; 4.5, S-09 |
| X-18 | Deterministic replay as a product feature: extend Hx recordings + event replay to full runs (record model calls and tool effects; refuse incomplete), with a UI entry point to replay a run. | Backend | Any failed run in the fixture set replays exactly; replay entry point live | 10X Wave E; Hx M7 |
| X-19 | "Harness coverage" metric: rubric counting recurring failure classes detected + prevented + replayable (inputs: denial-learning, eval corpus, drills); first measurement published with the benchmark. | Quality | Metric defined; first measurement in the 5.3 benchmark report; candidate row for the E-10 dated comparison | 10X Wave E; I-08, 5.3 |
| X-20 | Memory/skills evolution: keep the digest-pinned review model; learned proposals (S-10) and curator consolidation (X-13) are promoted only if the benchmark improves. | Backend + quality | Promotion decisions recorded per the S-10 gate; CD-02 scope respected | 10X Wave E; S-10/CD-02 |

**Owner decisions opened here:** X-D1, X-D2 (§17).

**Maintenance:** the technical lead updates task state weekly and after each release. The owner approves scope or priority changes by decision record. This file does not certify implementation, compliance, readiness or dates.
