"""Session 8 execution-policy invariants at the Section 4 SDK boundary."""

from pathlib import Path

from harness_orchestrator.engine import DeepSeekHarnessWrapper

from .test_execution_support import env as env


async def test_plan_forbidden_paths_fail_closed_before_sdk_execution(env):
    await env.new_run("r1")
    plan = env.svc.planner.get_plan(env.svc.runs["r1"]["plan_id"])
    plan.forbiddenPaths = ["secrets/", "pyproject.toml"]
    await env.start("r1")
    await env.work("r1")

    assert env.engine.calls == []
    current = await env.get("lead", "/runs/r1/execution-status")
    assert current.status_code == 200
    assert current.json()["state"] == "blocked"
    assert "cannot enforce plan forbidden paths" in current.json()["failure"]["reason"]


def test_sdk_policy_overlay_is_final_and_profile_cannot_be_overridden(tmp_path, monkeypatch):
    from .test_run_isolation_and_stop_truth import _fake_sdk

    seen = []
    _fake_sdk(monkeypatch, seen)
    operator_patch = tmp_path / "operator.patch.yml"
    operator_patch.write_text(
        "- id: sandbox-policy\n  name: '@deepseek-ai/dsh-sandbox-policy'\n"
        "  config:\n    mode: danger-full-access\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DSH_PROFILE", "danger-full-access")
    monkeypatch.setenv("DSH_PATCH_FILES", str(operator_patch))
    wrapper = DeepSeekHarnessWrapper(
        workspace_dir=str(tmp_path / "workspace"),
        session_root=str(tmp_path / "sessions"),
        isolated=False,
        live_mode="dry",
    )

    _, status, *_ = wrapper._run_live_events("goal", "run-s8")

    assert status == "completed"
    options = seen[0]
    assert options["profile"] == "sdk-minimal"
    assert options["patches"][0] == str(operator_patch)
    assert options["patches"][-1].endswith("harness-execution-policy.yml")
    policy = Path(options["patches"][-1]).read_text(encoding="utf-8")
    assert "mode: workspace-write" in policy
    assert f'workspaceRoot: "{Path(wrapper.run_workspace("run-s8")).resolve()}"' in policy
