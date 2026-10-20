# Session 9 — reliability, scale, observability

Date: 2026-10-19. Scope: master-prompt parts 53, 54 and 55. Read
`docs/briefs/path-to-9-plan.md`, `docs/briefs/capability-matrix.md`, and the newest
`docs/briefs/path-to-9-checkpoint.md` before work. Preserve all pre-existing working-tree
changes; this repository currently contains edits from earlier sessions.

## Required outcomes

1. Map all 30 directives in `docs/master-prompts/harness-multiplayer-master-build-prompt.md`
   sections 53–55 to shipped controls, a named gap, and evidence.
2. Set measurable availability, control-plane latency, and event-delivery objectives; state
   their measurement windows, exclusions, and alert/runbook response. Distinguish targets from
   measured results.
3. Document dependency outage behavior, retry/idempotency and breaker coverage, queued-work
   visibility, state-before-ack behavior, restart recovery, and graceful degradation. Add a
   repeatable failure-injection drill for critical run and approval paths, or mark its execution
   as pending if the required live services are unavailable.
4. State measured and unmeasured capacity, operating limits, expected concurrency/burst cases,
   event-history management, pagination/payload bounds, rate limiting, DB/event/socket metrics,
   independent worker scaling, and cost per run/team.
5. Provide operational dashboards/queries and alert response for service health, latency,
   approvals, failures, cancellations, stalled work, event delivery, and unsafe lifecycle states.
   Give support diagnostics that preserve tenant boundaries and avoid collecting private payloads.
6. Update capability rows 53–55 and the current checkpoint with only supported claims.

## Evidence and verification rules

- Do not represent an authored target, synthetic benchmark, or structural test as production
  evidence. Record command, result, date, source revision, and environment for every fresh run.
- Targeted tests, lint and type checks are session exit requirements. If not run, state that
  plainly and leave the affected row partial.
- Part 53 is capped at 8 until a real S3-compatible WAL-G backup, restore and recovery-point
  round trip succeeds. Existing local/Docker drills do not satisfy that gate.
- Do not make live provider calls or change deployed alert destinations as part of this session.

## Exit

The session is complete only when its directive map, evidence, targeted verification, matrix
rows and compact checkpoint agree. Do not commit mixed working-tree changes or push.
