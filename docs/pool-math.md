# Pool Math — connection budget for the deployed stack (M13-W3, lane H1a)

> Finding H-017: every service used to boot a bare `create_async_engine()`
> and silently inherit SQLAlchemy's defaults — `pool_size=5` +
> `max_overflow=10` ⇒ **up to 15 connections per service**, ×7 services =
> 105 potential connections against Postgres' default `max_connections=100`.
> A busy stack could exhaust the server before accounting for migrations,
> admin tooling or operator sessions. This page is the fix's contract.

## The numbers

| Knob | Value | Where it lives |
|---|---|---|
| `pool_size` | 8 | `deploy/runners/db.py` (`POOL_SIZE`, env `HARNESS_DB_POOL_SIZE`) |
| `max_overflow` | 4 | `deploy/runners/db.py` (`MAX_OVERFLOW`, env `HARNESS_DB_MAX_OVERFLOW`) |
| Services with an async engine | 7 | identity, github, models, orchestrator, sandbox, meeting, review (`SERVICES` in `deploy/runners/services.py`) |
| Postgres `max_connections` | 120 | `db:` service command in `deploy/docker-compose.yml` |

**The equation (asserted by `section1/tests/test_deploy_pool_math.py`,
so any drift fails CI):**

```
(pool_size + max_overflow) × services
    = (8 + 4) × 7
    = 84  ≤  max_connections(120)
```

Headroom: 120 − 84 = **36 slots** for the migrate one-shot job, the opt-in
events-retention sidecar (single engine), admin tooling and operator psql.

## One factory, zero bypasses

Every deploy-side engine is built by **`build_engine()` in
`deploy/runners/db.py`**:

- `services.get_factory()` → `build_engine()` — feeds all seven FastAPI
  services via uvicorn factory aliases.
- `events_retention.main()` → `build_engine()` — the retention sidecar.
- The `migrate` entrypoint path reuses `get_factory()` (entrypoint.sh), so it
  inherits the same math and disposes its engine when done.
- SQLite URLs (aiosqlite; tests/local scripts) get `NullPool` from the same
  function — there is no server ceiling to honor there.

The test suite greps the deploy tree for `create_async_engine(` and asserts
the only hits are inside `db.py` itself plus one sanctioned exception:
`migrate_selftest.py` (M17-B2) builds its own NullPool/AUTOCOMMIT admin
engine because CREATE/DROP DATABASE cannot run inside a transaction block
and the probe targets throwaway scratch databases it drops afterwards. If
you genuinely need another call site, you need to change that test and this
doc in the same commit — which is the point.

## Operator overrides (retuning ≠ drifting)

`HARNESS_DB_POOL_SIZE` / `HARNESS_DB_MAX_OVERFLOW` override the shipped 8/4
per deployment. If you retune, re-do the arithmetic above: the CI test pins
the *shipped* values and compose cap; a deployment that raises overflow past
its headroom owns its own outage.

## Pair-contract note (H-P1)

H4a's migration 0020 (cost columns → `NUMERIC(12,6)`) takes no extra
connection budget: the migrate job is a short-lived single-engine process.
This lane ships **no migration** — config only — per the pair contract.

## Related

- W2 carryover context: Docker DNS crash-loop on harness-backend-1 (G2 lane,
  not this one); `db:` already had `restart: unless-stopped`.
- Wave-2 close baseline: `8232957`. Verifier team W3-VT1 runs a live-PG pool
  exhaustion probe against these numbers.
