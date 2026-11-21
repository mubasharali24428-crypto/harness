# Hand-back: D1-verification-first-default-on
Model: GLM 5.3 Flash   Date: 2026-11-14   Revision: edits cut at 04b819b; full-suite check ran at f7af86d (branch `w28-s1-delegate`)
Status: DONE
Token use (estimate): ≈0.35M (session-level estimate; includes probe work earlier in the same session)

Note on the moving tree: the owner committed UI/demo work (04b819b → 6207006 → f7af86d) while this change was in flight. None of those commits touch my files (`git diff --name-only 04b819b..f7af86d | grep -c "service.py|reviewer.py|signals.py|conftest|test_done_signal"` → 0); they add exactly one test to `section4/tests/unit/test_demo_seed.py`, which the pass-count delta below accounts for.

Implements docs/decisions/2026-11-14-verification-first-default-on.md (owner-accepted). Done as owner-directed work in session, mapped onto G2's scope (plan item 4.7).

## Files changed (uncommitted)
- `section4/src/harness_orchestrator/service.py` — `_verification_first_enabled` default sentinel `"false"` → `"true"`; docstring now cites the decision.
- `section4/src/harness_orchestrator/reviewer.py` — strict verdict rule: `passed` requires `test_results_summary == "passed"`; unknown/absent evidence appends an issue and fails the review (was: anything ≠ "failed" passed).
- `section4/src/harness_orchestrator/signals.py` — docstrings updated to default-on (two spots; behavior unchanged).
- `section4/tests/conftest.py` — session pin `HARNESS_VERIFICATION_FIRST=false` via `os.environ.setdefault` (the file's established `HARNESS_AUTHZ_TEST_MODE` pattern), so suites that record no live tests keep their legacy flow without per-test edits; default-on tests use `monkeypatch.delenv` to see the real default.
- `section4/tests/test_done_signal.py` — `test_verification_first_default_off_preserves_flow` renamed/reworked to `test_verification_first_explicit_off_preserves_flow` (explicit `setenv("…","false")`); added `test_verification_first_default_is_on`, `test_verification_first_default_on_accepts_recorded_green` (async, service seam with `delenv`); replaced `test_verification_default_off_needs_no_evidence` with an env-free sync test plus two seam tests `test_service_verification_default_is_on` / `test_service_verification_explicit_off`.
- `section4/tests/conformance/test_orchestrator_conformance.py` — both golden tests (`test_full_orchestration_lifecycle_with_done_signal`, `test_rest_api_conformance`) pin `HARNESS_VERIFICATION_FIRST=false` explicitly; golden fixtures untouched.
- `section4/tests/test_stateless_orchestrator_n1.py` — the three tests that call `svc.signal_done(...)` pin explicit `false` (they assert eviction/replay semantics, not verification).
- `section4/tests/stress/test_section4_stress.py` — `TestOrchestratorServiceStress.test_human_edit_then_done_signal_reviewer_pass/fail` pin explicit `false`.
- `.env.example` — I-09 block rewritten: default true since the 2026-11-14 decision; demo/dry deployments must pin `false` explicitly.

## Checks run
- `cd section4 && .venv/bin/python -m pytest tests -q` — exit 0 — `2379 passed, 60 skipped, 26259 warnings in 163.27s` (baseline before change, same command at 04b819b: `2374 passed, 60 skipped`; delta = +4 new tests from this change + +1 test added by the owner's commits in `test_demo_seed.py`)
- `cd section4 && .venv/bin/python -m pytest tests/test_done_signal.py -q` — exit 0 — `27 passed` (was 24; +3 net new)
- `cd section4 && .venv/bin/python -m ruff check src/harness_orchestrator/{service,reviewer,signals}.py tests/conftest.py tests/test_done_signal.py tests/conformance/test_orchestrator_conformance.py tests/test_stateless_orchestrator_n1.py tests/stress/test_section4_stress.py` — exit 0 — `All checks passed!`
- Blast-radius sweep (pre-edit): `grep -rln "signal_done\|done-signal" section4/tests` → 14 files; every file that reaches `signal_done`/`gate_done_signal` without recorded engine tool calls is covered by the conftest pin or an explicit `setenv` pin. `grep -c HARNESS_VERIFICATION_FIRST` across unit/stress/conformance files confirmed no other file referenced the flag before this change.

## Checks not run
- Demo-stack deploy env change (`HARNESS_VERIFICATION_FIRST=false` at next deploy) — deploy step for the reviewer per the decision, not runnable here — NEEDS-REVIEWER-RUN.
- Live-model done-signal flow on RC — not in scope, no live calls made — NEEDS-REVIEWER-RUN.

## Decisions I made (reviewer may overrule)
- Used the session `conftest.py` pin (`setdefault`, one place) instead of editing ~6 legacy test files individually; mirrors the file's existing `HARNESS_AUTHZ_TEST_MODE` pattern and keeps legacy suites untouched. Tests that assert the default use `monkeypatch.delenv` (monkeypatch restores the conftest value afterwards).
- Strict reviewer rule implemented as "pass requires literal 'passed'" rather than "fail on failed or unknown": identical outcomes for today's callers (service seam maps evidence to passed/failed/unknown), simplest to read, and it makes the vacuous default impossible.
- `test_budget_warning_delivery._review` helper pins via `os.environ` save/restore inside the helper (its two callers had no common monkeypatch fixture at the helper layer) — conftest pin already covers these; the explicit pin is belt-and-braces for standalone runs.
- Did NOT pin `section4/tests/unit/test_budget.py` / `test_done_diff_reference.py` individually: the conftest pin covers them (verified by full-suite run); adding per-test pins there was started and reverted as redundant.
- The `budget rv-warn-ok` pin from an earlier batch was abandoned mid-way (regex anchor too brittle); conftest covers it. Net file state for `test_budget.py`: unchanged.

## Open questions
- Probe mode 1 remains open: `service.py:1887` still opens the done-gate on any clean engine finish without consulting `_recorded_verification` — with the gate now armed by default the exploit surface is smaller, but the engine-side half of I-09 is still unwired. Follow-up packet candidate.
- Probe mode 3 remains open: `reviewer.py` model text still replaces the deterministic summary line.
- `docs/briefs/evidence/90day/week-06.md:40` and `i09-dry-live-2026-11-13.md` describe the old default; both are historical evidence docs — reviewer decides whether to annotate.

## Unrelated issues seen
- `test_done_signal.py:328` and `:627` call `configure_orchestrator(svc)` and never reset the module-global router service; running that file before conformance/stress REST tests in one process poisons them with a disposed scratch DB (5 × 404). Pre-existing isolation leak, invisible in the canonical suite order (conformance/stress run first). Not fixed (outside scope).
- Baseline warning count 26227 → 26259 (+32): the new tests emit the same deprecation warnings as their neighbors (FastAPI/httpx TestClient), no new warning classes.

## Known limits of this change
- The default flip changes behavior only behind the HTTP route seam (`api.py:924`); direct `service.signal_done()` callers bypass `gate_done_signal` by design (pre-existing structure, unchanged).
- The reviewer strictness makes any caller that does not pass `test_results_summary` fail review unless the service seam computed evidence for it — `test_demo_seed.py:350` calls `evaluate_changes` without the kwarg and still passes because it asserts `passed is recorded["passed"]` from fixtures with `"passed"` recorded semantics unchanged (verified in full run).
