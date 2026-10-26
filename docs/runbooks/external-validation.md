# External validation — staging procedures & status

Status as of 2026-10-25. OIDC SSO, WAL-G/PITR and the native-Linux container
boundary now have executed drills (self-hosted real implementations, evidence
under `docs/briefs/evidence/pilot-prep/`). Stripe remains undrilled by
decision: Stripe is not available to the operator in Pakistan. Every other
surface still needs either a drill or an explicit accepted risk.

| Surface | Verified so far (evidence) | Missing | Classification |
|---|---|---|---|
| OIDC SSO | **DRILLED 2026-10-25 against Keycloak 26.1.5** (`./scripts/oidc_drill.sh`): discovery, S256 PKCE, real login form, code exchange, JWKS/nonce, session mint, plus replay / redirect mismatch / non-member negatives. 14/14 checks. Evidence `docs/briefs/evidence/pilot-prep/oidc-keycloak-drill.json` | A hosted customer IdP (Okta/Auth0/Entra) with customer-specific claims; SPA callback exists since `412fc30` | VERIFIED for a standards-compliant IdP; hosted-tenant drill remains customer-specific |
| Stripe billing | Adapter + signed idempotent webhooks vs stub (`STRIPE_API_BASE`); routes mount only with `STRIPE_API_KEY` set (`app.py:210-213`) | Test-mode drill (checkout → webhook → replay) | **ACCEPTED RISK — Stripe is not available in Pakistan.** Billing stays flag-off; pilot plans are invoiced manually or left on free/team overrides |
| WAL-G / PITR | **DRILLED 2026-10-25 against MinIO's S3 API** (`./scripts/pitr_drill.sh`): versioned bucket, base backup, WAL archiving, delete after target, restore to target, promote. Base backup 2 s, fetch 1 s, replay-to-pause 1 s. Evidence `docs/briefs/evidence/pilot-prep/walg-s3-pitr-drill.json` | A provider-specific drill (AWS/R2) with the production bucket policy; multi-team data volume | VERIFIED for S3 semantics; provider/IAM drill remains a deployment step |
| Container sandbox (native Linux) | **DRILLED 2026-10-25 on ubuntu-latest (kernel 6.17.0-1022-azure)** (`./scripts/native_linux_drill.sh`, job `native linux container tool drill`): shell-gated write lands on a bind-mounted workspace, a sibling read is denied and an outside write is denied. 4/4 checks. Evidence `docs/briefs/evidence/pilot-prep/native-linux-container-drill.json`. The drill exposed and fixed two allow-list holes: `/` granted via `/bin/sh`, and `/` accepted as HOME in `--user` containers | Non-root Docker-daemon deployment and image provenance acceptance | VERIFIED on native Linux; Docker Desktop/macOS remains unsupported for Landlock writes |
| Model providers | Simulated mode honest outside real keys; provider failure surfaces verbatim; `HARNESS_ENV=dev` gate (`section3 providers.py`); **re-drilled 2026-10-26** with OpenRouter via `scripts/e2e/openrouter_smoke.py`: a tool-using run (2 live tool calls, real `hello.txt` side effect) and a **goal image** the model actually read back (`(220, 40, 40)` from an attached PNG). Evidence `docs/briefs/evidence/pilot-prep/m07-openrouter-smoke.json` | usage stays `null` through the openai-completions transport patch, so `cost_ledger` accrual on this path is unproven; container-isolated tool writes still need a native Linux host | VERIFIED for text + image on OpenRouter; metering on this transport and other providers remain open |

### Provider and model facts this drill settled (2026-10-26)

- The shipped `sdk-minimal` profile registers **one** LLM route,
  `deepseek-official`. The route *name* is not the wire format: unpatched it
  posts an Anthropic Messages body, which OpenRouter rejects with
  `Invalid Anthropic Messages API request`. Reaching an OpenAI-compatible
  gateway means `DSH_PATCH_FILES=deploy/patches/openrouter.patch.yml`, not a
  provider env var — there is deliberately no `HARNESS_DSH_PROVIDER` knob, and
  probing one produces `NO_ADAPTER` from the runtime either way.
- The patch declares `input: [text]`. With that declaration the runtime
  **silently offloads** an attached image and the model reports "image omitted
  because this model accepts text only". Declaring `input: [text, image]` makes
  the same run read the pixels correctly. So goal images require a model entry
  that declares image input, and `HARNESS_VISION_MODELS` is what refuses a
  text-only model *before* the call.
- Providers enforce minimum image dimensions: an 8×8 PNG was refused with
  `height:8 or width:8 must be larger than 10`. A real screenshot is required.
- Model choice for paid work, measured rather than assumed (single-token
  latency probe over the 467-model catalog, sorted by price):
  `deepseek/deepseek-v4-flash` for agent runs (tool-calling is what the harness
  exercises), `qwen/qwen3.7-flash` when an image is in play,
  `inclusionai/ling-3.0-flash` for plain summarisation/classification chores.


## OIDC SSO — executed drill and re-run procedure

The shipped implementation is client-side (PKCE) and config lives per-team in
the DB, not env vars. `./scripts/oidc_drill.sh` starts a disposable Keycloak
(`--profile sso-drill`), provisions the drill member through the IdP admin
API, raises the team plan, invites that member, configures SSO, drives the
real login form, and asserts the session plus three fail-closed negatives.
The last run passed 14/14 checks (Keycloak 26.1.5).

To re-run:

```
docker compose --env-file .env -f deploy/docker-compose.yml up -d   # dev stack
./scripts/oidc_drill.sh
```

Drill-only realm and credentials: `deploy/keycloak/harness-realm.json`.
Evidence is written to
`docs/briefs/evidence/pilot-prep/oidc-keycloak-drill.json` (no secrets, codes
or tokens). A customer-hosted IdP still needs its own app registration and a
run of the same script with `DRILL_ISSUER`/`DRILL_CLIENT_*` overrides.

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

## WAL-G / PITR — executed drill and re-run procedure

`./scripts/pitr_drill.sh` brings up MinIO (S3 API, versioned bucket) and the
`walg-db` drill instance, takes a base backup, writes a marker *after* the
backup, archives the WAL, deletes the marker, then restores to the recorded
second and verifies both rows on a paused server before promoting. The live
primary is never touched. Last run: 2 s base backup, 1 s fetch, 1 s
replay-to-pause. Evidence:
`docs/briefs/evidence/pilot-prep/walg-s3-pitr-drill.json`.

The full timeline, storage targets and cutover checklist stay in
`docs/runbooks/backup-and-pitr.md`; production still needs the provider's own
bucket/IAM drill (AWS/R2) and a real data-volume rehearsal.

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
