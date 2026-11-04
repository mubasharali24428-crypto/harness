"""F6r — OS-level confinement for LocalSandboxRunner.

macOS: Seatbelt via /usr/bin/sandbox-exec (executes REAL profiles in these tests).
Linux: Landlock via ctypes (skipped on darwin; best-effort at runtime).
Policy: reads broadly allowed, writes confined to the task workspace (+/tmp),
network configurable. Interpreters stay allow-listed — this layer is what makes
them safe to run.
"""

import shlex
import shutil
import sys
from pathlib import Path

import pytest

from harness_sandbox import confinement
from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.service import SandboxService

pytestmark = pytest.mark.skipif(
    not confinement.seatbelt_available(),
    reason="Seatbelt confinement tests require macOS sandbox-exec",
)

# The interpreter running the tests: macOS hosts do not ship a bare `python`.
PY = shlex.quote(sys.executable)


def _svc(tmp_path, **kw):
    runner = LocalSandboxRunner(storage_root=tmp_path / "tasks", **kw)
    return SandboxService(runner=runner)


@pytest.mark.asyncio
async def test_seatbelt_runs_allowed_command(tmp_path):
    svc = _svc(tmp_path, confinement="seatbelt")
    handle = svc.run_task("c1", "o/r", "m", [])
    res = await svc.exec_structured(handle, "echo seatbelt-ok")
    assert res.status == "passed"
    assert res.exit_code == 0


@pytest.mark.asyncio
async def test_seatbelt_blocks_write_outside_workspace(tmp_path):
    # Must live OUTSIDE every allowed subpath (ws, $TMPDIR): pytest's tmp_path
    # sits under TMPDIR, so a temp-adjacent "outside" would be writable legally.
    outside = Path.home() / ".harness-f6r-blocked"
    outside.mkdir(exist_ok=True)
    victim = outside / "pwned.txt"
    victim.unlink(missing_ok=True)
    try:
        svc = _svc(tmp_path, confinement="seatbelt", allow_network=False)
        handle = svc.run_task("c2", "o/r", "m", [])
        res = await svc.exec_structured(
            handle, f"{PY} -c \"open('{victim}','w').write('x')\""
        )
        assert res.exit_code != 0, f"write outside must fail: {res.diagnostics}"
        assert not victim.exists()
    finally:
        shutil.rmtree(outside, ignore_errors=True)


@pytest.mark.asyncio
async def test_seatbelt_allows_write_inside_workspace(tmp_path):
    svc = _svc(tmp_path, confinement="seatbelt")
    handle = svc.run_task("c3", "o/r", "m", [])
    res = await svc.exec_structured(
        handle, f"{PY} -c \"open('inside.txt','w').write('x')\""
    )
    assert res.exit_code == 0
    assert (tmp_path / "tasks" / "c3" / "workspace" / "inside.txt").exists()


@pytest.mark.asyncio
async def test_off_mode_has_no_confinement(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    svc = _svc(tmp_path, confinement="off")
    handle = svc.run_task("c4", "o/r", "m", [])
    victim = outside / "fine.txt"
    res = await svc.exec_structured(handle, f"{PY} -c \"open('{victim}','w').write('x')\"")
    assert res.exit_code == 0
    assert victim.exists()


def test_auto_mode_resolves_on_this_platform(tmp_path):
    runner = LocalSandboxRunner(storage_root=tmp_path / "t5")
    mode = runner._resolve_confinement()
    if sys.platform == "darwin":
        assert mode == "seatbelt"


@pytest.mark.skipif(sys.platform == "darwin", reason="landlock is linux-only")
def test_landlock_module_imports():
    from harness_sandbox import confinement  # noqa: F401


@pytest.mark.asyncio
async def test_seatbelt_blocks_forbidden_path_even_via_interpreter(tmp_path):
    """S-01 regression: allow-listed python must not read a forbidden path."""
    svc = _svc(tmp_path, confinement="seatbelt", allow_network=False)
    handle = svc.run_task("c-s01", "o/r", "m", ["payroll"])
    ws = tmp_path / "tasks" / "c-s01" / "workspace"
    (ws / "payroll").mkdir(parents=True)
    (ws / "payroll" / "data.txt").write_text("CANARY")

    res = await svc.exec_structured(
        handle, f"{PY} -c \"print(open('pay'+'roll/data.txt').read())\""
    )
    assert res.exit_code != 0
    assert "CANARY" not in res.summary


def test_write_roots_replace_the_workspace_in_both_backends(tmp_path, monkeypatch):
    """M03: a narrower intent scope becomes the boundary's write set."""
    import json

    from harness_sandbox import confinement as c

    scope = tmp_path / "src"
    scope.mkdir()
    profile = c.seatbelt_profile(tmp_path, False, None, tmp_path / "t", write_roots=[scope])
    allow = next(line for line in profile.splitlines() if line.startswith("(allow file-write*"))
    assert f'(subpath "{scope.resolve()}")' in allow
    assert f'(subpath "{tmp_path.resolve()}")' not in allow
    assert f'(subpath "{tmp_path.resolve()}")' in c.seatbelt_profile(tmp_path, False)

    monkeypatch.setattr(c, "seatbelt_available", lambda: False)
    monkeypatch.setattr(c, "landlock_supported", lambda: True)
    argv = c.confine_argv(["/bin/sh", "-c", "x"], workspace=tmp_path, tmpdir=tmp_path / "t",
                          write_roots=[scope])
    assert json.loads(argv[8]) == [str(scope)] and argv[9:] == ["/bin/sh", "-c", "x"]
    plain = c.confine_argv(["/bin/sh"], workspace=tmp_path, tmpdir=tmp_path / "t")
    assert json.loads(plain[8]) is None and plain[9:] == ["/bin/sh"]
