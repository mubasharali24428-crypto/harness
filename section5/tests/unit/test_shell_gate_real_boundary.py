"""Batch 5 D2 - real-boundary characterisation for ``shell_gate.run`` (test-only).

Runs the real gate in a throw-away workspace with a canary beside it. Nothing is
stubbed: ``Popen``, ``confine_argv`` and ``signal`` all run as shipped. Every row
reuses the ``requires_confinement`` skip from ``test_shell_gate.py``; on a host
with no OS backend the rows skip rather than running unconfined. Fixture paths
are created with ``mktemp -d`` under /private/tmp and removed at teardown.

The private hand-back holds the per-row measurements and the findings. This file
contains only assertions and the one-line xfail reasons the rules allow.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness_sandbox import confinement
from tests.unit.test_shell_gate import gate, requires_confinement

pytestmark = requires_confinement


def runtime() -> dict:
    if confinement.seatbelt_available():
        backend = "seatbelt"
    elif confinement.landlock_supported():
        backend = "landlock"
    else:
        backend = "none"
    return {"sys.platform": sys.platform, "backend": backend}


def layer(res: dict) -> str:
    code = res.get("code")
    if code == "COMMAND_REFUSED":
        return "policy refusal"
    if code == "OUT_OF_SCOPE":
        return "arg-level denial"
    if code == "FORBIDDEN_PATH_EXPOSED":
        return "policy refusal (forbidden path)"
    if code == "CONFINEMENT_UNAVAILABLE":
        return "confinement unavailable"
    if "denied" in res:
        return f"other denial: {code}"
    if res.get("exitCode") not in (0, None):
        return "OS-level failure"
    return "allow"


def diag(res: dict, marker: str) -> str:
    return json.dumps({
        "denied": res.get("denied"), "code": res.get("code"),
        "exitCode": res.get("exitCode"),
        "marker_in_output": marker in str(res.get("output", "")),
        "layer": layer(res), **runtime(),
    }, sort_keys=True)


def render(template: str, b) -> str:
    return (template.replace("{CANARY}", str(b.canary))
            .replace("{CANARY_NAME}", b.canary.name)
            .replace("{DOCS_MARKER}", b.docs_marker))


@pytest.fixture
def boundary():
    base = Path(tempfile.mkdtemp(prefix="hy4-b5-", dir="/private/tmp"))
    ws = base / "ws"
    ws.mkdir()
    # A sibling of the workspace, never inside it; the name matches no
    # policy.SECRET_PATTERNS entry, so name filtering cannot mask the boundary.
    canary = Path(subprocess.check_output(
        ["mktemp", "-d", str(base / "hy4-canary.XXXX")], text=True).strip())
    for name in ("src", "docs", "src2"):
        (ws / name).mkdir()
    canary_marker = "CANARY-" + uuid.uuid4().hex
    docs_marker = "DOCS-" + uuid.uuid4().hex
    (ws / "src" / "x.txt").write_text("x\n")
    (ws / "docs" / "y.txt").write_text(docs_marker + "\n")
    (ws / "src2" / "x.txt").write_text("z\n")
    (canary / "plain-notes.txt").write_text(canary_marker + "\n")
    (canary / "id_rsa_canary").write_text(canary_marker + "\n")
    # W6 needs a throw-away repository inside the workspace.
    git = subprocess.run(["git", "init", "-q", str(ws)], capture_output=True,
                         text=True, check=False)
    yield SimpleNamespace(base=base, ws=ws, canary=canary,
                          canary_marker=canary_marker, docs_marker=docs_marker,
                          git_available=git.returncode == 0)
    shutil.rmtree(base, ignore_errors=True)


def target(b, spelling: str) -> Path:
    p = Path(render(spelling, b))
    return p if p.is_absolute() else (b.ws / p).resolve()


# Group W: no file may be left behind outside src. {CANARY} is rendered to the
# throw-away canary directory described in the fixture.
W_ROWS = [
    pytest.param("W1", "python3 -c \"open('src/new.txt','w').write('ok')\"",
                 ["src"], "src/new.txt", True, None, id="W1-write-inside-scope-allowed"),
    pytest.param("W2", "python3 -c \"open('docs/new.txt','w').write('ok')\"",
                 ["src"], "docs/new.txt", False, None, id="W2-write-docs-through-code"),
    pytest.param("W3", "python3 -c \"open('../escape.txt','w').write('ok')\"",
                 ["src"], "../escape.txt", False, None, id="W3-write-parent-through-code"),
    pytest.param("W4", "python3 -c \"open('{CANARY}/write.txt','w').write('ok')\"",
                 ["src"], "{CANARY}/write.txt", False, None,
                 id="W4-write-canary-through-code"),
    pytest.param("W5", "echo hi > docs/y.txt", ["src"], "docs/y.txt", True,
                 "COMMAND_REFUSED", id="W5-redirect-refused-by-policy"),
    pytest.param("W6", "git diff --output=docs/out.txt", ["src"], "docs/out.txt",
                 False, None, id="W6-git-output-option"),
    # scopeTargets/scope_roots drop a scope path that does not exist yet, so the
    # write set is empty: fail-closed (docs/decisions/2026-11-05-delegated-scope-existing-paths.md).
    pytest.param("W7", "python3 -c \"open('newdir/f.txt','w').write('ok')\"",
                 ["newdir"], "newdir/f.txt", False, None, id="W7-missing-scope-path"),
]


@pytest.mark.parametrize(("rid", "command", "scope", "spelling", "expect_created", "code"), W_ROWS)
def test_write_boundary(boundary, rid, command, scope, spelling, expect_created, code):
    before = (boundary.ws / "docs" / "y.txt").read_text()
    res = gate(boundary.ws, render(command, boundary), scopePaths=scope)
    created = target(boundary, spelling).exists()
    d = diag(res, boundary.docs_marker)
    assert created is expect_created, f"{rid}: created={created} {d}"
    assert (boundary.ws / "docs" / "y.txt").read_text() == before, f"{rid}: docs/y.txt changed {d}"
    assert boundary.docs_marker not in str(res.get("output", "")), f"{rid}: {d}"
    if code:
        assert res.get("code") == code, f"{rid}: {d}"
    if rid == "W1":
        assert res.get("exitCode") == 0 and target(boundary, spelling).read_text() == "ok", d
    if rid == "W6" and not boundary.git_available:
        assert res.get("denied") or res.get("exitCode") not in (0, None), f"{rid}: {d}"


def read_xfail(command: str, wrong: str):
    return pytest.mark.xfail(
        sys.platform == "darwin", strict=True,
        reason=(f"rule 7: {command!r} reads outside the workspace but macOS returns "
                f"denied=False, exitCode=0 ({wrong})"))


# Group R: a scoped run must not read outside the workspace, nor an in-workspace
# file outside scope. {CANARY}/{CANARY_NAME} are rendered by the fixture.
R_ROWS = [
    pytest.param("R1", "cat docs/y.txt", ["src"], False, None,
                 id="R1-in-workspace-outside-scope-pins-arg-scan"),
    pytest.param("R2", "cat {CANARY}/plain-notes.txt", ["src"], False,
                 "canary marker in output", id="R2-absolute-canary-read"),
    pytest.param("R3", "ls {CANARY}", ["src"], False,
                 "canary directory listed", id="R3-absolute-canary-list"),
    pytest.param("R4", "ls ..", ["src"], False,
                 "workspace parent listed", id="R4-parent-directory-list"),
    pytest.param("R5", "cat ../{CANARY_NAME}/plain-notes.txt", ["src"], False,
                 "canary marker in output", id="R5-relative-canary-read"),
    pytest.param("R6", "cat ~/plain-notes.txt", ["src"], True,
                 "canary marker in output", id="R6-tilde-with-canary-home"),
    pytest.param("R7", "cat $HOME/plain-notes.txt", ["src"], True,
                 "canary marker in output", id="R7-home-variable-with-canary-home"),
    pytest.param("R8", "cat {CANARY}/id_rsa_canary", ["src"], False, None,
                 id="R8-secret-looking-name-refused-by-policy"),
]

_R_FAIL_OPEN = {"R2", "R3", "R4", "R5", "R6", "R7"}


def r_params():
    out = []
    for p in R_ROWS:
        rid, command, scope, home, wrong = p.values
        marks = [read_xfail(command, wrong)] if rid in _R_FAIL_OPEN else []
        # R6/R7 set HOME to the canary for this test only; monkeypatch restores it.
        out.append(pytest.param(rid, command, scope, home, id=p.id, marks=marks))
    return out


def test_boundary_runtime_is_recorded():
    """Record sys.platform and the backend for R4 and every platform-dependent row.

    This test does not execute a command, so it runs even where confinement is
    unavailable; ``diag`` carries the same two values into each row's failure
    message on hosts that do run them.
    """
    r = runtime()
    assert r["sys.platform"] == sys.platform, r
    assert r["backend"] in {"seatbelt", "landlock", "none"}, r
    assert set(r) == {"sys.platform", "backend"}, r


@pytest.mark.parametrize(("rid", "command", "scope", "home"), r_params())
def test_read_boundary(boundary, monkeypatch, rid, command, scope, home):
    if home:
        monkeypatch.setenv("HOME", str(boundary.canary))
    res = gate(boundary.ws, render(command, boundary), scopePaths=scope)
    out = str(res.get("output", ""))
    blocked = "denied" in res or res.get("exitCode") not in (0, None)
    d = diag(res, boundary.canary_marker)
    assert boundary.canary_marker not in out and blocked, f"{rid}: {d}"
    if rid == "R1":
        assert res.get("code") == "OUT_OF_SCOPE", f"{rid}: {d}"
    if rid == "R8":
        assert res.get("code") == "COMMAND_REFUSED", f"{rid}: {d}"
    # R4 and every platform-dependent row record sys.platform and the backend.
    assert runtime()["sys.platform"] == sys.platform, f"{rid}: {d}"


def leak_xfail(command: str, wrong: str):
    return pytest.mark.xfail(
        strict=True,
        reason=(f"rule 7: {command!r} reads docs/y.txt outside scope; {wrong}"))


# Group G: does the marker from docs/y.txt show up in the output? The number in
# each id is the policy.py line where the command's binary is allow-listed (or
# the allow-list range for binaries that are not listed at all). At most 15 rows.
G_ROWS = [
    pytest.param("G1", "cat *", ["src"], None, id="G1-cat-star-policy.py-91"),
    pytest.param("G2", "cat d?cs/y.txt", ["src"], "docs marker in output",
                 id="G2-cat-glob-dir-component-policy.py-91"),
    pytest.param("G3", "cat docs/y.tx[t]", ["src"], None,
                 id="G3-cat-glob-file-component-policy.py-91"),
    pytest.param("G4", "cat ./*", ["src"], None, id="G4-cat-dot-star-policy.py-91"),
    pytest.param("G5", "cat docs/*", ["src"], None, id="G5-cat-docs-star-policy.py-91"),
    pytest.param("G6a", "grep -r {DOCS_MARKER}", ["src"], "docs marker in output",
                 id="G6a-grep-recursive-no-path-policy.py-95"),
    pytest.param("G6b", "grep -rn {DOCS_MARKER} *", ["src"], "docs marker in output",
                 id="G6b-grep-recursive-star-policy.py-95"),
    pytest.param("G7a", "grep --file=docs/y.txt src/x.txt", ["src"], None,
                 id="G7a-grep-file-equals-policy.py-95"),
    pytest.param("G7b", "grep -f docs/y.txt src/x.txt", ["src"], None,
                 id="G7b-grep-f-policy.py-95"),
    pytest.param("G7c", "grep --exclude-from=docs/y.txt -r {DOCS_MARKER} src", ["src"], None,
                 id="G7c-grep-exclude-from-policy.py-95"),
    pytest.param("G7d", "rg -f docs/y.txt src/x.txt", ["src"], None,
                 id="G7d-rg-f-policy.py-96"),
    pytest.param("G7e", "cat --file=docs/y.txt", ["src"], None,
                 id="G7e-cat-file-equals-policy.py-91"),
    pytest.param("G7f", "find docs -name y.txt", ["src"], None,
                 id="G7f-find-policy.py-94"),
    pytest.param("G7i", "python3 -c \"print(open('docs/y.txt').read())\"", ["src"],
                 "docs marker in output", id="G7i-python-code-string-policy.py-102"),
    pytest.param("G7k", "sed -f docs/y.txt src/x.txt", ["src"], None,
                 id="G7k-sed-not-in-allow-list-policy.py-87-120"),
]


def g_params():
    out = []
    for p in G_ROWS:
        rid, command, scope, wrong = p.values
        marks = [leak_xfail(command, wrong)] if wrong else []
        out.append(pytest.param(rid, command, scope, id=p.id, marks=marks))
    return out


@pytest.mark.parametrize(("rid", "command", "scope"), g_params())
def test_expansion_and_option_forms(boundary, rid, command, scope):
    res = gate(boundary.ws, render(command, boundary), scopePaths=scope)
    out = str(res.get("output", ""))
    d = diag(res, boundary.docs_marker)
    assert boundary.docs_marker not in out, f"{rid}: {d}"
    assert (boundary.canary / "plain-notes.txt").read_text().strip() == boundary.canary_marker
