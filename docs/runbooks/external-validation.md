# External validation — staging procedures & status

Status as of 2026-10-17. Nothing in this file has been executed against a real
external provider yet; every row below is hermetically or stub-verified only.
Run these procedures BEFORE any pilot that depends on the surface. Until a row
shows a drill date, its classification is **HUMAN DECISION REQUIRED**
(execute drill or accept the risk explicitly).

| Surface | Verified so far (evidence) | Missing | Classification |
|---|---|---|---|
| OIDC SSO (backend) | PKCE flow, discovery issuer-match, JWKS RS256, config CRUD, encrypted secret at rest, rate-limited start/complete (`test_sso_oidc.py:620-830`) | Live IdP drill only — **SPA callback BUILT** 2026-10-17 (`412fc30`: `/auth/sso/callback` page + login SSO entry; frontend 507/507 + `tsc`) | HUMAN DECISION REQUIRED (IdP creds) |
| Stripe billing | Adapter + signed idempotent webhooks vs stub (`STRIPE_API_BASE`); routes mount only with `STRIPE_API_KEY` set (`app.py:210-213`) | Test-mode drill (checkout → webhook → replay) | HUMAN DECISION REQUIRED |
| WAL-G / PITR | Dev-sized drill: recovered to 4 s before a destructive DELETE (`docs/runbooks/backup-and-pitr.md`) | Drill against a real disposable object store (S3/MinIO) | HUMAN DECISION REQUIRED |
| Model providers | Simulated mode honest outside real keys; provider failure surfaces verbatim; `HARNESS_ENV=dev` gate (`section3 providers.py`) | Live provider call with a real key through orchestrator; cost-metering accrual check | HUMAN DECISION REQUIRED |

## OIDC SSO — staging drill (API walkthrough)

Config lives per-team in the DB (NOT env vars). The IdP redirect returns to the
SPA, which POSTs `{teamId, code, state, redirectUri}` — the SPA side of this
flow is not built yet, so run the drill via API calls.

1. IdP: create an OIDC app (Auth0/Keycloak/Google). Redirect URI = the value
   you will store as `redirectUri` on the config (must be echoed exactly at
   complete time).
2. As team admin: `PUT /api/v1/teams/{team_id}/sso/config` with
   `issuerUrl`, `clientId`, `clientSecret`, `redirectUri`. Secret is
   Fernet-encrypted at rest; GET returns only a `secretHint`.
3. `POST /api/v1/auth/sso/start` `{teamId}` → returns authorization URL
   (discovery doc issuer must match `issuerUrl` exactly, §4.3).
4. Log in at the IdP, capture `code`+`state`, then
   `POST /api/v1/auth/sso/complete` `{teamId, code, state, redirectUri}` →
   session issued, `principal` team-scoped.
5. Negative checks (expect fail-closed): mismatched `redirectUri`; replayed
   `state`; tampered JWKS signature; issuer mismatch in discovery.

Evidence to capture: session issuance JSON (redacted), `secretHint` on GET,
rate-limit 429 on burst start/complete, and the browser round-trip through the
SPA callback (`/auth/sso/callback`, built 2026-10-17).

## Stripe — test-mode drill

Env: `STRIPE_API_KEY=sk_test_…`, `STRIPE_WEBHOOK_SECRET=whsec_…`,
`STRIPE_PRICE_FREE/PRO/TEAM` = test-mode price IDs. Leave `STRIPE_API_BASE`
unset (it exists only so CI can point at a stub).

1. `stripe listen --forward-to http://127.0.0.1:8001/api/v1/billing/webhook`
   → use the printed `whsec_…` as `STRIPE_WEBHOOK_SECRET`.
2. `POST /api/v1/billing/checkout-session` → open the returned URL → pay with
   card `4242 4242 4242 4242`.
3. Verify: webhook accepted (signature valid), billing state advances, and a
   REPLAY of the same event id is idempotent (no double-apply).
4. Negative checks: tampered signature → 400; unknown price → honest error;
   with `STRIPE_API_KEY` unset the routes are unmounted and billing answers
   503 honestly (M10 behavior bit-for-bit unchanged).

Evidence: checkout session id, webhook delivery log with event id, before/after
`GET /api/v1/billing/state`, replay response, tamper rejection.

## WAL-G / PITR — real object store drill

Procedure already written: `docs/runbooks/backup-and-pitr.md` (logical
snapshots + physical PITR, cron cadence, drill commands, measured dev RTO).
Remaining work is ONLY point the `WALG_*` credentials at a disposable bucket
and re-run the timed restore. Capture RTO/RPO numbers there; do not duplicate
the procedure here.

## Model providers — live smoke

1. Attach a real provider key to a team (not a shared global) and set
   `DEEPSEEK_BASE_URL` only if pointing at a non-default endpoint.
2. Drive one completion through the orchestrator with `HARNESS_ENV` not `dev`
   and no key → expect honest unavailability (never fabricated output).
3. With the key attached → expect a real completion, cost metering accrues for
   the run, and the key never appears in logs or event payloads.

Evidence: completion id, metering delta, redaction grep (`grep -ri "sk-" logs/`
→ no hits).

## After a drill

Record the drill date in the table above, attach the evidence to the
pilot-remediation corpus, and downgrade the row from HUMAN DECISION REQUIRED
to VERIFIED (or file the accepted-risk note with who accepted it).
