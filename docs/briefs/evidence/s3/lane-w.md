# Lane W evidence (part 39 workspaces)

Files: section1/migrations/0040_workspaces.sql, section1/src/harness_identity/workspaces.py,
section4/src/harness_orchestrator/workspace_gate.py (+ 2 call sites in service.py:
`start_execution`, `_dispatch_claim`), docs/workspaces.md, tests below.

Commands (from each section dir, venv python):
- section1: `./.venv/bin/python -m pytest tests/test_workspaces_core.py tests/test_workspaces_lifecycle.py` -> 17 + 11 = 28 passed.
- section1 whole suite: 776 passed, 26 skipped (includes other lanes' files present at run time).
- section4: `tests/unit/test_workspace_gate.py` -> 10 passed; whole suite 844 passed, 32 skipped.
- `ruff check src tests`: section4 clean; section1 clean for lane-W files. One E501 remains in
  another lane's `tests/test_session3_pg.py:122` (not touched).

Callers of run start / claim grepped: `start_execution` (api.py /runs/{id}/start, plan_gate.py
start_run), `_dispatch_claim` (poll_once single + `_poll_batch`), `resume_agent` (not gated).

Honest gaps:
- Tests run on sqlite; migration 0040 was applied to sqlite with `ADD COLUMN IF NOT EXISTS`
  stripped. Not run on Postgres (lead's job). Partial unique index and row-value cursor compare
  are PG-valid but unverified there.
- Race test interleaves on one event loop; it proves the invariant (nothing spawns after the archive
  commit) not a true multi-process race. The deterministic queued-then-archived test is the strong one.
- Quota check is count-then-insert (can overshoot under concurrent creates).
- Residency is a validated label only; settings are not read by the orchestrator; no purge job
  for `purge_after`; a running run is not stopped on archive; `resume_agent` not gated.
- `teams.org_id` comes from lane O; org behavior is tested by adding the column in the test DB.
  Org-level admins cannot manage the org allowlist yet (team admins only).
- The gate fails open when the workspaces schema/column is missing (pre-0040 stores).
- Router not registered in app.py (lead wires `from .workspaces import router`).
- No models.py ORM for workspace tables, so `Base.metadata.create_all` does not create them.
