# Part 38.005 enforcement carry-over (lead, Session 3 follow-up)

What changed
- Pure engine moved to `contracts/src/harness_contracts/policy_engine.py`; `section1/.../policy.py` re-exports it (existing policy tests unchanged).
- New `contracts/src/harness_contracts/policy_enforce.py`: route classification tables, raw-SQL policy loader, HTTP middleware.
- Installed in `section1/.../app.py:make_app` and `deploy/runners/services.py:app_orchestrator`.
- `policy.denied` registered as an audit action.

Defect found while testing: FastAPI 0.141 nests included routers (`_IncludedRouter`), so `app.routes` and `route.path` walks are
vacuous and route-template lookup in middleware matched nothing. The gate classifies the concrete request path instead, and the
coverage tests walk `app.openapi()["paths"]` (asserting more than 50 / 25 paths so an empty walk fails).

Commands run (by me)
- `section1/tests/test_policy_gate.py` 6 passed; `section4/tests/unit/test_policy_gate_orchestrator.py` 3 passed.
- section1 whole suite 784 passed, 27 skipped; section4 847 passed, 32 skipped; ruff clean in both.
- `make pg-probe-s1` 810 passed, 1 skipped (includes `test_session3_pg.py`: a policy deny blocks `GET /audit/export` on real
  Postgres, the allow returns after the document is restored, `policy.denied` rows chain correctly).
- `run_smoke.py --otp` 1 passed with the gate installed (unlinked pilot teams unaffected).

Not run: a load/latency measurement of the extra policy reads; WebSocket gating; `make gate`.
