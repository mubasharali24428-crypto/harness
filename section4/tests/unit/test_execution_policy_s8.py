"""Session 8 execution-policy invariants at the Section 4 SDK boundary."""

from pathlib import Path

from harness_orchestrator.engine import DeepSeekHarnessWrapper

from .test_execution_support import env as env


async def test_plan_forbidden_paths_fail_closed_before_sdk_execution(env):
    await env.new_run("r1")
    plan = env.svc.planner.get_plan(env.svc.runs["r1"]["plan_id"])
    plan.forbiddenPaths = ["secrets/", "pyproject.toml"]
    # Persist it like a real plan: a replica that replays the run reads agent_plans, not RAM.
    await env.x("UPDATE agent_plans SET forbidden_paths = :f WHERE id = :i",
                f='["secrets/", "pyproject.toml"]', i=env.svc.runs["r1"]["plan_id"])
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


def test_the_patch_turns_on_the_file_and_search_tools_inside_the_workspace(tmp_path, monkeypatch):
    """W1: sdk-minimal ships tool-fs with read/write/edit off, so the agent could
    not open a file without the shell gate. The patch enables them inside the
    container, whose mounts are what confine them (W21)."""
    from harness_orchestrator.engine import _execution_policy_patch

    (tmp_path / "ws").mkdir()
    monkeypatch.setattr("harness_orchestrator.engine.confine.resolve_mode", lambda: "container")
    text = Path(_execution_policy_patch(str(tmp_path), str(tmp_path / "ws"))).read_text()

    assert "- id: tool-fs\n" in text
    for expected in ("    read: true", "    readImage: true", "    write: true", "    edit: true"):
        assert expected in text, expected
    assert "read: false" not in text and "write: false" not in text
    assert "dsh-tool-fs-search'\n  disabled: false" in text  # glob/grep, no bash round-trip

    # the policy pin still travels with it
    assert "mode: workspace-write" in text
    assert f'workspaceRoot: "{Path(tmp_path / "ws").resolve()}"' in text


def test_native_file_tools_are_off_outside_the_container(tmp_path, monkeypatch):
    """W21/M01: `workspaceRoot` confines nothing — in `off` mode native read and
    write reached any path by absolute path, `../`, symlink or cd. Only the
    container's mounts confine them, so every other mode turns them off."""
    from harness_orchestrator.engine import _execution_policy_patch

    (tmp_path / "ws").mkdir()
    for mode in ("none", "seatbelt", "landlock"):
        monkeypatch.setattr("harness_orchestrator.engine.confine.resolve_mode", lambda m=mode: m)
        text = Path(_execution_policy_patch(str(tmp_path), str(tmp_path / "ws"), {})).read_text()
        for off in ("read: false", "readImage: false", "write: false", "edit: false"):
            assert f"    {off}" in text, (mode, off)
        assert "dsh-tool-fs-search'\n  disabled: true" in text, mode
        assert "confined only by the container" in text, mode  # the reason travels


def test_write_and_edit_need_the_container_to_freeze_forbidden_paths(tmp_path, monkeypatch):
    """Forbidden paths live INSIDE the workspace and the tool does not know them;
    the container's nested read-only binds freeze them."""
    from harness_orchestrator.engine import _execution_policy_patch

    (tmp_path / "ws").mkdir()
    policy = {"forbiddenPaths": ["pyproject.toml", "secrets/"]}

    monkeypatch.setattr("harness_orchestrator.engine.confine.resolve_mode", lambda: "container")
    text = Path(_execution_policy_patch(str(tmp_path), str(tmp_path / "ws"), policy)).read_text()
    assert "    write: true" in text and "    edit: true" in text
    assert "    read: true" in text

    monkeypatch.setattr("harness_orchestrator.engine.confine.resolve_mode", lambda: "none")
    text = Path(_execution_policy_patch(str(tmp_path), str(tmp_path / "ws"), policy)).read_text()
    assert "    write: false" in text and "    read: false" in text


def test_a_narrower_intent_scope_disables_native_writes(tmp_path, monkeypatch):
    """M01/M03: the runtime confines tool-fs to the workspace root only, so an
    approved intent narrower than the workspace cannot be enforced by a native
    tool — writes stay off instead of silently exceeding the approval."""
    from harness_orchestrator.engine import _fs_write_capability

    workspace = tmp_path / "ws"
    (workspace / "src").mkdir(parents=True)
    monkeypatch.setattr("harness_orchestrator.engine.confine.resolve_mode", lambda: "container")

    allowed, _ = _fs_write_capability({"forbiddenPaths": [], "scopePaths": []}, str(workspace))
    assert allowed is True

    # scope naming the workspace itself is not narrower
    allowed, _ = _fs_write_capability({"scopePaths": [str(workspace)]}, str(workspace))
    assert allowed is True

    allowed, why = _fs_write_capability(
        {"scopePaths": [str(workspace / "src")]}, str(workspace)
    )
    assert allowed is False
    assert "narrower scope" in why


def test_the_patch_records_why_writes_were_disabled(tmp_path, monkeypatch):
    from harness_orchestrator.engine import _execution_policy_patch

    workspace = tmp_path / "ws"
    (workspace / "src").mkdir(parents=True)
    monkeypatch.setattr("harness_orchestrator.engine.confine.resolve_mode", lambda: "container")
    text = Path(
        _execution_policy_patch(
            str(tmp_path), str(workspace), {"scopePaths": [str(workspace / "src")]}
        )
    ).read_text()
    assert "    write: false" in text
    assert "narrower scope" in text
