# Task 23 — Full `make pg-probe` on the compose Postgres

- Date (UTC): 2026-11-24
- Status: **PASS on compose PG** (closes G-ENV-2 / G-ENV-3 parity half)
- SHA at verification: `8a78c5dc` (compose stack from Task 21, unchanged)

## Command

```sh
cd /Users/user/untitled folder 2/harness
HARNESS_TEST_PG=1 make pg-probe
# rc=0
```

`PG_URL` is the Makefile default `postgresql+asyncpg://harness:harness@localhost:5499/harness` —
the **compose** database, not the Sprint 3 external-PG scratch path. Task 21 had already applied
0076–0081 to that database (`max(version)=0081`).

## Output (trimmed)

```
# pg-probe-integration
3 passed, 1 skipped in 0.92s
# pg-probe-s1 (full section1 suite against real PG)
1361 passed, 1726 warnings in 229.51s (0:03:49)
# pg-probe-migrate
HARNESS_DATABASE_URL="postgresql+asyncpg://harness:harness@localhost:5499/harness" ... migrate_selftest.py
migrate_selftest PASS: 79 migrations (0001→0081) applied clean on real PG; re-run idempotent.
```

## Acceptance

All three stages green on compose `:5499`; no `HARNESS_TEST_PG_EXTERNAL` fallback was used.
The day90 ledger gets exactly one engineering-delta row for this parity; no owner line moved.
