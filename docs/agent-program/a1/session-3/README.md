# A1-S3 — Approved model routing and measurable efficiency

Owner A1. Features F083 F084 F085 F086 F089 F090. Risks VR041 VR048 (+VR017 tenant isolation of cache/policy).

## Built
- `packages/security-policy/.../model_policy.py` (pure): `TeamModelPolicy` (validated admin JSON), `evaluate_model`
  (unknown/unapproved => DENY), `resolve_model` (explicit request > routing rule > role default),
  `plan_fallback` (opt-in only, attributed, only on unavailable/rate-limited, equivalent must itself be approved),
  `TenantCache` (team-scoped key, TTL, LRU bound, per-team/model invalidation), `score_response` +
  `compare_models`, `validate_local_endpoint` (SSRF rules) + `parse_local_models`.
- section1 migration `0036_model_allowlist.sql` + `model_policy_api.py` (`/api/v1/identity/model-allowlist`,
  `/model-policy`): members request, ONLY admins approve/deny/revoke; re-request after deny/revoke; per-team
  isolation (identical 404); policy PUT is admin-only, validated, stored in `team_settings['model_policy']`;
  corrupt stored policy loads as enforced-with-nothing-approved (fail closed).
- section3 `model_policy_seam.py` + `api.py`: `POST /models/call` consults the team policy when a provider is
  wired (`set_policy_provider(pool_policy_provider(pool))`): denies disallowed/unknown models (403/422, never
  substituted), applies routing by optional `taskType`, serves cache hits for temperature-0 calls when the team
  enabled caching (no provider call, no spend), and performs attributed fallback via `X-Harness-Fallback-From /
  Served-Model / Fallback-Reason` headers. `POST /models/compare` (same task on 2-5 approved models, deterministic
  scoring, unscored rows never ranked), `GET /models/local/discover` (SSRF-bounded probe: loopback default,
  operator-listed private hosts, link-local/public refused, no redirects, 3s, 1MB), `POST /models/cache/invalidate`.
- Without a wired provider section3 behaves exactly as before (opt-in rollout).

## Findings
- LEGACY (not fixed on the unwired path): `ModelRouterService.call_model` silently replaces an unknown requested
  model with the role default. With the policy provider wired the request is rejected instead (422 MODEL_UNKNOWN).
  Recommend deploy always wires the provider.
- section3 now declares `harness-security-policy` as a hard dependency; the deploy image (A5) must install
  `packages/security-policy` or section3 fails at import (loud, not silent).

## Verification (2026-10-17)
- package: 90 passed; section3: 106 passed (95 baseline + 11 new in `tests/unit/test_a1_model_policy.py`);
  section1: 665 passed / 10 skipped (+5 `test_model_policy_api.py`; FK-inventory guard 44->47); ruff clean everywhere.
- All 36 migrations applied to a throwaway local Postgres database (dropped afterwards).
- Covered: requested-model preservation, disallowed/unknown/default-unapproved denial, outage with no opt-in = honest 503,
  consented fallback attribution (unapproved equivalent skipped), no fallback on invalid key, cache cross-tenant denial,
  TTL, invalidation scoping, cache off by default, compare ranking + allowlist enforced before any spend, local endpoint
  SSRF/abuse (metadata, private, public, userinfo, scheme, oversize, 5xx), pool-provider caching and fail-closed read.
- NOT measured: real latency/cost (all providers faked); no live provider or live Ollama probe; compare quality is a
  deterministic rubric, not a human/LLM-judged benchmark.

## Dispositions
| F | Status |
|---|---|
| F083 | PARTIAL: explicit per-team task routing implemented; no adaptive/learned router (by design) |
| F084 | VERIFIED INTENTIONAL: no forced downgrade; only admin-enabled, attributed, approved-equivalent fallback; default off |
| F085 | PARTIAL: same-task comparison with deterministic scoring; no real-traffic quality study |
| F086 | PARTIAL: tenant-isolated response cache controls (TTL/invalidate); provider-side prompt-caching passthrough not implemented |
| F089 | VERIFIED IMPLEMENTED (API+enforcement at s3 seam) — NEEDS INTEGRATION: deploy must wire `set_policy_provider`; UI (A4) |
| F090 | PARTIAL: on-demand discovery + health of OpenAI/Ollama endpoints; no scheduled monitoring/alerts |
Risks: VR048 PARTIAL CONTROL (per-team approval workflow limits which providers are used; provider-terms legal review still a human task).
VR041 PARTIAL CONTROL (cache + routing reduce spend; unmeasured). VR017: cache/policy are team-keyed, tested.

## Rollback
Unset the policy provider (behavior returns to baseline); remove routers from `app.py`; `DROP TABLE model_allowlist_entries`.
