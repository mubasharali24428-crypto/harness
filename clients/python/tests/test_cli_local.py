"""W6 slice 4: local-workspace mode — fail-closed launch, manifest apply, honest trace.

No docker on this path: the launcher is injected as a callable, exactly like the
HTTP transport is injected in the run tests. What is pinned is the contract the
local mode must not break — the gate still happens, start_run never does, the
apply touches only what the boundary's diff named, and refusals are actionable.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from harness_sdk.cli import config
from harness_sdk.cli import local as local_mod
from harness_sdk.cli.commands import CliError, execute_run

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "deploy" / "runners"))
import local_engine  # the launcher under test


@pytest.fixture()
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("HARNESS_TOKEN", raising=False)
    return tmp_path / "cfg"


@pytest.fixture()
def workspace(tmp_path):
    ws = tmp_path / "proj"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "a.py").write_text("one\n")
    (ws / "keep.txt").write_text("unchanged\n")
    (ws / ".git").mkdir()
    (ws / ".git" / "config").write_text("secretive\n")
    return ws


@pytest.fixture()
def out_dirs(tmp_path):
    work = tmp_path / "work"
    base = tmp_path / "baseline"
    work.mkdir()
    base.mkdir()
    return work, base


def test_deletions_come_only_from_hunk_headers():
    diff = (
        "--- src/gone.py\n+++ /dev/null\n@@\n-x\n"
        "--- a/keep.py\n+++ b/keep.py\n@@\n-x\n+y\n"
        "--- /dev/null\n+++ b/new.py\n@@\n+z\n"
    )
    assert local_engine.deletions_from_diff(diff) == ["src/gone.py"]


# ------------------------------------------------------------------ launcher


def test_copy_tree_skips_git(workspace, tmp_path):
    dst = tmp_path / "copy"
    local_engine.copy_tree(workspace, dst)
    assert (dst / "src" / "a.py").read_text() == "one\n"
    assert not (dst / ".git").exists()


def test_preflight_refuses_without_container_mode():
    with pytest.raises(local_engine.Refusal) as exc:
        local_engine.preflight({"HARNESS_ENGINE_CONFINEMENT": "off"})
    assert "container" in str(exc.value)


def test_preflight_refuses_missing_image(monkeypatch, tmp_path):
    monkeypatch.setattr(local_engine.shutil, "which", lambda _x: "/usr/bin/docker")
    monkeypatch.setattr(
        local_engine, "_docker",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="27", stderr=""),
    )

    def no_image(*args):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="no such image")

    monkeypatch.setattr(local_engine, "_docker", no_image)
    with pytest.raises(local_engine.Refusal) as exc:
        local_engine.preflight(
            {"HARNESS_ENGINE_CONFINEMENT": "container", "DEEPSEEK_API_KEY": "k"}
        )
    assert "image" in str(exc.value)


def test_run_local_refuses_unsafe_run_id(workspace):
    with pytest.raises(local_engine.Refusal):
        local_engine.run_local(workspace, "goal", "../escape")


def test_run_local_refuses_missing_directory(tmp_path):
    with pytest.raises(local_engine.Refusal):
        local_engine.run_local(tmp_path / "nope", "goal", "run-1")


def test_copy_out_changed_limited_to_names(workspace, tmp_path):
    work = tmp_path / "work"
    run_dir = workspace / "src"
    local_engine.copy_out_changed(run_dir.parent, work, ["src/a.py", "src/missing.py"])
    assert (work / "src" / "a.py").read_text() == "one\n"
    assert list(work.rglob("src/missing.py")) == []


def test_normalize_diff_strips_scratch_prefixes(tmp_path):
    run_dir = tmp_path / "ws" / "run-1"
    base = tmp_path / "baseline"
    raw = f"--- {base}/a.py\n+++ {run_dir}/a.py\n@@\n-x\n+y\n"
    text, omitted = local_engine.normalize_diff(raw, run_dir, base)
    assert text == "--- a.py\n+++ a.py\n@@\n-x\n+y\n"
    assert omitted is None


# ------------------------------------------------------------------ apply


def test_apply_back_touches_only_named_files(workspace, out_dirs):
    work, _base = out_dirs
    (work / "src").mkdir(parents=True)
    (work / "src" / "a.py").write_text("two\n")
    result = local_mod.apply_back(workspace, work, ["src/a.py"], [])
    assert (workspace / "src" / "a.py").read_text() == "two\n"
    assert (workspace / "keep.txt").read_text() == "unchanged\n"  # never named, never touched
    assert result == {"applied": ["src/a.py"], "deleted": []}


def test_apply_back_removes_what_the_engine_deleted(workspace, out_dirs):
    work, _base = out_dirs
    (workspace / "src" / "gone.py").write_text("x\n")  # exists until the engine deletes it
    result = local_mod.apply_back(workspace, work, [], ["src/gone.py"])
    assert not (workspace / "src" / "gone.py").exists()
    assert result["deleted"] == ["src/gone.py"]


def test_apply_back_refuses_escaping_names(workspace, out_dirs):
    work, _base = out_dirs
    with pytest.raises(CliError):
        local_mod.apply_back(workspace, work, ["../outside.py"], [])
    with pytest.raises(CliError):
        local_mod.apply_back(workspace, work, [], [".git/config"])


# ------------------------------------------------------------------ execute_run --local


class _Http:
    """The transport seam: local provenance uploads the patch as RAW bytes."""

    def __init__(self, calls: list, fail: bool = False):
        self.calls = calls
        self.fail = fail

    def request(self, base_url, method, path, *, params=None, content=None, **kw):
        self.calls.append(("raw", method, path, dict(params or {}), content))
        if self.fail:
            raise RuntimeError("EMPTY_ARTIFACT (422)")
        return {"artifact": {"id": "art-7"}}


class _Orch:
    def __init__(self):
        self.calls: list[tuple] = []
        self.base_url = "http://orch.test"

    def propose_plan(self, body):
        self.calls.append(("propose", body))
        return {"planId": "plan-1"}

    def approve_plan(self, plan_id):
        self.calls.append(("approve", plan_id))
        return {"runId": "run-9"}

    def start_run(self, run_id):
        self.calls.append(("start", run_id))
        return {}

    def get_plan(self, run_id):
        self.calls.append(("get_plan", run_id))
        return {"shellPolicy": {"forbiddenPaths": ["payroll"]}}

    def get_run(self, run_id):
        self.calls.append(("get", run_id))
        return {"runId": run_id, "state": "pending", "steps": []}

    def run_comments_create(self, scope_id, body):
        self.calls.append(("comment", scope_id, body))
        return {"comment": {"id": "c-1"}}


class _Sdk:
    def __init__(self, upload_fails=False):
        self.identity = type("I", (), {"list_projects": lambda self, limit=None: {"items": [{"id": "p1"}]}})()
        self.orchestrator = _Orch()
        self.http = _Http(self.orchestrator.calls, fail=upload_fails)


def _fake_launch(work, base, changed=("src/a.py",), deleted_name="gone.py"):
    def launch(directory, goal, run_id, *, shell_policy=None, timeout=900.0, note=None):
        if note:
            note("launched")
        (work / "src").mkdir(parents=True, exist_ok=True)
        (work / "src" / "a.py").write_text("two\n")
        (base / "src").mkdir(parents=True, exist_ok=True)
        (base / "src" / "a.py").write_text("one\n")
        (base / "src" / deleted_name).write_text("bye\n")
        assert shell_policy == {"forbiddenPaths": ["payroll"]}, shell_policy
        return {
            "status": "completed",
            "finalResponse": "made the change",
            "toolCalls": [{"tool": "write_file"}],
            "diff": "--- a/src/a.py\n+++ b/src/a.py\n+two\n",
            "changed": list(changed),
            "deleted": [deleted_name],
            "workDir": str(work),
            "local": {"confinement": "container", "image": "harness-engine-worker:p0"},
        }

    return launch


def test_local_run_gates_on_server_but_never_starts_server_execution(workspace, tmp_path):
    sdk = _Sdk()
    work, base = tmp_path / "w", tmp_path / "b"
    result = execute_run(
        sdk, "edit a",
        assume_yes=True,
        local_dir=str(workspace),
        local_launch=_fake_launch(work, base),
        local_apply=lambda *a: {"applied": ["src/a.py"], "deleted": ["src/gone.py"]},
    )
    names = [c[0] for c in sdk.orchestrator.calls]
    assert "propose" in names and "approve" in names
    assert "start" not in names  # the queue only claims EXECUTING runs: no phantom, no double-run
    assert result["state"] == "done"
    assert result["local"]["applied"] == ["src/a.py"]
    assert result["local"]["confinement"] == "container"


def test_local_run_records_artifact_and_provenance_comment(workspace, tmp_path):
    """The trace the plan promised, with the two shapes the server actually accepts:
    an artifact whose BODY is the patch, and a comment that references an id."""
    sdk = _Sdk()
    work, base = tmp_path / "w", tmp_path / "b"
    result = execute_run(
        sdk, "edit a", assume_yes=True,
        local_dir=str(workspace),
        local_launch=_fake_launch(work, base),
        local_apply=lambda *a: {"applied": ["src/a.py"], "deleted": []},
    )
    raw = next(c for c in sdk.orchestrator.calls if c[0] == "raw")
    assert raw[1] == "POST" and raw[2] == "/api/v1/orchestrator/runs/run-9/artifacts"
    assert raw[3]["kind"] == "diff" and raw[3]["name"].endswith(".patch")
    assert "+two" in raw[4]  # the boundary's own bytes, not a placeholder
    comment = next(c for c in sdk.orchestrator.calls if c[0] == "comment")
    body = comment[2]
    assert "executed" in body["body"] and "diffSha256" in body["body"]
    assert body["refKind"] == "diff" and body["refId"] == "art-7"  # BAD_REF without refId
    assert result["trace"]["artifact"] == "art-7"
    assert result["trace"]["failures"] == []


def test_local_run_with_no_diff_records_the_skipped_trace_honestly(workspace, tmp_path):
    sdk = _Sdk()
    work, base = tmp_path / "w", tmp_path / "b"

    def no_diff(directory, goal, run_id, **kw):
        out = _fake_launch(work, base)(directory, goal, run_id, **kw)
        out["diff"] = ""
        out["changed"] = []
        return out

    result = execute_run(
        sdk, "edit a", assume_yes=True, local_dir=str(workspace),
        local_launch=no_diff,
        local_apply=lambda *a: {"applied": [], "deleted": []},
    )
    assert not [c for c in sdk.orchestrator.calls if c[0] == "raw"]  # EMPTY_ARTIFACT on the server
    assert any("no diff" in f for f in result["trace"]["failures"])


def test_local_refusal_is_actionable_and_leaves_no_server_execution(workspace, tmp_path):
    sdk = _Sdk()

    def refuse(*a, **k):
        raise CliError("local engine refused: HARNESS_ENGINE_CONFINEMENT='off': "
                       "local mode runs ONLY the container boundary")

    with pytest.raises(CliError) as exc:
        execute_run(sdk, "edit a", assume_yes=True, local_dir=str(workspace), local_launch=refuse)
    assert "container" in str(exc.value)
    names = [c[0] for c in sdk.orchestrator.calls]
    assert "start" not in names


def test_main_run_local_flag_wires_through_and_json_stays_one_object(
    workspace, tmp_path, config_dir, monkeypatch
):
    """The argv path: `harness run --local DIR --yes --json` must reach execute_run
    with local_dir set and print exactly one object."""
    from harness_sdk.cli import commands as commands_mod

    sdk = _Sdk()
    # argv path: execute_run is faked; no launch happens
    seen = {}

    def fake_execute(client, goal, **kwargs):
        seen.update(kwargs)
        return {
            "runId": "run-9", "planId": "plan-1", "state": "done",
            "answer": "made the change", "toolCalls": [], "steps": [],
            "local": {"workspace": kwargs.get("local_dir"), "applied": ["src/a.py"],
                      "deleted": [], "diffChars": 10, "confinement": "container",
                      "image": "harness-engine-worker:p0"},
        }

    monkeypatch.setattr(commands_mod, "execute_run", fake_execute)
    import importlib

    main_module = importlib.import_module("harness_sdk.cli.main")
    monkeypatch.setattr(main_module, "execute_run", fake_execute)
    config.save(config.Credentials("tok"))
    out, err = io.StringIO(), io.StringIO()
    code = main_module.main(
        ["run", "edit a", "--local", str(workspace), "--yes", "--json"],
        sdk=sdk, stdout=out, stderr=err,
    )
    assert code == 0, err.getvalue()
    assert seen["local_dir"] == str(workspace)
    payload = json.loads(out.getvalue())  # one object, pipe-parseable
    assert payload["local"]["confinement"] == "container"


def test_execute_run_helper_launchers_resolve_to_local_module(workspace):
    # the injected-callable seams default to the real launcher/apply helpers
    assert local_mod.launch is not None and local_mod.apply_back is not None
    from harness_sdk.cli.commands import _default_apply, _default_launch

    assert _default_launch() is local_mod.launch
    assert _default_apply() is local_mod.apply_back


def test_failed_trace_is_reported_in_the_result_not_swallowed(workspace, tmp_path):
    """A silent audit gap is the worst outcome: the failure must be in --json."""
    sdk = _Sdk(upload_fails=True)
    work, base = tmp_path / "w", tmp_path / "b"
    result = execute_run(
        sdk, "edit a", assume_yes=True, local_dir=str(workspace),
        local_launch=_fake_launch(work, base),
        local_apply=lambda *a: {"applied": ["src/a.py"], "deleted": []},
    )
    assert result["trace"]["artifact"] is None
    assert any("EMPTY_ARTIFACT" in f for f in result["trace"]["failures"])
    # the provenance comment still goes up, just unreferenced: losing the
    # artifact must not also lose the record that the run executed locally
    assert result["trace"]["comment"] == "c-1"
    comment = next(c for c in sdk.orchestrator.calls if c[0] == "comment")
    assert "refKind" not in comment[2] and "refId" not in comment[2]


def test_launch_shows_the_workers_progress_events(tmp_path, monkeypatch, workspace):
    """Off-host tool events cannot become server agent_events, so they must at
    least reach the operator — the docstring promised never to drop them."""
    ndjson = "\n".join(
        [
            json.dumps({"progress": {"tool": "bash", "command": "printf present > present.txt"}}),
            json.dumps({"progress": {"tool": "read_file", "args": {"path": "present.txt"}}}),
            json.dumps(
                {
                    "engine_result": {"status": "completed", "finalResponse": "ok",
                                      "changed": [], "deleted": []},
                    "diff": "",
                }
            ),
        ]
    )

    class _Proc:
        returncode = 0
        stdout = ndjson
        stderr = ""

    monkeypatch.setattr(local_mod.subprocess, "run", lambda *a, **k: _Proc())
    seen = []
    out = local_mod.launch(str(workspace), "goal", "run-1", note=seen.append)
    assert out["status"] == "completed"
    progress = [s for s in seen if s.startswith("engine: ")]
    assert len(progress) == 2
    assert "bash" in progress[0] and "printf present" in progress[0]
    assert "read_file" in progress[1]


# ---------------------------------------------------------------- chat --local


def test_a_local_chat_turn_gates_on_server_and_never_starts_it(config_dir, workspace, tmp_path):
    from harness_sdk.cli import session as session_store
    from harness_sdk.cli.commands import chat_loop

    sdk = _Sdk()
    session = session_store.new_session("p1", str(workspace))
    work, base = tmp_path / "cw", tmp_path / "cb"
    emitted = []
    lines = iter(["touch present.txt", None])
    out = chat_loop(
        sdk, session,
        read_line=lambda _p: next(lines),
        emit=emitted.append,
        assume_yes=True,
        local_launch=_fake_launch(work, base),
        local_apply=lambda *a: {"applied": ["src/a.py"], "deleted": []},
    )
    names = [c[0] for c in sdk.orchestrator.calls]
    assert "propose" in names and "approve" in names and "start" not in names
    assert out["exchanges"][0]["runId"] == "run-9"
    assert "local container" in emitted[-1]  # the human line says where it ran


def test_a_session_keeps_its_directory_across_resume(config_dir, workspace, tmp_path):
    from harness_sdk.cli import session as session_store

    session = session_store.new_session("p1", str(workspace))
    session.add("hi", "hi", {"runId": "r1", "planId": "p1", "state": "done", "answer": "yo"})
    session.save()
    again = session_store.load_session(session.session_id)
    assert again.local_dir == str(workspace)
