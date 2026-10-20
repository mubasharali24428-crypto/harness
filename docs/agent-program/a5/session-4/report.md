# A5-S4 report — tracing and measurable developer value

Session scope (master prompt line 220): F112–F117 — OpenTelemetry export, agent
quality dashboard, developer productivity dashboard, cost-per-task and
cost-per-accepted-change, time-to-first-useful-change, searchable
organization-wide run archive. Acceptance: known event fixtures with
failures/rework/abandonment, trace propagation tests, PII scrubbing, archive
ownership/retention, transparent metric denominators; draft pilot measurement
instruments and privacy-conscious opt-in; no invented ROI.

Deliverable: `packages/telemetry` (`harness-telemetry` 0.1.0) + goldens,
pilot instruments and evidence under `docs/agent-program/a5/session-4/`.
Independently hosted dashboard — Agent4 frontend untouched.

## What shipped

### 1. `harness_telemetry.otel` — OpenTelemetry export (F112)

Frozen `EventType` stream → span tree `harness.run` → {plan, execution,
review×n, change}. Trace/span ids are **deterministic** (md5 of `runId`), so
goldens and cross-service propagation are reproducible. `export_records`
replays through the **real OTel SDK** (explicit OK/ERROR status per outcome);
W3C `traceparent` works both directions with strict validation.

### 2. `harness_telemetry.metrics` — quality/productivity/cost (F113–F116)

The single non-negotiable distinction:

```
accepted change    = pr_opened observed AND last review verdict passed=true
claimed completion = engine_completed event (model says done)  ← separate number
```

Fixture `run-a3` (claims done → review rejects → no PR) is the regression
guard: it appears in `claimedCompletions` and never in accepted denominators.
Every metric's formula and denominator ship in `DEFINITIONS`, rendered verbatim
by the API and the HTML page. Percentiles are nearest-rank (documented).

### 3. `harness_telemetry.archive` — run archive (F117)

SQLite runs+events built from the stream. Text is scrubbed **at ingest**
(tokens/keys/passwords/bearer/emails) — the raw DB is proven in tests to never
contain the originals. Team scoping is fail-closed at the API layer
(`visible_teams`: None=admin org-wide, set=those teams, empty=nothing).
Retention is an explicit per-ingest stamp (default 90 days) with
`purge_expired` cascading to events.

### 4. `harness_telemetry.dashboard` — read-only APIs + minimal page

`/api/v1/telemetry/{quality,productivity,cost}`,
`/api/v1/telemetry/archive/{search,runs/{runId}}`, `/healthz`, `/`.
Unwired principal dep → **503**, never open. Member requesting another team →
**403 TEAM_FORBIDDEN**. Admin org-wide or `?team=`.

### 5. Pilot instruments (draft only)

`pilot-measurement.md` — formulas, denominators, opt-in/privacy checklist,
qualitative survey kept separate from product dashboards, explicit "no
individual productivity score" stance. No ROI number exists or is claimed.

## Verification (commands run, output inspected; log in `verification.log`)

| # | Command | Result |
| --- | --- | --- |
| 1 | `ruff check` (telemetry, extensions, wiki, workflows, scripts/a5) | All checks passed |
| 2 | `pytest packages/extensions/tests -q` | 90 passed |
| 3 | `pytest integrations/connectors/wiki/tests -q` | 8 passed |
| 4 | `pytest integrations/connectors/workflows/tests -q` | 35 passed |
| 5 | `pytest packages/telemetry/tests -q` | **31 passed** — incl. real HTTP OTLP collector receiving protobuf, exact denominators over 8 fixture runs, raw-DB scrubbing proof, dashboard auth/scoping |
| 6 | `python scripts/agent-program/a5/capture_golden_s4.py` | 21 goldens: 12 dashboard API + 8 span files + traceparents.json (deterministic, AS_OF-pinned) |
| 7 | `python packages/extensions/scripts/verify_distribution.py` | **DISTRIBUTION_VERIFIED** — 5 wheels incl. `harness_telemetry-0.1.0`, fresh venv installs + telemetry smoke → `interop/distribution-verify.log` |

Total: **164 tests green** (90+8+35+31). Gate key values re-checked from
goldens: `accepted 3 / rejected 2 / abandoned 1 / blocked 1 / active 1`,
`claimedButNotAccepted 1`, `costPerAcceptedChange 4.3667 ≠ costPerClaimedCompletion 4.2750`,
TTFUC p50 75 / p90 85, scrubbed goal `Fix login for [EMAIL]; [REDACTED]`.

## Decisions log

- **Deterministic ids over random** — traces are evidence artifacts; md5(runId)
  keeps `traceparent` stable across re-exports and lets goldens diff cleanly.
- **Fixtures are the spec** — 8 synthetic runs encoding the acceptance's
  failures/rework/abandonment cases; tests assert exact numbers, so a silent
  semantics change breaks loudly.
- **Scrub at ingest, not read** — the archive must never contain raw secrets,
  even transiently on disk. Cost: searches can't match raw token text (desired).
- **Separate claimed vs accepted everywhere** (metrics, spans, cost) — this is
  the S4 acceptance's bright line between model claims and measured value.
- **Read-only dashboard, no Agent4 edits** — mountable in Section 10 assembly
  later; principal wiring follows the house `app.state.get_principal` pattern.
- **Nearest-rank percentiles** — deterministic, explainable to pilot teams;
  interpolation would fake precision on 3-sample data.

## Known limits carried forward

- No live production OTLP collector, Grafana/Honeycomb stack, or real pilot —
  collector-side TLS/auth and dashboards/tooling are deployment concerns.
- Archive is single-node SQLite with LIKE search; org-scale search needs a
  real store (documented, not pretended).
- OTel dependency pins are lower-bound only (VR014 partial control).
- Quality is event-derived; review issue text is not semantically scored.
- All numbers in this session are synthetic-fixture numbers; **no productivity
  uplift, ROI, willingness-to-pay or retention claim is made**.
