# Lane A evidence (part 45 agent management and orchestration)

Files: `section1/migrations/0044_agents.sql`, `section1/src/harness_identity/agents.py`,
`section4/src/harness_orchestrator/agent_tree.py`, `docs/agents.md`; tests
`section1/tests/test_agents.py` (+ `test_agents_support.py`, helpers only),
`section4/tests/unit/test_agent_tree.py`, `test_agent_tree_races.py` (+ `agent_tree_support.py`,
helpers only). No shared file was edited (no `models.py`, `app.py`, `audit.py`, `policy_enforce.py`).

Commands (venv python, from each section dir, run sequentially):
- section1: `./.venv/bin/python -m pytest -q -p no:cacheprovider` -> 859 passed, 27 skipped (60 s);
  `tests/test_agents.py` alone: 28 passed.
- section4: same command -> 979 passed, 32 skipped (40 s); lane A files: 43 passed
  (39 in `test_agent_tree.py`, 4 in `test_agent_tree_races.py`).
- `ruff check src tests` (never `--fix`): section1 clean. section4: lane A files clean; 12 E501
  remain, all in another lane's `tests/unit/test_control.py`.
- Mutation check: removing the `children_count<max_children` and budget clauses from the child-spawn
  CAS made 3 tests fail (both race tests and the live-children test); restored, 43 pass.

Directive coverage: see `docs/agents.md` ("Directive by directive"). All 001-010 are implemented
as a registry, gate and ledger; none is out of scope, but 006, 007, 009 and 010 are only as real as
the (absent) engine integration, see gaps.

Paths used exactly as in the brief. Needs outside the brief's path list (not invented):
a dedicated progress/finish path. Progress and `finish: completed|failed` ride on the tool-calls
body instead. Recommend `POST runs/{runId}/agents/{assignmentId}/progress` if the lead wants it.

Honest gaps:
- The external engine does NOT call spawn, tool-calls or read pause/cancel. Pause/cancel/reassign
  change rows and audit only; no process is signalled. Scope is recorded and subset-checked but
  not enforced in the sandbox.
- Tests run on sqlite with the real 0044 SQL; not run on Postgres (lead's job). PG-specific points
  to confirm: `ON CONFLICT (run_id) DO NOTHING`, CAS `UPDATE ... rowcount`, the
  `UNIQUE (run_id, request_id)` with NULL request ids, `TIMESTAMPTZ` binds of `db_now()`.
- Race tests interleave on one event loop over a sqlite file (writers serialize on the file lock);
  they prove the CAS invariants, not a multi-process race.
- Tool-fallback relies on the caller reporting `unavailable`; model availability is an operator env
  list plus the in-process circuit breaker, so a multi-replica deployment sees only its own breaker.
- Cost is caller-reported; the team budget gate (`check_budget`) is called at spawn only.
  The run pool takes env values at first spawn.
- Summaries are redacted for obvious secret shapes and structurally cannot carry a reasoning field,
  but free text in `summary` is not inspected for reasoning.
- Not coordinated with part 42 control leases or part 40 sessions (`session_members.kind='agent'`
  rows are not created). Routers are not registered anywhere (lead wires; `agent_tree` imports
  `api`, so include it from the assembly). The policy gate classifies `/api/v1/agents` writes as
  `administer`, so org-team engineers cannot register agents.
- Self-approval is blocked by default (two-admin rule); a one-admin team needs
  `HARNESS_AGENT_ALLOW_SELF_APPROVAL=1`.
- Retiring a definition does not stop its live assignments. No GC of old pool rows or tool calls.
