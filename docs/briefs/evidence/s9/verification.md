# Session 9 verification — 2026-10-19

Environment: local macOS workspace, Python 3.13 section virtual environments, SQLite scratch
fixtures. No PostgreSQL, Docker-backed deployment, Grafana instance, production provider, or real
object store was used. Results below establish local behavior only.

## Changes covered

- `deploy/observability/dashboards/harness-overview.json`: importable operations dashboard with
  scrape health, 5xx ratio, request latency, run backlog, event/worker health, approval denials,
  cancellations, isolation violations and event delivery outcomes.
- `deploy/observability/alerts.yml`: route-level request p95 alert at the proposed 250 ms target,
  sustained for 10 minutes and linked to the SLO runbook.
- `section1/tests/test_deploy_observability.py`: structural dashboard check for unique panel IDs
  and required operations signals.
- `section4/src/harness_orchestrator/service.py`: parse SQLite JSON string columns as JSON when
  rehydrating persisted plans. Previously, `list("[]")` became `["[", "]"]`, which incorrectly
  triggered the fail-closed forbidden-path refusal after worker restart.
- `section4/tests/test_stateless_orchestrator_n1.py`: scratch schema now includes the daily
  reservation and run-participant tables consumed by the current service.
- Existing recovery assertion now includes the engine result when a recovery execution fails.

## Commands and results

| Command | Result |
|---|---|
| `cd section1 && ./.venv/bin/python -m pytest tests/test_alert_rules_load.py tests/test_deploy_observability.py -q` | 30 passed, 1 skipped, 9 warnings. The skip is the optional external PromQL parser check; structural rule and new dashboard checks passed. |
| `cd section1 && ./.venv/bin/ruff check tests/test_deploy_observability.py` | Passed. |
| `cd section4 && ./.venv/bin/python -m pytest tests/unit/test_observability.py tests/unit/test_engine_runtime.py tests/unit/test_lifecycle_recovery.py -q --tb=short` | 30 passed, 5 warnings. Covers request/metrics behavior, circuit breaker behavior and two-replica restart/reclaim recovery. |
| `cd section4 && ./.venv/bin/ruff check src/harness_orchestrator/service.py tests/test_stateless_orchestrator_n1.py tests/unit/test_lifecycle_recovery.py` | Passed. |
| `python3 -m json.tool deploy/observability/dashboards/harness-overview.json >/dev/null` | Passed; dashboard is valid JSON. |
| `git diff --check` | Passed. |

The first Section 4 run before the JSON decoding fix had 27 passed and 3 failed recovery tests.
The restart tests surfaced the false non-empty forbidden path; after the fix, the full focused set
passed. This failure and repair are retained here rather than hidden.

## Still open

- No real PostgreSQL restart/concurrency drill; no S3-compatible WAL-G PITR round trip. Part 53
  remains externally capped at 8.
- No burst/steady load ladder on the current tree, multi-replica topology, WebSocket scale run, or
  live provider cost measurement.
- No Alertmanager destination, alert delivery test, Grafana import/visual review, durable SLO
  attainment measurement, or end-to-end event freshness histogram.
- No dedicated support bundle/access workflow or failure-injection drill covering the full
  critical lifecycle and approval path on deployed services.
- `promtool` was not available as a local binary; PromQL full-parser validation was not run.
- Docker CLI was present, but `docker info` could not connect to
  `unix:///Users/user/.docker/run/docker.sock` because the daemon is not running; the isolated
  compose load stack and PostgreSQL drills could not be started.
- The latency alert is a route-level p95 guard and does not measure end-to-end event freshness or
  guarantee notification delivery.

Parts 53–55 remain partial. This evidence is limited to the commands and local setup above.
