# STATUS — where the project stands (updated 2026-08-22, end of wave 4)

## Working right now

- `docker compose -f deploy/docker-compose.yml up` → healthy stack (PG + 7
  backends + frontend). Migrations 0001–0003 applied via migrate.py.
- **388 pytest green** (incl. real-PG fanout test + 9 OTP tests + 5 confinement
  tests) and **43 vitest green**; ruff clean; tsc clean.
- Live proofs today: ticket-auth WS delivering sandbox `terminal_output`
  cross-instance in 0.7s; orchestrator flow; restart rehydration.

## Wave 2 (this session) — read docs/GAP_ANALYSIS_AND_FIXES.md for full detail

1. **H8 DONE**: one-time WS tickets (`POST /api/v1/ws-ticket`, 30s, project-
   bound, pop-first redemption). S7 mints a fresh ticket per connect; bearer
   tokens no longer ride URLs. 8 new S1 tests + frontend rewired.
2. **M2 DONE**: additive EventTypes `terminal_output` / `diff_progress`
   (contracts py+ts, conformance pin now "twelve types"). S5 emits via injected
   `on_stream` (per-line streaming + edit progress); deploy bridges to the bus;
   S7 deriveView exposes capped `terminalLines` → TerminalPanel renders live.
3. **G-LIVE FOUND+FIXED (FATAL)**: two EventBus instances per stack meant live
   fan-out never crossed processes; and seq-gap close(1013) was wrong because
   `agent_events.seq` is table-GLOBAL (cross-project jumps are normal).
   Fix: migration 0003 pg_notify trigger + per-instance LISTEN/NOTIFY delivery;
   overflow detection moved onto a per-subscriber `overflowed` flag set by the
   bus itself. Proven live across a 27→28 seq jump.
4. **Engine fallback chain**: `DSH_FALLBACK_MODELS` env or ctor list; primary →
   fallbacks → honest `error`; never fake success.
5. Quick wins: `/health?deep=1` DB ping; bounded `list_projects?limit=`.

Incidents caught by tests/live probes this wave (details in GAP doc Part 5):
asyncpg ≥0.30 `add_listener` is a coroutine; `expire_on_commit=True` factories
made `row.seq` lazy-load outside greenlet after commit (flush-before-commit now);
migration splitter needed $$-dollar-quote AND comment-line awareness; host 5432
already taken → compose publishes PG on 127.0.0.1:**5499**.

## Wave 3 (latest) — both public-exposure blockers closed

1. **H2r DONE**: `HARNESS_REQUIRE_OWNERSHIP` flag (default off for dev) gates the
   passwordless hole behind an OTP flow — request-login (enumeration-safe,
   hashed codes, 5-attempt lockout, single-use) → verify-otp provisions exactly
   like legacy login. Migration 0004. Verified live: flag-on legacy=403, full
   request→verify→/me→replay-401 chain; flag-off dev login still 200.
   Dev code delivery prints `HARNESS-OTP ...` to container logs; SMTP is a hook.
2. **F6r DONE**: OS confinement around LocalSandboxRunner — Seatbelt on macOS
   (deny-default profile, writes confined to workspace+tmp, network flag),
   Landlock on Linux ≥5.13 (write-family-only restriction; degrades safely).
   auto|seatbelt|landlock|off per runner; 5 macOS tests execute REAL profiles;
   container sanity passed post-rebuild.

To run the platform with ownership enforcement on:
`HARNESS_REQUIRE_OWNERSHIP=true docker compose -f deploy/docker-compose.yml up -d --build backend`

## Wave 5 (latest) — GitHub OAuth connect flow + exhibition demo readiness

1. **E-oauth DONE (S2)**: real GitHub web application flow — `GET /api/v1/github/oauth/start`
   (302 to github.com/login/oauth/authorize, CSRF `state` bound server-side to the
   session user), `GET /api/v1/github/oauth/callback` (code→token exchange, single-use
   state, token encrypted at rest via KeyVault into `github_connections`, browser lands
   on `{frontend_base}/settings?connected=<login>` or `?error=<ExcName>` — never a stack).
   Browsers can't send Authorization headers on navigation, so a one-time
   `POST /api/v1/github/oauth/start-ticket` (60s TTL) bridges fetch→redirect.
   `GET /api/v1/github/connection` → `{connected, login, scopes}`; no token ever in a
   response body. Unconfigured creds ⇒ clean 503 `NOT_CONFIGURED`. 10 new tests pin the
   flow incl. replay/unknown-state rejection and ciphertext-at-rest assertions.
   Compose maps GITHUB_CLIENT_ID/SECRET (+FRONTEND_BASE/GITHUB_REDIRECT_URI) onto S2's
   HARNESS_ prefixed settings.
2. **Settings page DONE (S7)**: `/settings` with Connect GitHub button driving the
   ticket-hop flow, connected state (`@login`, scopes), error banner, URL-param cleanup;
   wired via `connectGithub()`/`getGithubConnection()` in src/lib/api.ts. Added to
   AppShell nav.
3. **Working nav restored (S7)**: AppShell "Working" item re-added; links to
   `/projects/{projectId}/runs/{runId}` from localStorage; disabled with tooltip when
   either is missing.
4. **Demo seed DONE**: deploy/seed_demo.py drives only the live REST API (session,
   project match-by-name, repo connect upsert) and prints the localStorage lines for
   DevTools. Idempotency fix: S1's passwordless login mints a NEW solo team (and an
   auto-seeded `demo-repo` project) per call — the script now persists its session to
   deploy/.seed_session.json (gitignored) and reuses it while valid, verified by two
   consecutive runs converging on the same team/project.

Test totals: 396 pytest + 58 vitest green; ruff clean; tsc clean; next build green.
Live proofs: :8002 start w/o creds → 503 NOT_CONFIGURED body; bogus callback state →
400 INVALID_STATE; unauth connection → 401; seed run twice → same team/project,
"already connected".

Remaining known work: none from this task's scope. Ledger additions: E-seed recorded
fixed-in-wave-5. Still open from earlier: M4/M5 trivia, E-s3, nightly probes (CI job).

## Wave 4 — ledger cleared: consolidation + E-smtp + E-cat + CI + E-codegen

1. **S7/S9 consolidation DONE**: one frontend now. S7 gained S9's design system,
   AppShell, and pages (command center, meeting, review, login) wired to the real
   typed API layer (meeting/review/OTP clients added, TDD'd). S9 retired
   (`git rm`); deploy/frontend.Dockerfile + compose now build section7.
   Live: all four routes 200 on :3000 against the running stack.
2. **E-fe-otp DONE**: /login is flag-aware — legacy login auto-switches to the
   request→verify OTP flow on 403 OWNERSHIP_REQUIRED. Verified live end-to-end:
   request → code in container logs → verify-otp → admin session on :8001.
3. **E-smtp DONE**: `notifier.py` — SMTP via HARNESS_SMTP_* env (host/port/
   user/pass/from/starttls), sync smtplib in a worker thread, delivery failures
   logged never raised (enumeration-safety kept). No host configured → visible
   dev log printer. 6 new tests; compose env-wired.
4. **E-cat DONE**: S3 `catalog.py` — declarative provider catalog with env-var
   indirection (names only), coverage asserted against the pricing catalog
   (caught a missing qwen provider), local fallback candidates = zero-priced
   models. GET /api/v1/models/catalog exposed; S4 engine consults it as
   last-resort fallback chain (ctor > env > catalog).
5. **CI gates DONE**: .github/workflows/ci.yml — sh -n entrypoint + compose
   config + TS-mirror drift check, pytest+ruff matrix over all 8 Python
   sections, tsc+vitest for section7, nightly real-PG probe job.
6. **E-codegen DONE**: contracts/ts/generate.py — AST-based pydantic→TS mirror
   generator; --check gate in CI makes hand-editing the mirror pointless.
   Regenerated mirrors; optional fields handled; consumers updated.

Test totals: 395 pytest (+4 PG integration) + 58 vitest green; ruff clean;
tsc clean; next build green.

Remaining known work: none from the GAP Part 10 ledger. Next candidates:
checkpoint-aware resume (Appendix C), per-service containers if scale demands,
real GitHub OAuth app for repo connection.

To run the platform with ownership enforcement on:
`HARNESS_REQUIRE_OWNERSHIP=true docker compose -f deploy/docker-compose.yml up -d --build backend`

## Known gaps / next actions (GAP doc Part 10 ledger)

1. **E-smtp**: wire real email delivery into the notifier hook before enabling
   HARNESS_REQUIRE_OWNERSHIP publicly (frontend UI already done — see wave 4).
2. E-cat provider catalog via S3; E-codegen pydantic→TS contract generation;
   CI gates (sh -n, compose config, nightly security probes incl. the PG fanout
   test with HARNESS_TEST_PG=1).

## Environment notes

- PG from host: `localhost:5499` (loopback; 5432 is your local Postgres).
- Integration PG test: `HARNESS_TEST_PG=1 HARNESS_DATABASE_URL=postgresql+asyncpg://harness:harness@localhost:5499/harness ../section1/.venv/bin/python -m pytest integration/test_checkpoint1.py`.
- `EventBus.publish` flushes before commit — safe under any expire_on_commit.
- deepseek-harness-sdk pinned 0.1.1rc1 (no 0.2.0 on PyPI). CORS env must be JSON.

## Wave 5 — GitHub OAuth connect flow + exhibition demo readiness

1. **S2 OAuth web flow DONE**: `GET /api/v1/github/oauth/start` (302 to
   github.com/login/oauth/authorize, CSRF state bound to session user) and
   `GET /api/v1/github/oauth/callback` (code→token exchange, single-use state,
   token stored encrypted via KeyVault with granted scopes, browser redirected
   to `{frontend_base}/settings?connected=<login>`, errors land as
   `?error=<Exc>` — never a crash). `GET /api/v1/github/connection` returns
   `{connected, login, scopes}` without the token. Legacy JSON `/oauth/url`
   removed. Browsers can't send headers on navigation → one-time 60s
   `/oauth/start-ticket` hop (same pattern as WS tickets).
2. **Compose wiring fixed**: `GITHUB_CLIENT_ID/SECRET` were plumbed but S2's
   Settings read `HARNESS_GITHUB_*` — mapped across (+ redirect/frontend base).
3. **Frontend DONE**: `/settings` page (status card + Connect GitHub button
   driving the ticket flow, ?connected/?error banners); AppShell gains Settings;
   Working nav restored → latest project/run from localStorage, disabled when
   either is missing.
4. **seed_demo.py DONE**: idempotent REST-only seeding (login → find/create
   demo-repo project → connect repo) printing ready-to-paste localStorage.
   Verified twice against the live stack.

Live proofs: NOT_CONFIGURED=503 envelope; fake-creds start→302 authorize URL,
bogus-code callback→302 settings?error=HTTPStatusError (no crash);
/settings=200; connection={connected:false}. Note: each seed run mints a fresh
solo team (legacy login semantics) — projects dedupe per team.

## Session log (most recent first)

- 2026-08-22 wave 3: H2r OTP ownership proof + F6r OS confinement, live-verified.
  Tests 415→431 total. GAP doc 1095 lines.
- 2026-08-22 wave 2: H8, M2, G-LIVE(+global-seq trap), engine fallback, health/
  limit quick wins. Tests 392→415 total. GAP doc now 1055 lines.
- 2026-08-22 wave 1: 9 FATAL + HIGH fixes, event-log rehydration, live authz
  proofs. See GAP doc Appendix E.
