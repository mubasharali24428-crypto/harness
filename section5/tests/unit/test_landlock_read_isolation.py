"""S-01: Linux Landlock really denies reads of a forbidden path.

The macOS counterpart is
``test_confinement.py::test_seatbelt_blocks_forbidden_path_even_via_interpreter``;
this is the Linux version of the same promise, exercised against a real
Landlock boundary (these tests only run where the runtime permits it).
"""

import sys

import pytest

from harness_sandbox import confinement
from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.service import SandboxService

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="landlock is linux-only")


@pytest.fixture()
def svc(tmp_path):
    if not confinement.landlock_supported():
        pytest.skip("landlock not usable in this runtime")
    runner = LocalSandboxRunner(
        storage_root=tmp_path / "tasks", confinement="landlock", allow_network=False
    )
    return SandboxService(runner=runner)


async def test_landlock_blocks_forbidden_path_even_via_interpreter(svc, tmp_path):
    """S-01 regression: an allow-listed interpreter must not read a forbidden path."""
    handle = svc.run_task("c-s01", "o/r", "m", ["payroll"])
    ws = tmp_path / "tasks" / "c-s01" / "workspace"
    (ws / "payroll").mkdir(parents=True)
    (ws / "payroll" / "data.txt").write_text("CANARY")

    res = await svc.exec_structured(
        handle, "python3 -c \"print(open('pay'+'roll/data.txt').read())\""
    )
    assert res.exit_code != 0, res.summary
    assert "CANARY" not in res.summary


async def test_landlock_allows_normal_reads_and_toolchain(svc, tmp_path):
    """The boundary must not break the toolchain: workspace files and system
    reads still work, only the forbidden path is denied."""
    handle = svc.run_task("c-ok", "o/r", "m", ["payroll"])
    ws = tmp_path / "tasks" / "c-ok" / "workspace"
    (ws / "payroll").mkdir(parents=True)
    (ws / "payroll" / "data.txt").write_text("CANARY")
    (ws / "readme.md").write_text("hello")

    res = await svc.exec_structured(handle, "cat readme.md")
    assert res.exit_code == 0, res.summary

    # The content really came through: copy it into another workspace file.
    res = await svc.exec_structured(
        handle, "python3 -c \"open('got.txt','w').write(open('readme.md').read())\""
    )
    assert res.exit_code == 0, res.summary
    assert (ws / "got.txt").read_text() == "hello"

    # A system read (the interpreter's own loader path) still works.
    res = await svc.exec_structured(
        handle, "python3 -c \"print(open('/etc/os-release').read()[:4])\""
    )
    assert res.exit_code == 0, res.summary


async def test_landlock_sibling_workspace_is_neither_writable_nor_readable(svc, tmp_path):
    runner = svc.runner
    a = svc.run_task("ta", "o/r", "m", [])
    svc.run_task("tb", "o/r", "m", [])
    b_ws = runner.active_tasks["tb"]["workspace"]
    (b_ws / "b-data.txt").write_text("B-DATA")

    write = await svc.exec_structured(
        a, f"python3 -c \"open('{b_ws}/pwn.txt','w').write('x')\""
    )
    assert write.exit_code != 0
    assert not (b_ws / "pwn.txt").exists()

    read = await svc.exec_structured(
        a, f"python3 -c \"d=open('{b_ws}/b-data.txt').read();open('got.txt','w').write(d)\""
    )
    assert read.exit_code != 0
    assert not (runner.active_tasks["ta"]["workspace"] / "got.txt").exists()


async def test_landlock_still_confines_writes_to_the_workspace(svc, tmp_path):
    handle = svc.run_task("c-w", "o/r", "m", [])
    outside = tmp_path / "outside.txt"
    res = await svc.exec_structured(
        handle, f"python3 -c \"open('{outside}','w').write('x')\""
    )
    assert res.exit_code != 0
    assert not outside.exists()


async def test_without_the_os_boundary_the_same_command_reads_the_file(tmp_path):
    """Non-vacuity control: the string policy alone does NOT stop the read, so
    the Landlock read ruleset is what actually denies the forbidden path."""
    runner = LocalSandboxRunner(storage_root=tmp_path / "tasks", confinement="off")
    plain = SandboxService(runner=runner)
    handle = plain.run_task("c-off", "o/r", "m", ["payroll"])
    ws = tmp_path / "tasks" / "c-off" / "workspace"
    (ws / "payroll").mkdir(parents=True)
    (ws / "payroll" / "data.txt").write_text("CANARY")

    res = await plain.exec_structured(
        handle, "python3 -c \"print(open('pay'+'roll/data.txt').read())\""
    )
    assert res.exit_code == 0, res.summary
