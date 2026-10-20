# harness-telemetry (A5-S4)

Tracing and measurable value for the harness — built **only** from the frozen
event vocabulary (`harness_contracts.events.EventType`) and the cost-ledger row
shape. No new tracking, no per-keystroke analysis, no invented ROI.

Three surfaces:

| Module | Purpose |
| --- | --- |
| `otel.py` | Frozen events → OpenTelemetry spans, deterministic trace ids, W3C `traceparent` in/out, real SDK export to any OTLP/HTTP collector |
| `metrics.py` | Quality / productivity / cost metrics over `RunRecord`s, every formula declared in `DEFINITIONS` |
| `archive.py` | SQLite run archive: scrubbed text at ingest, team-scoped search, explicit retention |
| `dashboard.py` | Read-only JSON APIs + a small HTML page over metrics + archive; fail-closed auth |

## The one distinction that matters

```
accepted change = pr_opened observed  AND  last independent review verdict passed=True
claimed completion = engine_completed event (the model says it is done)
```

`claimedCompletions` and `costPerClaimedCompletion` are reported **next to**,
never instead of, `acceptedChanges` / `costPerAcceptedChange`. Fixture `run-a3`
is the regression guard: engine claims done, review rejects, no PR — it must
never count as value.

## Usage

```python
from harness_telemetry import (
    Archive, build_records, cost_metrics, fixtures,
    parse_cost, parse_stream, export_records,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

events = parse_stream(fixtures.events())          # or live event-store rows
costs = [parse_cost(row) for row in fixtures.costs()]
records = build_records(events, costs, {"proj-a": "team-a"}, as_of=fixtures.AS_OF)

print(cost_metrics(records))
export_records(records, as_of=fixtures.AS_OF,
               exporter=OTLPSpanExporter(endpoint="http://collector:4318/v1/traces"))
```

Dashboard (host it yourself; it is not mounted into any agent-facing service):

```sh
TELEMETRY_DB=./telemetry.sqlite TELEMETRY_FIXTURES=1 \
  uvicorn --factory harness_telemetry.dashboard:app_factory
```

Deployments wire `app.state.get_principal` (house pattern); **unwired auth
returns 503, never open access**. Non-admins are pinned to `principal.teamId`;
requesting another team returns `403 TEAM_FORBIDDEN`. Admins see org-wide by
default and may pass `?team=`.

Endpoints: `/healthz`, `/`, `/api/v1/telemetry/{quality,productivity,cost}`,
`/api/v1/telemetry/archive/search`, `/api/v1/telemetry/archive/runs/{runId}`.

## Privacy and retention

- All goal text and event payloads pass `scrub_text` **before** storage
  (tokens, API keys, passwords, bearer values, emails). The raw text never
  reaches SQLite — see `test_archive.py::test_search_is_scrubbed_and_finds_by_goal`.
- Retention is a per-ingest policy stamp (`retention_days=90` default,
  `retentionUntil` per run); `purge_expired()` deletes expired runs and their
  events. No indefinite accumulation by default.
- Search is team-scoped at the API layer; an empty `visible_teams` set sees
  nothing. There is deliberately **no per-developer productivity score** —
  metrics are per-run/team, not per-person ranking.

## Known limits

- SQLite single-node store; `q` is a `LIKE` scan, fine for pilot scale, not a
  search cluster.
- OTLP export is push-only with the SDK's default retry; endpoint/TLS/auth are
  deployment concerns (collector side), not hardcoded.
- Fixtures are synthetic; S4 makes no claim about real-world productivity or
  ROI. Pilot instruments live in `docs/agent-program/a5/session-4/pilot-measurement.md`.

## Tests

```sh
packages/extensions/.venv/bin/python -m pytest packages/telemetry/tests -q   # 31
```

Includes a real HTTP OTLP collector round-trip (protobuf on the wire), exact
denominator assertions over 8 fixture runs (failures, rework, abandonment,
blocked, abandoned plan, in-flight), scrubbing proofs against the raw DB, and
dashboard auth/scoping tests.
