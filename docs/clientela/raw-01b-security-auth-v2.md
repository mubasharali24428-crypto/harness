# Security / Auth Audit — harness monorepo (Section 1 focus + deploy)

Analyst re-dispatch x2 · confirm-and-write pass · 2026-08-23 · read-only, no repo modifications.
Every lead was re-verified against source; file:line refs are from the working tree at
`untitled folder 2/harness`.

---

## CRITICAL

### C1. Passwordless `/auth/login` mints verified-equivalent identities — enables cross-team account injection
- **Where:** `section1/src/harness_identity/app.py:197-217`, `auth.py:25-80`; default OFF gate: `config.py:13` (`require_ownership: bool = False`), `deploy/docker-compose.yml:13` (`HARNESS_REQUIRE_OWNERSHIP: ... :-false`).
- **Confirmed:** `login_or_register()` takes any email string, creates the `User` row if missing (`auth.py:37-43`), creates a solo team with the caller as **ADMIN** plus a project (`auth.py:56-68`), and returns a 14-day bearer token. No inbox proof on this path; the OTP flow exists but is opt-in via `require_ownership`, shipped disabled everywhere.
- **Attacker scenario:** (a) anyone becomes ADMIN of unlimited free teams — fine for MVP, but (b) the real chain: attacker logs in with `engineer@victimco.com` → a `User` row now exists *for an address the attacker controls*. Any team admin who later invites that email via the legacy direct-add endpoint (`app.py:327-363`) silently attaches the **attacker-controlled user** to their team as ENGINEER — no consent, no token, no email proof. From there they hold `write` on the victim team's BYO keys scope and can be escalated further.
- **Fix:** default `HARNESS_REQUIRE_OWNERSHIP=true` in compose; make `/auth/login` require OTP proof always; refuse to attach an existing `User` to a team without that user completing a redeem/challenge.

### C2. Audit logging is dead code — zero production `write_audit` call sites
- **Where:** `section1/src/harness_identity/audit.py:7-24` (whitelist includes `apikey.added`, `apikey.removed`, …), `audit.py:27-37` (`write_audit` raises `ValueError` for unknown actions), `audit.py:53-60` (append-only ORM guard).
- **Confirmed:** repo-wide grep finds `write_audit(` referenced **only** in `tests/test_section1.py`. None of these emit audit rows: key mint/revoke (`api_keys.py:68-98,132-159`), invite mint/revoke/redeem (`invites.py:77-101,185-208,211-291`), billing plan change (`billing.py:225-252`), login/OTP/logout (`app.py`). The append-only guard faithfully protects an empty table.
- **Attacker/compliance scenario:** a malicious admin rotates plans, exfiltrates-and-revokes keys, or mass-invites; there is no tamper-evident trail for incident response or SOC2-style review.
- **Fix:** call `write_audit` inside every mutating handler above (same DB transaction), extend the action whitelist accordingly, add alerting on `apikey.removed` / `merge.performed`.

## HIGH

### H1. Compose ships the dev master key *and* defaults the escape hatch ON — silent weak crypto
- **Where:** `deploy/docker-compose.yml:10-11` — `HARNESS_MASTER_KEY: ${HARNESS_MASTER_KEY:-dev-master-key-change-me-in-prod!}` **and** `HARNESS_ALLOW_DEV_MASTER_KEY: ${HARNESS_ALLOW_DEV_MASTER_KEY:-1}`. Fail-fast exists at `deploy/runners/services.py:18-28` but is bypassed by the compose default; `section1/src/harness_identity/config.py:9` carries the same hardcoded default for standalone runs.
- **Attacker scenario:** operator runs `docker compose up` in a "prod-ish" host without exporting secrets → every BYO provider key in `api_keys.key_encrypted` is Fernet-encrypted under a **public constant**. Any DB dump (backup leak, WAL-G sidecar misroute) decrypts all customer LLM/API keys offline.
- **Fix:** remove both compose defaults (leave `${VAR:?required}`), keep the fail-fast, add startup telemetry distinguishing "dev key accepted".

### H2. Rate limiter guards a route that doesn't exist; real login throttling is per-process memory only
- **Where:** `ratelimit.py:55` — `DEFAULT_ROUTES = ("/api/v1/auth/login", "/api/v1/auth/otp/request")`. Actual routes: `/api/v1/auth/login` (`app.py:197`) and `/api/v1/auth/request-login` (`app.py:172`). There is **no** `/auth/otp/request`. `verify-otp` (`app.py:189`) is in neither limiter.
- **Compounding:** the DB-backed fixed-window store (`ratelimit.py:61-71`, race-free upsert) therefore only ever sees the legacy login path, while OTP request/verify fall back to `_login_rate_limited` (`app.py:24-44`) — an **in-process deque** that resets on restart, doesn't span uvicorn workers, and contradicts `ratelimit.py:5-7`'s own stated rationale for Postgres backing.
- **Plus:** fail-open on DB error is deliberate (`ratelimit.py:167-173`) — acceptable for login, wrong for money paths: `PUT /billing/plan` and key routes are unthrottled entirely.
- **Fix:** correct `DEFAULT_ROUTES` to the real paths, add `/api/v1/auth/verify-otp`, keep fail-open only on the two credential-spray routes.

### H3. Legacy "invite" endpoint adds teammates with no proof — bypasses the whole token system
- **Where:** `app.py:327-363` (`POST /teams/{team_id}/members/invite`): admin supplies an email; endpoint **creates the User if absent** (`app.py:345-348`) and inserts membership immediately, ENGINEER role. No token, no email challenge, no consent.
- **Attacker scenario:** see C1(b) — this is the injection primitive that turns an attacker-chosen identity into a seat on any team whose admin fat-fingers/typo-scopes the address. Also lets an admin add arbitrary emails as permanent members with no audit trail (C2).
- **Fix:** delete or feature-flag this endpoint; route everyone through `invites.py` token redeem (which is done well — see P2).

## MEDIUM

### M1. OTP brute-force economics + email-bombing relay
- **Where:** `auth.py:190-207` (lockout after `settings.otp_max_attempts=5` **per OTP row**, `config.py:15`); re-issue deletes prior rows and starts a fresh counter (`auth.py:140-150`); `verify_otp` route has **no IP throttle** (`app.py:189-194`); request-side is in-memory only (H2). Codes are 6 digits (`auth.py:113,138`), TTL 10 min.
- **Scenario:** distributed attackers hold ~20 fresh codes/min/IP via `/auth/request-login` × 5 guesses each ≈ 100 guesses/min/IP against a 10^6 space — weeks single-IP, hours botnet-sized. Separately, `issue_otp` fires a real outbound email for **any** address with no per-email cooldown → your domain becomes a free mail-bomb relay.
- **Fix:** per-email + per-IP counters on both OTP endpoints in the Postgres limiter; global exponential backoff per email; cap OTP emails/hour/address.

### M2. Invite lifecycle: unlimited pending mints, multiple live tokens per email
- **Where:** `invites.py:77-101` — no cap on pending invites per team/email; re-inviting never invalidates earlier tokens (each is independently redeemable for 7 days, `INVITE_TTL_DAYS`, `invites.py:33`).
- **Scenario:** compromised admin account spams thousands of pending invites (spam vector via future email delivery, junk state); an old leaked token stays valid even after a "re-invite rotated it" assumption.
- **Fix:** cap pending invites per email (e.g. 1-3), invalidate older tokens for the same email on mint (mirror `issue_otp`'s replacement semantics).

### M3. Orchestrator mutates process-global `os.environ` with the live API key
- **Where:** `section4/src/harness_orchestrator/engine.py:133-141` — saves/swaps `DEEPSEEK_API_KEY` (and base URL) around `DeepSeekHarness.run()`, restores in `finally`.
- **Scenario:** two runs interleave (async) → run B executes with run A's key; any library/subprocess spawned during `run()` that dumps environ (error reporters, `env` in agent shell tools) captures the credential; `engine.py:159-160` returns `f"...{exc}"` to the UI, and SDK exceptions may embed config/env context → key material risk in error surfaces.
- **Fix:** pass credentials explicitly to the SDK constructor (no environ round-trip); redact exception text before returning to UI.

### M4. KeyVault KDF is a single unsalted SHA-256
- **Where:** `contracts/src/harness_contracts/security.py:12-14` — Fernet key = `base64(sha256(master_key))`, one iteration, no salt/KDF.
- **Scenario:** with ciphertexts in hand (any DB leak), weak/guessable master keys fall to instantaneous dictionary attack; even strong keys get no work-factor margin.
- **Fix:** PBKDF2/scrypt/argon2 with stored random salt before Fernet; document migration path for existing ciphertexts.

### M5. CORS: dual-shape parser leaves normalization gaps; credentials always on
- **Where:** `deploy/runners/services.py:32-48` (JSON-array-or-comma parse), `app.py:67-73` (`allow_credentials=True, allow_methods=["*"], allow_headers=["*"]`).
- **Gaps confirmed:** no scheme/host normalization (trailing slash or case drift in operator config = silently dead CORS, availability bug), no wildcard-subdomain support (`allow_origin_regex` unused), and blanket `credentials+* methods/headers` means any future origin misconfiguration is maximally exploitable rather than minimally.
- **Fix:** normalize/compare lowercased origins, support `*.domain` via regex, scope methods/headers to what S7 uses.

## LOW

### L1. API-key hint leaks last-4 of secrets
- **Where:** `api_keys.py:93` → `KeyVault.hint` = `"…"+secret[-4:]` (`security.py:26-27`). Displayed in every list response (`api_keys.py:53`). Marginal entropy loss + shoulder-surf surface. **Positive:** no lookup/compare-by-secret path exists in S1, so no timing oracle; digest compares (`auth.py:91,198`, `invites.py:230`, `ephemeral.py` SQL) compare SHA-256 hex — standard, not a practical timing vuln. Fix: hint on label-side metadata or drop to provider-prefixed mask.

### L2. Security headers: report-only CSP, HSTS opt-in
- **Where:** `security_headers.py:28-32` (CSP REPORT-ONLY only), `:37-49` + `:74-77` (HSTS behind `HARNESS_TLS_ENABLED`, unset by default — compose never sets it; backend ports published bare on 0.0.0.0, `docker-compose.yml:81,99,…`). No Permissions-Policy/COOP. Fine for API JSON; the HTML path is unprotected until CSP enforces. Fix: enforce CSP for HTML, set `HARNESS_TLS_ENABLED=1` whenever the Caddy proxy profile runs.

### L3. WS tickets: sound single-use, 30 s theft window, not client-bound
- **Where:** `ephemeral.py:19` (TTL 30), `:40-62` (SHA-256 stored, expiry set), `:66-89` (**atomic single-use**: `DELETE … WHERE token_hash=? AND expires_at>now RETURNING`, project-bound). Confirmed server-side enforcement — good. Residual: a ticket sniffed in transit works from any IP within TTL; consider binding to issuing-session hash or shortening TTL.

### L4. OTP hashes unsalted SHA-256 of a 6-digit space
- **Where:** `auth.py:116-119`, `models.py:262-276`. A DB dump yields all active codes in milliseconds (10^6 candidates). Fix: HMAC with server pepper or argon2 for `code_hash`.

### L5. `verify_otp` TOCTOU double-redeem
- **Where:** `auth.py:167-211` — check-then-consume is not atomic; two concurrent valid submits both pass the unconsumed select and each run `login_or_register`, producing duplicate teams/sessions. Harmless today (both belong to same email) but inconsistent state. Fix: consume with `UPDATE … WHERE consumed_at IS NULL RETURNING`.

## Positives verified (no action)

- **Logout revocation is real:** `app.py:220-236` deletes the `auth_sessions` row; every request re-resolves the token from DB (`auth.py:88-95`, `app.py:135-143`), so revocation holds across replicas immediately. Tokens stored only as SHA-256 (`models.py:256`).
- **Invite redemption is atomic:** single-statement `UPDATE … WHERE token_hash=? AND redeemed_at IS NULL AND expires_at>now RETURNING` (`invites.py:225-240`) — no double-join; honest 404/409/410 distinction; tokens never stored raw (`invites.py:41-43,100`).
- **Rate-limit upsert is race-free** by PK-lock serialization (`ratelimit.py:26-38,61-71`) — the mechanism is right, only its route wiring is wrong (H2).
- **Cross-tenant scoping** consistently enforced: keys 403/404 without existence oracle (`api_keys.py:112-116,147-154`), projects checked against caller team (`app.py:47-58`), WS tickets project-bound (`ephemeral.py:74`).

---
**Totals:** 2 CRITICAL · 3 HIGH · 5 MEDIUM · 5 LOW · 4 positives. Priority order: C1→H3 (identity chain), C2 (compliance), H1 (crypto at rest), H2 (limiter rewiring).
