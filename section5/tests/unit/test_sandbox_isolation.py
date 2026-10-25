"""Session 2 Lane B: sandbox isolation integrity (threat model T15, T16, T17, T7b).

Each test reproduces the probe recorded in docs/briefs/threat-model.md; see
docs/sandbox-isolation.md for the rule -> enforcement -> test map.
"""

import os
import shlex
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.signatures import TaskHandle

from harness_sandbox import confinement
from harness_sandbox.api import configure_sandbox, get_principal, router
from harness_sandbox.checkpoints import CheckpointManager
from harness_sandbox.diff_engine import DiffApplyError, apply_unified_diff
from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.policy import (
    InvalidTaskIdError,
    SecurityPolicyViolationError,
    TaskIdInUseError,
    validate_command,
)
from harness_sandbox.service import SandboxService

URL = "/api/v1/sandbox/tasks"
BODY = {"repoRef": "org/r", "branch": "main"}

# The interpreter running the tests: macOS hosts do not ship a bare `python`.
PY = shlex.quote(sys.executable)


def _principal(team):
    return SimpleNamespace(userId=f"u-{team}", teamId=team, role="admin")


@pytest.fixture()
def svc(tmp_path):
    runner = LocalSandboxRunner(storage_root=tmp_path / "tasks", confinement="off")
    service = SandboxService(
        runner=runner, checkpoints=CheckpointManager(root=tmp_path / "tasks")
    )
    yield service
    configure_sandbox(SandboxService())


def _as(svc, team):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: _principal(team)
    configure_sandbox(svc)
    return TestClient(app)


# --------------------------------------------------------------------- T15


def test_t15_other_team_cannot_take_over_task_id(svc):
    a, b = _as(svc, "team-a"), _as(svc, "team-b")
    assert a.post(URL, json={"taskId": "t1", **BODY}).status_code == 201
    (svc.runner.active_tasks["t1"]["workspace"] / "mine.txt").write_text("A-data")

    res = b.post(URL, json={"taskId": "t1", **BODY})
    assert res.status_code == 409
    assert res.json()["detail"]["error"]["code"] == "TASK_ID_IN_USE"

    # A still owns it; B never gained access to the workspace or the record.
    assert svc.runner.active_tasks["t1"]["team_id"] == "team-a"
    assert a.post(f"{URL}/t1/exec", json={"command": "ls"}).status_code == 200
    assert (svc.runner.active_tasks["t1"]["workspace"] / "mine.txt").read_text() == "A-data"
    assert b.post(f"{URL}/t1/exec", json={"command": "ls"}).status_code == 404
    assert b.post(f"{URL}/t1/destroy").status_code == 200  # absent-style ack, no effect
    assert "t1" in svc.runner.active_tasks


def test_t15_foreign_resume_flag_does_not_help(svc):
    a, b = _as(svc, "team-a"), _as(svc, "team-b")
    assert a.post(URL, json={"taskId": "t1", **BODY}).status_code == 201
    res = b.post(URL, json={"taskId": "t1", "resume": True, **BODY})
    assert res.status_code == 409
    assert svc.runner.active_tasks["t1"]["team_id"] == "team-a"


def test_t15_same_team_reuse_needs_explicit_resume(svc):
    a = _as(svc, "team-a")
    assert a.post(URL, json={"taskId": "t1", **BODY}).status_code == 201
    ws = svc.runner.active_tasks["t1"]["workspace"]
    (ws / "keep.txt").write_text("state")
    assert a.post(URL, json={"taskId": "t1", **BODY}).status_code == 409
    res = a.post(URL, json={"taskId": "t1", "resume": True, **BODY})
    assert res.status_code == 201
    assert res.json()["taskId"] == "t1"
    assert (ws / "keep.txt").read_text() == "state"


def test_t15_runner_level_refusal(tmp_path):
    runner = LocalSandboxRunner(storage_root=tmp_path / "s", confinement="off")
    runner.create_task("t1", "o/r", "m", team_id="A")
    with pytest.raises(TaskIdInUseError):
        runner.create_task("t1", "o/r", "m", team_id="B")
    assert runner.active_tasks["t1"]["team_id"] == "A"


def test_t15_on_disk_owner_survives_runner_restart(tmp_path):
    root = tmp_path / "s"
    first = LocalSandboxRunner(storage_root=root, confinement="off")
    first.create_task("t1", "o/r", "m", team_id="A")
    (root / "t1" / "workspace" / "f.txt").write_text("A")

    restarted = LocalSandboxRunner(storage_root=root, confinement="off")
    with pytest.raises(TaskIdInUseError):
        restarted.create_task("t1", "o/r", "m", team_id="B", resume=True)
    with pytest.raises(TaskIdInUseError):
        restarted.create_task("t1", "o/r", "m", team_id="A")  # no explicit resume
    handle = restarted.create_task("t1", "o/r", "m", team_id="A", resume=True)
    assert handle.taskId == "t1"
    assert restarted.active_tasks["t1"]["team_id"] == "A"
    assert (root / "t1" / "workspace" / "f.txt").read_text() == "A"


def test_t15_id_is_reusable_after_destroy(svc):
    a, b = _as(svc, "team-a"), _as(svc, "team-b")
    assert a.post(URL, json={"taskId": "t1", **BODY}).status_code == 201
    assert a.post(f"{URL}/t1/destroy").status_code == 200
    assert b.post(URL, json={"taskId": "t1", **BODY}).status_code == 201
    assert svc.runner.active_tasks["t1"]["team_id"] == "team-b"
    assert not (svc.runner.active_tasks["t1"]["workspace"] / "mine.txt").exists()


# --------------------------------------------------------------------- T16

BAD_IDS = [
    "..",
    "../escaped",
    "a/../../escaped",
    "a/b",
    "a\\b",
    "/abs",
    ".hidden",
    ".",
    "",
    "a\x00b",
    "a b",
    "x" * 65,
]


@pytest.mark.parametrize("bad", BAD_IDS)
def test_t16_runner_rejects_bad_task_id(tmp_path, bad):
    runner = LocalSandboxRunner(storage_root=tmp_path / "store", confinement="off")
    before = {p for p in tmp_path.rglob("*")}
    with pytest.raises(InvalidTaskIdError):
        runner.create_task(bad, "o/r", "m", team_id="evil")
    assert {p for p in tmp_path.rglob("*")} == before
    assert runner.active_tasks == {}


def test_t16_destroy_with_traversal_handle_never_deletes_outside(tmp_path):
    runner = LocalSandboxRunner(storage_root=tmp_path / "store", confinement="off")
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep").write_text("x")
    handle = TaskHandle(taskId="../victim", containerId="local-x", branch="m")
    with pytest.raises(InvalidTaskIdError):
        runner.destroy(handle)
    assert (victim / "keep").read_text() == "x"


@pytest.mark.parametrize("bad", ["../escaped", "..", ".hidden", "a b", "x" * 65, ""])
def test_t16_http_create_rejects_bad_task_id(svc, tmp_path, bad):
    client = _as(svc, "team-a")
    res = client.post(URL, json={"taskId": bad, **BODY})
    assert res.status_code == 400
    assert res.json()["detail"]["error"]["code"] == "INVALID_TASK_ID"
    assert not (tmp_path / "escaped").exists()
    assert svc.runner.active_tasks == {}


@pytest.mark.parametrize("ok", ["t1", "e2e-1760000000", "A_b-9", "a" * 64, "0f8fad5b-d9cb"])
def test_t16_legitimate_ids_still_accepted(tmp_path, ok):
    runner = LocalSandboxRunner(storage_root=tmp_path / "store", confinement="off")
    assert runner.create_task(ok, "o/r", "m").taskId == ok


# --------------------------------------------------------------------- T17

SECRET_DIR = "secrets"


def _ws_with_secret(tmp_path):
    ws = tmp_path / "ws"
    (ws / SECRET_DIR).mkdir(parents=True)
    (ws / SECRET_DIR / "x.txt").write_text("ORIGINAL\n")
    return ws


DIFF = "@@ -1 +1 @@\n-ORIGINAL\n+PWNED\n"


def test_t17_file_symlink_to_forbidden_target_is_blocked(tmp_path):
    ws = _ws_with_secret(tmp_path)
    (ws / "link.txt").symlink_to(ws / SECRET_DIR / "x.txt")
    with pytest.raises(SecurityPolicyViolationError):
        apply_unified_diff(ws, "link.txt", DIFF, [f"{SECRET_DIR}/"])
    assert (ws / SECRET_DIR / "x.txt").read_text() == "ORIGINAL\n"


def test_t17_relative_file_symlink_to_forbidden_target_is_blocked(tmp_path):
    ws = _ws_with_secret(tmp_path)
    os.symlink(f"{SECRET_DIR}/x.txt", ws / "link.txt")  # the exact probe spelling
    with pytest.raises(SecurityPolicyViolationError):
        apply_unified_diff(ws, "link.txt", DIFF, [f"{SECRET_DIR}/"])
    assert (ws / SECRET_DIR / "x.txt").read_text() == "ORIGINAL\n"


def test_t17_intermediate_directory_symlink_is_blocked(tmp_path):
    ws = _ws_with_secret(tmp_path)
    os.symlink(SECRET_DIR, ws / "innocent")
    with pytest.raises(SecurityPolicyViolationError):
        apply_unified_diff(ws, "innocent/x.txt", DIFF, [f"{SECRET_DIR}/"])
    assert (ws / SECRET_DIR / "x.txt").read_text() == "ORIGINAL\n"


def test_t17_symlink_escaping_workspace_is_blocked(tmp_path):
    ws = _ws_with_secret(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("ORIGINAL\n")
    (ws / "esc.txt").symlink_to(outside)
    (ws / "escdir").symlink_to(tmp_path)
    with pytest.raises(DiffApplyError):
        apply_unified_diff(ws, "esc.txt", DIFF, [])
    with pytest.raises(DiffApplyError):
        apply_unified_diff(ws, "escdir/outside.txt", DIFF, [])
    assert outside.read_text() == "ORIGINAL\n"


def test_t17_dangling_symlink_outside_workspace_is_blocked(tmp_path):
    ws = _ws_with_secret(tmp_path)
    (ws / "dangling.txt").symlink_to(tmp_path / "not-yet.txt")
    with pytest.raises(DiffApplyError):
        apply_unified_diff(ws, "dangling.txt", "+new\n", [])
    assert not (tmp_path / "not-yet.txt").exists()


def test_t17_in_workspace_symlink_to_allowed_file_still_edits(tmp_path):
    ws = _ws_with_secret(tmp_path)
    (ws / "real.txt").write_text("ORIGINAL\n")
    (ws / "alias.txt").symlink_to(ws / "real.txt")
    apply_unified_diff(ws, "alias.txt", DIFF, [f"{SECRET_DIR}/"])
    assert (ws / "real.txt").read_text() == "PWNED\n"


def test_t17_runner_apply_edit_blocks_symlink_to_forbidden(tmp_path):
    runner = LocalSandboxRunner(storage_root=tmp_path / "s", confinement="off")
    handle = runner.create_task("t1", "o/r", "m", forbidden_paths=[f"{SECRET_DIR}/"])
    ws = runner.active_tasks["t1"]["workspace"]
    (ws / SECRET_DIR).mkdir()
    (ws / SECRET_DIR / "x.txt").write_text("ORIGINAL\n")
    os.symlink(f"{SECRET_DIR}/x.txt", ws / "link.txt")
    with pytest.raises(SecurityPolicyViolationError):
        runner.apply_edit(handle, "link.txt", DIFF)
    assert (ws / SECRET_DIR / "x.txt").read_text() == "ORIGINAL\n"


@pytest.mark.parametrize("cmd", ["cat link.txt", "cat innocent/x.txt", "cat ./link.txt"])
def test_t17_exec_read_through_symlink_to_forbidden_is_blocked(tmp_path, cmd):
    ws = _ws_with_secret(tmp_path)
    os.symlink(f"{SECRET_DIR}/x.txt", ws / "link.txt")
    os.symlink(SECRET_DIR, ws / "innocent")
    with pytest.raises(SecurityPolicyViolationError):
        validate_command(cmd, [f"{SECRET_DIR}/"], cwd=str(ws))


def test_t17_exec_read_through_symlink_to_secret_file_is_blocked(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".env").write_text("K=V\n")
    os.symlink(".env", ws / "notes.txt")
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("cat notes.txt", [], cwd=str(ws))


def test_t17_exec_ordinary_symlink_still_allowed(tmp_path):
    ws = _ws_with_secret(tmp_path)
    (ws / "a.txt").write_text("x")
    os.symlink("a.txt", ws / "b.txt")
    validate_command("cat b.txt", [f"{SECRET_DIR}/"], cwd=str(ws))


# --------------------------------------------------------------------- T7b


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_t7b_per_task_private_tmp_dir_is_0700_and_distinct(tmp_path):
    runner = LocalSandboxRunner(storage_root=tmp_path / "s", confinement="off")
    runner.create_task("t1", "o/r", "m")
    runner.create_task("t2", "o/r", "m")
    t1, t2 = runner.active_tasks["t1"]["tmpdir"], runner.active_tasks["t2"]["tmpdir"]
    assert t1 != t2
    for tmp, tid in ((t1, "t1"), (t2, "t2")):
        assert Path(tmp).is_dir()
        assert _mode(Path(tmp)) == 0o700
        assert _mode(tmp_path / "s" / tid) == 0o700
        assert Path(tmp).resolve().is_relative_to((tmp_path / "s" / tid).resolve())


async def test_t7b_exec_gets_private_tmpdir_env(tmp_path):
    runner = LocalSandboxRunner(storage_root=tmp_path / "s", confinement="off")
    svc = SandboxService(runner=runner)
    handle = svc.run_task("t1", "o/r", "m", [])
    res = await svc.exec_structured(
        handle,
        f"{PY} -c \"import tempfile; open('out.txt','w').write(tempfile.gettempdir())\"",
    )
    assert res.exit_code == 0
    got = Path((runner.active_tasks["t1"]["workspace"] / "out.txt").read_text()).resolve()
    assert got == Path(runner.active_tasks["t1"]["tmpdir"]).resolve()


def test_t7b_tmp_dir_removed_on_destroy(tmp_path):
    runner = LocalSandboxRunner(storage_root=tmp_path / "s", confinement="off")
    handle = runner.create_task("t1", "o/r", "m")
    tmp = Path(runner.active_tasks["t1"]["tmpdir"])
    (tmp / "scratch").write_text("x")
    runner.destroy(handle)
    assert not tmp.exists()
    assert not (tmp_path / "s" / "t1").exists()


def test_t7b_seatbelt_profile_has_no_system_temp_write_or_sibling_read(tmp_path):
    from harness_sandbox import confinement

    root = (tmp_path / "s").resolve()
    task = root / "t1"
    forbidden = task / "workspace" / "payroll"
    prof = confinement.seatbelt_profile(
        task / "workspace",
        True,
        task_dir=task,
        tmpdir=task / "tmp",
        forbidden_paths=["payroll"],
        denied_task_dirs=[root / "t2"],
    )
    (write_rule,) = [ln for ln in prof.splitlines() if ln.startswith("(allow file-write*")]
    assert write_rule.count("(subpath") == 3  # workspace, private tmp, /dev/null only
    assert f'(subpath "{task / "workspace"}")' in write_rule
    assert f'(subpath "{task / "tmp"}")' in write_rule
    assert f'(deny file-read-data (subpath "{root / "t2"}"))' in prof
    assert f'(deny file-read-data file-write* (literal "{forbidden}"))' not in prof
    assert f'(deny file-read* file-write* (subpath "{forbidden}"))' in prof


pytestmark_seatbelt = pytest.mark.skipif(
    not confinement.seatbelt_available(),
    reason="Seatbelt tests require macOS sandbox-exec",
)


@pytestmark_seatbelt
async def test_t7b_seatbelt_sibling_workspace_is_neither_writable_nor_readable(tmp_path):
    runner = LocalSandboxRunner(storage_root=tmp_path / "s", confinement="seatbelt")
    svc = SandboxService(runner=runner)
    a = svc.run_task("ta", "o/r", "m", [])
    svc.run_task("tb", "o/r", "m", [])
    b_ws = runner.active_tasks["tb"]["workspace"]
    (b_ws / "b-data.txt").write_text("B-DATA")

    write = await svc.exec_structured(
        a, f"{PY} -c \"open('{b_ws}/pwn.txt','w').write('x')\""
    )
    assert write.exit_code != 0
    assert not (b_ws / "pwn.txt").exists()

    read = await svc.exec_structured(
        a, f"{PY} -c \"d=open('{b_ws}/b-data.txt').read();open('got.txt','w').write(d)\""
    )
    assert read.exit_code != 0
    assert not (runner.active_tasks["ta"]["workspace"] / "got.txt").exists()

    own = await svc.exec_structured(a, f"{PY} -c \"open('mine.txt','w').write('ok')\"")
    assert own.exit_code == 0


@pytestmark_seatbelt
async def test_t7b_seatbelt_shared_system_temp_is_not_writable_but_private_tmp_is(tmp_path):
    shared = Path(tempfile.mkdtemp(prefix="harness-t7b-"))
    try:
        runner = LocalSandboxRunner(storage_root=tmp_path / "s", confinement="seatbelt")
        svc = SandboxService(runner=runner)
        a = svc.run_task("ta", "o/r", "m", [])
        bad = await svc.exec_structured(
            a, f"{PY} -c \"open('{shared}/leak.txt','w').write('x')\""
        )
        assert bad.exit_code != 0
        assert not (shared / "leak.txt").exists()
        good = await svc.exec_structured(
            a,
            f"{PY} -c \"import tempfile,os;"
            "p=os.path.join(tempfile.gettempdir(),'f');open(p,'w').write('x');"
            "open('where.txt','w').write(p)\"",
        )
        assert good.exit_code == 0
        where = (runner.active_tasks["ta"]["workspace"] / "where.txt").read_text()
        assert Path(where).resolve().is_relative_to(
            Path(runner.active_tasks["ta"]["tmpdir"]).resolve()
        )
    finally:
        shutil.rmtree(shared, ignore_errors=True)
