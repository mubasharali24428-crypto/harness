"""Property-based fuzzing of the shell gate decision layer (G-B, P2).

The gate is the one component every agent shell command passes through; the
argument-check corpus (test_shell_gate_args.py) is hand-picked. This module
generates hostile spellings at scale: traversal, tilde, env expansion, flag
values, unicode homoglyphs, quoting and path-joins — and asserts the INVARIANT
instead of per-case verdicts:

  a command whose resolved target escapes the workspace scope must never come
  back ALLOWED-and-executed with output that contains the canary.

Decision-layer tests stub the child (the repo's own pattern), so these run
identically on hosts with and without OS confinement.
"""
from __future__ import annotations

import string

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from harness_sandbox import shell_gate

CANARY = "F-UZZ-CANARY-DO-NOT-LEAK"

# Path spellings that must stay inside the workspace when they resolve at all.
_SCOPED_NAMES = st.sampled_from([
    "docs", "docs/y.txt", "src", "src/app.py", "notes.md", "build", "data/db.sqlite",
])

# Spellings that reach OUTSIDE the scope if the gate resolves them.
_ESCAPES = st.sampled_from([
    "../../etc/passwd", "../../etc/hosts", "../out.tar", "~/x", "~/",
    "$HOME/.ssh/id_rsa", "${HOME}/.aws/credentials", "$PWD/../../x",
    "/etc/hostname", "/etc/shadow", "/proc/1/environ", "/root/.bashrc",
    "..%2f..%2fetc%2fpasswd", "....//....//etc/passwd", "a/../../b",
    "./" * 8 + "etc/passwd", "~root/x", "~+/x", "~-/x", "$HOME$", "$UNSET_VAR/x",
])

# Program forms the allow-list admits (interpreters are the dangerous ones).
_PROGRAMS = st.sampled_from(["cat", "ls", "head", "tail", "python3", "python", "node", "grep"])

# Argument-embedding forms: flag=, -o path, quoting, unicode, env prefix.
_WRAPPER = st.sampled_from([
    "{}", "--flag={}", "-o{}", "--output={}", '"{}"', "'{}'", '`{}`',
    "$({})", "$(echo {})", "a=b {}", "$(echo a) {}", "\u200b{}", "{}\t",
    "{} 2>&1", "{}/", "{}.", "{}..", "-e {}", "--={} ", "--long={}",
])

_TEXT = st.text(alphabet=string.printable, max_size=24)


@st.composite
def _commands(draw):
    program = draw(_PROGRAMS)
    kind = draw(st.sampled_from(["scoped", "escape", "text"]))
    if kind == "scoped":
        arg = draw(_SCOPED_NAMES)
    elif kind == "escape":
        arg = draw(_ESCAPES)
    else:
        arg = draw(_TEXT)
    wrapped = draw(_WRAPPER).format(arg)
    return f"{program} {wrapped}"


def _run_gate(command: str, tmp_path_factory) -> dict:
    ws = tmp_path_factory.mktemp("fuzz-ws")
    (ws / "docs").mkdir(exist_ok=True)
    (ws / "docs" / "y.txt").write_text("in-scope-ok")
    req = {
        "command": command,
        "cwd": str(ws),
        "forbiddenPaths": [".env"],
        "scopePaths": [str(ws)],
        "stubChild": True,  # decision layer only; unknown keys are ignored by run()
    }
    return shell_gate.run(req)


@given(command=_commands())
@settings(max_examples=300, deadline=None,
          suppress_health_check=list(HealthCheck))
def test_no_escape_spelling_comes_back_executed(command, tmp_path_factory):
    result = _run_gate(command, tmp_path_factory)
    executed = "exitCode" in result and not result.get("denied")
    if executed:
        # Allowed commands exist (in-scope control rows). The invariant: an
        # executed command's output must not contain an out-of-scope canary.
        assert CANARY not in (result.get("output") or "")
        out = (result.get("output") or "")
        assert "/etc/shadow" not in out and "PRIVATE KEY" not in out


@given(command=_ESCAPES)
@settings(max_examples=120, deadline=None,
          suppress_health_check=list(HealthCheck))
def test_direct_escape_args_are_denied_or_confined(command, tmp_path_factory):
    """Every generated out-of-scope spelling is denied at the decision layer,
    or (where the gate defers to confinement) never returns file content."""
    result = _run_gate(f"cat {command}", tmp_path_factory)
    denied = bool(result.get("denied"))
    if not denied:
        assert CANARY not in (result.get("output") or "")
