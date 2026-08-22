"""F6r — OS-level confinement for LocalSandboxRunner.

macOS: Seatbelt via /usr/bin/sandbox-exec (executes REAL profiles in these tests).
Linux: Landlock via ctypes (skipped on darwin; best-effort at runtime).
Policy: reads broadly allowed, writes confined to the task workspace (+/tmp),
network configurable. Interpreters stay allow-listed — this layer is what makes
them safe to run.
"""

import shutil
import sys
from pathlib import Path

import pytest

from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.service import SandboxService

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("sandbox-exec") is None,
    reason="Seatbelt confinement tests require macOS sandbox-exec",
)


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
            handle, f"python -c \"open('{victim}','w').write('x')\""
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
        handle, "python -c \"open('inside.txt','w').write('x')\""
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
    res = await svc.exec_structured(handle, f"python -c \"open('{victim}','w').write('x')\"")
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
