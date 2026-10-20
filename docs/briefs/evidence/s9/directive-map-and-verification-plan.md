# Session 9 directive map and verification plan

Date: 2026-10-19. Scope: master-prompt parts 53–55. This is an initial mapping, not a
verification report. References are repository artifacts inspected for this session; no fresh
tests or live drills were run while preparing it.

## Part 53 — Reliability and failure handling

| Directive | Existing evidence/control | Remaining work / status |
|---|---|---|
| 001 SLOs | Backend HTTP duration metrics; load baseline | Set target and measurement windows; no measured rolling SLO yet. Partial. |
| 002 dependency outages | `docs/runbooks/degraded-mode.md`; provider breaker and DB readiness behavior | Queue and integration outage behavior needs an executable drill. Partial. |
| 003 bounded retries/idempotency | Provider retry and idempotent run/approval paths exist | Verify bounds across dependency classes and capture fresh test output. Partial. |
| 004 circuit breaker | Provider circuit breaker in Section 4 | Tests exist; broad dependency coverage and current-run evidence not reviewed. Partial. |
| 005 queued work visible | Queue depth/age metrics; run status API | Verify user/operator visibility during maintenance and outage. Partial. |
| 006 persist before acknowledge | Durable run/event paths documented | Need a failure-injection assertion at each critical action boundary. Partial. |
| 007 restart without duplicate side effects | Claim leases and recovery tests; current S9 focused run in `verification.md` | Local two-replica restart/reclaim checks pass after correcting JSON string decoding on SQLite; deployed PostgreSQL drill remains open. Partial. |
| 008 graceful degradation | Provider BLOCK behavior, presence/search fallback docs | Verify each noncritical feature and user-visible explanation. Partial. |
| 009 DR/RPO | `docs/runbooks/backup-and-pitr.md` has prior local drill evidence | Real object-store WAL-G round trip absent; externally capped at 8. |
| 010 failure injection | Lifecycle/approval and live-PG test files exist | No fresh combined drill evidence for this session. Partial. |

## Part 54 — Scalability and performance

| Directive | Existing evidence/control | Remaining work / status |
|---|---|---|
| 001 documented limits | `docs/contracts-and-limits.md`; Section 4 participant and run caps | Several entity ceilings and deployment operating envelope are not unified. Partial. |
| 002 concurrency/burst model | `docs/benchmarks/BASELINE.md` documents 8–64 VU closed-loop run | Baseline is historical, single-replica and dry-engine; define realistic scenarios. Partial. |
| 003 event history | Partition/retention mechanisms and runbooks | Fresh query responsiveness/retirement evidence absent. Partial. |
| 004 pagination/bounds | API conventions and endpoint-specific limits | No fresh cross-endpoint audit in this session. Partial. |
| 005 bounded fan-out | Presence/participation caps and delivery backpressure | Fan-out behavior at large session sizes needs measurement. Partial. |
| 006 rate limits | Section 1 protected-route limits; docs/contracts-and-limits.md | Document missing event/socket and team-scoped cases. Partial. |
| 007 DB/event/socket measurements | Pool, queue, event lag and delivery metrics; Prometheus scrape config | WebSocket load and query-cost dashboards/evidence incomplete. Partial. |
| 008 independent workers | Queue worker and executor are separate concerns | Multi-replica/API/worker scaling has not been verified. Partial. |
| 009 realistic load scenarios | Existing loadtest scripts and baseline | Add a scenario matrix including burst, steady arrival, outage and recovery. Partial. |
| 010 cost/run/team | Cost ledger and baseline estimate | Synthetic cost only; no live provider cost trend or team dashboard. Partial. |

## Part 55 — Observability and support

| Directive | Existing evidence/control | Remaining work / status |
|---|---|---|
| 001 safe correlation | Request IDs, run/session/event identifiers and log correlation tests | Verify agent-task linkage and redaction end to end. Partial. |
| 002 structured telemetry | Structured request logs, metrics and secret-scrubbing work | Trace coverage and sensitive-field audit need fresh verification. Partial. |
| 003 dashboards | Prometheus metrics/rules plus importable `deploy/observability/dashboards/harness-overview.json` | JSON and panel-signal structure checked; dashboard not imported or visually verified. Partial. |
| 004 alerts | Prometheus alerts for stalled runs, failures, lifecycle safety, plus route p95 objective alert | Alertmanager/delivery receiver is not configured; rules alone do not notify. Partial. |
| 005 scoped support diagnostics | Existing team-authorized run/status APIs | No dedicated support diagnostic bundle/access workflow. Partial. |
| 006 user explanations | Structured API errors and UI error surfaces | Common outage/blocked-state wording not comprehensively verified. Partial. |
| 007 incident evidence | Audit/event export and request IDs | Need a privacy-bounded incident capture recipe and retention check. Partial. |
| 008 runbooks | `docs/runbooks/{ops,alerting,degraded-mode,backup-and-pitr}.md` | Reconcile procedures with fresh current-tree drills. Partial. |
| 009 support-to-gap tracking | Capability matrix and issue/review docs | No dedicated support feedback loop identified. Partial. |
| 010 rollout trend review | Historical load and pilot evidence | No current unified trend review; rollout decision belongs to Session 10. Partial. |

## Fresh verification status

Focused current-tree checks and lint are recorded in `docs/briefs/evidence/s9/verification.md`.
The local SQLite two-replica restart/reclaim checks and provider breaker tests pass after fixing
JSON string decoding during plan rehydration. Remaining drills: crash after durable enqueue and
before ack; approval decision retry/race; event-delivery interruption and cursor replay; burst
load with queue-age/DB-pool/event-lag measurements. Live PostgreSQL and deployed topology are
needed for database and deployment claims.
