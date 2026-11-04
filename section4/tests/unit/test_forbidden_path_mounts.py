"""S-01: forbiddenPaths are enforced by the boundary, not only by reading command strings."""

import shutil
import subprocess

import pytest

from harness_orchestrator import confine

BYPASS = "open('pay'+'roll/data.txt', 'w').write('x')"  # slips past the string policy


def _tree(tmp_path):
    ws, session = tmp_path / "workspace", tmp_path / "session"
    (ws / "payroll").mkdir(parents=True)
    (ws / "payroll" / "data.txt").write_text("keep")
    (ws / "pyproject.toml").write_text("[tool]")
    (ws / "src").mkdir()
    session.mkdir()
    return ws, session


def test_targets_are_existing_real_paths_inside_workspace(tmp_path):
    ws, _ = _tree(tmp_path)
    (ws / "outside").symlink_to(tmp_path)
    got = confine.forbidden_targets(
        str(ws), ["payroll/", "pyproject.toml", "src/*.nope", "../x", "/pyproject.toml", "outside"]
    )
    real = ws.resolve()
    assert got == sorted([str(real / "payroll"), str(real / "pyproject.toml")])


def test_too_many_matches_fail_closed(tmp_path):
    ws, _ = _tree(tmp_path)
    for i in range(confine.MAX_FORBIDDEN_TARGETS + 1):
        (ws / f"f{i}.txt").write_text("x")
    with pytest.raises(confine.ConfinementUnavailableError):
        confine.forbidden_targets(str(ws), ["f*.txt"])


def test_container_argv_mounts_forbidden_paths_readonly(tmp_path):
    ws, session = _tree(tmp_path)
    argv = confine.wrap_command(
        ["python", "-m", "w"], mode="container", roots=[], workspace=str(ws),
        session_dir=str(session), tmp_dir=str(tmp_path), container_name="n", network="p",
        forbidden_paths=["payroll"],
    )
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]
    target = str(ws.resolve() / "payroll")
    assert f"type=bind,src={target},dst={target},readonly" in mounts
    assert mounts.index(f"type=bind,src={target},dst={target},readonly") > 1  # after workspace


def test_seatbelt_profile_denies_forbidden_writes_after_the_workspace_allow(tmp_path):
    # Executing sandbox-exec is not possible under the CI/dev sandbox (it cannot nest), so the
    # rule ORDER is asserted: in SBPL the last matching rule wins.
    ws, session = _tree(tmp_path)
    target = confine.forbidden_targets(str(ws), ["payroll"])
    profile = confine.seatbelt_profile([], str(ws), str(session), str(tmp_path), target)
    deny = f'(deny file-write* (subpath "{target[0]}"))'
    assert deny in profile
    assert profile.index(deny) > profile.index("(allow file-write*")
    assert "(deny file-write* (subpath" not in confine.seatbelt_profile([], str(ws), "s", "t")


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker")
def test_real_container_readonly_bind_blocks_bypass(tmp_path):
    image = "python:3.13-alpine"  # any Python image proves the mount law; pull if absent
    ws, session = _tree(tmp_path)
    ws = ws.resolve()
    target = ws / "payroll"
    def run(code):
        return subprocess.run(
            ["docker", "run", "--rm", "--cap-drop=ALL", "--security-opt", "no-new-privileges",
             "--user", "10001:10001", "--mount", f"type=bind,src={ws},dst={ws}",
             "--mount", f"type=bind,src={target},dst={target},readonly",
             "--workdir", str(ws), "--entrypoint", "python", image, "-c", code],
            capture_output=True, text=True,
        )
    ws.chmod(0o777)
    for p in (ws / "src", ws / "payroll"):
        p.chmod(0o777)
    (ws / "payroll" / "data.txt").chmod(0o666)
    assert run(BYPASS).returncode != 0
    assert (ws / "payroll" / "data.txt").read_text() == "keep"
    assert run("open('src/ok.txt', 'w').write('x')").returncode == 0


def test_snapshot_detects_change_and_new_file(tmp_path):
    (tmp_path / "pay").mkdir()
    (tmp_path / "pay" / "a.txt").write_text("1")
    before = confine.forbidden_snapshot(str(tmp_path), ["pay"])
    (tmp_path / "pay" / "b.txt").write_text("2")
    assert confine.forbidden_snapshot(str(tmp_path), ["pay"]) != before
    assert confine.forbidden_snapshot(str(tmp_path), ["nope"]) == {}
