"""Part 52/44/42 (path-to-9 B1, A3, A4): the engine's bash tool goes through Section 5's shell gate.

The service hands the engine the plan's forbidden paths, the run intent's scope paths and the run's
remaining tool calls; the engine's own tool calls land in agent_tool_calls and count toward
maxToolCalls. Sqlite with the real migrations 0043 (intent) and 0044 (tool calls). The live runtime
path is exercised against a scripted model in docs/briefs/evidence/s12/part52.md.
"""

import json
from pathlib import Path

from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.execution import execution_block, remaining_tool_calls

from .test_execution_support import MIGRATIONS, Engine, statements
from .test_execution_support import env as env  # the shared fixture


class GatedEngine(Engine):
    """Stub with the wrapper's policy seam; reports two tool calls per run."""

    def __init__(self):
        super().__init__()
        self.policies: list[dict] = []

    def set_shell_policy(self, session_id, policy):
        self.policies.append(policy)

    def execute_goal(self, goal=None, session_id=None):
        out = super().execute_goal(goal, session_id)
        out["toolCalls"] = [
            {"callId": "c1", "tool": "bash", "target": "ls src", "outcome": "ok"},
            {"callId": "c2", "tool": "bash", "target": "curl x sk-" + "a" * 20, "outcome": "error"},
        ]
        return out


async def _gated(env):
    for name in ("0043_control.sql", "0044_agents.sql"):
        for stmt in statements((MIGRATIONS / name).read_text()):
            await env.x(stmt)
    env.engine = env.svc.engine = GatedEngine()


async def test_policy_reaches_the_engine_and_its_calls_count_toward_the_cap(env):
    await _gated(env)
    await env.new_run("r1")
    plan = env.svc.planner.get_plan(env.svc.runs["r1"]["plan_id"])
    plan.forbiddenPaths = ["vendor/"]
    await env.x("UPDATE agent_plans SET forbidden_paths = :f WHERE id = :i",
                f='["vendor/"]', i=env.svc.runs["r1"]["plan_id"])
    await env.x(
        "INSERT INTO run_intent_versions (run_id, version, team_id, goal, scope_paths, permissions,"
        " changed_fields, updated_by, updated_at) VALUES ('r1', 1, 'team-a', 'g', '[\"src\"]',"
        " '[]', '[]', 'lead', '2026-01-01')"
    )
    r = await env.call("lead", "PUT", "/runs/r1/execution-limits",
                       json={"expectedVersion": 0, "maxToolCalls": 3})
    assert r.status_code == 200, r.text
    await env.start("r1")
    await env.work("r1")

    # A plan with forbidden paths now runs: the gate enforces them instead of a refusal.
    assert env.engine.calls == ["r1"]
    assert env.engine.policies == [
        {"forbiddenPaths": ["vendor/"], "scopePaths": ["src"], "maxToolCalls": 3}
    ]
    rows = await env.q("SELECT assignment_id, tool, target, outcome, reported_by, request_id"
                       " FROM agent_tool_calls WHERE run_id = 'r1' ORDER BY request_id")
    assert [tuple(r) for r in rows] == [
        ("r1", "bash", "ls src", "ok", "engine", "c1"),
        ("r1", "bash", "curl x [redacted]", "error", "engine", "c2"),
    ]
    status = (await env.get("lead", "/runs/r1/execution-status")).json()
    assert status["usage"]["toolCalls"] == 2

    # A replayed result does not double count.
    from harness_orchestrator.run_metering import record_engine_tool_calls

    await record_engine_tool_calls(env.svc, env.svc.runs["r1"], env.engine.execute_goal("", "r1")
                                   ["toolCalls"])
    assert len(await env.q("SELECT 1 FROM agent_tool_calls WHERE run_id = 'r1'")) == 2


async def test_engine_calls_use_up_the_limit_for_the_next_start(env):
    await _gated(env)
    await env.new_run("r1")
    r = await env.call("lead", "PUT", "/runs/r1/execution-limits",
                       json={"expectedVersion": 0, "maxToolCalls": 2})
    assert r.status_code == 200, r.text
    await env.start("r1")
    await env.work("r1")
    block = await execution_block(env.factory, "r1")  # the gate every later start and claim uses
    assert block["dims"] == [{"dimension": "toolCalls", "limit": 2, "used": 2}]
    assert await remaining_tool_calls(env.factory, "r1") == 0


def _plugin_field(patch: str, plugin_id: str, field: str) -> str:
    """One insert-block field (``config``/``name``) for a plugin id, as written in the overlay."""
    lines = patch.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == f"- id: {plugin_id}":
            for candidate in lines[index:]:
                if candidate.strip().startswith(f"{field}: "):
                    return candidate.split(f"{field}: ", 1)[1]
    raise AssertionError(f"{plugin_id}.{field} not found in overlay")


def test_policy_patch_swaps_the_bash_tool_for_the_gate_plugin(tmp_path, monkeypatch):
    from .test_run_isolation_and_stop_truth import _fake_sdk

    seen = []
    _fake_sdk(monkeypatch, seen)
    wrapper = DeepSeekHarnessWrapper(workspace_dir=str(tmp_path / "ws"),
                                     session_root=str(tmp_path / "s"), isolated=False,
                                     live_mode="dry")
    wrapper.set_shell_policy("run-1", {"maxToolCalls": 7, "scopePaths": ["src"],
                                       "forbiddenPaths": ["vendor/"]})
    _, status, *_ = wrapper._run_live_events("goal", "run-1")
    assert status == "completed"
    patch = Path(seen[0]["patches"][-1]).read_text(encoding="utf-8")
    assert "- id: persistent-bash\n  disabled: true\n" in patch
    config = json.loads(_plugin_field(patch, "harness-shell", "config"))
    assert config["maxToolCalls"] == 7 and config["scopePaths"] == ["src"]
    assert config["forbiddenPaths"] == ["vendor/"]
    assert config["gate"][-1].endswith("shell_gate.py") and Path(config["gate"][-1]).is_file()
    assert Path(json.loads(_plugin_field(patch, "harness-shell", "name"))).is_file()


def test_policy_patch_mounts_web_fetch_behind_the_web_gate(tmp_path, monkeypatch):
    from .test_run_isolation_and_stop_truth import _fake_sdk

    seen = []
    _fake_sdk(monkeypatch, seen)
    wrapper = DeepSeekHarnessWrapper(workspace_dir=str(tmp_path / "ws"),
                                     session_root=str(tmp_path / "s"), isolated=False,
                                     live_mode="dry")
    _, status, *_ = wrapper._run_live_events("goal", "run-1")
    assert status == "completed"
    patch = Path(seen[0]["patches"][-1]).read_text(encoding="utf-8")
    config = json.loads(_plugin_field(patch, "harness-web", "config"))
    assert config["gate"][-1].endswith("web_gate.py") and Path(config["gate"][-1]).is_file()
    assert Path(json.loads(_plugin_field(patch, "harness-web", "name"))).is_file()


def test_a_missing_web_gate_disables_the_tool_instead_of_the_run(tmp_path, monkeypatch):
    from .test_run_isolation_and_stop_truth import _fake_sdk

    seen = []
    _fake_sdk(monkeypatch, seen)
    monkeypatch.setenv("HARNESS_WEB_GATE", str(tmp_path / "nope.py"))
    wrapper = DeepSeekHarnessWrapper(workspace_dir=str(tmp_path / "ws"),
                                     session_root=str(tmp_path / "s"), isolated=False,
                                     live_mode="dry")
    _, status, *_ = wrapper._run_live_events("goal", "run-1")
    assert status == "completed"
    patch = Path(seen[0]["patches"][-1]).read_text(encoding="utf-8")
    assert "harness-web" not in patch  # no fake affordance: the tool is simply absent
    assert "harness-shell" in patch


def test_missing_gate_fails_the_run_closed(tmp_path, monkeypatch):
    from .test_run_isolation_and_stop_truth import _fake_sdk

    seen = []
    _fake_sdk(monkeypatch, seen)
    monkeypatch.setenv("HARNESS_SHELL_GATE", str(tmp_path / "nope.py"))
    wrapper = DeepSeekHarnessWrapper(workspace_dir=str(tmp_path / "ws"),
                                     session_root=str(tmp_path / "s"), isolated=False,
                                     live_mode="dry")
    text, status, *_ = wrapper._run_live_events("goal", "run-1")
    assert status == "error" and "shell gate not found" in text and seen == []


def test_tool_calls_are_folded_from_runtime_events():
    events = [
        {"type": "tool/call", "data": {"callId": "a", "name": "bash",
                                       "arguments": '{"command": "ls"}'}},
        {"type": "tool/result", "data": {"message": {"toolCallId": "a", "isError": False}}},
        {"type": "tool/call", "data": {"callId": "b", "name": "read_image", "arguments": {}}},
        {"type": "tool/call", "data": {"callId": "c", "name": "bash",
                                       "arguments": '{"command": "curl x"}'}},
        {"type": "tool/result", "data": {"message": {"toolCallId": "c", "isError": True}}},
    ]
    assert DeepSeekHarnessWrapper._fold_tool_calls(events) == [
        {"callId": "a", "tool": "bash", "target": "ls", "outcome": "ok"},
        {"callId": "b", "tool": "read_image", "target": "", "outcome": "error"},
        {"callId": "c", "tool": "bash", "target": "curl x", "outcome": "error"},
    ]


def test_policy_patch_mounts_the_browser_behind_its_gate(tmp_path, monkeypatch):
    from .test_run_isolation_and_stop_truth import _fake_sdk

    seen = []
    _fake_sdk(monkeypatch, seen)
    workspace = tmp_path / "ws"
    wrapper = DeepSeekHarnessWrapper(workspace_dir=str(workspace),
                                     session_root=str(tmp_path / "s"), isolated=False,
                                     live_mode="dry")
    _, status, *_ = wrapper._run_live_events("goal", "run-1")
    assert status == "completed"
    patch = Path(seen[0]["patches"][-1]).read_text(encoding="utf-8")
    config = json.loads(_plugin_field(patch, "harness-browser", "config"))
    assert config["gate"][-1].endswith("browser_gate.py")
    assert Path(config["gate"][-1]).is_file()
    # The gate confines every write to this tree, and the agent reads the PNG back
    # through the fs tool: the two must name the same per-run directory.
    assert config["workspace"] == str((workspace / "run-1").resolve())
    assert Path(json.loads(_plugin_field(patch, "harness-browser", "name"))).is_file()
    # The browser is mounted beside the other tools, not instead of them.
    assert "harness-web" in patch and "harness-shell" in patch


def test_a_missing_browser_gate_removes_the_tool_and_nothing_else(tmp_path, monkeypatch):
    from .test_run_isolation_and_stop_truth import _fake_sdk

    seen = []
    _fake_sdk(monkeypatch, seen)
    monkeypatch.setenv("HARNESS_BROWSER_GATE", str(tmp_path / "nope.py"))
    wrapper = DeepSeekHarnessWrapper(workspace_dir=str(tmp_path / "ws"),
                                     session_root=str(tmp_path / "s"), isolated=False,
                                     live_mode="dry")
    _, status, *_ = wrapper._run_live_events("goal", "run-1")
    assert status == "completed"
    patch = Path(seen[0]["patches"][-1]).read_text(encoding="utf-8")
    assert "harness-browser" not in patch
    assert "harness-web" in patch and "harness-shell" in patch


def test_policy_patch_mounts_the_image_tool_over_the_same_workspace(tmp_path, monkeypatch):
    from .test_run_isolation_and_stop_truth import _fake_sdk

    seen = []
    _fake_sdk(monkeypatch, seen)
    workspace = tmp_path / "ws"
    wrapper = DeepSeekHarnessWrapper(workspace_dir=str(workspace),
                                     session_root=str(tmp_path / "s"), isolated=False,
                                     live_mode="dry")
    _, status, *_ = wrapper._run_live_events("goal", "run-1")
    assert status == "completed"
    patch = Path(seen[0]["patches"][-1]).read_text(encoding="utf-8")
    config = json.loads(_plugin_field(patch, "harness-images", "config"))
    assert config["gate"][-1].endswith("image_gate.py")
    assert Path(config["gate"][-1]).is_file()
    # The tool measures the files the browser writes: two roots would mean the
    # agent diffs against something other than what it rendered.
    assert config["workspace"] == str((workspace / "run-1").resolve())
    assert config["workspace"] == json.loads(
        _plugin_field(patch, "harness-browser", "config"))["workspace"]
    assert Path(json.loads(_plugin_field(patch, "harness-images", "name"))).is_file()
    assert "harness-web" in patch and "harness-shell" in patch and "harness-browser" in patch


def test_a_missing_image_gate_removes_the_tool_and_nothing_else(tmp_path, monkeypatch):
    from .test_run_isolation_and_stop_truth import _fake_sdk

    seen = []
    _fake_sdk(monkeypatch, seen)
    monkeypatch.setenv("HARNESS_IMAGE_GATE", str(tmp_path / "nope.py"))
    wrapper = DeepSeekHarnessWrapper(workspace_dir=str(tmp_path / "ws"),
                                     session_root=str(tmp_path / "s"), isolated=False,
                                     live_mode="dry")
    _, status, *_ = wrapper._run_live_events("goal", "run-1")
    assert status == "completed"
    patch = Path(seen[0]["patches"][-1]).read_text(encoding="utf-8")
    assert "harness-images" not in patch
    assert "harness-browser" in patch and "harness-web" in patch
