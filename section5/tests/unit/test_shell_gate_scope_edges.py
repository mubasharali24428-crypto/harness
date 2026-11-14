"""Batch 3 C2 — the shell gate's answer to awkward scope-path spellings (test-only).

Scope is ["src"] and cwd is a workspace containing src/, docs/ and src2/. Every
expected value below was measured from ``shell_gate.run`` after reading
``shell_gate.py:60-84`` / ``:122-178`` and ``policy.py:312-380``; it is the
gate's CURRENT answer, not the answer a human would want. Execution is stubbed
(``Popen`` and ``confine_argv`` never run a command) so the decision layer is
deterministic on hosts with and without OS confinement. ``shell_gate.py`` is not
changed by this file.

Fail-open rows are strict xfails: they assert the answer a human would expect
and must fail today, with the exact command and the wrong output in the reason.
All four (flag=path, ~, .., $HOME) were closed by the 2026-11-13/14 argument-level
fix and now assert directly.
"""

from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[2] / "src" / "harness_sandbox" / "shell_gate.py"


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
    monkeypatch.setattr(shell_gate.confinement, "confine_argv",
                        lambda *a, **k: ["/bin/true"])
    monkeypatch.setattr(shell_gate.signal, "signal", lambda *a, **k: None)
    return shell_gate


@pytest.fixture
def ws(tmp_path):
    for name in ("src", "docs", "src2"):
        (tmp_path / name).mkdir()
    (tmp_path / "src" / "x.txt").write_text("x")
    (tmp_path / "docs" / "y.txt").write_text("y")
    (tmp_path / "src2" / "x.txt").write_text("z")
    return tmp_path


def _answer(gate_mod, ws, command):
    res = gate_mod.run({"command": command, "cwd": str(ws), "scopePaths": ["src"]})
    return "denied" in res, res.get("code")


def _xfail(command: str, wrong: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(
        strict=True,
        reason=(f"fail-open: '{command}' can touch a file outside src, but the gate "
                f"returns {wrong} instead of denying it"),
    )


# command, expected (denied, code), expected _scope_hit token; {WS} is the
# workspace path. Rows marked strict xfail state the human-expected answer.
CASES = [
    pytest.param("cat docs/y.txt", (True, "OUT_OF_SCOPE"), "docs/y.txt", id="cat-docs"),
    pytest.param("cat ./docs/y.txt", (True, "OUT_OF_SCOPE"), "./docs/y.txt",
                 id="leading-dot-slash-docs"),
    pytest.param("cat src/../docs/y.txt", (True, "OUT_OF_SCOPE"), "src/../docs/y.txt",
                 id="dotdot-escapes-src"),
    pytest.param("cat src/./x.txt", (False, None), None, id="internal-dot-inside-src"),
    pytest.param('cat "docs/y.txt"', (True, "OUT_OF_SCOPE"), "docs/y.txt",
                 id="double-quoted-docs"),
    pytest.param("cat 'docs/y.txt'", (True, "OUT_OF_SCOPE"), "docs/y.txt",
                 id="single-quoted-docs"),
    pytest.param("cat docs/y.txt src/x.txt", (True, "OUT_OF_SCOPE"), "docs/y.txt",
                 id="mixed-args-first-outside"),
    pytest.param("echo hi > docs/y.txt", (True, "COMMAND_REFUSED"), "docs/y.txt",
                 id="single-redirection"),
    pytest.param("echo hi >> docs/y.txt", (True, "COMMAND_REFUSED"), "docs/y.txt",
                 id="append-redirection"),
    pytest.param("tee docs/y.txt", (True, "COMMAND_REFUSED"), "docs/y.txt", id="tee"),
    pytest.param("cp src/x.txt docs/", (True, "COMMAND_REFUSED"), "docs/", id="cp"),
    pytest.param("mv docs/y.txt src/", (True, "COMMAND_REFUSED"), "docs/y.txt", id="mv"),
    pytest.param("sed -i s/a/b/ docs/y.txt", (True, "COMMAND_REFUSED"), "docs/y.txt",
                 id="sed-in-place"),
    pytest.param("cat --file=docs/y.txt", (True, "OUT_OF_SCOPE"), "docs/y.txt",
                 id="flag-equals-path"),
    pytest.param("cat -- docs/y.txt", (True, "OUT_OF_SCOPE"), "docs/y.txt",
                 id="double-dash-then-path"),
    pytest.param("cat $HOME/x", (True, "OUT_OF_SCOPE"), "$HOME/x", id="env-var-path"),
    pytest.param("cat ~/x", (True, "OUT_OF_SCOPE"), "~/x", id="tilde-path"),
    pytest.param("cat /etc/passwd", (True, "COMMAND_REFUSED"), "/etc/passwd",
                 id="etc-passwd"),
    pytest.param("ls ..", (True, "OUT_OF_SCOPE"), "..", id="parent-directory"),
    pytest.param("cd .. && cat docs/y.txt", (True, "COMMAND_REFUSED"), "..",
                 id="chained-cd"),
    pytest.param("cat {WS}/docs/y.txt", (True, "OUT_OF_SCOPE"), "{WS}/docs/y.txt",
                 id="absolute-in-workspace-outside-src"),
    pytest.param("cat {WS}/src/x.txt", (False, None), None,
                 id="absolute-inside-src"),
    pytest.param("cat docs/../src/x.txt", (False, None), None,
                 id="dotdot-resolves-into-src"),
    pytest.param("cat src2/x.txt", (True, "OUT_OF_SCOPE"), "src2/x.txt",
                 id="src2-prefix-lookalike"),
    pytest.param("", (True, "COMMAND_REFUSED"), None, id="empty-command"),
]


@pytest.mark.parametrize(("command", "expected", "hit"), CASES)
def test_scope_edge_answer(gate_mod, ws, command, expected, hit):
    rendered = command.replace("{WS}", str(ws))
    expected_hit = hit.replace("{WS}", str(ws)) if hit else hit
    assert (_answer(gate_mod, ws, rendered),
            gate_mod._scope_hit(rendered, str(ws), ["src"])) == (expected, expected_hit)


@pytest.mark.parametrize(("command", "token"), [
    pytest.param("cat docs/y.txt", "docs/y.txt", id="deny-side"),
    pytest.param("cat src/./x.txt", None, id="allow-side"),
    pytest.param("cat src2/x.txt", "src2/x.txt", id="prefix-lookalike"),
])
def test_scope_violation_agrees_with_scope_hit(gate_mod, ws, command, token):
    """scope_violation is the message form of _scope_hit; both are read-only."""
    message = gate_mod.scope_violation(command, str(ws), ["src"])
    assert (message is None) is (token is None)
    if token is not None:
        assert token in message


def test_scope_roots_is_the_write_set_for_this_fixture(gate_mod, ws):
    assert gate_mod.scope_roots(str(ws), ["src"]) == [ws.resolve() / "src"]
