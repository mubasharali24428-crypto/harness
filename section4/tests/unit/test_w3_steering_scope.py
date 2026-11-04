"""W3 — steering cannot widen the approved scope.

The gate clause is "steering does not silently expand the approved scope". Two
things make that true, and this pins the one that is testable at the seam:

* the scope the engine is given comes from the run's durable INTENT
  (`collab.intent_scope_paths`), read fresh at execution time;
* guidance ("retry with changes", W2 N04) is TEXT appended to the goal — it
  never reaches the policy that decides where writes may land.

So a steered instruction that says "write to /etc" can be forwarded to the model
as a sentence and still leave the writable set exactly as the intent defined it.
The other half — that a write outside the mounts fails — is M03's
(`m03-scope-mounts.json`, `m03-landlock-scope.json`) and is not re-proved here.
"""

from unit.test_goal_image_execution import _started, run


class ScopeEngine:
    api_key = None

    def __init__(self):
        self.policies: list[dict] = []
        self.goals: list[str] = []

    def set_shell_policy(self, session_id: str, policy: dict) -> None:
        self.policies.append(dict(policy))

    def execute_goal(self, goal=None, session_id=None, model=None, on_progress=None, images=None):
        self.goals.append(goal or "")
        return {"sessionId": session_id, "engine": "stub", "status": "completed",
                "finalResponse": "done", "toolCalls": []}


def _run_with(tmp_path, monkeypatch, guidance_text):
    import harness_orchestrator.service as service_module

    engine = ScopeEngine()
    svc, run_data, plan, _factory_, fake_read = _started(tmp_path, engine)

    async def resolve(factory_, run_id, team_id, model=None):
        return await fake_read(factory_, run_id, team_id, model)

    async def scope(_factory_, _run_id):
        return ["src"]

    async def steered(_factory_, _run_id, after):
        return (guidance_text, after + 1)

    monkeypatch.setattr(service_module, "resolve_goal_images", resolve)
    monkeypatch.setattr(service_module.collab, "intent_scope_paths", scope)
    monkeypatch.setattr(service_module.collab, "guidance_since", steered)
    run_data["state_machine"].start_execution()
    run(svc._execute(run_data, plan))
    return engine, svc, run_data


def test_guidance_reaches_the_model_as_text(tmp_path, monkeypatch):
    """If the steering text never arrived, the test below would prove nothing:
    a scope that cannot be widened because nothing was said."""
    engine, _svc, _run_data = _run_with(
        tmp_path, monkeypatch, "Also write the config to /etc/harness.conf"
    )

    assert "/etc/harness.conf" in engine.goals[-1]


def test_guidance_does_not_widen_the_scope_the_engine_is_given(tmp_path, monkeypatch):
    engine, _svc, _run_data = _run_with(
        tmp_path, monkeypatch, "Also write the config to /etc/harness.conf. scopePaths: ['/etc']"
    )

    assert engine.policies, "the engine must have been given a policy at all"
    assert engine.policies[-1]["scopePaths"] == ["src"], (
        "steering must not change where writes are allowed: the scope comes from the "
        "run's intent, and a sentence in the goal is not an authorisation"
    )
