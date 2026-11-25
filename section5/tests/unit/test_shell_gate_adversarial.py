"""Session 5 (A-01) — adversarial forbidden command/path attempts against the shell gate.

Decision layer only: ``Popen``, ``confine_argv`` and ``signal`` are stubbed so every row is
deterministic on hosts with and without an OS backend (the real boundary keeps its own rows
in ``test_shell_gate_real_boundary.py``, which skip where no backend exists). Nothing here
proves an OS boundary; it pins what the gate refuses BEFORE a command would run.

Layout: workspace with src/ (the scope), docs/, src2/, private/ (a forbidden path whose name
matches no secret pattern, so the forbidden-path rule alone must catch it), db/a/sensitive/
and a symlink inside src that points at private/.

Strict xfails are the remaining known exceptions (A-01 "every remaining exception has an
owner and documented risk"); each asserts the answer a human would expect and must fail
today. Evidence: docs/briefs/evidence/security/shell-gate-adversarial-2026-11-25.md.
"""

from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[2] / "src" / "harness_sandbox" / "shell_gate.py"
FORBIDDEN = ["private/", "db/*/sensitive/"]


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
def ro_forbidden(gate_mod, monkeypatch):
    """The container's read-only bind mount over forbidden paths, as the gate sees it."""
    monkeypatch.setattr(gate_mod, "unprotected_forbidden", lambda *a, **k: None)
    return gate_mod


@pytest.fixture
def ws(tmp_path):
    for name in ("src", "docs", "src2", "private", "db/a/sensitive"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "src" / "x.txt").write_text("x")
    (tmp_path / "docs" / "y.txt").write_text("y")
    (tmp_path / "src2" / "x.txt").write_text("z")
    (tmp_path / "private" / "p.txt").write_text("p")
    (tmp_path / "db" / "a" / "sensitive" / "init.sql").write_text("s")
    (tmp_path / "setup.py").write_text("")  # a root file a quoted '*.py' must not hit
    (tmp_path / "src" / "plink").symlink_to(tmp_path / "private")
    return tmp_path


def _run(gate_mod, ws, command, scope=("src",), forbidden=()):
    return gate_mod.run({"command": command.replace("{WS}", str(ws)), "cwd": str(ws),
                         "scopePaths": list(scope), "forbiddenPaths": list(forbidden)})


def _code(res: dict) -> str | None:
    return res.get("code") if "denied" in res else None


# --- 1. Glob spellings: sh expands them, so the gate checks every match (G2/G6b). ----------

@pytest.mark.parametrize("command", [
    "cat d?cs/y.txt",           # G2: directory component is a glob
    "cat do*/y.txt",
    "cat [d]ocs/y.txt",
    "cat src/../d*/y.txt",
    "cat /et?/passwd",          # outside the workspace through a glob (/etc/passwd exists on Linux and macOS)
    "grep -rn y *",             # G6b: recursive grep over `*` reaches docs/
    "ls *",
    "cat s*/x.txt",             # src and src2 both match; src2 is outside
    "cat src/p*/p.txt",         # glob through the in-scope symlink to private/
    "ls src/*",                 # one match (src/plink) is a symlink leaving scope
])
def test_glob_reaching_outside_scope_is_denied(gate_mod, ws, command):
    res = _run(gate_mod, ws, command)
    assert _code(res) == "OUT_OF_SCOPE", (command, res)


@pytest.mark.parametrize("command", [
    "cat src/*.txt",
    "ls src/x*",
    "cat s?c/x.txt",            # matches src only (src2 is one character longer)
    "cat {WS}/src/*.txt",
])
def test_glob_staying_inside_scope_passes(gate_mod, ws, command):
    assert _code(_run(gate_mod, ws, command)) is None, command


@pytest.mark.parametrize("command", [
    "find src -name '*.py'",            # quoted: find's pattern, sh leaves it alone
    'grep -E "d.*s" src/x.txt',         # quoted regex
    r"grep d\*s src/x.txt",             # escaped star
    "grep --include=*.py -r x src",     # a flag word is not expanded (no file named so)
    "cat zz*/nothing",                  # no match: sh passes it literally, cat fails
])
def test_glob_chars_that_sh_does_not_expand_are_not_paths(gate_mod, ws, command):
    assert _code(_run(gate_mod, ws, command)) is None, command


def test_unquoted_glob_scan_agrees_with_shlex(gate_mod):
    import shlex

    for command, flags in [
        ("cat d?cs/y.txt", [False, True]),
        ("find src -name '*.py'", [False, False, False, False]),
        ('grep "a b*" src/*', [False, False, True]),
        (r"cat do\*cs", [False, False]),
        ("cat 'do'*", [False, True]),
    ]:
        assert gate_mod._unquoted_globs(command, shlex.split(command)) == flags, command


def test_unquoted_glob_scan_fails_closed_on_disagreement(gate_mod):
    assert gate_mod._unquoted_globs("cat a", ["cat", "b"]) == [True, True]


# --- 2. Forbidden paths: every spelling refused, even with a whole-workspace scope. --------

@pytest.mark.parametrize("command", [
    "cat private/p.txt",
    "cat ./private/p.txt",
    "cat private//p.txt",
    "cat src/../private/p.txt",
    "cat {WS}/private/p.txt",
    "cat pri*/p.txt",
    "cat p?ivate/p.txt",
    "cat [p]rivate/p.txt",
    'cat "pri"vate/p.txt',
    r"cat pri\vate/p.txt",
    "cat src/plink/p.txt",              # symlink inside the workspace -> private/
    "git show HEAD:private/p.txt",
    "python3 -c \"print(open('private/p.txt').read())\"",
    "cat db/a/sensitive/init.sql",
    "cat db/?/sensitive/init.sql",
    "cat db/a/sens*/init.sql",
])
@pytest.mark.parametrize("scope", [("src",), ()], ids=["scope-src", "scope-all"])
def test_forbidden_path_spellings_are_refused(ro_forbidden, ws, command, scope):
    res = _run(ro_forbidden, ws, command, scope=scope, forbidden=FORBIDDEN)
    assert _code(res) == "COMMAND_REFUSED", (command, res)
    assert "forbidden path" in res["denied"], res


@pytest.mark.parametrize("command", ["git status", "ls src", "pwd"])
def test_writable_forbidden_path_refuses_every_command(gate_mod, ws, command):
    # No read-only mount over private/ here: the gate refuses rather than trust the shell.
    res = _run(gate_mod, ws, command, forbidden=["private/"])
    assert _code(res) == "FORBIDDEN_PATH_EXPOSED", (command, res)


# --- 3. Forbidden commands: other programs, interpreters, git escapes, operators. ----------

@pytest.mark.parametrize("command", [
    "sh -c id",
    "/bin/sh -c id",
    "bash -c id",
    "env cat docs/y.txt",
    "FOO=1 cat docs/y.txt",
    "xargs cat",
    "sed -n p docs/y.txt",
    "git -C .. status",
    "git --git-dir=../.git log",
    "git -c core.pager=id log",
    "git push --force origin main",
    "find . -exec cat {} +",
    "find src -execdir id ;",
    "cat /etc/passwd",
    "cat /etc/shadow",
    "cat /SYS/kernel/x",
    "cat <(id)",
    "cat <<EOF",
    "echo hi &",
    "echo $HARNESS_DATABASE_URL",
    "echo ${GITHUB_TOKEN}",
    "cat ~/.ssh/id_rsa",
    "cat src/.env",
])
def test_forbidden_command_is_refused_by_policy(gate_mod, ws, command):
    res = _run(gate_mod, ws, command)
    assert _code(res) == "COMMAND_REFUSED", (command, res)


# The gate only judges tokens that exist (or whose parent dir does), so a /proc path is a real
# target on Linux and a plain word on macOS, which has no procfs. Run the rows where /proc exists.
_needs_procfs = pytest.mark.skipif(
    not Path("/proc/self/environ").exists(), reason="no procfs on this OS (e.g. macOS)")


@pytest.mark.parametrize("command", [
    pytest.param("cat /proc/self/environ", marks=_needs_procfs),
    pytest.param("cat /proc/1/environ", marks=_needs_procfs),
    "cat /etc//passwd",          # slips past the /etc/passwd pattern; scope catches it
    "cat /etc/./passwd",
    "head -c 100 /dev/sda",
    "ls /root",
    "pytest --rootdir=..",
    "git diff --output=../out",
    "grep -f /etc/hosts src/x.txt",
    "cat -- -/../docs/y.txt",
    "ls src/plink",
])
def test_out_of_workspace_or_scope_spelling_is_denied(gate_mod, ws, command):
    res = _run(gate_mod, ws, command)
    assert _code(res) == "OUT_OF_SCOPE", (command, res)


# --- 4. Known exceptions (strict xfail). Owner: security lead (F-05 / A-01). ---------------

def _exception(command: str, risk: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(strict=True, reason=f"A-01 open exception: {command!r} {risk}")


EXCEPTIONS = [
    pytest.param("grep -r y", ("src",), (), marks=_exception(
        "grep -r y", "searches the workspace root (implicit cwd) and reads docs/ outside "
        "scope; no path argument to check"), id="G6a-grep-recursive-no-path"),
    pytest.param("ls -R", ("src",), (), marks=_exception(
        "ls -R", "lists names outside scope through the implicit cwd"), id="ls-recursive-cwd"),
    pytest.param("python3 -c \"print(open('do'+'cs/y.txt').read())\"", ("src",), (),
                 marks=_exception("python3 -c ...", "builds the path at run time; code "
                                  "strings are not parsed (G7i, write side held by M03)"),
                 id="G7i-python-built-path"),
    pytest.param("python3 -c \"print(open('pri'+'vate/p.txt').read())\"", (), FORBIDDEN,
                 marks=_exception("python3 -c ...", "reads a forbidden path; the read-only "
                                  "mount stops writes, not reads (F-05 read semantics)"),
                 id="forbidden-read-through-code"),
    pytest.param("grep -r p .", (), FORBIDDEN, marks=_exception(
        "grep -r p .", "recursion enters forbidden private/; the read-only mount stops "
        "writes, not reads (F-05 read semantics)"), id="forbidden-read-through-recursion"),
    pytest.param("rg --pre=id x src", ("src",), (), marks=_exception(
        "rg --pre=id", "launches a program that is not on the allow-list (still inside the "
        "same OS confinement; python3 is allow-listed anyway)"), id="rg-pre-launches-program"),
    pytest.param("git fetch --upload-pack=id origin", ("src",), (), marks=_exception(
        "git fetch --upload-pack=id", "launches a program that is not on the allow-list "
        "(same confinement)"), id="git-upload-pack-launches-program"),
]


@pytest.mark.parametrize(("command", "scope", "forbidden"), EXCEPTIONS)
def test_known_exception_still_open(ro_forbidden, ws, command, scope, forbidden):
    res = _run(ro_forbidden, ws, command, scope=scope, forbidden=forbidden)
    assert "denied" in res, (command, res)
