# Week 12 — P-23 denial-learning loop (I-08): the correction becomes a rule

Opened 2026-11-21 (executed ahead of the calendar week; the plan's W12 window is
Jan 27–Feb 2, 2027). Base: branch `w28-s1-delegate`. Engineering only, offline,
zero spend.

Plan row: P-23 (W12, S+B): "I-08 denial-learning loop: every human correction
becomes a project rule + regression test (extends W27 repeated-denial stop).
Acceptance: Repeat-denial count → 0 on pilot tasks; rules linked to
corrections."

## 1. Scope and states

| Work | Role | State | Evidence |
|---|---|---|---|
| `section4/src/harness_orchestrator/denial_learning.py` | S+B | **Implemented — 6/6 tests pass** | deterministic bounded rule suggestion from W27 scope requests; `repeat_denial_stops` metric; §2 |
| Wiring: denial stop carries the suggestion | S+B | **Implemented — wiring test passes** | `_guard_stopped` appends the suggestion to the durable `engine_failed` reason; the run record keeps `_suggested_rule` for the API; stream test through the M06 harness (`tests/unit/test_denial_learning.py`) |
| Collection seam | S+B | **Implemented** | `_emit_tool_record` collects each `scopeRequest` onto the run (deduped, capped at 20) |
| The human saves the rule | R / pilot users | **The loop ends here by design** | saving goes through the EXISTING rules API (`PUT /projects/{id}/rules`); the orchestrator proposes, never writes |
| Repeat-denial count → 0 on pilot tasks | R / pilot | **Not met — owner-gated** | needs real pilot runs; the metric that proves it (`repeat_denial_stops`) ships today |
| P-22 (3–5 teams, first invoice collected) | R / owner | **Not started — owner-gated** | sales, not engineering |

## 2. The loop, end to end

1. W27 guard stops a probing run (`denials_repeated`) — unchanged.
2. The stop's durable reason now ends with: *"Suggested project rule —
   LEARNED FROM DENIALS (run `<id>`): include `path…` in the plan's scopePaths
   — these paths were repeatedly denied by the sandbox boundary, so future
   plans must request them up front."* (deterministic, ≤400 chars, ≤5 paths)
3. The human saves it via the rules API (one edit), and the existing
   M12-G10 loader injects it into every future planning prompt for that project.
4. `repeat_denial_stops(db, project_id)` counts the durable stops per project —
   the number the acceptance line wants at zero.

## 3. Laws held

- **A suggestion never saves itself** (project rules are user-authored;
  `docs/extensions.md` §3, migration 0019).
- **No new enforcement**: the suggestion is advisory prompt text; hard
  constraints stay with section 5 (sandbox/forbidden_paths).
- **The "regression test" is the pipeline** (text determinism, wiring, prompt
  injection through the untouched loader), not the prose.

## 4. Test results (run 2026-11-21, section4 venv)

- `tests/unit/test_denial_learning.py` — 6/6.
- `tests/unit/test_loop_guard_wiring.py` + `tests/unit/test_loop_guard.py` —
  still green (stop-path change is additive).
- ruff clean (I001 fixes applied to service.py + the test).
- Full section4 suite: re-run recorded in the commit note (see STATUS).
