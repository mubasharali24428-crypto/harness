# Close-out: scheduled maintenance jobs and the orchestrator route limiter

Closes carry-overs recorded in Sessions 5 and 6 (parts 43, 46, 48, 50): four maintenance functions that "nothing schedules", and
`MUTATION_ROUTE_LIMITS` that was never installed on the deployed orchestrator.

## What changed

* `section4/src/harness_orchestrator/schedule.py`: `run_periodically(label, job, interval)` (logs a failed tick, keeps going, ends on cancel)
  and `maintenance_jobs(factory, env)` returning the enabled jobs. Jobs and intervals:

  | Job | Function | Env var | Default |
  |---|---|---|---|
  | search-sync | `search.sync_index` | `HARNESS_SEARCH_SYNC_SECONDS` | 60 |
  | export-purge | `exports.purge_expired_exports` | `HARNESS_EXPORT_PURGE_SECONDS` | 3600 |
  | discussion-purge | `discussion.purge_expired` | `HARNESS_DISCUSSION_PURGE_SECONDS` | 3600 |
  | artifact-purge | `artifacts.purge_expired` | `HARNESS_ARTIFACT_PURGE_SECONDS` | 3600 |

  `0` or a negative value turns a job off; a non-numeric value fails at startup.
* `deploy/runners/services.py` `app_orchestrator()`: starts the jobs (and the part 44 limit sweeper) on startup and cancels all background
  tasks on shutdown; installs `install_orchestrator_rate_limit` with `DEFAULT_PROTECTED_ROUTES + MUTATION_ROUTE_LIMITS` and
  `ORCHESTRATOR_ROUTE_LIMITS + MUTATION_ROUTE_LIMITS`, before `RequestContextMiddleware` so a 429 carries a request id. A limiter store failure
  fails open (existing behaviour of the shared limiter).
* Docs and docstrings that said "nothing schedules it" corrected: `search-and-export.md`, `discussion.md`, `artifacts.md`, `realtime.md`,
  `search.py`, `exports.py`, `discussion.py`, `delivery.py`; matrix rows 43, 46, 48, 50.

## Tests

`section4/tests/unit/test_schedule.py` (6): the loop repeats, survives a failing tick and stops on cancel; the default job table; `0`/negative
disables only that job; a garbage interval raises; each job calls its own function with the session factory; AST wiring law on
`app_orchestrator` (limiter installed once and before the request-context middleware, jobs and sweeper started, tasks cancelled on shutdown).
Mutation check: replacing the limiter install with a no-op makes the wiring test fail; restored.
Existing proofs reused: `test_delivery.py` (merged limits return 429 on a mutation route and never list a polled path),
`test_g2_ratelimit_hooks.py`. One-off probe (not in the suite): the real `app_orchestrator()` built on SQLite shows the limiter inside
`RequestContextMiddleware` and the startup and shutdown hooks registered.

## Verification run

See the commit message for the suite totals of this tree. Section1 `test_deploy_observability.py` + `test_deploy_resource_limits.py` 53 passed;
section10 `test_deploy_boot_gate.py` 4 passed.

## Honest limits

* Jobs are not leader-elected. They are idempotent, but with N replicas the same job runs N times per interval. `discussion.purge_expired`
  selects rows first and updates by id, so two replicas racing on the same batch can write a duplicate `comment.purged` audit row (the bodies
  are already blank, so no data is lost or revived).
* Not scheduled on purpose: run-workspace `retention.purge` (dry run by default, deletes by age: an operator decision) and session `purge`
  (explicit admin route).
* The limiter budgets are per IP behind the proxy's trusted-CIDR policy; no live-stack 429 was observed (Docker down). The per-IP budgets were
  designed as an abuse guard behind NAT; a team larger than the budget behind one address could hit 120/min on redirect, which is why the
  numbers are generous and the limiter fails open.
* `RequestContextMiddleware` is added twice on the orchestrator app (once inside `simple_app`, once in the builder). That predates this change
  and was left alone.
* NOT run: live PostgreSQL, the smoke, a multi-replica run of the jobs.
