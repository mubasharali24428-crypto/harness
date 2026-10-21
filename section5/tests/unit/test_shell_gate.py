"""shell_gate.py: the engine bash tool's Section 5 gate, run standalone as the worker runs it."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness_sandbox import confinement

GATE = Path(__file__).resolve().parents[2] / "src" / "harness_sandbox" / "shell_gate.py"

requires_confinement = pytest.mark.skipif(
    not (confinement.seatbelt_available() or confinement.landlock_supported()),
    reason="no OS confinement on this host",
)


def gate(cwd: Path, command: str, **extra) -> dict:
    # -I and a foreign cwd: the gate must load its siblings by path, not via the package.
    out = subprocess.run(
        [sys.executable, "-I", str(GATE)],
        input=json.dumps({"command": command, "cwd": str(cwd), **extra}),
        capture_output=True, text=True, cwd="/", timeout=60, check=True,
    )
    return json.loads(out.stdout)


@pytest.mark.parametrize(
    "command",
    [
        "curl https://example.com",
        "echo hi; id",
        "git status && python -c 1",
        "echo $(id)",
        "cat .env",
        "echo x > out.txt",
        "git push",
    ],
)
def test_policy_denials(tmp_path, command):
    assert "denied" in gate(tmp_path, command)


def test_forbidden_path_denied(tmp_path):
    res = gate(tmp_path, "cat vendor/lib.py", forbiddenPaths=["vendor/"])
    assert "forbidden path" in res["denied"]


def test_scope_paths(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    for command in ("ls docs", "ls .", "ls src/../docs", "find docs/new.txt"):
        assert "outside the run intent" in gate(tmp_path, command, scopePaths=["src"])["denied"]


@requires_confinement
def test_bare_words_and_code_strings_are_not_scope_checked(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    for command in ("echo hello", "python3 -c \"print('docs/x')\"", "ls src/new.txt"):
        assert "denied" not in gate(tmp_path, command, scopePaths=["src"])


@requires_confinement
def test_allowed_command_runs_in_workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.txt").write_text("hello\n")
    res = gate(tmp_path, "cat src/a.txt", scopePaths=["src"])
    assert res == {"exitCode": 0, "output": "hello\n"}
    assert gate(tmp_path, "ls missing")["exitCode"] != 0


@requires_confinement
def test_confinement_blocks_writes_outside_workspace(tmp_path):
    ws, outside = tmp_path / "ws", tmp_path / "outside.txt"
    ws.mkdir()
    res = gate(ws, f"python3 -c \"open('{outside}', 'w').write('x')\"")
    assert res["exitCode"] != 0
    assert not outside.exists()


@requires_confinement
def test_environment_is_sanitized(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-not-leak")
    res = gate(tmp_path, "python3 -c \"import os; print(os.environ.get('DEEPSEEK_API_KEY'))\"")
    assert res["output"].strip() == "None"
