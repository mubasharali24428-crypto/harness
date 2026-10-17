# A3-S4 — Install approvals, worker durability and reproducible incidents

New modules in `section5/src/harness_sandbox/`: `install_consent.py`, `offline.py`, `worker_pool.py`, `dr.py`, `repro.py`; tests `tests/unit/test_s4_*.py` (7 + 6 + 7 tests); `scripts/agent-program/a3/pg-restore-drill.py`; `offline-guide.md`. No existing file modified in this session.

## Evidence
| Command | Result |
|---|---|
| `cd section5 && .venv/bin/ruff check src tests` | clean |
| `.venv/bin/python -m pytest tests -q` | 226 passed, 1 skipped (pre-existing) — see final run below |
| `python3 scripts/agent-program/a3/pg-restore-drill.py` | exit 0; `pg-restore-drill.json` (PG 18.4 disposable cluster, checksum match, corruption detected) |

Failure classification during the session: Postgres drill first failed (environment: `initdb` locale and macOS multithreaded-postmaster hint → fixed by `LC_ALL=C`, TCP-only); worker-pool init failed (regression in my code: `executescript` implicitly committed the open transaction → schema init moved outside the transaction).

## Security notes
- The sandbox command policy still blocks `pip install`/`npm install` inside tasks (regression-tested). The broker is a separate, human-gated path and never accepts a shell string.
- Stack-trace/issue text is treated as data; hostile traces (`; rm -rf`, `$(…)`, `../../etc/passwd`, `.env`) yield no command.
- The pool uses SQLite: correct across processes on one host. Do not point several hosts at a network filesystem copy of the file.

## Limits / open items
- Consent grants are in memory (`InstallConsentStore`); a restart voids outstanding grants (fails closed). Durable store needs A1 schema.
- Pool is not yet exposed over HTTP and not integrated with `SandboxService` task execution.
- `repro` classification reads sandbox `diagnostics` (pytest short summary), so exception types appear only for pytest-style output.

## Rollback
Delete the five modules, their tests, the script and `docs/agent-program/a3/session-4`.

## Integration proposals
- A1: migration for `harness_worker_leases` + `install_consents` (columns mirror `worker_pool.SCHEMA` / `_Grant`) and a Postgres `LeaseStore`.
- A5: expose pool/consent/bundle endpoints under `/api/v1/sandbox/...`; run `pg-restore-drill.py` and an object-store drill with real credentials in staging.
- A4: approval UI posting consent grants.
