# harness-workflow-connectors

Signed-webhook intake for six workflow platforms (Slack, Teams, Linear, Jira,
Sentry, Datadog/Grafana alerts). An intake turns an inbound message or incident
into a **proposed plan** on the Section 4 orchestrator plan gate. It can never
approve, start, or execute anything — that stays a human action.

## Service contract

| Route | Method | Purpose |
|---|---|---|
| `/healthz` | GET | liveness + configured providers |
| `/webhooks/{provider}` | POST | signed inbound delivery for `slack` / `teams` / `linear` / `jira` / `sentry` / `alerts` |

Responses (`{provider}` in the path, raw body + provider headers in the request):

| Status | Body | Meaning |
|---|---|---|
| 202 | `{status: "plan_proposed", planId, teamId, awaitingApproval: true}` | plan minted, waiting for a human |
| 200 | `{status: "duplicate"}` | same delivery seen before; no second plan |
| 400 | `EVENT_IGNORED` / `PAYLOAD_INVALID` | event kind is not actionable (edit, resolve, non-issue resource…) |
| 401 | `SIGNATURE_INVALID` / `SIGNATURE_STALE` / `SIGNATURE_MISSING` | request is not provably from the provider |
| 403 | `TENANT_UNKNOWN` | signature valid but no tenant mapping (indistinct, no oracle) |
| 429 | `RATE_LIMITED` | per-tenant intake window exhausted |
| 502 | `PLAN_GATE_REJECTED` | upstream plan gate failed; delivery is retryable, not marked done |

Errors use the frozen envelope `{"error": {code, message}}` (contracts/
api-conventions.md).

## Security model

- **Signatures** are verified on the raw body before parsing anything. Schemes:
  - Slack: real `v0=hmac-sha256` over `v0:{ts}:{body}` with a 300s freshness window.
  - Sentry: real `Sentry-Hook-Signature` (hmac-sha256 hex) + `Sentry-Hook-Resource` check.
  - Teams / Jira / Grafana+Datadog alerts: `Bearer <hmac>` / `X-Jira-Secret` /
    `X-Alert-Secret` — documented **adapter simplifications**, because those
    platforms have no single canonical inbound HMAC (Teams legitimately posts
    unsigned to a secret webhook URL; enterprise setups front it with a proxy).
    The `SignatureScheme` objects (`sig_scheme.py`-style rows in each module)
    are the swap point for a real scheme per deployment.
- **Tenant mapping is fail-closed**: provider tenant id (Slack `team_id`,
  Sentry org uuid, Linear orgId, Jira `baseUrl` host, Teams `channelData.tenant.id`,
  Alertmanager `labels.org_id`) must be present in the configured map. Unknown →
  403 with no distinguishing detail.
- **Idempotency** is a SQLite claim/settle/release table keyed by
  `(provider, delivery id)`. A concurrent duplicate loses the claim and gets
  200; an upstream failure releases the claim so a provider retry re-enters.
- **Rate limiting** is a per-tenant sliding window (`rate_limit_per_minute`,
  default 60). Refused before any plan gate call.
- **Secret redaction**: `xoxb-…`, `sk_live_…`, `ghp_…`, `Bearer …`, AKIA… and
  generic `api_key=`/`token=` patterns are scrubbed from text before it enters
  a goal or any log line.
- **Untrusted context, never instructions**: every goal carries the banner
  `[connector intake | provider=… | external_id=… | untrusted context, requires
  human plan approval]`. Incident text is attacker-controlled by definition;
  the plan gate is human-gated and an agent never sees it as a command.

## Plan gate integration

The only upstream call is `ProposePlanGate.propose_goal(goal)` → Section 4
`POST /api/v1/orchestrator/plans`, which mints a run and leaves it at
`AWAITING_PLAN_APPROVAL`. The client class has **one method** — there is no
approve/start/execute code path anywhere in this package, and the service
exposes no such routes (asserted in `tests/test_incidents.py` and the A5-S3
real-gate smoke).

Verified against the real orchestrator: `scripts/agent-program/a5/smoke_real_plan_gate.py`
(log: `docs/agent-program/a5/session-3/real-gate-smoke.log`).

## Configuration (deploy app_factory)

| Env | Meaning |
|---|---|
| `CONNECTOR_TENANTS` | path to tenants JSON: `{provider: {provider_tenant_id: {team_id, rate_limit_per_minute?}}}` |
| `CONNECTOR_SECRET_SLACK` etc. | one signing secret per provider |
| `PLAN_GATE_URL` / `PLAN_GATE_TOKEN` | Section 4 base URL + bearer token |
| `CONNECTOR_DB` | SQLite path for the idempotency store |

## Development

```sh
# from repo root, using the extensions venv (has the package installed -e)
packages/extensions/.venv/bin/python -m pytest integrations/connectors/workflows/tests -q
packages/extensions/.venv/bin/ruff check integrations/connectors/workflows/src integrations/connectors/workflows/tests
```

Goldens: `scripts/agent-program/a5/capture_golden_s3.py` →
`docs/agent-program/a5/session-3/interop/` (24 fixtures + `routes.json`).

## Known limits (honest)

- Outbound notifiers (`SlackNotifier`, `TeamsNotifier`, `LinearNotifier`,
  `JiraNotifier`) are unit-tested against real fake HTTP servers but have not
  been run against live vendor APIs.
- Teams/Jira/alerts inbound signature schemes are adapter simplifications (see
  above); Slack and Sentry are the real schemes.
- No retry queue: a failed plan-gate call returns 502 and relies on the
  provider's own retry. Idempotency makes that retry safe.
- SQLite idempotency store is single-node; a multi-replica deploy needs a
  shared store (the `IdempotencyStore` class is the seam).
