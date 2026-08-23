# Sweep F — Security Hardening Report (attempt 3)

Repo: `/Users/user/untitled folder 2/harness`
Scope: write-from-leads sweep over accumulated leads + standard hardening checklist expanded per-endpoint (~60 route families across S1–S9).
Method: static review of route handlers, middleware wiring, CI, deploy config. No repo modifications made.
Status marks: **[CONFIRMED]** verified in source this sweep · **[MITIGATED]** prior lead checked, guard exists (residual noted) · **[LEAD]** from earlier sweeps, direction unverified · **[CHECKLIST]** systematic gap class.

---

## A. AuthN / AuthZ gaps

- [section3/src/harness_router/api.py:120,148] **[CONFIRMED]** The model router's ONLY router-level dependency is `_count_request` — a metrics counter, not auth. `POST /api/v1/models/call`, `GET /spend`, `POST /estimate-cost` are all callable unauthenticated → anyone who can reach :8003 burns provider credits through the fallback chain and reads spend ledger totals. Fix: `APIRouter(dependencies=[Depends(require_bearer)])` mirroring S1's `get_principal`; add test asserting 401 without token.
- [section3/src/harness_router/api.py:251] **[CONFIRMED]** Worse sibling: `POST /api/v1/models/config` is unauthenticated — remote callers can rewrite router/model configuration on a live router. Fix: same bearer dep + admin role gate.
- [section3/src/harness_router/api.py:263] **[CONFIRMED]** `GET /metrics` renders full Prometheus traffic counts (method×path) unauthenticated → internal usage topology disclosure. Fix: require auth or bind to loopback/internal network only.
- [section1/src/harness_identity/app.py:189] **[CONFIRMED]** `POST /auth/verify-otp` has NO rate limiting of any kind — no middleware covers it and the handler adds no counter (contrast `request_login` which calls `_login_rate_limited`). Numeric OTP + unlimited attempts = brute-forceable within TTL. Fix: attempt counter per email+IP (max 5), invalidate code after N failures, backoff.
- [section1/src/harness_identity/ratelimit.py + app.py:63] **[CONFIRMED]** `RateLimitMiddleware` (Mission 10 node B5, PG fixed-window) exists and is tested, but `make_app()` never calls `install_rate_limit()` — only CORSMiddleware is added. The rate-limit control is dead code in production wiring. Fix: install it with `DEFAULT_ROUTES` covering `/auth/request-login` AND `/auth/verify-otp`; add integration test asserting 429.
- [deploy/docker-compose.yml:210–230] **[CONFIRMED, softened]** Caddyfile IS wired — but only via opt-in `profiles: [proxy]`; default `docker compose up` exposes all seven backends + frontend directly with no TLS edge. Fix: ONBOARDING must mark `--profile proxy` mandatory for any non-loopback deployment; consider binding backend ports to 127.0.0.1 in default profile.
- [section1/src/harness_identity/app.py:64–69] **[CHECKLIST]** CORS uses `allow_origins=settings.cors_origins` with `allow_credentials=True`. If operators ever set `["*"]` (or a reflective echo), browsers can ride authenticated sessions. Fix: settings validator rejecting wildcard+credentials combo.
- [section4 approve/reject/pause routes] **[CHECKLIST]** Confirm each state-transition endpoint gates on approver role, not bare authentication (human-governance invariant).
- [section5 api.py:37,97] **[PARTIAL]** `apply_edit` requires writer principal — good pattern; confirm exec/destroy carry the same and that principals are team-scoped to the task's project.
- [docs URLs] **[CHECKLIST]** FastAPI serves `/docs`, `/redoc`, `/openapi.json` by default on every section → schema disclosure. Disable when env=production.

## B. Input validation

Length caps — no `max_length` constraints observed on free-text models:
- [section4 plan-create: goal] **[CHECKLIST]** Add `Field(max_length=8_000)`.
- [section6 meeting title/agenda] **[CHECKLIST]** Cap title ~200 chars, body ~20K.
- [section6 message content / brief fields] **[CHECKLIST]** Cap lengths; strip control chars (log-injection surface).
- [section8 inline comment body] **[CHECKLIST]** Cap ~4K.
- [section2 diffText passthrough] **[CHECKLIST]** Reject diffs >N MB with 413 rather than buffering whole payloads in memory.
- [label/name fields everywhere] **[CHECKLIST]** Cap length + charset where names feed event keys/filenames.

Path traversal:
- [section5/src/harness_sandbox/diff_engine.py:22–25] **[MITIGATED]** Human-edit paths DO resolve and assert `relative_to(workspace_dir.resolve())` before apply — the classic `../` escape is guarded here. Residuals: (1) confirm `local_runner.apply_edit` and `service.apply_edit` funnel through THIS guarded function, not their own join; (2) `policy.validate_path_edit` (policy.py:246) is a second, separate checker — consolidate to one chokepoint so future edit endpoints can't bypass; (3) symlinked files inside workspace pointing out are only caught if the link itself is the target — test a pre-planted symlink.
- [section5 confinement.py:51,138] **[OK]** Workspace resolved before Seatbelt/Landlock profile generation — consistent.

Numeric bounds:
- [pagination params, all sections] **[CHECKLIST]** Enforce `limit ∈ [1,100]`, `offset ≥ 0` via pydantic `Field(ge/le)`; negative values currently surface as ugly driver errors (500) instead of 422.
- [section3 sinceDays/window params] **[CHECKLIST]** Cap (≤90) — huge windows force full ledger scans.
- [section3 max_tokens] **[CHECKLIST]** Clamp to per-model ceiling before pricing; huge values defeat cost estimates.
- [path IDs before DB hit] **[CHECKLIST]** Parse UUIDs at schema layer; avoids raw strings reaching SQL and gives clean 404s.
- [WS ?afterSeq=N] **[CHECKLIST]** Validate int64 range; reject negatives.

Enums & formats:
- [status filters S4/S6/S8] **[CHECKLIST]** Use `Literal[...]` so `status=zzz` → 422, not silent-empty 200.
- [email fields S1 LoginRequest/VerifyOtpRequest] **[CONFIRMED]** Both models take plain `str` email — no format validation, no normalization; `LoginRequest.email` feeds OTP delivery and dev-notifier logs verbatim. Fix: `EmailStr` + lowercase normalization shared by both schemas (also kills duplicate-account case drift).

## C. Injection surfaces

- [section1/src/harness_identity/notifier.py:159–175] **[MITIGATED]** HTML bodies ARE escaped — each rendered line passes through `_html.escape()` before wrapping in `<p>`/`<h2>`. Residuals: (1) `subject` is `str.format(**context)` UNESCAPED — user-derived project/team names land in subject headers; if any SMTP path allows raw header assembly, `\r\n` in content is header-injection; sanitize CRLF from subject context values. (2) plain-text body unescaped — acceptable for text/plain, note only.
- [section2 repo URL fetch] **[LEAD]** Server-side fetch of repo URLs = SSRF pivot (cloud metadata 169.254.169.254, localhost admin ports, internal PG :5432). Not re-verified this sweep due to budget. Fix: https-only scheme allowlist + resolve-and-block private/link-local/loopback IPs before connect, or restrict to `github.com/<org>/<repo>` regex.
- [ORDER BY / sort params] **[CHECKLIST]** Grep found no f-string ORDER BY in section sources this pass; keep ruff `S608` enabled to ban f-string SQL permanently, whitelist-map any future sort param.
- [cross-section raw SQL] **[CHECKLIST]** Frozen table-name constants are contract-pinned (safe); bound params ($1…) observed in ledger queries — hold the line in review.
- [section5 shell allowlist] Quote-aware rejection + operator ban exist (policy.py:24–49); **[CHECKLIST]** every newly allowed command must rerun the quote-injection fuzz matrix.

## D. Crypto / token hygiene

- [section1/src/harness_identity/auth.py:138] **[CONFIRMED]** OTP = `secrets.choice("0123456789") × OTP_CODE_LENGTH` — cryptographically random (good) but decimal-digit alphabet ⇒ ~3.32 bits/char (6 digits ≈ 19.9 bits). Combined with A's missing throttle that is brute-force territory. Fix: raise to 8 digits (26.6 bits) AND throttle attempts; or switch to `token_urlsafe`-style codes.
- [auth.py:119] Token/OTP storage = SHA-256 hexdigest — appropriate (fast hash fine for high-entropy secrets; less fine for 20-bit OTPs where rainbow tables over 10⁶ codes are trivial — another reason to lengthen/throttle).
- [all sections] **[CONFIRMED]** ZERO uses of `hmac.compare_digest` across `section*/src`. Every secret comparison (token lookup, code-hash match) is ordinary equality. For 256-bit urlsafe tokens timing leakage is impractical; for short OTP hashes it is a real (if exotic) accelerator. Fix: use `hmac.compare_digest` in `verify_otp` and ticket/state redemption paths.
- [auth.py:70] Session tokens `secrets.token_urlsafe(32)` — **[OK]**, 192-bit entropy, URL-safe alphabet. Document minimum length.
- [ws_tickets / oauth_states] Atomic single-statement `DELETE … RETURNING` redemption + ISO-text TTL normalization — **[OK]** (matches known SQLite pitfall design).
- [cookies] **[CHECKLIST]** Design is bearer-only; zero cookie sessions observed — keep it that way; any future cookie must set HttpOnly+Secure+SameSite explicitly.

## E. Config exposure

- [section1/src/harness_identity/config.py:9] **[CONFIRMED]** `master_key: bytes = b"dev-master-key-change-me-in-prod!"` — KeyVault encryption key ships as a source-code default; any deployment that forgets to override encrypts provider keys under a public constant. Fix: refuse boot when env=production and master_key equals default; generate per-deploy in ONBOARDING.
- [deploy/docker-compose.yml:9] **[CONFIRMED]** DB credentials hardcoded in compose: `postgresql+asyncpg://harness:harness@db:5432/harness`. Fix: `${POSTGRES_PASSWORD:?err}` required var + `.env.example`; rotate since it's in git history anyway.
- [HARNESS_GITHUB_CLIENT_SECRET empty slot] **[LEAD]** Present-but-empty secret slots risk "empty string treated as configured". Fix: fail-fast validation — required settings must be non-empty or absent entirely.
- [.gitignore coverage] **[CHECKLIST]** Verify `.env*` (except `*.example`), `*.pem`, `*.key`, sqlite artifacts ignored in every subpackage, not only root.
- [verbose errors] **[CHECKLIST]** Standardize error envelopes; ensure no handler returns exception internals/tracebacks outside dev.
- [dev OTP printer app.py:166] **[NOTE]** `_dev_notifier` prints OTP codes to stderr — correct for dev, but ensure prod always injects a real notifier (E-smtp hook) so codes can't land in container logs.

## F. Supply chain / CI

- [.github/workflows/ci.yml:121–144] **[CONFIRMED]** pip-audit runs WARN-posture ("WARN only this wave") — findings print but never fail the build; gitleaks IS present (good). Fix: flip to failing posture on HIGH+/critical with an explicit, reviewed suppressions list.
- [trivy / image scanning] **[CONFIRMED ABSENT]** Zero references to trivy (or any image scanner) in `.github/` or `deploy/`. Built backend/frontend images ship unscanned. Fix: add trivy step post-build, fail on critical CVEs.
- [deepseek-harness-sdk pin] **[CONFIRMED ABSENT]** No reference in any pyproject.toml / requirements*.txt / setup.py greps this pass — the SDK is consumed unpinned (skill history says working pin was 0.1.1rc1; 0.2.0 does not exist upstream, so a floating requirement WILL break). Fix: pin exact version wherever declared; add bump-checklist (rerun task0/hello.py).
- [base images] **[CHECKLIST]** Pin python/node base images by digest, not floating tags.
- [require-hashes] **[CHECKLIST]** Once pins settle, add `--require-hashes` to deploy-time installs.

---

## Endpoint coverage ledger

| Service | Route families swept | Headline findings |
|---|---|---|
| S1 :8001 | request-login, verify-otp, login, ws tickets, audit/events, teams | verify-otp unthrottled; RateLimitMiddleware never installed; no EmailStr; no compare_digest |
| S2 :8002 | repos, diffs, PR fetch | SSRF lead stands (unverified this pass) |
| S3 :8003 | call, spend, estimate-cost, catalog, config, metrics | entire router unauthenticated incl. POST /config |
| S4 :8004 | plans, approve/reject, execute, state transitions | goal cap; approver-role confirmation |
| S5 :8005 | tasks, exec, edits, destroy | traversal guard exists in diff_engine; consolidate chokepoints |
| S6 :8006 | meetings, messages, briefs | length caps; subject-header sanitization |
| S8 :8008 | reviews, risk, inline comments | comment caps; enum filters |
| S7/S9 UI | bus client, mockApi | mockApi on S9 = functional debt, not security |

## Fix order (top 10)

1. S3: bearer-auth the whole router — especially `POST /config` (credit burn + live config rewrite)
2. S1: actually call `install_rate_limit()` and cover BOTH auth routes; add per-email OTP attempt counter
3. Lengthen OTP alphabet/length (≥8 digits) given sub-20-bit entropy today
4. `hmac.compare_digest` in verify/redemption paths
5. Prod-secret fail-fast: master-key default, hardcoded `harness:harness` DSN, empty client-secret slots
6. S2 SSRF allowlist (carry-over lead)
7. notifier subject CRLF sanitize
8. Make `--profile proxy` mandatory outside loopback deployments; loopback-bind backend ports by default
9. CI: pip-audit → failing posture; add trivy; pin deepseek-harness-sdk
10. Input-cap checklist batch (goal/title/message/comment/diffText + pagination ge/le)

## Verification provenance

CONFIRMED items were re-checked against source during this attempt: S3 api.py deps (lines 120/148/180/215/230/235/251/263), S1 app.py 150–230 + ratelimit.py, notifier.py render(), diff_engine.py 22–25, policy.py, confinement.py, auth.py 70/119/138, config.py:9, docker-compose.yml 9/210–235, ci.yml 121–144, plus global greps for `compare_digest` (none), `trivy` (none), SDK pins (none).
Prior-sweep context on disk: `SECURITY_AUTH_AUDIT.md`, `api-contracts-audit.md`, `db_schema_audit_report.md`, `orchestrator_engine_audit_report.md`, `sweep_d_report.md`.
