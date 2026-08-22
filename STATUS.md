# STATUS — where the project stands (updated 2026-08-22, end of wave 3)

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

## Known gaps / next actions (GAP doc Part 10 ledger)

1. **E-smtp**: wire real email delivery into the notifier hook before enabling
   HARNESS_REQUIRE_OWNERSHIP publicly (+ E-fe-otp frontend UI for the flow).
2. E-cat provider catalog via S3; E-codegen pydantic→TS contract generation;
   CI gates (sh -n, compose config, nightly security probes incl. the PG fanout
   test with HARNESS_TEST_PG=1).

## Environment notes

- PG from host: `localhost:5499` (loopback; 5432 is your local Postgres).
- Integration PG test: `HARNESS_TEST_PG=1 HARNESS_DATABASE_URL=postgresql+asyncpg://harness:harness@localhost:5499/harness ../section1/.venv/bin/python -m pytest integration/test_checkpoint1.py`.
- `EventBus.publish` flushes before commit — safe under any expire_on_commit.
- deepseek-harness-sdk pinned 0.1.1rc1 (no 0.2.0 on PyPI). CORS env must be JSON.

## Session log (most recent first)

- 2026-08-22 wave 3: H2r OTP ownership proof + F6r OS confinement, live-verified.
  Tests 415→431 total. GAP doc 1095 lines.
- 2026-08-22 wave 2: H8, M2, G-LIVE(+global-seq trap), engine fallback, health/
  limit quick wins. Tests 392→415 total. GAP doc now 1055 lines.
- 2026-08-22 wave 1: 9 FATAL + HIGH fixes, event-log rehydration, live authz
  proofs. See GAP doc Appendix E.
