# Harness — Future Plan

**Planning baseline:** 11 November 2026  
**Purpose:** One forward-looking execution plan for Harness, consolidating the existing product plans and adding the owner's role-based multiplayer vision.  
**Planning status:** Proposed working plan. Task owners below are accountable roles, not claims that a person has accepted an assignment. The project owner assigns named people, dates, budgets and capacity at kickoff. Existing signed decisions and release controls remain binding until explicitly revised.

## 1. Product vision

Harness is a multiplayer AI engineering workspace where an administrator brings people and AI agents together around a shared project. AI helps turn a goal into a plan and role-specific assignments; each member has a workspace containing the work and information authorized for them. The administrator has a project-wide view of people, agents, plans, tasks, approvals, activity, dependencies and results.

**Example:** An admin invites frontend, backend and security engineers. AI proposes a plan that assigns interface work to the frontend engineer, API work to the backend engineer, and threat analysis and security checks to the security engineer. Each member sees their own authorized tasks and relevant information. The admin reviews the whole plan, progress, agent work and final result. People can accept, clarify, complete or hand off work within their authority; agents operate within approved boundaries.

**Default privacy requirement from the owner:** Individual members see their own assigned work and authorized project information; only project admins see the complete project-wide work of every person and agent. Visibility must be enforced by the server, event delivery, search, exports, notifications, artifacts and agent context—not merely by hiding interface elements. Specific cross-member sharing, dependencies and emergency admin access require explicit product decisions and authorization rules; do not silently expand access.

**Product hypothesis:** The shared, role-specific workroom is the headline experience. Governance, security, model choice and self-hosting make that experience trustworthy. Neither multiplayer nor any single control is asserted to be unique without a current competitive comparison.

## 2. Baseline and evidence discipline

- Reviewed primary checkout: `w28-s1-delegate`, commit `7c1acf4`, one local commit ahead of its tracked remote on 11 November; untracked files exist. This is not a clean release baseline. Do not overwrite, delete, merge or deploy existing work as part of planning.
- `STATUS.md` and the 6 November RC plan state **NOT READY** for production or a general pilot. The latest documented deployment used mixed image tags and lagged source; current live state was not independently checked for this plan.
- There are substantial existing platform components and prior passing drills. The historical 61-part matrix and old readiness/effort estimates are snapshots, not present-day completion claims. Reconcile each claim against source, tests, deployment and dated evidence before marking it complete.
- The multiplayer concept above is an explicit future requirement. Its complete role-specific privacy and assignment workflow has not been established as implemented.
- Older `EXPANSION_PLAN.md` says cloud-first/self-host later; the current README and roadmap emphasize self-hosting. **Decision D-01:** owner must settle deployment and commercial priority. Preserve both deployment options until then; do not silently treat the older plan as current policy.
- Existing rules: frozen contracts require versioning, regenerated TypeScript mirrors, mocks and conformance updates; no auto-merge, no silent model downgrade, no unapproved execution, secrets excluded from logs/model context, and forbidden paths remain hard constraints. See `AGENTS.md`, `contracts/` and `docs/roadmap-3.0.md`.

**Evidence states for every task:** `Not started`, `In progress`, `Implemented—unverified`, `Verified at <revision>`, `Blocked`, or `Deferred by <decision>`. A checkmark means its acceptance evidence is linked, not simply that code exists. All tasks here start **open/revalidate** unless a current evidence record proves otherwise. Earlier work should be reused, not rebuilt by default.

## 3. Roles, assignments and working rules

| Role | Accountable work |
|---|---|
| **Owner / product lead** | Product decisions, priorities, budget, risk acceptance, final release decision and named staffing. |
| **Technical lead / integrator** | Architecture, contracts, branch reconciliation, dependencies, integration and release candidate. |
| **Security / identity lead** | Authorization, confinement, privacy, threat model, adversarial tests and independent security-review coordination. |
| **Backend / orchestration lead** | Project/task APIs, AI planning, assignments, runs, events, queues, providers and agent coordination. |
| **Frontend / design lead** | Admin and member workspaces, interaction design, accessibility and browser acceptance. |
| **Quality / evaluation lead** | Test strategy, real-user journeys, benchmarking, negative tests, evidence ledger and independent validation. |
| **Infrastructure / SRE lead** | Deployment, reliability, backup, alerting, scale, observability and operational drills. |
| **Pilot / customer lead** | Design partners, research, onboarding, pilot measurement and support. |
| **Legal / privacy adviser** | Terms, privacy, data-processing and external legal review. |
| **Independent reviewer** | Review security and release evidence without approving their own implementation. |

A small team may combine roles, but a security or release decision needs independent review. Proposed assignment to a role is not a staffed commitment. The project owner controls merges, deployments and paid tests under the existing operating rules. Work can proceed in parallel only where contracts and file ownership are agreed.

## 4. First actions — start now

| ID | Task and accountable owner | Acceptance evidence / dependency |
|---|---|---|
| F-01 | **Owner + technical lead:** adopt or amend this plan; name owners, team capacity, decision makers and a weekly review cadence. | Decision record, named roster and prioritized backlog; no inferred deadlines. |
| F-02 | **Integrator:** inventory main branch, remote PRs, unique commits, worktrees and untracked work; identify superseded patches without destructive cleanup. | Reviewed inventory with commit IDs and disposition per branch. |
| F-03 | **SRE + integrator:** capture exact running images, digests, configuration and source mapping; compare with checkout. | Versioned environment manifest and drift report; use scratch infrastructure for tests. |
| F-04 | **Quality lead:** refresh capability matrix and 61-part crosswalk; label current evidence, missing evidence, stale claims and owners. | Every row linked to an exact revision and result; independently review a sample. |
| F-05 | **Owner + security lead:** decide current pilot threat model, supported hosts, path/read confidentiality semantics, and treatment of known shell-scope expected failures. | Written decisions and testable requirements; no claim of complete isolation from shell checks alone. |
| F-06 | **Owner + product/design leads:** validate the admin/member experience with at least two representative teams; resolve D-02–D-07 below. | Signed scenarios, permission matrix, task lifecycle and pilot scope. |
| F-07 | **Owner + SRE + legal:** start independent security review, legal review, incident/on-call ownership, design-partner recruitment and restore planning. | Named contact, scope, expected date and status for each external dependency. |
| F-08 | **Integrator:** establish one reviewed integration baseline and release branch; preserve unique work. | Reproducible clean checkout, CI and explicit branch dispositions; no unreviewed release merge. |

## 5. Milestone A — trustworthy private design-partner candidate

**Goal:** Complete the existing release-candidate work before exposing the platform to a real team. This milestone follows the detailed two-week RC plan as a task source, not as a promise that it will take two weeks.

| ID | Work and owner | Done when |
|---|---|---|
| A-01 | **Security:** fix or explicitly disposition shell argument/path scope failures, symlink and traversal cases; document what OS, container and network boundaries each backend actually enforces. | Negative and adversarial tests run on each supported host; independent review; every remaining exception has an owner and documented risk. |
| A-02 | **Security + SRE:** review dedicated worker/daemon, host certification, per-run isolation, egress, secret handling and fail-closed behavior. | Real boundary drills, cancellation and cross-run isolation evidence on the intended platform. |
| A-03 | **Backend:** reconcile W27 denials, scope requests, repeated-denial stop, versioned approvals and actual versus estimated costs. | Live approved task and denial/recovery flow; no misleading permission or cost claim. |
| A-04 | **Backend:** finish W28 scope binding and cancellation semantics; validate child approvals and synthesis with real model output. | Two-agent scenario, including cancellation, no orphan resources and reviewable output, on the release build. |
| A-05 | **Backend + SRE:** finish W13 real remote-host agent loop, lease fencing, cancellation, re-dispatch and replay; separate currently proven local runner behavior from remote proof. | Remote-host end-to-end and interruption drills; no false completion. |
| A-06 | **SRE:** reconcile and deploy one reviewed image set; record digests, migration state and rollback compatibility. | Drift checker clean and real-provider smoke on the deployed version; rollback/restore drill on scratch and intended deployment. |
| A-07 | **Quality:** run required CI, section suites, frontend build, real PostgreSQL migration and browser journey. Track skips, flakes, failures and environment differences. | Green gates at exact release revision; independent review of exceptions. |
| A-08 | **Quality:** rerun approved benchmark with held-out tasks, multiple trials, latency, cost, interventions, failures and same-conditions comparator. | Raw results, methodology and limitations linked; no success-rate or superiority claim from single trials. |
| A-09 | **SRE + owner:** adopt measurable SLOs, alert delivery, on-call, backup freshness and incident/rollback responsibilities. | Named owners and tested alert/restore evidence. |
| A-10 | **Frontend + quality:** complete accessibility sweep and manual screen-reader journey; confirm onboarding, honest run state and review flows. | Recorded findings resolved or accepted with owners; live browser journey. |
| A-11 | **Legal + owner + independent reviewer:** complete pilot terms, privacy/security review, rights decisions and release review. Billing remains off unless separately approved. | Written gate decisions and owner go/no-go. |

## 6. Milestone B — role-based multiplayer foundation

**Goal:** Implement the owner's admin/member visibility model safely before AI assigns real team work. This is a new product requirement; first audit existing identity, membership, collaboration, policy, task and event code to avoid duplicating it.

| ID | Work and owner | Done when |
|---|---|---|
| B-01 | **Product + security:** define team, project, project admin, member, agent, assignment and role; distinguish organizational admin from project admin. | Reviewed permission matrix for create, invite, read, assign, approve, steer, export, revoke and delete; approved defaults. |
| B-02 | **Backend + security:** design versioned schemas for membership, role/capability profiles, task ownership, task visibility, agent ownership and relationships. | Contract review, migration plan, TS mirror and compatibility tests; no undocumented cross-section imports. |
| B-03 | **Backend:** create admin-controlled invitation, acceptance, removal, role change and transfer flows. | Idempotent invitation lifecycle; expired/revoked invite denied; removal revokes access and active sessions according to policy. |
| B-04 | **Security + backend:** implement server-side project and assignment authorization, scoped queries and event subscriptions. | Cross-tenant and cross-member negative tests for API, WebSocket, search, exports, notifications and artifacts; admin full view works. |
| B-05 | **Backend:** model assignment lifecycle: proposed → approved/assigned → accepted/in progress → blocked/review → complete, including rejection, reassignment and handoff. | Durable state, actor and timestamps, concurrency handling, audit events and clear recovery rules. Exact state names subject to design. |
| B-06 | **Frontend:** build admin workspace showing whole-project goals, plan, people, agents, task ownership, dependencies, approvals, progress, cost and exceptions. | Admin completes a real multi-member journey with accessible UI and accurate permission-filtered data. |
| B-07 | **Frontend:** build member workspace showing only their authorized tasks, relevant instructions, agent work, status, requests and notifications. | Two members cannot view each other's restricted information via UI or direct requests; empty/error states work. |
| B-08 | **Security + backend:** enforce least-privilege context assembly for planner, member-facing agents, summaries, logs and model calls. | Canary tests show restricted member content does not leak through outputs, retrieval, tools, events or error messages. |
| B-09 | **Quality:** test invite-to-revoke with an admin and at least two differently skilled members, including concurrent updates and reconnect. | Browser/API integration evidence at the same revision; unauthorized access denied throughout. |

## 7. Milestone C — AI planning and role-specific assignment

| ID | Work and owner | Done when |
|---|---|---|
| C-01 | **Product + backend:** define structured project brief, constraints, roles, capabilities, dependencies, priorities, expected outputs and acceptance criteria. | Schema and example plans are independently reviewed; unknown skills and absent members represented honestly. |
| C-02 | **Backend / AI:** generate a structured plan and proposed human/agent assignments using authorized context only. | Versioned plan identifies rationale, uncertainty, dependencies, estimated resource/cost and missing expertise; no fabricated availability. |
| C-03 | **Frontend:** provide an admin planning review to edit, reassign, reject or approve proposals before assignments or agent runs take effect. | Changes create a new plan version; clear approver identity, timestamp and scope; conflicts prompt renewed review when required. |
| C-04 | **Backend:** deliver each approved task and only its authorized context to the assigned member; support clarification, rejection, progress, evidence and handoff requests. | Security engineer receives security-specific work; other members cannot fetch restricted details; admin sees all. |
| C-05 | **Backend:** represent shared dependencies using the minimum authorized information, with an admin-mediated request when a member needs additional context. | No dependency status or notification leaks restricted text; approved grants/revocations propagate to cached and live views. |
| C-06 | **Backend + security:** attach agents to approved assignments with per-run authority, bounded workspace, cost ceiling, cancellation and output review. | Human and agent assignments remain distinguishable; child runs cannot exceed parent or member grants; no auto-merge. |
| C-07 | **Quality + pilot lead:** evaluate plan usefulness, role fit, omissions, unsafe assignments and human corrections on realistic tasks. | Repeated held-out scenarios and real pilot feedback; record failures and disagreement, not just completion. |
| C-08 | **Backend:** assess Scheduler neutral allocation as an optional proposal source only; independently validate outputs. | Respect existing pilot-only decision: no dispatch, vendoring or expanded permissions; distribution rights cleared before bundling. |

## 8. Milestone D — complete collaborative workroom

| ID | Work and owner | Done when |
|---|---|---|
| D-01 | **Frontend + backend:** real-time scoped updates, approximate presence, reconnect/replay and offline recovery. | Ordering/gap tests; no presence or events to unauthorized users; degraded mode is clear. |
| D-02 | **Frontend + backend:** task-scoped discussion, mentions, handoffs, decisions and notifications with user controls. | Each audience is permission-checked; private text and notification previews cannot leak. |
| D-03 | **Backend:** concurrent control, edit conflicts, locking/version checks and cross-member dependency resolution. | Simultaneous changes do not overwrite; conflicting scope/plan changes require appropriate review. |
| D-04 | **Backend + frontend:** artifacts, diffs, versions, GitHub integration, review and manual merge flow. | Access checks follow artifacts into previews/downloads and repo actions; review proves who approved what. |
| D-05 | **Backend:** permission-aware search, history, export, retention and deletion. | Revoked content is absent from all surfaces and reindex/cache tests; legal holds handled as specified. |
| D-06 | **Frontend + pilot lead:** accessibility, localization strategy, responsive/mobile needs and onboarding based on pilot demand. | WCAG-oriented checks and human usability sessions; supported languages and clients stated accurately. |
| D-07 | **Backend:** optional agent management, skills, memory, context controls and scoped integrations. | Each extension has explicit consent, least-privilege access, provenance, revocation and test evidence. |

## 9. Milestone E — capability, reliability and scale

| ID | Work and owner | Done when |
|---|---|---|
| E-01 | **Backend:** verify usable coding tools: safe structured file/search/edit operations, diagnostics, CLI and VS Code real-window task. | Same governed workflow works from supported clients; tool permissions and source revisions are recorded. |
| E-02 | **Backend + security:** evaluate web, browser, visual inputs, previews and computer use against real engineering needs. | Approved egress and isolation, reproducible visual task and permission checks; no blanket claim of competitor parity. |
| E-03 | **Backend:** improve provider compatibility, capability-aware routing, BYOK, health, explicit fallback and cost ledger reconciliation. | Provider contract tests, unsupported-model refusal, clear user-controlled changes and estimate/actual distinction. |
| E-04 | **SRE + quality:** prove 5–20 concurrent runs per team first, including real-model 20-run test and second worker host. | Follow `docs/decisions/2026-11-06-scale-tiers.md` A1–A5; publish tested N, latency, resource use and failures. |
| E-05 | **SRE:** remove measured bottlenecks for ~100 when customer demand warrants: shared breaker, claim locking, fleet, pooling, event and heartbeat costs. | Load, failover and soak results on representative multi-host topology. |
| E-06 | **SRE + owner:** plan 1,000 per team only when a customer and hardware justify it. | Fleet, approval-at-scale, rate limits, chaos and sustained load demonstrated; claim only tested capacity. |
| E-07 | **SRE:** harden observability, redaction, tracing, backup/restore, disaster recovery, incident response and capacity. | Alert delivered to an owned endpoint; restore and incident exercises on the intended deployment. |
| E-08 | **Security + independent reviewer:** complete threat modeling, independent penetration testing, remediation and tenant isolation review. | Findings fixed or explicitly risk-accepted; release gate reviewed independently. |
| E-09 | **Owner + legal + SRE:** decide hosted versus self-hosted order, support model, legal terms, privacy, data residency and pricing. | Written operating model; no payment integration enabled before availability, provider and legal decisions. |
| E-10 | **Product + quality:** maintain a dated comparison of Claude Code, Codex, Muse/Muse Code, Grok Bot and other relevant agents using official sources and reproducible trials. | Feature distinctions and quality claims include scope, dates, configuration and raw evidence. |

## 10. Pilot, launch and measurement

**Private design-partner gate:** Milestone A passes; the specific multiplayer slice is authorized, threat-modeled and tested; role-based privacy and invitations pass B-09; the owner and independent reviewer explicitly approve the pilot. Supervised first, with a documented rollback and support contact. C can initially run as an admin-reviewed proposal workflow; no autonomous assignments or execution beyond approved policy.

**Pilot program — pilot lead:** recruit 1–2 representative teams; capture consent, current workflow baseline, roles and success criteria. Observe at least one end-to-end project with admin and distinct member roles. Measure assignment relevance, acceptance/reassignment, time to first useful output, completed and accepted tasks, human interventions, blocked/denied actions, security incidents, time/cost per task, handoff friction and satisfaction. Protect private information and publish aggregate measurements only. Define sample size and budget before drawing conclusions.

**Broad release gate:** independently reviewed security, privacy and legal posture; production deployment and restore evidence; support/on-call and SLO ownership; supported-host matrix; reliable authorization across surfaces; versioned evidence and a named release decision. Pilot outcomes must justify further scope. No milestone automatically grants a production release.

## 11. Owner decisions needed before dependent work

| ID | Decision | Default until decided |
|---|---|---|
| D-01 | Self-hosted first, managed cloud first, or both? | Preserve existing architecture and pilot deployment; make no new cloud-only dependency. |
| D-02 | Who can be project admin, can there be multiple admins, and how is ownership transferred? | Treat project admins as fully visible; administrative lifecycle requires owner approval. |
| D-03 | Does a member see only assigned items, or also explicitly shared project-wide goals and dependency status? | Assigned and explicitly authorized information only; admin sees all. |
| D-04 | Can members communicate directly, request visibility, hand work off, or invite another member? | Requests go to admin; no implicit access or invitation authority. |
| D-05 | Must admin approve every proposed human task and every agent plan, and which member actions require separate approval? | AI proposes; admin approves assignments; existing mandatory agent plan approval remains. |
| D-06 | Can members use agents on their own tasks and approve their results, and can an agent see more than its owner? | No wider access than explicit grants; retain existing approval/merge rules. |
| D-07 | Are roles fixed job titles, custom capabilities, multiple roles, or both? How are availability and workload provided? | Admin-maintained role/capability attributes; do not infer authority or availability from a title. |
| D-08 | What happens when a person leaves, an admin is unavailable, or ownership must be recovered? | Revoke promptly; preserve audit and require an authorized admin for reassignment. |
| D-09 | Which providers, deployment jurisdictions, supported hosts and external security reviewers are required for the first pilot? | Follow existing documented pilot limits; no unsupported compliance or capacity claims. |

These defaults are planning assumptions, not a substitute for product approval. Product decisions that affect visibility or security must be recorded before implementation relies on them.

## 12. Dependencies and order

`F baseline + decisions → A trusted candidate → B role-based authorization → C reviewed AI assignments → supervised pilot → D collaboration depth → E broader capability, operations and scale → independent broad-release decision`.

Threat modeling, design research, legal work and measurement can start immediately. B's schema/design may proceed alongside A, but real-user rollout depends on A and authorization evidence. D and E should be prioritized using pilot evidence; capacity tier A can be tested earlier on a scratch environment. Dates and cost estimates require named staffing, a fresh baseline and explicit budgets. Existing weekly estimates are historical and are not commitments.

## 13. Source index and reconciliation

- `README.md`, `AGENTS.md`, `STATUS.md`, `contracts/`: product, invariants and recorded readiness.
- `docs/briefs/PROJECT-STATE.md`, `docs/briefs/capability-matrix.md`, `docs/briefs/path-to-9-plan.md`: 61-part program and historical status; refresh old counts.
- `docs/master-prompts/harness-multiplayer-master-build-prompt.md`, `docs/briefs/multiplayer-pilot-roadmap.md`, `docs/briefs/multiplayer-readiness.md`: multiplayer requirements, staged pilot and historical evidence.
- `docs/roadmap-3.0.md`, `docs/plans/two-week-release-candidate-plan-2026-11-06.md`, `docs/reviews/w33-readiness-pack-2026-11-05.md`: current sequence, RC gates and independent-review inputs.
- `docs/roadmap-2026-10-25.md`, `docs/roadmap.md`, `docs/EXPANSION_PLAN.md`, `HARNESS_PLAN_V2.md` in the repository's parent: earlier feature scope; revalidate status rather than carry forward old gap claims or timelines.
- `docs/decisions/2026-11-06-scale-tiers.md`, `docs/decisions/2026-11-04-w31-scheduler.md`, `docs/decisions/2026-11-05-w30-candidate-audit.md`: scale targets and limited integration decisions. Scheduler is proposal-only; other candidates remain subject to their recorded decisions. Hermes is out of scope unless a new owner decision resolves rights and need.
- `docs/pilot-remediation/`, `docs/briefs/evidence/rc/`, `docs/runbooks/`, `docs/capability-review-2026-11-05.md`: security, known limits, operational evidence and competitor review.

**Maintenance:** Technical lead updates task state and evidence at least weekly and after each release. Owner approves scope or priority changes. Link decisions and test records; keep superseded plans as historical references. This file does not certify implementation, compliance, readiness or dates.
