# Session 6, lane EX: execution and completion (part 44.004–010)

## Verification run

- `cd section4 && .venv/bin/python -m pytest -p no:cacheprovider -q tests/unit/test_execution*.py` — 66 passed.
- `section4/.venv/bin/ruff check` on Session 6 implementation and tests — all checks passed.
- Full Section 4 suite was attempted. It did not complete: after 1353 passed and 37 skipped, it found
  one unrelated 1.011-second cost-rollup performance threshold miss and two macOS Seatbelt failures
  (`sandbox_apply: Operation not permitted`), then stalled at 93% and was interrupted after 159 s.
- `test_session6_live_pg.py` is opt-in and was collected as skipped because PostgreSQL was
  unavailable (Docker socket absent; `127.0.0.1:5499` did not accept connections).

## Coverage

`test_execution.py` covers dependency and cycle checks, blockers, evidence, task ownership, version
conflicts, completion evidence, separation of agent report from human acceptance, and cross-team
404 behavior. `test_execution_runs.py` covers start/claim/run-sweep limits, checkpoint and safe-resume
answers, preserving evidence on abandonment, active-attempt refusal, request-id replay, and
concurrent retry requests. Migration `0048_execution.sql` is loaded by the lane fixture.

The only `service.py` changes are additive guards at run start and queue claim. They use existing
state transitions and finish blocked claims rather than dispatching them to the engine.

## Honest limits

- Tool-call limits are recorded but not enforced because the engine reports no tool-call count.
- Model-call count and cost are derived from completed cost-ledger rows; in-flight usage is not
  visible until the engine call completes.
- The running-limit sweep is implemented and tested but has no deploy scheduler yet.
- Checkpoint files remain Section 5's responsibility. The safe-resume API reports known durable
  state and a warning about side effects; it cannot roll back an interrupted step.
- Native plan version approval and execution remain open, documented in lane PL; therefore part 44
  must remain `partial`.
- PostgreSQL behavior is not verified in this environment.
