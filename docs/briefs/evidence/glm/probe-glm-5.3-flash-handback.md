# Hand-back: probe-glm-5.3-flash
Model: GLM 5.3 Flash   Date: 2026-11-14   Revision: 04b819b (branch `w28-s1-delegate`)
Status: DONE
Token use (estimate): ≈0.2M (session-level estimate; no per-call meter surfaced in this environment)

## Files changed (uncommitted)
- `docs/briefs/evidence/glm/probe-glm-5.3-flash-handback.md` (new) — this hand-back, the only file written. No source file was edited (probe instruction: read-only).

## The function that decides whether a run is marked complete

Terminal DONE is written in exactly one place from the review path:

- **`AgentStateMachine.reviewer_passed()`** — `section4/src/harness_orchestrator/state_machine.py:329`, DONE at `:341` (`self.transition_to(AgentState.DONE)`), and only when the engine-opened done-gate flag is set (`:335`). It has exactly one caller: **`OrchestratorService.signal_done()`** — `section4/src/harness_orchestrator/service.py:2993`, verdict applied at `:3062` (`if findings.passed:` → `reviewer_passed` at `:3063`, else `reviewer_failed` at `:3065`).

The pass boolean is computed by `ReviewerSubAgent.evaluate_changes()` — `section4/src/harness_orchestrator/reviewer.py:28`, rule at `:52`.

The full decision chain, including the precondition that arms it:

1. Engine settles. Live path: `engine.py:1516` `return text, "completed", model, events` — every SDK finish without `finish_reason == "error"` maps to `"completed"`. (`execute_goal` entry: `engine.py:1184`; the injected `backend_runner` seam always says completed: `engine.py:1202-1204`.)
2. `OrchestratorService._execute` consumes it — `service.py:1887-1888`: `if status == "completed": sm.execution_completed(summary[:4000])` → EXECUTING → AWAITING_DONE_SIGNAL (`state_machine.py:264-269`), done-gate flag set.
3. Executor done-signal: route `api.py:915/924` → `service.gate_done_signal` (`service.py:2925-2946`) → `signals.validate_done_signal` (`signals.py:108`): role `:130`, liveness `:132`, diff evidence `:137`, verification `:139`.
4. `service.signal_done` (`service.py:2993`) runs the reviewer (`reviewer.py:28`) and applies the verdict (`service.py:3062-3065`) → `state_machine.py:329` → DONE `:341`.
5. Operator-facing "completed" phase: `lifecycle.lifecycle_view` — `lifecycle.py:141`, `:160` (`phase = "completed" if engine_quiesced else "stopping"`). Restart replay mirrors the chain: `runs_index.replay_state` — `runs_index.py:149` (`:170` engine_completed opens the gate, `:177` review_passed behind an open gate → done).

## Three ways a model message could wrongly make a run look complete

**Mode 1 — any clean model finish reports "completed"; the message body is never checked.**
`engine.py:1516` maps every non-error SDK finish to status `"completed"`. A final message "all tests passed" and one that never ran a test are indistinguishable at this layer. `service.py:1887` then opens the done-gate and emits ENGINE_COMPLETED; from `runs_index.py:170` onward the run shows `awaiting_done_signal`/`reviewing` — it looks complete having proven nothing.
Stopping line: `service.py:1887` — condition the `sm.execution_completed(...)` call on `_recorded_verification(run_id)` being green. The helper already exists (`service.py:2951`, evidence from `review_summary.verification_evidence` `review_summary.py:167`); today it is consulted only inside the done-signal gate, never at this transition.

**Mode 2 — with `HARNESS_VERIFICATION_FIRST` off (the default), the reviewer's test verdict is the vacuous "passed".**
`reviewer.py:32` `test_results_summary: str = "passed"` is the default; `service.py:3049-3055` overrides it from recorded evidence only when verification-first is enabled, and the enabling default is `service.py:2921` (`os.environ.get("HARNESS_VERIFICATION_FIRST", "false")`, method at `:2919`). With it off, the only evidence bar for a done-signal is a non-empty diff (`signals.py:137`), and `reviewer.py:52` passes any diff without heuristic markers.
Stopping line: `signals.py:139` `if verification_required and not (verification and verification.get("green")):` — the refusal already exists and is exactly right; what disarms it today is the default at `service.py:2921`. Flipping an operator default is an owner decision: G2 must ask, not do.

**Mode 3 — the reviewer model's output text replaces the human-facing summary and can claim a success the deterministic verdict did not earn.**
`reviewer.py:98` `summary = result.text.strip()` — the model_router output (prompt wraps brief/tests/diff as untrusted data, `reviewer.py:85-89`, instruction at `:79`) overwrites the deterministic summary built at `:54`. `passed` itself cannot be flipped by the model (`:52` computes it before the call; `service.py:3062` consumes the boolean, never the text) — but the model-written summary rides the REVIEW_* payload (`verdict_payload`, `service.py:3060-3061`) and can tell operators "Review passed cleanly" while `passed=False`.
Stopping line: `reviewer.py:98` — do not let model text replace the verdict stamp (keep or prepend the deterministic passed/failed sentence), the same discipline `verdict_comment.py:6` already applies to the GitHub comment ("never say 'passed' … unless that exact fact was recorded").

## Checks run
- `cd "/Users/user/untitled folder 2/harness" && git rev-parse --abbrev-ref HEAD && git rev-parse --short HEAD` — exit 0 — `BRANCH: w28-s1-delegate` / `REV: 04b819b`
- `git status --short | grep -c section4` — exit 0 — `0` (every cited section4 line reflects committed state; the 35 dirty files are other lanes' work in section5/docs/section7)
- `grep -rn "done_signal\|review_passed\|execution_completed" section4/src/harness_orchestrator/*.py` — exit 0 — call-site map used above (api.py:924/946, service.py:1888/2993/3062-3065, state_machine.py:264/308/329, runs_index.py:170-177, execution.py:114-116)
- `grep -n "def evaluate_changes\|test_results_summary\|passed = \|result.text.strip" section4/src/harness_orchestrator/reviewer.py` — exit 0 — `28`, `32`, `49`, `52`, `98`
- `grep -n "verification_required\|HARNESS_VERIFICATION_FIRST\|_recorded_verification" section4/src/harness_orchestrator/service.py signals.py` — exit 0 — `service.py:2919/2921/2938/2951/3049`, `signals.py:139`

## Checks not run
- `cd section4 && .venv/bin/python -m pytest tests -q` — read-only probe: no code changed and no scored claim depends on the suite — informational only (reviewer may run it for environment sanity; nothing here asserts NEEDS-REVIEWER-RUN).

## Decisions I made (reviewer may overrule)
- Read "the function that decides whether a run is marked complete" as the terminal-DONE writer (`reviewer_passed`, `state_machine.py:329`) driven by `signal_done` (`service.py:2993`); I also mapped the precondition branch (`service.py:1887`) and the operator-phase view (`lifecycle.py:160`) because "looks complete" has three layers in this codebase.
- Excluded the `backend_runner` seam (`engine.py:1202-1204`, always `"completed"`) from the three modes: it is a test-injection point, not reachable by a model message in the shipped profile.
- Ranked severity: mode 1 > mode 2 > mode 3.

## Open questions
- For G2: strengthen `service.py:1887` (mode 1), arm `signals.py:139` by default (mode 2 — owner decision on the default), or both?
- Mode 3: should the REVIEW_* event payload summary get the same recorded-fact discipline `verdict_comment.py` applies to GitHub comments?

## Unrelated issues seen
- None in section4 at `04b819b`. (section5 has another lane's uncommitted work — `shell_gate.py`, two modified test files, untracked `test_shell_gate_args.py`; that is Task A of `docs/briefs/glm-handback-2026-11-13.md`, not an issue.)

## Known limits of this change
- Read-only probe: no test was run or changed; every claim is a file:line citation verified by grep/sed at `04b819b` with a clean section4 tree.
- Token estimate is a session-level estimate, not a meter reading.
