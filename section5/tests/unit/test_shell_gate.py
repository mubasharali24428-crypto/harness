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


def test_writable_forbidden_path_refuses_to_run(tmp_path):
    (tmp_path / "keep.txt").write_text("x")
    res = gate(tmp_path, "python3 -c pass", forbiddenPaths=["keep.txt"])
    assert "not read-only" in res["denied"]


def test_missing_forbidden_path_is_not_checked(tmp_path):
    res = gate(tmp_path, "python3 -c pass", forbiddenPaths=["absent.txt"])
    assert "not read-only" not in res.get("denied", "")


@requires_confinement
def test_scope_is_the_write_set_so_code_strings_cannot_leave_it(tmp_path):
    """M03: the M01 bypass. The string check skips code strings; the OS boundary does not."""
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    for code in ("open('docs/leak.txt','w').write('x')", "open('do'+'cs/leak2.txt','w')",
                 "open(__import__('os').path.join('docs','leak3.txt'),'w')", "open('top.txt','w')"):
        res = gate(tmp_path, f'python3 -c "{code}"', scopePaths=["src"])
        assert res.get("exitCode") not in (0, None), (code, res)
    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == []
    ok = gate(tmp_path, "python3 -c \"open('src/ok.txt','w').write('x')\"", scopePaths=["src"])
    assert ok["exitCode"] == 0 and (tmp_path / "src" / "ok.txt").read_text() == "x"


def test_scope_roots(tmp_path):
    sys.path.insert(0, str(GATE.parent))
    try:
        import shell_gate
    finally:
        sys.path.pop(0)
    (tmp_path / "src").mkdir()
    (tmp_path / "away").symlink_to(tmp_path.parent)
    real = tmp_path.resolve()
    assert shell_gate.scope_roots(str(tmp_path), []) is None
    assert shell_gate.scope_roots(str(tmp_path), ["src", "."]) is None  # whole workspace
    assert shell_gate.scope_roots(str(tmp_path), ["src/", "missing", "away"]) == [real / "src"]


def test_denials_carry_a_code_an_alternative_and_the_scope_to_ask_for(tmp_path):
    """W27: every refusal says what to do instead; only a scope miss names a path to request,
    and no alternative suggests retrying, bypassing, or another model."""
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    miss = gate(tmp_path, "ls docs", scopePaths=["src"])
    assert miss["code"] == "OUT_OF_SCOPE" and miss["requestScope"] == "docs"
    assert "src" in miss["alternative"] and "add it to this run's scope" in miss["alternative"]
    assert miss["scopeRequest"] == {"path": "docs", "grants": False}
    cmd = gate(tmp_path, "git push")
    assert cmd["code"] == "COMMAND_REFUSED" and cmd["alternative"] and "requestScope" not in cmd
    for res in (miss, cmd):
        assert not any(w in res["alternative"].lower() for w in ("retry", "try again", "model"))


@requires_confinement
def test_a_timeout_is_marked_and_is_not_a_denial(tmp_path):
    """W27: a slow command says so; it carries no denial code and no scope request."""
    res = gate(tmp_path, 'python3 -c "import time; time.sleep(5)"', timeoutSeconds=1)
    assert res["timedOut"] is True and res["exitCode"] is None
    assert "denied" not in res and "scopeRequest" not in res
