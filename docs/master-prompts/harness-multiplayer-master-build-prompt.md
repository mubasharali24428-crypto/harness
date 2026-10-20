

# Harness Multiplayer AI — Full Product Master Build Prompt

Use this prompt in Claude Code or another coding agent with repository access. The numbered directives are intentionally compact so they can be used as a durable plan across a maximum of four sessions.

## HOW TO USE THIS PROMPT

Paste the complete prompt once at the start of the work. Keep its stable opening and shared instructions unchanged across sessions to support prompt-cache reuse. Maintain the project checkpoint file requested below and provide only the newest checkpoint plus the current session objective when resuming. A cache hit is generally helpful because it reuses repeated prompt text; optimize for less repeated context and smaller outputs, not fewer cache hits.

## 01. ROLE AND OUTCOME
001. You are the technical lead for the Harness Multiplayer AI product and its staged delivery.
002. Deliver the first safe foundation in at most four sessions; do not claim the whole product fits that limit.
003. Work in the Harness repository at /Users/user/untitled folder 2/harness.
004. Treat the repository as the source of truth; verify every inherited claim.
005. Do not assume the product idea is already implemented.
006. Build a shared live agent workroom for real team collaboration.
007. Center the product on a shared run, not a generic chat room.
008. Let teammates observe, guide, redirect, review, and hand off work.
009. Keep human authority explicit for sensitive changes and approvals.
010. Leave a working, documented, demonstrable result at each session end.
## 02. PRODUCT DIRECTION
001. Target small teams collaborating on agent-run software tasks.
002. Start with one repeated task that teams already perform.
003. Use a small code issue from plan through review as the default pilot.
004. Support teammates joining the same run and seeing current state.
005. Show a concise plan, progress, changed files, decisions, and outcomes.
006. Show a useful action history without exposing private model reasoning.
007. Allow collaborators to give normal guidance during a run.
008. Record every meaningful human redirection as a collaboration event.
009. Provide a deliberate handoff between teammates where feasible.
010. Do not claim multiplayer capability beyond demonstrated behavior.
## 03. PILOT SUCCESS
001. Optimize for a pilot that real users can complete repeatedly.
002. Measure task completion and accepted reviewable outcomes.
003. Measure elapsed time against the team's existing workflow.
004. Measure repeat usage across participating teams and sessions.
005. Record redirection frequency and reasons where users provide them.
006. Track approval latency and blocked time for high-impact actions.
007. Track rework, failures, abandoned runs, and user-reported friction.
008. Define baseline and measurement method before interpreting improvement.
009. Do not invent user traction, ROI, or performance evidence.
010. Document pilot limitations and what evidence remains uncollected.
## 04. INHERITED BASELINE TO VERIFY
001. Verify team roles and team-scoped authorization in current code.
002. Verify durable run events, sequence numbers, replay, and reconnect behavior.
003. Verify the run interface exposes plan, terminal, and recent events.
004. Verify pause, resume, stop, and human-edit routes and their semantics.
005. Verify initial plan approval behavior and the identities it records.
006. Verify audit records, actor fields, retention, and queryability.
007. Verify run isolation in deployed configuration and execution code.
008. Verify cancellation behavior for active provider or SDK calls.
009. Treat earlier review findings as leads, never as unquestioned truth.
010. Report evidence with paths, symbols, and concise implementation notes.
## 05. KNOWN GAP CHECKLIST
001. Check whether the system has an explicit session leader assignment.
002. Check whether participants and presence are visible to collaborators.
003. Check whether a general run redirection or nudge operation exists.
004. Check whether redirects include actor, timestamp, run, and changed intent.
005. Check whether scope or permission changes require a reason.
006. Check whether a teammate handoff has explicit ownership semantics.
007. Check whether high-impact file actions have a per-action approval gate.
008. Check whether changed proposals invalidate prior approvals automatically.
009. Check whether audit events identify requester, approver, and outcome.
010. Check whether pause and stop reliably terminate active execution.
## 06. SAFETY AND TRUST
001. Make authorization server-side and deny by default.
002. Treat UI hiding as presentation, never as an authorization boundary.
003. Bind every action to an authenticated actor and authorized team.
004. Validate run ownership on every read and mutation.
005. Return indistinguishable not-found behavior for foreign resources where required.
006. Never let an approval apply to a materially changed proposal.
007. Require leader approval before executing designated high-impact actions.
008. Keep execution blocked while a required approval is pending.
009. Record approval request, decision, affected files, and final outcome.
010. Provide a clear cancellation path and report its actual completion state.
## 07. HIGH-IMPACT APPROVAL
001. Define high-impact actions precisely before implementing policy.
002. Include deletion and substantial file modification in the initial policy.
003. Display a human-readable summary of proposed changes for approval.
004. Show affected paths and a reviewable diff when available.
005. Identify the requester and designated approving session leader.
006. Require a short reason for goal, scope, or permission changes.
007. Persist approval state durably and make decisions race-safe.
008. Re-check proposal identity immediately before execution.
009. Invalidate approval after any change to files, scope, or permissions.
010. Audit denied, approved, rejected, expired, and completed requests.
## 08. REDIRECTION AND HANDOFF
001. Separate ordinary guidance from changes to goal or permissions.
002. Allow ordinary guidance without unnecessary confirmation steps.
003. Persist every redirection with actor, time, run, and content reference.
004. Require a reason when redirection changes approved scope or permissions.
005. Show collaborators the resulting run status and changed direction.
006. Define who owns the next action after a handoff.
007. Record handoff initiator, recipient, time, and accepted state.
008. Prevent two operators from believing they exclusively control a run.
009. Resolve simultaneous instructions using explicit, documented rules.
010. Preserve the run's complete collaboration history across handoff.
## 09. RUN ISOLATION
001. Give each run an isolated working directory or equivalent boundary.
002. Verify isolation across concurrent runs, not only sequential examples.
003. Bind artifacts and file actions to the correct run identifier.
004. Prevent one run from reading or mutating another run's workspace.
005. Clean up isolated workspaces safely after terminal states.
006. Preserve user-approved outputs and review artifacts as needed.
007. Document filesystem, container, and process isolation assumptions.
008. Check path traversal and symlink handling in file operations.
009. Ensure workspace setup failures fail closed before model execution.
010. Record the workspace identity in run metadata without leaking secrets.
## 10. CANCELLATION AND LIFECYCLE
001. Model run lifecycle as explicit, durable state transitions.
002. Define pause, resume, stop, timeout, failure, and completion semantics.
003. Ensure stop reaches the underlying process or provider task where supported.
004. Do not label a run stopped while execution can still mutate files.
005. Make cancellation idempotent under repeated requests.
006. Handle races between pause, resume, stop, and completion.
007. Record cancellation requested and cancellation confirmed separately if needed.
008. Recover consistently after service restart or worker crash.
009. Prevent duplicate execution when a run is resumed or reclaimed.
010. Surface honest lifecycle status to every collaborator.
## 11. EVENTS AND AUDIT
001. Use typed event schemas with stable, versioned fields.
002. Include run, team, actor, event type, and server timestamp.
003. Store before-and-after values for consequential changes where safe.
004. Keep audit records append-only or otherwise tamper-evident.
005. Separate product events from security audit decisions.
006. Support ordered replay with clear duplicate-delivery handling.
007. Make reconnect behavior resume from a known event cursor.
008. Bound event payloads and exclude credentials or sensitive data.
009. Define retention, access control, and export expectations.
010. Test that unauthorized actions produce useful audit evidence.
## 12. SHARED EXPERIENCE
001. Make the shared run the main workspace object.
002. Show current goal, approved plan, state, and responsible person.
003. Show what changed, where, and whether the change is approved.
004. Show pending decisions with the person responsible for deciding.
005. Show participant list and their roles in the run.
006. Show concise progress and actionable errors rather than internal reasoning.
007. Make redirect and handoff actions easy to find and understand.
008. Provide a readable chronological action and decision history.
009. Keep terminal details available without making them the entire interface.
010. Design for keyboard access, responsive layout, and clear status language.
## 13. ARCHITECTURE REVIEW
001. Map existing services, packages, contracts, and data stores.
002. Identify authoritative state ownership for runs and collaboration.
003. Trace authentication and authorization from UI to database.
004. Trace run events from producer through persistence to subscribers.
005. Trace approval decisions through route, state machine, and execution.
006. Identify shared database or filesystem assumptions between services.
007. Inspect deployment configuration and local development setup.
008. Identify existing patterns that should be extended rather than duplicated.
009. Document technical debt directly affecting pilot safety or delivery.
010. Prefer the smallest coherent architecture that fits current contracts.
## 14. CONTRACT DESIGN
001. Define participant, session leader, and role semantics explicitly.
002. Define collaboration event and audit schemas before UI wiring.
003. Define redirect request, response, errors, and idempotency behavior.
004. Define handoff request, acceptance, and rejection behavior.
005. Define high-impact approval states and legal transitions.
006. Define approval invalidation rules for proposal changes.
007. Define event ordering, replay, and reconnect contract.
008. Define limits for text, paths, payloads, and event rates.
009. Keep external API changes backwards compatible where practical.
010. Update OpenAPI or equivalent contract documentation with implementation.
## 15. IMPLEMENTATION METHOD
001. Start with a short architecture map and prioritized gap list.
002. Write a thin vertical slice for one end-to-end pilot scenario.
003. Build backend policy and persistence before relying on UI controls.
004. Connect the UI to authoritative server state and event streams.
005. Add observability for approvals, redirects, handoffs, and cancellation.
006. Keep commits small enough for another engineer to review.
007. Use feature flags only when they meaningfully reduce rollout risk.
008. Do not expand into unrelated platform features during this pilot.
009. Preserve existing public contracts unless a justified change is needed.
010. Update docs and operator instructions alongside shipped behavior.
## 16. AGENT OPERATING MODEL
001. Use subagents for bounded, independent research or implementation lanes.
002. Use the main agent as integrator and owner of final decisions.
003. Assign each agent one deliverable, explicit files, and a deadline.
004. Have agents return findings and concise evidence, not full transcripts.
005. Require every report to include changed files and unresolved questions.
006. Keep agents from editing the same files concurrently.
007. Give agents only the context and files required for their lane.
008. Ask agents to avoid broad repository dumps and redundant summaries.
009. Integrate changes centrally after reviewing diffs and contracts.
010. Never treat agent output as verified until inspected.
## 17. SUGGESTED AGENT LANES
001. Agent A maps lifecycle, cancellation, process control, and run isolation.
002. Agent B maps auth, roles, approvals, and audit data flows.
003. Agent C maps events, collaboration UI, presence, and handoff affordances.
004. Agent D may independently review threat model and failure cases.
005. Run only lanes that are independent and useful in this repository.
006. Do not delegate one large vague task to every agent.
007. Do not let parallel agents modify shared contracts without coordination.
008. Name a lead for contract decisions and integration conflicts.
009. Request compact findings with exact file and symbol references.
010. Terminate or re-scope agents whose work duplicates another lane.
## 18. CONTEXT BUDGET
001. Maintain a short project brief in a durable Markdown progress file.
002. Keep the durable brief under two pages unless evidence demands more.
003. Record decisions, current task, owners, blockers, and next actions.
004. Summarize each session in a compact handoff note.
005. Load only files relevant to the current implementation question.
006. Prefer targeted symbol searches and narrow file excerpts.
007. Do not paste entire logs, generated files, or dependency trees.
008. Ask agents to report conclusions, not their full reasoning traces.
009. Store detailed evidence in files and reference it by path.
010. Before compaction, write an explicit checkpoint with remaining work.
## 19. PROMPT CACHE AND TOKEN EFFICIENCY
001. Keep stable project instructions and shared context at the prompt beginning.
002. Put session-specific task details after the stable prompt prefix.
003. Reuse the same stable prompt text across sessions when possible.
004. Do not mutate shared instructions gratuitously between tool calls.
005. Prefer prompt-cache reuse; a cache hit usually reduces repeated input cost.
006. Reduce context consumption rather than trying to reduce cache hits.
007. Do not resend repository history already captured in the progress brief.
008. Do not ask agents to return code already present in shared files.
009. Use concise summaries with links or paths for supporting evidence.
010. Record cache controls only when the chosen tool exposes them.
## 20. SESSION PLAN OVERVIEW
001. Use three core sessions and one optional hardening session for the first delivery tranche.
002. Keep the initial tranche within four sessions; plan subsequent product phases separately.
003. Each session must end with a usable checkpoint and clear next steps; full product maturity requires later phases.
004. Session One establishes baseline, contracts, risks, and vertical-slice scope.
005. Session Two implements safe run lifecycle and collaboration backend.
006. Session Three delivers shared workspace UI and end-to-end pilot flow.
007. Session Four is reserved for integration, hardening, and pilot readiness.
008. Skip Session Four only if evidence shows readiness criteria are met.
009. Do not defer critical isolation or cancellation risks for visual polish.
010. Re-plan openly if repository evidence contradicts this sequence.
## 21. SESSION ONE: DISCOVERY
001. Read repository guidance and current project documentation first.
002. Inspect git status and avoid overwriting unrelated user changes.
003. Map the current architecture and identify runnable local workflows.
004. Verify inherited findings about roles, events, approvals, and audit.
005. Investigate cancellation and workspace isolation with focused code tracing.
006. Identify existing tests and documented verification commands.
007. Record current behavior separately from planned behavior.
008. Create a risk-ranked gap matrix tied to product requirements.
009. Select one end-to-end pilot scenario and define its success evidence.
010. Write a concise implementation plan and checkpoint before ending.
## 22. SESSION TWO: BACKEND SAFETY
001. Implement or repair truthful cancellation and stop semantics first.
002. Implement per-run workspace isolation or a safe, enforceable equivalent.
003. Preserve restart recovery and idempotent lifecycle transitions.
004. Add server-enforced participant and session leader authorization.
005. Add structured redirect and handoff commands with actor identity.
006. Add collaboration event persistence and ordered replay integration.
007. Add high-impact action requests that block pending approval.
008. Bind approval to an immutable proposal version or digest.
009. Invalidate approval when approved content or permissions change.
010. Add audit entries for every consequential request and outcome.
## 23. SESSION TWO: BACKEND EVIDENCE
001. Add focused tests for cancellation completion and race handling.
002. Add isolation tests with concurrent runs and cross-run access attempts.
003. Add authorization tests for each role and foreign resource.
004. Add approval tests for changed proposal invalidation.
005. Add audit assertions for actor, run, decision, and outcome.
006. Add event replay tests for reconnect and duplicate delivery.
007. Exercise failure paths such as worker crash and approval timeout.
008. Document any external provider cancellation limitation honestly.
009. Review schema migrations and rollback behavior.
010. Checkpoint all changes with a concise evidence report.
## 24. SESSION THREE: SHARED WORKSPACE
001. Build the run-centered collaborative view using current product conventions.
002. Show goal, approved plan, current state, and responsible operator.
003. Render ordered progress events and changed-file summaries.
004. Show participants, roles, leader, and handoff state.
005. Add a redirect composer with clear scope and reason handling.
006. Add visible approval requests with diff, actor, and decision status.
007. Show why execution is blocked and who can approve it.
008. Show handoff recipient and acceptance state.
009. Keep private model reasoning out of the shared interface.
010. Make errors and cancellation state understandable to non-engineers.
## 25. SESSION THREE: END-TO-END FLOW
001. Run the selected pilot scenario from creation to reviewed outcome.
002. Verify teammates can observe the same authoritative run state.
003. Verify guidance becomes a recorded redirection event.
004. Verify a handoff changes responsible ownership visibly.
005. Verify a high-impact action remains blocked pending leader approval.
006. Verify a changed proposal requires renewed approval.
007. Verify stop prevents further workspace mutation.
008. Verify one run cannot affect another run's files.
009. Verify activity survives reconnect and page refresh.
010. Record user-visible rough edges and resolve pilot-blocking issues.
## 26. SESSION FOUR: INTEGRATION
001. Reserve this session for full-stack integration and hardening.
002. Review all diffs and resolve contract inconsistencies.
003. Run the repository's relevant verification commands and record results.
004. Exercise the pilot flow in the deployed-like configuration.
005. Check migrations, environment settings, secrets handling, and rollback.
006. Re-run cancellation, isolation, approval, and audit acceptance scenarios.
007. Verify operational logs and health indicators are actionable.
008. Check documentation matches actual shipped behavior.
009. Do not claim readiness if a critical risk remains uncontained.
010. Produce a final readiness decision with evidence and remaining risks.
## 27. ACCEPTANCE: CORE FLOW
001. A team member can create or join the selected shared run.
002. Authorized teammates see one consistent plan and run state.
003. Unauthorized users cannot view or control another team's run.
004. Every redirect is associated with an actor and the correct run.
005. High-impact changes remain blocked until the session leader approves.
006. Approval applies only to the exact reviewed proposal.
007. A handoff is explicit and visible to the participants.
008. Stop prevents further action or honestly reports incomplete cancellation.
009. Run outputs remain isolated from other concurrent runs.
010. Collaborators can review the final artifact and its action history.
## 28. ACCEPTANCE: OPERATIONS
001. Services start from documented instructions in a clean environment.
002. Health checks distinguish dependency failure from application failure.
003. Logs correlate requests, runs, and collaboration actions safely.
004. Run cancellation and workspace cleanup have observable outcomes.
005. Database migrations apply cleanly and have a rollback plan.
006. Concurrent execution limits are documented and enforced.
007. Failure recovery does not create duplicate runs or actions.
008. Alerts cover stalled runs and broken event delivery where available.
009. Known provider limits are visible to operators.
010. Pilot support has a basic incident and recovery procedure.
## 29. SECURITY REVIEW
001. Use the existing security review process and repository conventions.
002. Threat-model cross-team access, compromised accounts, and stale approvals.
003. Threat-model malicious file paths and crafted diffs.
004. Threat-model duplicate requests and replayed decisions.
005. Threat-model concurrent leader decisions and race conditions.
006. Threat-model forged event actor fields and client-supplied identities.
007. Threat-model provider calls that outlive local cancellation.
008. Threat-model shared storage, caches, and temporary artifacts.
009. Document mitigations and residual risks with owners.
010. Block release for unresolved critical security or data-loss findings.
## 30. QUALITY AND TESTING
001. Prefer focused tests for changed contracts and critical invariants.
002. Use existing test frameworks and established repository commands.
003. Do not add broad flaky end-to-end tests without a clear need.
004. Test state transitions, authorization boundaries, and error responses.
005. Test concurrency where approval or lifecycle races are possible.
006. Test event ordering, replay, and reconnect behavior.
007. Test file isolation and cleanup under failure.
008. Run lint, type checks, and targeted tests relevant to changed files.
009. Run broader verification when session integration warrants it.
010. Report commands run and results without implying unrun checks passed.
## 31. DATA AND PRIVACY
001. Collect only collaboration data needed for product behavior and audit.
002. Minimize event payload size and avoid copying large file contents.
003. Never log access tokens, provider secrets, or credentials.
004. Treat user prompts and generated content as potentially sensitive.
005. Define who can read run history and audit records.
006. Define retention and deletion behavior for pilot data.
007. Protect exported evidence with the same access controls as source data.
008. Use synthetic data for demos unless users explicitly provide real data.
009. Explain telemetry fields to pilot participants.
010. Do not send user data to external analytics without existing authorization.
## 32. RELEASE STRATEGY
001. Keep the pilot opt-in and limited to named teams if possible.
002. Use a feature flag or controlled rollout where architecture supports it.
003. Start with low-risk code tasks and bounded workspaces.
004. Require review before merging any generated changes.
005. Keep automatic merge disabled unless separately approved by product policy.
006. Provide a rollback path for collaboration features.
007. Monitor failed approvals, cancellation, isolation, and event delivery.
008. Have a human operator available during early pilot runs.
009. Collect feedback after each real task, not only at launch.
010. Expand access only after acceptance evidence is reviewed.
## 33. SESSION CHECKPOINT TEMPLATE
001. Record session number and completed objective.
002. Record verified behavior and supporting file paths.
003. Record changed files and migration state.
004. Record tests and verification commands actually run.
005. Record unresolved blockers and their severity.
006. Record security and operational risks still open.
007. Record product decisions made or awaiting user input.
008. Record agent contributions and integration status.
009. Record exact next tasks, owners, and dependencies.
010. Keep the checkpoint concise enough to reload quickly.
## 34. FINAL DELIVERABLES
001. Provide a concise executive summary of current and achieved state.
002. Provide a feature matrix: implemented, partial, missing, or blocked.
003. Provide evidence references for material implementation claims.
004. Provide a four-session outcome summary and session checkpoints.
005. Provide acceptance results with pass, fail, or not verified labels.
006. Provide a risk register with severity, mitigation, and owner.
007. Provide pilot setup and operator instructions.
008. Provide a user feedback and metric collection plan.
009. Provide a prioritized next-step list for post-pilot improvements.
010. State clearly whether the pilot is safe to begin with real users.
## 35. FINAL RESPONSE FORMAT
001. Start with the readiness decision in one plain-language sentence.
002. Summarize what existed before implementation and what changed.
003. List the pilot capabilities now verified end to end.
004. List critical remaining risks and whether each blocks pilot use.
005. Summarize session outcomes and any unused session capacity.
006. Report tests and checks actually completed with their results.
007. Link changed files, setup guide, and durable checkpoint.
008. State how to run the pilot and who should be included.
009. State which success metrics will be collected and how.
010. Ask one focused question only if a genuine decision remains.

## 36. PRODUCT BOUNDARY AND MATURITY
001. Define full product maturity separately from the first pilot release.
002. Treat four sessions as the first delivery tranche, not total product completion.
003. Plan later release phases for scaling, enterprise controls, and ecosystem growth.
004. Describe target users, team sizes, task types, and usage environments.
005. State explicit product non-goals and excluded high-risk use cases.
006. Separate required capabilities from optional advanced capabilities.
007. Map each capability to user value, implementation owner, and acceptance evidence.
008. Identify dependencies between capabilities and sequence them by risk.
009. Do not market a capability until its acceptance criteria are met.
010. Keep a versioned product capability matrix as the source of truth.

## 37. ORGANIZATIONS AND IDENTITY
001. Define organizations, teams, projects, workspaces, sessions, and runs distinctly.
002. Support users belonging to multiple organizations and teams where permitted.
003. Define organization ownership, administrator, billing administrator, and member roles.
004. Support invitation, acceptance, expiration, revocation, and domain policy flows.
005. Support account recovery, session expiry, and secure sign-out.
006. Support optional single sign-on and multi-factor authentication for eligible plans.
007. Record membership and role changes in an auditable organization history.
008. Define guest access boundaries and expiration behavior.
009. Support service identities for approved automation and integrations.
010. Ensure identity deletion and team departure revoke active access promptly.

## 38. PERMISSIONS AND POLICY MODEL
001. Define role-based permissions at organization, team, project, workspace, and run levels.
002. Allow policy inheritance with explicit override and precedence rules.
003. Separate read, comment, steer, approve, administer, export, and billing permissions.
004. Support custom roles only if policy evaluation remains understandable and testable.
005. Evaluate permissions on every server-side read and mutation.
006. Require step-up authentication for configured high-risk administrative actions.
007. Support emergency access with strict duration, reason, and audit requirements.
008. Provide administrators a preview of effective permissions before saving policy.
009. Prevent privilege escalation through invitations, handoffs, or agent-generated actions.
010. Maintain a permission decision log suitable for support and security review.

## 39. WORKSPACE AND PROJECT MODEL
001. Define workspace creation, naming, ownership, membership, and archival.
002. Support project linking without confusing repository access with collaboration access.
003. Allow organization-approved repository and environment bindings.
004. Represent workspace settings with version history and change audit.
005. Support reusable workspace templates with approved defaults.
006. Make workspace visibility and data residency policy explicit.
007. Support workspace pause, archive, restore, and deletion workflows.
008. Prevent archived or deleted workspaces from accepting new runs.
009. Provide workspace search and filtering across authorized content.
010. Document workspace limits, quotas, and isolation guarantees.

## 40. SESSION AND RUN DOMAIN MODEL
001. Define collaboration session separately from an individual execution run.
002. Allow a session to contain multiple runs, artifacts, discussions, and decisions.
003. Define session owner, leader, participants, observers, and agent identities.
004. Define run creation, queued, planning, approval, executing, paused, stopped, failed, and completed states.
005. Define terminal states and whether a session can launch another run.
006. Support session templates for repeatable team workflows.
007. Allow users to resume a session with its history and current policy.
008. Represent relationships between parent tasks, subtasks, and spawned agents.
009. Preserve session history when a participant leaves or loses access.
010. Make deletion and retention semantics clear for sessions and their runs.

## 41. PRESENCE AND PARTICIPATION
001. Show online, idle, disconnected, and last-seen presence with privacy controls.
002. Make presence indicators approximate and avoid implying guaranteed availability.
003. Show participant role, current control status, and notification preferences.
004. Allow users to join, leave, observe, mute, and rejoin sessions.
005. Support participant limits and clear behavior when capacity is reached.
006. Do not expose presence to users lacking session visibility.
007. Handle multiple devices for one user without conflicting presence state.
008. Expire stale presence records after disconnect or client crash.
009. Support accessibility announcements for participant and control changes.
010. Allow organizations to disable or limit presence data collection.

## 42. SIMULTANEOUS CONTROL AND CONFLICTS
001. Define whether a run has one active controller, shared steering, or both.
002. Use explicit control leases or ownership state when exclusive control is needed.
003. Show who currently holds control and how to request it.
004. Define lease duration, renewal, expiry, revocation, and disconnect behavior.
005. Queue, merge, or reject concurrent instructions using documented rules.
006. Detect conflicting goal, scope, or permission edits before applying them.
007. Show users the conflict and the current approved state.
008. Use version checks to prevent stale edits from silently overwriting newer ones.
009. Require renewed review when conflict resolution changes an approved proposal.
010. Audit control acquisition, release, transfer, conflict, and resolution events.

## 43. COLLABORATIVE DISCUSSION
001. Support comments or discussion attached to a session and relevant run events.
002. Allow replies, mentions, reactions, and links where they aid collaboration.
003. Support references to plans, files, diffs, approvals, and event positions.
004. Distinguish discussion from executable instructions to the agent.
005. Require an explicit action to turn a comment into agent guidance.
006. Show authorship, edit history, timestamp, and moderation state.
007. Define comment edit and deletion behavior for audit-sensitive discussions.
008. Prevent mentions from leaking private session names or content.
009. Support search across authorized discussions and session history.
010. Provide retention and export rules for comments and attachments.

## 44. TASKS, PLANS, AND EXECUTION
001. Support a clear task statement, success criteria, constraints, and owner.
002. Represent the agent plan as versioned, reviewable structured content.
003. Allow plan comments and proposed edits before approval.
004. Track task dependencies, subtasks, blockers, and completion evidence.
005. Support bounded execution budgets for time, model usage, and tool calls.
006. Provide checkpoints for long-running work and safe resume behavior.
007. Make generated outputs reviewable before they are accepted as completed.
008. Distinguish agent-reported completion from human-accepted completion.
009. Allow a run to fail or be abandoned with a reason and preserved evidence.
010. Provide safe retry semantics that prevent accidental duplicate side effects.

## 45. AGENT MANAGEMENT AND ORCHESTRATION
001. Represent agents, capabilities, tool permissions, and versions explicitly.
002. Allow authorized users to select or configure approved agents for a task.
003. Define when agents may spawn subagents and the limits they must obey.
004. Track parent-child agent relationships, assignments, and resource budgets.
005. Prevent agent-to-agent delegation from bypassing user permissions.
006. Show subtask ownership and concise progress in the shared workspace.
007. Require scoped access to files, tools, credentials, and network resources.
008. Record tool invocation summaries and outcomes without exposing hidden reasoning.
009. Allow users to pause, cancel, or reassign agent work where technically supported.
010. Provide fallback behavior when an agent, model, or tool is unavailable.

## 46. REAL-TIME DELIVERY
001. Choose transport and delivery guarantees based on existing architecture.
002. Support ordered event delivery with durable cursors and replay.
003. Handle duplicate, delayed, missing, and out-of-order events predictably.
004. Reconnect clients with backoff, resume cursors, and visible connection status.
005. Fall back to safe polling if the real-time channel is unavailable.
006. Apply backpressure and rate limits for high-volume sessions.
007. Separate critical state events from ephemeral presence updates.
008. Define event schema evolution and compatibility across client versions.
009. Prevent stale clients from issuing mutations against outdated state.
010. Measure delivery lag, reconnect frequency, and dropped-event recovery.
011. Protect real-time channels with authenticated, scoped subscriptions.

## 47. NOTIFICATIONS AND USER CONTROL
001. Support in-product notifications for approvals, mentions, handoffs, and failures.
002. Offer email or other channels only through explicit user preferences.
003. Allow per-event, per-session, and per-workspace notification settings.
004. Support quiet hours, digest delivery, and urgent escalation policies.
005. Deduplicate repeated alerts and group related event bursts.
006. Never send sensitive content in notification previews by default.
007. Provide direct links that re-check authorization on open.
008. Show notification delivery state when operationally useful.
009. Allow users to mute, snooze, or unsubscribe without losing audit history.
010. Make notification defaults accessible and respectful of user attention.

## 48. FILES, ARTIFACTS, AND VERSION CONTROL
001. Define how repositories, branches, worktrees, and run workspaces relate.
002. Bind every changed file to a run, proposal version, and review state.
003. Provide diffs with safe rendering for binary, large, and generated files.
004. Support artifact upload, download, preview, and retention under access control.
005. Detect stale repository bases and surface merge or rebase requirements.
006. Support multiple concurrent branches without sharing mutable run state.
007. Define who may commit, push, open a pull request, or merge.
008. Keep human merge approval separate from agent plan approval.
009. Scan or restrict executable artifacts and untrusted file content where appropriate.
010. Make artifact lineage traceable from task request through accepted result.

## 49. INTEGRATIONS AND EXTENSIBILITY
001. Define a versioned integration API with scoped OAuth or equivalent credentials.
002. Support repository providers and issue trackers through least-privilege scopes.
003. Support chat and notification integrations only with explicit team setup.
004. Provide webhooks with signatures, replay protection, and delivery retries.
005. Define idempotency keys for external side-effecting operations.
006. Offer a documented extension interface with sandbox and permission boundaries.
007. Review integrations for data exfiltration and privilege-escalation risks.
008. Provide integration health, reconnect, revoke, and credential rotation flows.
009. Maintain compatibility and deprecation policies for APIs and extensions.
010. Keep user approval requirements consistent across internal and external tools.

## 50. SEARCH, HISTORY, AND EXPORT
001. Provide search over authorized sessions, runs, comments, events, and artifacts.
002. Support filters for project, participant, status, date, and approval state.
003. Index only data permitted by the current authorization policy.
004. Re-check authorization when serving indexed results and snippets.
005. Support export of session history and audit evidence in documented formats.
006. Include event ordering and actor identity in exports where authorized.
007. Provide data deletion requests and explain retained audit exceptions.
008. Prevent exports from including secrets, hidden reasoning, or unauthorized content.
009. Define indexing delay and search consistency expectations.
010. Audit bulk exports and enforce configurable size limits.

## 51. ADMINISTRATION AND GOVERNANCE
001. Provide organization administration for memberships, roles, policies, and integrations.
002. Provide workspace policy controls for agent tools and high-impact actions.
003. Allow administrators to set approval thresholds and eligible approver roles.
004. Support policy simulation before enforcing a new configuration.
005. Provide audit views with filters, actor details, and export controls.
006. Support policy change approval for regulated or high-assurance environments.
007. Allow emergency disablement of agent execution at organization or workspace level.
008. Provide data retention, residency, and deletion configuration where supported.
009. Record administrative actions with before-and-after policy values.
010. Document boundaries between organization admin powers and session leadership.

## 52. SECURITY AND PRIVACY PROGRAM
001. Maintain a threat model for identities, sessions, runs, agents, and integrations.
002. Encrypt sensitive data in transit and at rest using platform controls.
003. Isolate tenant data across databases, object stores, caches, and event streams.
004. Store provider credentials in a dedicated secret-management system.
005. Rotate secrets and revoke credentials without exposing them to agents.
006. Apply least privilege to service accounts and internal services.
007. Protect against CSRF, XSS, SSRF, injection, and unsafe file handling.
008. Perform dependency scanning, code review, and vulnerability response.
009. Define incident response, evidence preservation, and customer communication procedures.
010. Publish a clear security and privacy posture based on verified controls.

## 53. RELIABILITY AND FAILURE HANDLING
001. Define service-level objectives for availability, latency, and event delivery.
002. Design for database, queue, model provider, and integration outages.
003. Use bounded retries with idempotency and jitter for transient failures.
004. Provide circuit breakers or equivalent protection for failing dependencies.
005. Make queued work visible during maintenance or provider unavailability.
006. Persist state before acknowledging critical user actions.
007. Recover in-flight runs without duplicate side effects after restart.
008. Provide graceful degradation for presence, search, and noncritical features.
009. Define backup, restore, disaster recovery, and recovery-point objectives.
010. Run failure-injection exercises for critical lifecycle and approval paths.

## 54. SCALABILITY AND PERFORMANCE
001. Set documented limits for organizations, users, sessions, events, and artifacts.
002. Model expected concurrent sessions and burst traffic before capacity planning.
003. Partition or archive event history to keep active run queries responsive.
004. Use pagination and bounded payloads for all list and history APIs.
005. Avoid unbounded fan-out when broadcasting to large sessions.
006. Apply per-user, per-team, and per-tenant rate limits.
007. Measure database query cost, event lag, and websocket connection load.
008. Scale workers independently from API and real-time delivery services.
009. Define load-test scenarios tied to realistic usage patterns.
010. Review cost per active run and per team as scale increases.

## 55. OBSERVABILITY AND SUPPORT
001. Correlate requests, sessions, runs, events, and agent tasks with safe identifiers.
002. Provide structured logs, metrics, and traces without sensitive payloads.
003. Create dashboards for availability, latency, approvals, failures, and cancellations.
004. Alert on stalled runs, repeated failures, event lag, and unsafe lifecycle states.
005. Give support staff scoped diagnostics that respect tenant boundaries.
006. Provide user-visible explanations for common errors and blocked actions.
007. Capture reproducible incident evidence without retaining excess private content.
008. Document operational runbooks for recovery, outage, and stuck-run handling.
009. Track support issues to product gaps and update acceptance criteria.
010. Review cost, performance, and incident trends before broad rollout.

## 56. ACCESSIBILITY, LOCALIZATION, AND CLIENTS
001. Meet a documented accessibility target for keyboard, screen readers, and contrast.
002. Ensure live status updates are announced without overwhelming assistive technology.
003. Provide responsive layouts for supported screen sizes.
004. Define supported browsers and client compatibility policy.
005. Localize user-facing dates, numbers, and essential status messages.
006. Keep permission and approval language clear across locales.
007. Support reduced motion and user preference settings.
008. Test keyboard-only use for approvals, redirects, and handoffs.
009. Make long histories navigable and readable with assistive technology.
010. Document unsupported client features and graceful fallback behavior.

## 57. BILLING, QUOTAS, AND COST CONTROLS
001. Decide whether the product is paid, internal, or usage-metered before implementing billing.
002. Represent usage by organization, workspace, run, and agent where appropriate.
003. Set configurable limits for concurrent runs and provider consumption.
004. Provide clear budget estimates before expensive or long-running execution.
005. Allow administrators to set alerts and hard limits for spend.
006. Handle quota exhaustion without losing run state or audit history.
007. Make usage attribution understandable to organization administrators.
008. Protect billing data and separate billing permissions from run permissions.
009. Define refunds, failed provider calls, and usage reconciliation behavior.
010. Keep billing scope out of the pilot unless user evidence requires it.

## 58. COMPLIANCE AND DATA LIFECYCLE
001. Identify target markets and applicable contractual or regulatory obligations.
002. Map personal data and sensitive business data across storage and providers.
003. Define retention, legal hold, deletion, and backup expiry policies.
004. Support customer data export and deletion workflows where required.
005. Document subprocessors and external model-provider data handling.
006. Offer residency controls only when architecture can enforce them.
007. Preserve audit evidence without retaining unnecessary content.
008. Define access review and periodic permission recertification procedures.
009. Maintain change management and release evidence for regulated customers.
010. Do not claim certification or compliance before independent verification.

## 59. ROLLOUT, MIGRATION, AND COMPATIBILITY
001. Plan feature flags, staged rollout, and tenant eligibility controls.
002. Define database migration ordering, backfill strategy, and rollback constraints.
003. Maintain API and event compatibility for supported client versions.
004. Provide a migration path from single-user runs to shared sessions.
005. Preserve legacy run history and access semantics during upgrade.
006. Use shadow reads or dry runs when policy migration risk is high.
007. Document operational prerequisites before enabling new collaboration features.
008. Provide release notes and an administrator communication plan.
009. Define rollback behavior when new events or schema versions exist.
010. Track rollout health and stop expansion automatically or manually on regressions.

## 60. FULL PRODUCT ROADMAP
001. Phase 1 delivers safe shared runs, roles, redirects, approvals, audit, and isolation.
002. Phase 2 adds richer presence, comments, notifications, search, and team workflows.
003. Phase 3 adds integrations, templates, administration, usage controls, and analytics.
004. Phase 4 hardens scale, availability, disaster recovery, and enterprise governance.
005. Phase 5 expands extensibility and ecosystem capabilities only after security review.
006. Keep the first four sessions focused on architecture and foundational pilot delivery.
007. Do not compress all maturity phases into the four-session implementation promise.
008. Set exit criteria and evidence for every roadmap phase.
009. Revisit priorities using pilot data and customer interviews.
010. Maintain a public or internal roadmap that distinguishes committed from exploratory work.

## 61. FULL PRODUCT ACCEPTANCE
001. Every requirement has an owner, priority, state, and acceptance test.
002. Every security-critical invariant has automated verification and operational evidence.
003. Every user-visible action has clear permission, state, and error behavior.
004. Every multi-user state change is ordered, attributed, and recoverable.
005. Every high-impact action has a reviewable approval and audit trail.
006. Every run has enforceable isolation and truthful lifecycle controls.
007. Every integration has scoped credentials, revocation, and failure handling.
008. Every stored data class has access, retention, and deletion rules.
009. Every product phase has reliability, accessibility, and support readiness criteria.
010. Full-product readiness is declared only after evidence across all critical domains.

## END OF MASTER PROMPT
