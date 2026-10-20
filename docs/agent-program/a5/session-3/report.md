# A5-S3 report — workflow connectors: Slack, Teams, Linear, Jira, Sentry, alerts

Date: 2026-10-17 · Baseline: `31bf4077e9b567b1f50ecb99818d501baab3115a`
New artifact: `integrations/connectors/workflows` (`harness-workflow-connectors` 0.1.0)
+ `scripts/agent-program/a5/{run_real_plan_gate,smoke_real_plan_gate,capture_golden_s3}.py`

Six signed-webhook connectors that turn inbound messages and incidents into
**proposed** plans on the Section 4 orchestrator plan gate. The design law for
this session: **incident text is untrusted context and never authorizes
execution** — the connector's only upstream capability is "propose a plan";
approval and execution stay human, verified end-to-end against the real
orchestrator.

## What shipped

### F121 — Slack task intake and status updates (VERIFIED IMPLEMENTED)
`POST /webhooks/slack` with the **real** Slack v0 signature scheme
(`v0=hmac-sha256` over `v0:{ts}:{body}`, 300s freshness window). Message event →
`team_id` tenant mapping → `event_id` idempotency claim → plan gate → 202
`{planId, awaitingApproval: true}`. Outbound `SlackNotifier.post_status`
(chat.postMessage) exercised against a real fake HTTP server. 9 tests:
propose, duplicate→200, 5-way concurrent duplicate→single plan, tampered sig
401, stale timestamp 401, tenant spoof 403, rate limit 429, upstream outage 502
then provider retry 202, real-fake status post incl. bad-token refusal.

### F122 — Microsoft Teams task intake and status updates (VERIFIED IMPLEMENTED)
`POST /webhooks/teams`, bearer-HMAC scheme over the raw body,
`channelData.tenant.id` mapping, message-id idempotency. Outbound MessageCard
notifier against a real fake receiver. 3 tests + goldens 14/15.

### F123 — Linear issue integration (VERIFIED IMPLEMENTED)
`POST /webhooks/linear` with the real `Linear-Signature` hex scheme; Issue
`create` only (`update`→400 EVENT_IGNORED); `Linear-Delivery` idempotency.
Outbound GraphQL `commentCreate` notifier against a real fake GraphQL server.
Goal carries identifier + description + source URL. 4 tests + golden 12.

### F124 — Jira issue integration (VERIFIED IMPLEMENTED)
`POST /webhooks/jira`, `X-Jira-Secret` HMAC, `jira:issue_created` only, ADF
description flattened to text; exact replays deduped by content-hash delivery
id (Jira sends no delivery header). Outbound REST v3 ADF comment notifier with
basic auth against a real fake Jira server (auth checked). 3 tests + golden 13.

### F126 — Sentry error-to-agent workflow (VERIFIED IMPLEMENTED)
`POST /webhooks/sentry` with the **real** `Sentry-Hook-Signature`
(hmac-sha256 hex) + `Sentry-Hook-Resource: issue` enforcement; `Request-ID`
idempotency; `created` only (`resolved`→400). Error text containing an embedded
token is redacted before the goal. 5 tests + goldens 08/09.

### F127 — Datadog or Grafana incident-to-agent workflow (PARTIAL)
`POST /webhooks/alerts` implements the Alertmanager-shaped receiver
(`X-Alert-Secret` HMAC): firing-only, `org_id` label → tenant,
`fingerprint+startsAt` episode idempotency. **Grafana is contract-complete**
(Grafana Alerting natively delivers the Alertmanager format). **Datadog is
PARTIAL by honesty rule**: its default payload shape
(`alert_title`/`alert_type`/`aggregation_key`) is not parsed — Datadog needs a
custom payload template matching this contract, which is documented in the
README but not validated against a live account. 4 tests + goldens 10/11.

### Cross-cutting (shared infra, under VR014/VR020)
- Signature verification on the raw body **before** parsing; unknown tenant
  fails closed 403 `TENANT_UNKNOWN` with no existence oracle.
- Atomic claim/settle/release idempotency store (SQLite): concurrent duplicates
  lose the claim → 200; upstream failure releases so provider retries re-enter
  and succeed.
- Per-tenant sliding-window rate limit (429), secret-pattern redaction before
  goals/logs, untrusted-content banner on every goal.
- `ProposePlanGate` has exactly **one method**; the service exposes only
  `/healthz` + `/webhooks/{provider}` — no approve/start/execute code path
  exists (asserted host-side: those routes 404).

## Verification (all commands run, output inspected)

| Check | Command | Result |
|---|---|---|
| Connector tests | `packages/extensions/.venv/bin/python -m pytest integrations/connectors/workflows/tests -q` | **35 passed** |
| Regression (service + SDK + wiki) | same venv, `packages/extensions/tests` + wiki connector tests | **90 + 8 passed** |
| Lint | `ruff check` on connector src/tests + `scripts/agent-program/a5` | clean |
| Real plan gate smoke | `packages/extensions/.venv/bin/python scripts/agent-program/a5/smoke_real_plan_gate.py` | **SMOKE PASS** (log: `real-gate-smoke.log`) |
| Goldens | `scripts/agent-program/a5/capture_golden_s3.py` | 24 fixtures + `routes.json` |
| Distribution | `verify_distribution.py` (now 4 wheels incl. `harness_workflow_connectors-0.1.0`) | exit 0, `DISTRIBUTION_VERIFIED` (`distribution-verify.log`) |

Real-gate smoke detail (the strongest evidence this session): the real Section 4
orchestrator ran as a separate process (`section4/.venv`,
`HARNESS_AUTHZ_TEST_MODE=1` bare mode, read-only usage); a signed Slack webhook
through the connector produced a real `plan-*` id; duplicate → 200; human
approve → real `run-*` id and idempotent re-approve; the approved run reports
`executing: false` with `planApproved: true` — **approval ≠ execution, and the
connector never executed anything**; the goal the human sees carries the
untrusted-content banner.

## Decisions log

1. **One upstream capability.** `ProposePlanGate.propose_goal` only. Rather
   than "not exposing" approve/start, the code path does not exist — capability
   absence by construction, not by policy check.
2. **Adapter signature schemes, labelled as such.** Slack + Sentry use their
   real schemes. Teams/Jira/alerts get documented HMAC adapters (real products
   don't sign uniformly; enterprise deploys front them with a secret aperture).
   Each is a single swappable `SignatureScheme` row; README states the reality.
3. **Content-hash delivery ids** for providers without delivery headers (Jira);
   exact replays dedupe, edited payloads re-enter (new incident state).
4. **Release-on-failure idempotency** (claim/settle/release): a 502 leaves the
   delivery re-claimable so the provider's own retry works — no retry queue
   invented this session.
5. **F127 split honestly** (Grafana complete, Datadog via templated payload):
   marking the whole feature verified would overclaim vendor coverage.
6. **Rule-based redaction, not entropy heuristics**: fixed vendor patterns +
   `key=value` shapes; false negatives possible, false positives cheap.

## Known limits carried forward

- No live vendor accounts were hit (Slack workspace, Linear, Jira, Sentry,
  Datadog) — transport contracts validated with real-shape fake servers; the
  master prompt's rule stands: an untested real integration stays unvalidated.
- Teams/Jira/alerts inbound schemes are adapters (above); Atlassian Connect JWT
  and the Slack OAuth install flow are future work.
- SQLite idempotency store is single-node; `IdempotencyStore` is the seam for a
  shared store in a multi-replica deploy.
- Comment-posting notifiers are fire-and-report (no retry); failures surface as
  typed `IntakeRejected` codes for the caller to decide.
- Outbound status updates (F121/F122) are exercised against fake receivers;
  live channel/thread posting remains unvalidated.
