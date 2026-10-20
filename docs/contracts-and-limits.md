# Contracts and limits

Citations are `file:line` as of this commit. "NOT ENFORCED" means no code path rejects or clamps it.

## Event schema versioning

- Source of truth: `contracts/src/harness_contracts/events.py`. `EventEnvelope` (:31) is `{type, payload, ts, seq, projectId, runId}`. `EventType` (:8) is a closed enum. The project-stream envelope has **no per-event schema-version field**: versioning is per package. The collaboration stream does carry one: `run_collab_events.schema_version` (migration 0035), `COLLAB_SCHEMA_VERSION = 1` in `section1/.../eventbus.py`, `schemaVersion` on every frame and polling page (`docs/realtime.md` 5.5). Collaboration requests, states, legal transitions and error codes: `docs/collaboration-contract.md`.
- Package version is `1.0.0rc2` in four places that must move in one commit: `contracts/pyproject.toml:3`, `contracts/src/harness_contracts/__init__.py:54`, the TS mirror header (`contracts/ts/types.d.ts`), and installed metadata. Enforced by `section1/tests/test_version_pin_conformance.py:153`. The TS mirror is regenerated and checked with `contracts/ts/generate.py --check`.
- Additive change (new event type, new optional field): allowed without a major bump, but bump patch/minor and update the TS mirror. Precedent: `ENGINE_COMPLETED`/`ENGINE_FAILED` (`events.py:24-25`).
- Breaking change: bump the version and update every consumer's mocks in the same commit; no deprecation shim in `contracts/` (`docs/api-versioning.md`, "Contracts versioning law"; `contracts/api-conventions.md`, "Versioning").
- Wire params: one-release acceptance window before removal (`docs/api-versioning.md`, "Deprecation policy"). Error codes are stable API surface.
- `seq` is monotonically increasing per project; WS reconnect replays from `afterSeq` (`contracts/api-conventions.md`, "WebSocket").
- Consumer-side drift gate: `scripts/spec_drift.py` checks every `section{3,4,5,6,8,10}/openapi.json` and `scripts/sdk-gen/specs/*` against the mounted routes (path+method sets). Hand-written section specs previously lagged the code; they are now regenerated with `--write`.

## Enforced limits

Request-body caps return 422 (Pydantic). Shared values live in `contracts/src/harness_contracts/limits.py`.

| Limit | Value | Where |
|---|---|---|
| Login email / team id / team name | 320 / 64 / 200 chars | `limits.py:30-32`, used `section1/.../app.py:280-282` |
| OTP code | 12 chars | `limits.py:33`, `app.py:348` |
| Project name / repo full name | 200 / 200 chars | `limits.py:34-35`, `app.py:477-478` |
| Run goal text | 20,000 chars | `limits.py:41`, `section4/.../api.py:520`, `plan_gate.py:99` |
| Human-edit path | 512 chars | `limits.py:44`, `api.py:509` |
| Human-edit content | 100,000 chars | `limits.py:45`, `api.py:510` |
| Done-signal diff text | 200,000 chars | `limits.py:46`, `api.py:515` |
| Meeting title / message content | 200 / 10,000 chars | `limits.py:51,54`, `section6/.../api.py:84,90` |
| Collab: paths per proposal, path length, diff, text, reason | 100, 512, 200,000, 4,000, 500 | `section4/.../collab.py:45-49`, `api.py:896-913` |
| Project rules | 64 KB (413 above) | `section1/.../rules_api.py:44,126-131`; loader clamp `section4/.../rules_loader.py:32` |
| CSP report body | 4,096 bytes | `section1/.../csp_report.py:33,67` |
| Path rules (collab) | relative only, no `..`, no NUL, no backslash | `collab.py:97-109` |
| Path rules (sandbox edits) | no `..`, secret files blocked | `section5/.../policy.py:303-306` |
| Path rules (diffstore, staging) | no leading `/`, no `..` | `section4/.../diffstore.py:60`, `staging.py:284` |
| Event payload on the bus | 8,192 bytes (`BUS_PAYLOAD_MAX_BYTES` overrides); `PayloadTooLargeError` before any DB write | `section1/.../eventbus.py:60,368-374` |
| WS replay on reconnect | 5,000 events (`REPLAY_MAX_EVENTS`) | `eventbus.py:27-36` |
| Audit page size | 200 | `app.py:567,585` |
| Sandbox command budget | 300 s default | `section5/.../config.py:7`, `local_runner.py:173` |
| Context injected into prompts | 4,000 tokens (`CONTEXT_MAX_TOKENS`) | `section4/.../context_resolver.py:43` |

## Event / request rates (per IP, fixed 60 s window, Postgres-backed, fails open)

Default 10/min (`ratelimit.py:95-96`) on OTP request/verify, `auth/login`, SSO start/complete, invite mint/redeem (`ratelimit.py:114-143`). Overrides (`ratelimit.py:164-182`): run start 30, model call 60, brief generate 20, CSP report 30, WS-ticket mint 60. In-process login limiter: 20 per 60 s (`app.py:41-42`). Fail-open on store outage is deliberate (`section1/.../ratelimit.py` docstring, lines 12-16).

## NOT ENFORCED

- Event publish rate on the bus: only the per-event byte cap exists. The sockets are capped on the way out and in (500 live frames/s, 120 client messages/min, 20 sockets per user per process: `docs/realtime.md` section 4) and the collaboration mutation routes are IP-limited, but a publisher is not rate-limited.
- Global HTTP request-body size cap (no middleware reads Content-Length to reject); only per-field caps above apply, so unmodelled endpoints are unbounded.
- Max length of free-text fields outside the lists above (e.g. sandbox exec command text, section 3 model-call prompts, section 8 review comments): no cap found.
- Per-event schema version on the project stream (`EventEnvelope`): none. Collaboration events have one (above).
- Sandbox exec stdout/stderr size and per-file edit size: only the time budget is enforced.
