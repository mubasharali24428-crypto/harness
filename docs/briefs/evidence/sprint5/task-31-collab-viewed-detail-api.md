# Task 31 — Collab API: optional `viewed_detail` (G-X07-3, part 1 of 2)

- Date (UTC): 2026-11-25
- Base: `260b24b` on `w28-s2-sprints1-4` (cloud, no Docker)
- Status: **Verified (unit, SQLite)** — API seam open; KPI wiring is Task 32.

## What changed

| Piece | Change |
|---|---|
| `section4/src/harness_orchestrator/api.py::ApprovalDecision` | new optional `viewed_detail: bool \| None = None` — same wire name and meaning as section8 `ApproveRequest` (`None` = not measured, never blind) |
| `collab_approval_decide` → `collab.decide_approval` | threaded as keyword-only `viewed_detail=`; it never affects the decision (CAS, digest, leader, expiry unchanged) |
| `section4/openapi.json`, `scripts/sdk-gen/specs/orchestrator.openapi.json` | `ApprovalDecision.properties.viewed_detail` (`boolean \| null`), the only hunk |
| `clients/python/harness_sdk/orchestrator.py`, `clients/ts/src/orchestrator.ts`, `clients/ts/dist/orchestrator.d.ts` | regenerated (`generate.py`, `generate.py --target ts`, `npm run build`) — one line each |

The section7 client already sends `viewed_detail` on Approve only when measured (Sprint 4 Task 27);
before this change FastAPI silently dropped it.

### Spec regeneration note

`spec_drift.py --write` in this container (newer pydantic) also rewrote unrelated hunks
(Decimal `pattern` keys; identity `/health?deep` param — the known runner-shadow defect noted in
`x07-backend-preference-2026-11-24.md`). Those were reverted; only the `ApprovalDecision` property
was applied, serialised with the script's own `json.dumps(indent=1, sort_keys=True)` format.

## Verification

```
section4: pytest tests/ -q -k "collab or approval"   → 269 passed, 8 skipped (new file: 6 tests)
section4: ruff check src tests                       → All checks passed!
scripts/spec_drift.py                                → orchestrator specs and UI calls match the router.
make check-ts-dist                                   → clients/ts dist matches a fresh build (26 tracked files).
make static-gates                                    → rc=0 (route/isolation guards, compose config, TS mirror, spec drift, eval, env knobs)
```

New tests (`section4/tests/unit/test_collab_viewed_detail.py`): omitted / `true` / `false` / `null`
all approve identically and reach `decide_approval` with that value; a non-boolean is a 422 and
leaves the request pending; reject with the signal present still rejects.
