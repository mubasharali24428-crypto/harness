# Full Shared Collaboration Specification

## Purpose

This document defines what the Harness must support to become a fully shared collaboration platform for multiple authenticated users working on the same AI engineering projects.

The target model is one authoritative private or hosted Harness deployment with multiple browser or desktop clients. The deployment is the source of truth for teams, projects, plans, runs, reviews, artifacts, budgets, events, and audit records.

## 1. Shared deployment

The shared deployment must include the frontend, identity, project management, orchestrator, sandbox, review, GitHub integration, meeting/collaboration services, PostgreSQL, event delivery, WebSockets, artifact storage, background workers, monitoring, and backup services.

Users must connect to one authoritative instance. The instance must provide health checks, migrations, upgrades, rollback procedures, backups, monitoring, and administrator recovery.

Supported deployment targets may include a private single-server installation, a private LAN/VPN installation, and a hosted deployment. Docker may remain an internal implementation detail, but users need a documented and manageable installation path.

## 2. Team and organization management

The platform must support team creation, team ownership, administrators, member invitations, expiring and revocable invitations, secure identity binding, member removal, suspension, ownership transfer, and users belonging to multiple teams.

At minimum, roles should include owner, administrator, engineer, reviewer, viewer, and guest. Permissions must explicitly cover project access, project-rule changes, plan proposal, plan approval, run control, review, integration management, budgets, audit logs, membership, retention, and security settings.

Every membership and invitation action must be audited and rate-limited.

## 3. Tenant and project isolation

Every REST request, WebSocket subscription, background task, event replay, artifact download, notification, search operation, export, and deletion must enforce authenticated user, active team, project membership, ownership, and role checks.

Users from one team must never access another team’s projects, runs, events, artifacts, audit records, budgets, credentials, or model keys.

Isolation must be enforced in backend services and database queries, not only by hiding controls in the frontend.

## 4. Shared projects

A project must contain its repository, branch configuration, members, project-specific roles, model policy, sandbox policy, network policy, budget, review policy, rules, context sources, active runs, plans, reviews, artifacts, and audit history.

Project settings require validation, permission checks, attribution, timestamps, version history, conflict handling, and rollback. Concurrent edits must not silently overwrite one another.

## 5. Shared AI-run workflow

The shared workflow is:

1. A user creates a goal.
2. The Harness generates a plan.
3. An authorized user reviews the plan.
4. An authorized user approves or rejects it.
5. The orchestrator starts the governed run.
6. Team members observe status, events, and logs.
7. Authorized users pause, resume, or stop the run.
8. The run produces diffs and artifacts.
9. A reviewer approves, requests changes, or rejects the result.
10. The run reaches a terminal state and all actions are audited.

The system must correctly handle concurrent approvals, duplicate starts, stale approvals, browser disconnects, provider failures, replica crashes, permission changes, reviewer changes, and replay after restart.

## 6. Real-time collaboration

Users must receive real-time events for project changes, plan proposals, approvals, rejections, run lifecycle changes, log output, budget warnings, budget blocks, reviews, artifacts, GitHub status, invitations, and permission changes.

WebSockets require authentication, project authorization, expiring tickets, rate limits, connection limits, reconnect behavior, sequence-based replay, backpressure, duplicate prevention, cross-instance delivery, and safe stale-client handling.

Every event must identify its project and resource, actor, type, causal sequence, and replay information.

## 7. Presence and ownership

The interface should show who is viewing a project, reviewing a plan, assigned to a run, assigned as reviewer, or responsible for the latest change.

Presence is informational and must never substitute for authorization. Ownership must be explicit for active plans, runs, reviews, conflict resolution, and integrations. Ownership must be transferable after timeout, user removal, or administrator reassignment.

## 8. Code, diff, and review collaboration

The platform should provide repository state, current branch and commit, pull-request association, changed files, build status, test status, syntax-highlighted diffs, comments, review state, security findings, and test evidence.

Review actions should include approve, request changes, reject, comment, resolve comment, assign reviewer, reassign reviewer, accept hunk, reject hunk, regenerate, and create a follow-up run.

Each review must reference an exact commit or snapshot and exact diff. Reviews must not apply to outdated code without an explicit conflict warning.

Editor hunk acceptance/rejection and snapshot checkpoints were previously identified as unfinished areas and must either be implemented or explicitly marked out of scope.

## 9. Snapshots and checkpoints

Create recoverable snapshots before runs, after approvals, before model edits, after major changes, before patch application, before review acceptance, and before merge.

Each snapshot should include repository state, filesystem changes, plan and run identifiers, actor, timestamp, tool actions, test results, environment metadata, and parent snapshot.

Users should be able to compare, restore, branch from, rerun, and discard snapshots. Restoration must be authorized and audited.

## 10. Conflict handling

Use revision numbers, optimistic locking, compare-and-swap updates, idempotency keys, conflict responses, clear UI messages, and recovery actions.

Conflicts must be detected for simultaneous approvals, project-setting edits, stale comments, concurrent file edits, pause/stop actions, budget changes, and review decisions.

The system must never silently use the last writer when the action affects security, spending, execution, or code approval.

## 11. Shared artifacts

Plans, diffs, logs, test reports, screenshots, generated files, model responses, review reports, deployment reports, and backup reports must have team/project scope, owner, parent run, version, content type, size limits, retention rules, access controls, integrity information, and audit events.

Large artifacts should use appropriate object storage rather than oversized database rows.

## 12. Notifications

Support notifications for approval requests, rejections, run completion, run failure, budget warnings, budget blocks, review assignment, requested changes, security events, integration failures, backup failures, invitations, and permission changes.

Channels may include in-app, email, desktop, webhooks, Slack, and Teams. Notifications must be tenant-scoped, retryable, deduplicated, audited, and free of secrets.

## 13. GitHub and external integrations

Support per-team connections, per-project repositories, secure OAuth or token handling, least-privilege scopes, credential rotation, disconnect/revoke, pull-request linking, commit statuses, review synchronization, verified webhooks, replay protection, and provider-outage handling.

Credentials must never cross teams or appear in model prompts, logs, events, or error messages.

## 14. Budgets and resource coordination

Coordinate team, project, and user budgets, concurrent-run limits, provider limits, sandbox CPU/memory, queue capacity, storage, and artifact quotas.

Expose current spend, reserved spend, projected spend, warning thresholds, hard limits, remaining budget, and cost by user, project, model, and run.

Concurrent reservations must be atomic so users cannot spend funds already reserved by another user.

## 15. Administration and observability

Administrators need dashboards for active users, runs, queues, provider health, database health, event lag, WebSocket connections, failures, budgets, storage, backups, audit search, security events, and rate limits.

Operators need structured logs, metrics, health/readiness checks, alerts, migration status, rollback procedures, backup procedures, and disaster-recovery runbooks.

Failures must distinguish user errors, authorization denials, provider failures, infrastructure failures, budget denials, security blocks, and software defects.

## 16. Security requirements

The shared deployment requires TLS, secure sessions, strong authentication, CSRF protection, strict CORS, security headers, rate limiting, secret storage, key rotation, least-privilege database access, network segmentation, private database access, sandbox confinement, egress controls, SSRF protection, dependency scanning, secret scanning, audit logs, encrypted backups, and incident-response procedures.

Internal service ports and database ports must not be publicly exposed. Provider keys, session secrets, and unredacted provider exceptions must never appear in logs, responses, metrics, events, or artifacts.

## 17. Local-first and offline behavior

If separate local installations are supported, explicitly define what synchronizes: projects, plans, runs, events, artifacts, reviews, settings, and Git references.

Define identity mapping, conflict resolution, duplicate events, clock differences, offline approvals, stale budgets, deleted resources, reconnection, encryption, export, and recovery.

Offline synchronization must not be implied unless implemented and tested. It is substantially more complex than a shared-server model.

## 18. Desktop application layer

If packaged as a desktop app, provide secure login, server configuration, local/remote connection selection, backend health detection, secure credential storage, desktop notifications, live run monitoring, log viewing, project switching, offline indicators, reconnection, updates, and diagnostics.

Docker should be hidden from normal users. The installer must either manage local services or connect to a remote/private Harness server.

## 19. Required multi-user acceptance tests

The release should verify:

- Three users joining one team.
- Invitation and role enforcement.
- Viewer rejection of administrative actions.
- Plan proposal and simultaneous approval attempts.
- Exactly one valid approval.
- Live updates to multiple clients.
- Disconnect and event replay.
- Pause, resume, stop, and reviewer handoff.
- Budget warning and hard-block behavior.
- Cross-team access rejection.
- Honest provider failure.
- Replica crash and recovery.
- Backup and restore.
- Immediate access removal after member deletion.
- Project-setting conflict detection.
- Review rejection for outdated diffs.

## 20. Current Harness foundation

The existing project already contains substantial foundations for users, teams, roles, projects, authentication, tenant scoping, plan approval, orchestration, budgets, metering, events, WebSockets, audit logs, sandbox execution, GitHub integration, notifications, billing, OIDC, MCP server functionality, deployment, and backup tooling.

The principal remaining product gaps are complete multi-user E2E coverage, conflict handling, shared code/diff review, hunk-level editor workflows, snapshot management, presence, notifications, one-click private deployment, desktop packaging, offline synchronization if required, multi-replica evidence, live provider validation, and remaining legal/retention decisions.

## Recommended delivery order

1. Harden identity, authorization, and tenant isolation.
2. Complete shared project and resource permissions.
3. Verify state-machine concurrency and exactly-once behavior.
4. Complete event replay, WebSocket reconnect, and multi-client behavior.
5. Implement artifacts, snapshots, and checkpoints.
6. Implement code diff review and conflict handling.
7. Complete notifications and presence.
8. Add multi-user E2E tests.
9. Package private deployment.
10. Add the desktop client.
11. Add offline synchronization only if required by real users.
12. Add hosted collaboration and billing later.

## Product decision

The most practical first fully shared product is one private Harness server with multiple authenticated browser or desktop clients. This delivers real team collaboration without requiring peer-to-peer synchronization or a complex offline merge system.
