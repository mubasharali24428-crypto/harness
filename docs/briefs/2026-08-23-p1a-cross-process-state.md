# TASK: Move ephemeral auth state (WS tickets, OAuth states) from per-process memory into Postgres (Phase 1 / plan §"Cross-process state → DB")

You are working in the AI Engineering Harness monorepo at the current directory
(`/Users/user/untitled folder 2/harness`). Read `AGENTS.md` first: `contracts/`
is FROZEN; sections import ONLY `harness_contracts`; schema changes need a NEW
numbered migration (never edit an existing one); tests must pass before hand-off.

## Project context

- Python >=3.11 FastAPI services, one folder per section, each with its own
  `.venv` (`cd sectionN && ./.venv/bin/python -m pytest tests/ -q`,
  `./.venv/bin/ruff check src tests`). Postgres 18 in production
  (`postgresql+asyncpg://...`); unit tests run against SQLite/aiosqlite.
- Migrations live in `section1/migrations/` (currently 0001–0004) and are plain
  SQL applied by `deploy/runners/migrate.py`. They must stay PostgreSQL-valid;
  unit tests that need tables declare their own minimal SQLite schema (see
  existing S1 tests for the pattern — follow it exactly).
- Auth: bearer sessions resolve to `Principal(userId, teamId, role)` via
  `make_bearer_dep` in FROZEN `contracts/src/harness_contracts/fastapi_auth.py`.

## The defect you are fixing (verified today)

Single-container memory breaks the moment the platform scales past one process:

1. **S1 WS tickets** (`section1/src/harness_identity/app.py`): one-time connect
   tickets live in `app.state.ws_tickets` (plain dict). A ticket minted by
   instance A cannot be redeemed at instance B. Semantics today: 30s TTL,
   project-bound, pop-on-first-redemption (single use), purge-expired sweep.
2. **S2 OAuth states** (`section2/src/harness_github/app.py`):
   `app.state.oauth_states` dict maps state -> (user_id, expiry). The CSRF
   `state` created by `/github/oauth/start` must survive to `/callback`, which
   may land on another replica. Single-use (popped), TTL ~10 min.

## What to do

### 1. Migration `section1/migrations/0005_ephemeral_state.sql`

Two tables, PostgreSQL dialect, following the style of earlier migrations:

```sql
CREATE TABLE ws_tickets (
    token_hash VARCHAR(64) PRIMARY KEY,      -- sha256 hex of the ticket
    user_id VARCHAR(36) NOT NULL,
    team_id VARCHAR(36) NOT NULL,
    role VARCHAR(20) NOT NULL,
    project_id VARCHAR(36) NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL
);
CREATE INDEX idx_ws_tickets_expires ON ws_tickets (expires_at);

CREATE TABLE oauth_states (
    state VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL
);
CREATE INDEX idx_oauth_states_expires ON oauth_states (expires_at);
```

### 2. S1: DB-backed ticket store

- Add a small store module (e.g. `section1/src/harness_identity/ephemeral.py`)
  with async functions `mint_ws_ticket(factory, principal, project_id, ttl)` and
  `redeem_ws_ticket(factory, token, project_id)` using SQLAlchemy `text()` raw
  SQL (portable: works on both asyncpg and aiosqlite; do NOT use PG-only syntax).
- Redemption MUST be atomic single-use even across processes: implement as one
  statement — `DELETE FROM ws_tickets WHERE token_hash = :h AND project_id = :p
  AND expires_at > :cutoff` (portable: SQLite has no now(), so ALWAYS pass the
  cutoff as a UTC datetime bind parameter, never call now() inside SQL) — and
  treat rowcount == 1 as success. Return the Principal fields on success, None
  otherwise.
- Rewire `_issue_ws_ticket` / the redemption path in
  `section1/src/harness_identity/app.py` to use the store through the app's
  session factory. DELETE the `app.state.ws_tickets` dict entirely — no dual
  paths, no feature flag. Keep response shapes and status codes unchanged
  (invalid/expired/redeemed => 401 exactly as today).
- Expired-row cleanup: piggyback a opportunistic `DELETE ... WHERE expires_at <
  now()` inside mint (cheap, keeps table small). Do not add background tasks.

### 3. S2: DB-backed OAuth state

- Same store approach (add functions to the same style of module inside S2, e.g.
  `section2/src/harness_github/state_store.py`; S2 must NOT import from S1 —
  duplicate the tiny helpers if needed, speaking only frozen table names).
- `start` inserts `(state, user_id, expires_at)`; `callback` consumes with a
  single atomic `DELETE FROM oauth_states WHERE state = :s AND expires_at >
  :cutoff` (`:cutoff` = UTC datetime bind parameter — never now() inside SQL,
  SQLite has no such function) — rowcount 1 = valid. Remove the in-memory dict.
  Unknown/expired/consumed => the exact same 400 INVALID_STATE behavior as today.
- S2 gets its session factory already (`get_factory()` pattern in
  `deploy/runners/services.py::app_github` passes one into make_app) — thread it
  through wherever the old dict lived.

### 4. Tests (must all pass)

- S1 (`section1/tests/`): follow existing test schema-setup patterns. Cover:
  mint→redeem roundtrip returns correct principal fields; SECOND redeem of same
  ticket fails (401); expired ticket rejected; ticket bound to wrong project_id
  rejected; **cross-instance**: two store objects over the SAME SQLite database
  file (tmp_path/shared.db) — mint with instance A's factory, redeem with
  instance B's factory, succeed once then fail.
- S2 (`section2/tests/`): start→callback roundtrip stores+consumes state;
  replayed callback => INVALID_STATE; expired state => INVALID_STATE;
  **cross-instance** same-pattern test with a shared SQLite file.
- Update any existing tests that referenced the in-memory dicts (grep
  `ws_tickets` / `oauth_states` in both sections' tests first).

## Constraints

- Do NOT touch `contracts/`, `section4/`, `section7/`, `deploy/` (migrate.py
  picks up numbered files automatically — verify that claim by reading it, and
  if it needs a registration list instead of globbing, say so in your report
  instead of editing it).
- Do NOT change HTTP contracts (routes, payloads, status codes) — internals only.
- No background jobs, no caching layers, no Redis. Plain SQL on the two tables.
- Timestamps: always timezone-aware UTC (`datetime.now(UTC)`); SQLite stores
  strings — compare with the same `now()`-style parameter you inserted, keep the
  comparison in SQL where possible, and prove behavior with the tests above.

## Deliverable

Do NOT `git commit`. Finish with: files changed, full pytest output summary for
section1 and section2, ruff results, confirmation whether migrate.py globs or
registers migrations explicitly, and anything intentionally not done.
