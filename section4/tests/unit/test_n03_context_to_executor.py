"""W2 (N03) — the context assembled at plan time has to survive to execution.

Two blocks are built when a run is planned and then dropped:

* ``run_data["rules_prompt_block"]`` — the project's own rules, i.e. the
  ``CLAUDE.md``/``AGENTS.md`` equivalent. Its loader comment says it is "stored on
  the run so … future engine calls can carry it into system context"; for the
  executor that future never arrived.
* ``run_data["context_bundle"]["rendered"]`` — the bounded @ref bundle. Its comment
  says it "rides on the run for engine prompt assembly" and it does not.

Both are advisory, never blocking: a run with neither must still execute exactly
as before.
"""

from unit.test_goal_image_execution import _started, run


class CapturingEngine:
    api_key = None

    def __init__(self):
        self.goals: list[str] = []

    def execute_goal(self, goal=None, session_id=None, model=None, on_progress=None, images=None):
        self.goals.append(goal or "")
        return {
            "sessionId": session_id,
            "engine": "capture",
            "status": "completed",
            "finalResponse": "ok",
        }


def _run(tmp_path, engine, mutate=None):
    svc, run_data, plan, _factory, _fake_read = _started(tmp_path, engine)
    if mutate is not None:
        mutate(run_data)
    run(svc._execute(run_data, plan))
    return engine.goals[-1]


def test_project_rules_reach_the_executor(tmp_path):
    engine = CapturingEngine()
    goal = _run(
        tmp_path,
        engine,
        lambda rd: rd.__setitem__("rules_prompt_block", "PROJECT RULES: run ruff before commit"),
    )
    assert "run ruff before commit" in goal


def test_the_refs_context_bundle_reaches_the_executor(tmp_path):
    engine = CapturingEngine()
    goal = _run(
        tmp_path,
        engine,
        lambda rd: rd.__setitem__(
            "context_bundle", {"rendered": "CURATED: the dialog lives in dialog.tsx"}
        ),
    )
    assert "the dialog lives in dialog.tsx" in goal


def test_rules_come_before_the_curated_context(tmp_path):
    """Instructions, then evidence, then the task. Reversing this puts the model's
    attention on a code snippet before it knows how the project wants it to work."""
    engine = CapturingEngine()

    def both(rd):
        rd["rules_prompt_block"] = "RULES-MARKER"
        rd["context_bundle"] = {"rendered": "CONTEXT-MARKER"}

    goal = _run(tmp_path, engine, both)
    assert goal.index("RULES-MARKER") < goal.index("CONTEXT-MARKER")


def test_a_run_with_neither_block_is_unchanged(tmp_path):
    """The historical prompt must not acquire stray separators or empty headings:
    this path is used by every run that has no rules and no @refs."""
    engine = CapturingEngine()
    goal = _run(tmp_path, engine)
    assert goal.strip() == "why does this dialog look wrong"


def test_an_empty_rendered_bundle_is_not_injected(tmp_path):
    engine = CapturingEngine()
    goal = _run(tmp_path, engine, lambda rd: rd.__setitem__("context_bundle", {"rendered": ""}))
    assert goal.strip() == "why does this dialog look wrong"
