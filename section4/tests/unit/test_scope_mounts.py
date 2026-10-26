"""M03: the run intent's scope is enforced by the container's mounts, not by reading strings.

M01 measured that ``python3 -c "open('docs/x','w')"`` wrote outside ``scopePaths=["src"]``
because the scope check skips code strings. Here the workspace is mounted read-only and only
the scope is bound back writable, so every writer in the worker - shell, interpreter, native
file tool - meets the same kernel answer.
"""

import shutil
import subprocess

import pytest

from harness_orchestrator import confine, engine

BYPASS = "open('do'+'cs/leak.txt', 'w').write('x')"


def _tree(tmp_path):
    ws, session = tmp_path / "workspace", tmp_path / "session"
    for d in ("src/pkg", "docs", "payroll"):
        (ws / d).mkdir(parents=True)
    (ws / "README.md").write_text("r")
    session.mkdir()
    return ws, session


def _mounts(tmp_path, **kw):
    ws, session = _tree(tmp_path)
    argv = confine.wrap_command(
        ["python", "-m", "w"], mode="container", roots=[], workspace=str(ws),
        session_dir=str(session), tmp_dir=str(tmp_path), container_name="n", network="p", **kw,
    )
    return ws.resolve(), [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]


def test_targets_are_existing_paths_inside_the_workspace_and_never_under_forbidden(tmp_path):
    ws, _ = _tree(tmp_path)
    (ws / "away").symlink_to(tmp_path)
    real = ws.resolve()
    forbidden = [str(real / "payroll")]
    got = confine.scope_targets(
        str(ws), ["src/", "README.md", "missing", "away", "payroll/sub", "payroll"], forbidden
    )
    assert got == sorted([str(real / "src"), str(real / "README.md")])


def test_no_scope_or_a_scope_naming_the_workspace_means_all_of_it(tmp_path):
    ws, _ = _tree(tmp_path)
    assert confine.scope_targets(str(ws), [], []) is None
    assert confine.scope_targets(str(ws), None, []) is None
    assert confine.scope_targets(str(ws), ["src", "."], []) is None
    assert confine.scope_targets(str(ws), ["/"], []) is None


def test_narrower_scope_freezes_the_workspace_and_binds_the_scope_writable(tmp_path):
    ws, mounts = _mounts(tmp_path, scope_paths=["src"], forbidden_paths=["src/pkg"])
    assert mounts[0] == f"type=bind,src={ws},dst={ws},readonly=true"
    src, pkg = ws / "src", ws / "src" / "pkg"
    assert f"type=bind,src={src},dst={src},readonly=false" in mounts
    # forbidden beneath the scope stays read-only, and is listed after the scope bind
    frozen = f"type=bind,src={pkg},dst={pkg},readonly"
    assert mounts.index(frozen) > mounts.index(f"type=bind,src={src},dst={src},readonly=false")


def test_whole_workspace_scope_keeps_todays_writable_mount(tmp_path):
    ws, mounts = _mounts(tmp_path)
    assert mounts[0] == f"type=bind,src={ws},dst={ws},readonly=false"
    assert len(mounts) == 2


def test_scope_that_names_nothing_existing_leaves_the_workspace_read_only(tmp_path):
    ws, mounts = _mounts(tmp_path, scope_paths=["not-there-yet"])
    assert mounts[0].endswith("readonly=true") and len(mounts) == 2


def test_native_writes_follow_the_observed_mount(tmp_path, monkeypatch):
    ws, _ = _tree(tmp_path)
    policy = {"scopePaths": ["src"]}
    monkeypatch.setattr(engine, "_read_only", lambda path: False)
    allowed, why = engine._fs_write_capability(policy, str(ws))
    assert allowed is False and "narrower scope" in why
    monkeypatch.setattr(engine, "_read_only", lambda path: True)  # the container froze the root
    assert engine._fs_write_capability(policy, str(ws)) == (True, "")


def test_relative_scope_is_resolved_against_the_workspace_not_the_cwd(tmp_path, monkeypatch):
    ws, _ = _tree(tmp_path)
    monkeypatch.chdir(ws / "src")  # "src" relative to this cwd would not exist
    monkeypatch.setattr(engine, "_read_only", lambda path: False)
    assert engine._fs_write_capability({"scopePaths": ["src"]}, str(ws))[0] is False
    assert engine._fs_write_capability({"scopePaths": ["."]}, str(ws))[0] is True


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker")
def test_real_container_scope_mounts_refuse_the_m01_bypass(tmp_path):
    image = "harness-engine-worker:m03"
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode:
        pytest.skip(f"{image} not built")
    ws, mounts = _mounts(tmp_path, scope_paths=["src"])
    for p in (ws, ws / "src", ws / "docs"):
        p.chmod(0o777)

    def run(code):
        argv = ["docker", "run", "--rm", "--cap-drop=ALL", "--security-opt", "no-new-privileges",
                "--user", "10001:10001", "--workdir", str(ws), "--entrypoint", "python"]
        for m in mounts:
            argv += ["--mount", m]
        return subprocess.run([*argv, image, "-c", code], capture_output=True, text=True)

    for code in (BYPASS, "open('top.txt', 'w')", "open('README.md', 'a').write('x')"):
        res = run(code)
        assert res.returncode != 0 and "Read-only file system" in res.stderr, (code, res.stderr)
    assert not (ws / "docs" / "leak.txt").exists() and (ws / "README.md").read_text() == "r"
    assert run("open('src/ok.txt', 'w').write('x')").returncode == 0
    assert (ws / "src" / "ok.txt").read_text() == "x"
