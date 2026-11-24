"""Task 8: `python -c` source is parsed (never executed) before the sandbox may run it."""

import asyncio

import pytest

from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.output_parser import _LOCATION_RE
from harness_sandbox.policy import (
    MAX_INLINE_SOURCE,
    SecurityPolicyViolationError,
    validate_command,
)


def test_valid_minimal_source_passes():
    validate_command("python3 -c 'print(1 + 1)'")


@pytest.mark.parametrize("src", ["def (", "x = = 1", "import\x00os", "(" * 300 + ")" * 300])
def test_bad_or_hostile_source_is_refused_with_a_diagnostic(src):
    with pytest.raises(SecurityPolicyViolationError) as e:
        validate_command(f"python -c {src!r}")
    msg = str(e.value)
    assert "nothing was executed" in msg and "Safe alternative" in msg
    m = _LOCATION_RE.match(msg)  # same path:line:col: error: shape output_parser reads
    assert m and m["path"] == "<python -c>" and m["kind"] == "error"


def test_oversize_payload_refused():
    with pytest.raises(SecurityPolicyViolationError, match="over"):
        validate_command(f"python3 -c '{'x=1;' * (MAX_INLINE_SOURCE // 4 + 1)}'")


def test_dash_c_belonging_to_a_module_or_script_is_not_python_source():
    validate_command("python -m pytest -c pytest.ini")
    validate_command("python3 tool.py -c 'not python ('")


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("HARNESS_PREEXEC_VALIDATE", "0")
    validate_command("python -c 'def ('")


def test_drill_invalid_source_never_reaches_a_process(tmp_path, monkeypatch):
    runner = LocalSandboxRunner(storage_root=tmp_path / "sb", confinement="none")
    handle = runner.create_task("pre-1", "main", "main", forbidden_paths=[".env"])
    spawned = []

    async def no_spawn(*a, **k):
        spawned.append(a)
        raise AssertionError("must not spawn")

    monkeypatch.setattr(runner, "_spawn", no_spawn)
    with pytest.raises(SecurityPolicyViolationError):
        asyncio.run(runner.exec_structured(handle, "python3 -c 'def ('"))
    assert spawned == []
    runner.destroy(handle)
