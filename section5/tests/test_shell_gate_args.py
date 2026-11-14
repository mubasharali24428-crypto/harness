"""Part 52 left items: argument-level path checks, one program per call (2026-11-13).

Assert-style cases for the gate's decision layer only: ``Popen`` and ``confine_argv``
are stubbed so the answers are deterministic on hosts with and without OS confinement
(the real boundary keeps its own rows in ``tests/unit/test_shell_gate.py`` and
``tests/unit/test_shell_gate_real_boundary.py``).
"""

from pathlib import Path

import pytest

# tests/ (not tests/unit/): parents[1] is section5, where src/harness_sandbox lives.
GATE = Path(__file__).resolve().parents[1] / "src" / "harness_sandbox" / "shell_gate.py"


class _NeverRuns:
    """Stand-in for the confined child; these tests measure the decision only."""

    pid = 1
    returncode = 0

    def communicate(self, timeout=None):
        return "", None


@pytest.fixture
def gate_mod(monkeypatch):
    monkeypatch.syspath_prepend(str(GATE.parent))
    import shell_gate

    monkeypatch.setattr(shell_gate.subprocess, "Popen", lambda *a, **k: _NeverRuns())
    monkeypatch.setattr(shell_gate.confinement, "confine_argv", lambda *a, **k: ["/bin/true"])
    monkeypatch.setattr(shell_gate.signal, "signal", lambda *a, **k: None)
    return shell_gate


@pytest.fixture
def ws(tmp_path):
    for name in ("src", "docs"):
        (tmp_path / name).mkdir()
    (tmp_path / "src" / "x.txt").write_text("x")
    (tmp_path / "docs" / "y.txt").write_text("y")
    return tmp_path


def _run(gate_mod, ws, command, scope):
    return gate_mod.run({"command": command, "cwd": str(ws), "scopePaths": scope})


@pytest.mark.parametrize(
    "command",
    [
        "echo hi; id",
        "git status && python -c 1",
        "git status || python -c 1",
        "echo hi | cat",
        "echo `id`",
        "echo $(id)",
        "echo hi\nid",
    ],
)
def test_one_program_per_call_chained_commands_are_denied(gate_mod, ws, command):
    res = _run(gate_mod, ws, command, scope=["src"])
    assert res.get("code") == "COMMAND_REFUSED", (command, res)


def test_dotdot_argument_leaving_the_workspace_is_denied(gate_mod, ws):
    res = _run(gate_mod, ws, "cat ../../etc/hosts", scope=["."])
    assert res.get("code") == "OUT_OF_SCOPE", res
    assert "'../../etc/hosts'" in res["denied"], res


def test_dotdot_etc_passwd_is_denied(gate_mod, ws):
    # The brief's literal case: this spelling also matches policy's blocked
    # /etc/passwd pattern, so a refusal from either layer is a pass.
    assert "denied" in _run(gate_mod, ws, "cat ../../etc/passwd", scope=["."])


def test_tilde_argument_is_denied(gate_mod, ws):
    res = _run(gate_mod, ws, "cat ~/x", scope=["src"])
    assert res.get("code") == "OUT_OF_SCOPE", res
    assert "'~/x'" in res["denied"], res


def test_parent_directory_argument_is_denied(gate_mod, ws):
    res = _run(gate_mod, ws, "ls ..", scope=["src"])
    assert res.get("code") == "OUT_OF_SCOPE", res
    assert "'..'" in res["denied"], res


def test_flag_value_path_is_denied(gate_mod, ws):
    res = _run(gate_mod, ws, "cat --file=docs/y.txt", scope=["src"])
    assert res.get("code") == "OUT_OF_SCOPE", res
    assert "'docs/y.txt'" in res["denied"], res


def test_allowed_command_inside_scope_still_passes(gate_mod, ws):
    assert _run(gate_mod, ws, "cat src/x.txt", scope=["src"]) == {
        "exitCode": 0,
        "output": "",
    }


def test_dotdot_that_resolves_back_into_scope_still_passes(gate_mod, ws):
    res = _run(gate_mod, ws, "cat docs/../src/x.txt", scope=["src"])
    assert "denied" not in res, res


def test_empty_command_is_denied_as_before(gate_mod, ws):
    res = _run(gate_mod, ws, "", scope=["src"])
    assert res.get("code") == "COMMAND_REFUSED", res
    assert res["denied"] == "Empty command is not allowed.", res


@pytest.mark.parametrize("command", ["grep -E a/../b src/x.txt", "sed -n s/../x/p src/x.txt"])
def test_dotdot_inside_a_pattern_is_not_a_path(gate_mod, ws, command):
    # `a` does not exist, so sh cannot walk `a/..`: this is a regex, not a path.
    assert gate_mod._scope_hit(command, str(ws), ["src"]) is None


def test_dotdot_after_an_existing_dir_is_still_checked(gate_mod, ws):
    assert gate_mod._scope_hit("cat docs/../../x", str(ws), ["src"]) == "docs/../../x"


@pytest.mark.parametrize("command,hit", [("cat -o/tmp/x src/x.txt", "/tmp/x"),
                                         ("cat -I../inc src/x.txt", "../inc")])
def test_short_flag_with_attached_path_is_checked(gate_mod, ws, command, hit):
    assert gate_mod._scope_hit(command, str(ws), ["src"]) == hit


def test_combined_short_flags_are_not_paths(gate_mod, ws):
    (ws / "a").write_text("")  # a file named like the flag letter must not trip it
    assert gate_mod._scope_hit("ls -la src", str(ws), ["src"]) is None


def test_env_var_path_is_expanded_and_denied(gate_mod, ws, monkeypatch):
    monkeypatch.setenv("HOME", str(ws.parent))
    res = _run(gate_mod, ws, "cat $HOME/x", scope=["src"])
    assert res.get("code") == "OUT_OF_SCOPE" and "'$HOME/x'" in res["denied"], res


def test_env_var_into_scope_still_passes(gate_mod, ws, monkeypatch):
    monkeypatch.setenv("WS_SRC", str(ws / "src"))
    assert gate_mod._scope_hit("cat $WS_SRC/x.txt", str(ws), ["src"]) is None


@pytest.mark.parametrize("command", ["cat $NOT_SET_HERE/x", "cat ${HOME:-/etc}/passwd"])
def test_var_the_gate_cannot_resolve_is_denied(gate_mod, ws, command):
    assert gate_mod._scope_hit(command, str(ws), ["src"]) is not None, command
