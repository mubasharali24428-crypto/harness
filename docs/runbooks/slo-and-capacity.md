# Service objectives and capacity envelope

Status: proposed operating objectives for Session 9, not production SLO attainment. The current
benchmark is a historical, single-replica, closed-loop dry-engine run; see
[`docs/benchmarks/BASELINE.md`](../benchmarks/BASELINE.md). Re-baseline on the release candidate
and deployed topology before promising these objectives externally.

## Objectives

| Signal | Target | Measurement and exclusions | Alert/action |
|---|---|---|---|
| API availability | ≥99.5% successful health-eligible requests per rolling 30 days | Count successful backend responses against eligible requests; exclude caller 4xx, planned maintenance announced in advance, and synthetic probes failing before reaching Harness. Do not treat `/health` alone as service availability. | `HarnessServiceDown` and `HarnessHigh5xxRate`; follow [alerting](alerting.md) and [ops](ops.md). |
| Human-gated control-plane latency | p95 ≤250 ms over 5-minute windows | Measure propose/approve/start server duration separately from queue wait and provider execution. Report by route and service; do not hide errors by excluding them. | Existing HTTP duration histogram and high-5xx alerts; investigate DB pool, route latency, then queue. |
| Event delivery freshness | ≥99% of committed collaboration events delivered or replayable within 5 seconds over 5-minute windows | Start at durable event commit; finish on the frame's write to a live socket or a successful replay send. A committed row is durable — hence replayable — at commit, so live delivery is the only variable part. Measured by `harness_event_delivery_lag_seconds` (live, **the SLO signal**) with `harness_event_replay_age_seconds` as a separate diagnostic (a long-closed client legitimately replays old events). Keep drops and duplicates visible on `harness_event_delivery_total{outcome}`. | `HarnessEventFreshnessSlo`: the `le="5.0"` bucket is below 99% of live observations for 10m, guarded so absent traffic does not page. Still **not attained**: the histogram and alert exist and were exercised end to end, but a human owner must adopt the target and it must hold over a real window on the deployed topology. |

These are engineering targets, not a service guarantee. A rolling SLO needs durable time-series retention,
alert routing, and a reviewed maintenance policy. The measurement instruments now exist on the deployed
stack: the live freshness histogram (with a matching alert) and the backup-freshness gauge feeding
`HarnessBackupStale`. The Alertmanager *mechanism* is exercised to a live target by
`scripts/e2e/w17_alert_delivery_drill.py` (a synthetic `page` alert reached a local webhook sink through
the real template), but no paging destination has been chosen by an owner: the running stack's email
receiver points at a catcher that is not running. Until a real destination is configured and exercised,
operators must keep the Prometheus alerts page open during a pilot.

An importable Grafana dashboard definition is provided at
[`deploy/observability/dashboards/harness-overview.json`](../../deploy/observability/dashboards/harness-overview.json).
Import it into a Grafana instance with a Prometheus data source. It is a visualization artifact;
the repository does not currently deploy Grafana or provision its data source. The 5xx ratio and
scrape status are useful signals but are not a complete request-based availability SLI.

## Capacity envelope and load scenarios

The published baseline exercised 8, 16, 32 and 64 Locust users for 120 seconds against one isolated
stack replica, a dry echo engine, PostgreSQL and the current worker poll loop. It reported zero journey
failures, control-plane p95 from 34 to 98 ms, and engine wait p50 from 4.1 to 38 seconds. Throughput
plateaued near 1.7–1.9 journeys/s because one queue claim is made per worker poll. These values describe
that setup only. They do not establish a production user/team cap or multi-replica capacity.

Session 12 re-ran it on the current tree (`docs/briefs/evidence/s12/part54.md`): one replica 12.1
journeys/s at 8 VUs and 17.6 at 32 (batch claims), two replicas on one queue 22.0 at 32 VUs with
single-owner claims and no lost or duplicated terminal transitions; one identity replica held 1000
concurrent ticketed sockets and delivered one event to all of them (p95 517 ms at 50 x 20). The
"Horizontal worker scale" row below is therefore exercised once; the others remain to run.

Use this scenario set before raising deployment limits:

| Scenario | Offered workload | Observe | Pass condition |
|---|---|---|---|
| Steady baseline | 8 VU, 120 s, closed-loop journeys | control p95, queue wait, error rate, DB pool | Reproduce prior baseline within a documented tolerance; no unexplained errors. |
| Concurrency ladder | 16/32/64 VU, 120 s each | journey completion, queue age, event lag, DB pool, CPU/memory | No lost/duplicate terminal transitions; publish saturation point and queueing curve. |
| Burst and drain | ramp 4 VU/s to 64, hold 2 min, stop arrivals | oldest queued age and drain time | Queue drains within an explicit, measured recovery objective. |
| Dependency outage | provider timeout/429 and transient DB interruption | breaker state, queued work visibility, retry count, recovery | Retries bounded; no duplicate side effects; blocked/degraded state is visible. |
| Horizontal worker scale | one then two worker replicas, same API/DB | throughput, duplicate claims, DB contention | Claims remain single-owner and throughput improves without invariant violations. |

Do not increase concurrency or team limits based only on the dry-engine ladder. Record image/revision,
compose profile, replica count, generator shape, provider mode, environment, raw output, and cost per
completed run/team for every run. Keep synthetic and provider-backed cost results separate.

## Operator limits and diagnostics

- Use the endpoint-specific limits in [`contracts-and-limits.md`](../contracts-and-limits.md),
  participant cap and queue/executor settings as explicit configuration ceilings. They are not a
  certified organization or user capacity guarantee.
- Keep run history paginated and payloads bounded; use the retention runbook for archival and purge.
- Check `harness_queue_depth`, `harness_oldest_queued_run_age_seconds`,
  `harness_stale_running_claims`, `harness_event_bus_lag_seconds`, and DB-pool histograms together.
- For a support case, collect request ID, team-scoped run ID, event cursor/sequence, timestamps,
  service revision and relevant state transitions. Retrieve details through the caller's existing
  team authorization. Do not attach prompts, artifacts, credentials, model output or raw request
  bodies unless a separately authorized evidence workflow requires them.
- Keep database, API, worker and real-time delivery scaling independently observable. Two-replica
  queue behaviour and 1000-socket capacity were measured once in session 12 (dry engine, one host).

## Session 9 verification still required

Run the targeted Section 1/4 metric, alert, retry, lifecycle recovery and event delivery suites;
run Ruff for touched Python; then perform the isolated load and restart/failure drills. Record exact
commands/results in `docs/briefs/evidence/s9/`. Do not label objectives achieved until measurement
and alert routing work on the target deployment.
