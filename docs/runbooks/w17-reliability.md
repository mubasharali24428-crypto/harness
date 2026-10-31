# W17 reliability operations — proposed pilot policy

This runbook supplements [service objectives](slo-and-capacity.md),
[alerting](alerting.md), [degraded mode](degraded-mode.md), and
[backup and PITR](backup-and-pitr.md). Targets below are proposed engineering
objectives, not a customer commitment or evidence of attainment. An operator
must assign a named on-call owner and escalation contact, configure and test
alert delivery, and validate the indicators on the deployed topology before
using them as a service promise.

## Availability and error budget

For the proposed 99.5% rolling 30-day availability objective, count eligible
requests and unsuccessful eligible requests according to the exclusions in
the service objectives. The request-based error budget is 0.005 × eligible
requests; remaining budget is that number minus unsuccessful eligible requests.
For example, 100,000 eligible requests permit 500 unsuccessful requests. This
is not a fixed allowance of minutes: traffic is not uniform and request-based
availability must not be computed from `/health` alone.

The existing 5xx alert is a diagnostic proxy, not the complete SLI: it does
not implement all eligibility and exclusion rules, and missed scrapes or absent
traffic cannot be treated as success. Record eligible and unsuccessful counts,
window, deployment, source query and missing-data intervals before reporting a
budget. If the required data or retention is missing, mark the budget unknown.
When the budget is exhausted, the incident owner should pause discretionary
risk-increasing releases, review the failure modes and agree a recovery plan;
the authorized release owner decides whether an urgent fix proceeds. Reassess
after the rolling window changes. This is a proposed operating policy until a
human owner adopts it.

The proposed control-plane p95 and event freshness objectives in the existing
runbook are separately assessed; they do not consume the availability budget
unless a separately defined request outcome fails. The existing freshness
instrumentation does not prove the end-to-end objective.

## Triage and escalation

1. Record detection time, affected deployment and user-visible symptoms; check
   alert delivery and monitoring freshness. Assign an incident commander and
   communications owner using the deployment's actual on-call roster.
2. For a growing queue, inspect depth, oldest queued age, stale claims, worker
   health, executor occupancy, DB pool and per-team limits together. Avoid
   raising concurrency until the limiting resource and safe envelope are known.
3. For provider failures, identify the affected provider/model and inspect
   timeout and circuit-breaker signals. A timed-out synchronous engine thread
   may still occupy a slot; fail and recover visibly without changing the
   requested model. The older [degraded-mode playbook](degraded-mode.md)
   describes a manual model switch for a past mission; it is not authorization
   to change a customer's selected model or modify a shared deployment.
4. For slow event clients, a live queue overflow closes the socket and requires
   replay from the last received cursor. Check replay truncation and use the
   documented backfill path; do not infer missing project events from gaps in
   globally allocated sequence numbers.
5. For lost data or database recovery, follow the restore runbook on an
   isolated target. The PITR drill script runs in its own throwaway compose
   project, refuses a project that already has containers and removes only what
   it created (see [backup and PITR](backup-and-pitr.md)); never point a drill
   at the production database or run a plain `up` of `walg-db` in the live
   project, which would archive into the live prefix.
6. Record recovery time, observed impact, evidence and follow-up owner. Do not
   include credentials, prompts or customer content in incident artifacts.

## Status communication

Health endpoints and Prometheus are operator diagnostics, not a public status
page. No public status page or assigned publisher is established by this
runbook. Until one is provisioned and authorized, the incident commander should
use the deployment's approved customer communication channel and record the
channel and times. Initial and follow-up updates should state affected services,
impact, current investigation or mitigation, and next update time; report
recovery only after a representative user journey is verified. Avoid naming a
cause until supported by evidence. A human owner must establish the public page,
publishing permissions, update cadence and accessibility before calling this
gate complete.
