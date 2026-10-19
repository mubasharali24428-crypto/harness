# S1 lane B (contracts, part 14) evidence

Interpreter: `section4/.venv/bin/python` (imports every service; no per-service venv needed, nothing faked).

1. Before changes: `scripts/spec_drift.py` -> "orchestrator specs and UI calls match the router.", rc 0.
   KNOWN_UNSPECCED was already empty; the "9 unspecced URLs" claim is stale. But section3/5/6/8 openapi.json were not checked at all.
2. After extending (FRAGMENTS map section3/5/6/8 -> router/sandbox/meeting/review apps), `--check` reported drift:
   section3 missing 6 ops, section5 missing 5, section6 missing 8, section8 missing 11 and 6 stale (`{prId}` vs code `{pr_id}`, plus new routes).
3. `scripts/spec_drift.py --write` -> rewrote section3/4/5/6/8/10 openapi.json (info blocks preserved) and sdk-gen specs.
4. `scripts/spec_drift.py` -> "orchestrator specs and UI calls match the router.", rc 0.
5. `section4/.venv/bin/python -m pytest scripts/test_spec_drift.py -q` -> 1 passed. The existing `section4/tests/unit/test_spec_drift.py` calls `main()`, so it now covers the new fragments too.
Not covered: docs/agent-program/a5/session-{1,2}/interop/openapi.json (archived program docs, not service specs).
