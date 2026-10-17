"""F043: bounded test-repair controller.

The controller owns the loop, the budget and the safety rules; the *fixer* (a model, a
human, a script) only proposes whole-file replacements. It works in a scratch copy and
returns the winning patch - it never edits the original tree and never merges anything.

Stops on: fixed | exhausted (attempts) | timeout | stalled (same failure fingerprint
repeated) | no_proposal | rejected_patch (guard violation).
Guards: patches may touch only allowed globs (tests are NOT allowed by default); when test
edits are enabled, deleting tests, dropping assertions, or adding skip/xfail is refused.
"""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}
_FAILED = re.compile(r"^(FAILED|ERROR) (\S+)(?: - (.*))?$", re.MULTILINE)


@dataclass
class Failure:
    ids: list[str]
    messages: list[str]
    tail: str
    exit_code: int

    def fingerprint(self) -> str:
        return hashlib.sha256("\n".join(sorted(self.ids) + sorted(self.messages)).encode()).hexdigest()[:16]


@dataclass
class Budget:
    max_attempts: int = 3
    max_seconds: float = 300.0
    max_patch_lines: int = 200


@dataclass
class Attempt:
    number: int
    files: list[str]
    outcome: str
    fingerprint: str | None = None


@dataclass
class RepairResult:
    status: str
    attempts: list[Attempt] = field(default_factory=list)
    patch: dict[str, str] = field(default_factory=dict)
    reason: str = ""
    initial_failure: Failure | None = None


Proposer = Callable[[Failure, list[Attempt], Path], "dict[str, str] | None"]


def run_pytest(workdir: Path, tests: list[str], timeout: float = 120.0,
               python: str = sys.executable) -> Failure | None:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(workdir), str(workdir / "src")]),
           "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        r = subprocess.run([python, "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider", *tests],
                           cwd=workdir, env=env, capture_output=True, text=True, timeout=timeout,
                           check=False)
    except subprocess.TimeoutExpired:
        return Failure(["<timeout>"], ["test run timed out"], "", -1)
    if r.returncode == 0:
        return None
    out = r.stdout + r.stderr
    hits = _FAILED.findall(out)
    return Failure([h[1] for h in hits] or ["<collection>"], [h[2] for h in hits], out[-1500:],
                   r.returncode)


# ---- weakening guard ---------------------------------------------------------------------
def _strength(src: str) -> dict:
    tree = ast.parse(src)
    tests: dict[str, int] = {}
    skips = 0
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"):
            asserts = sum(
                1 for a in ast.walk(n)
                if (isinstance(a, ast.Assert) and not (isinstance(a.test, ast.Constant) and a.test.value))
                or (isinstance(a, ast.Call) and getattr(a.func, "attr", "") in
                    ("assertEqual", "assertTrue", "assertRaises", "raises")))
            tests[n.name] = asserts
            for d in n.decorator_list:
                skips += bool(re.search(r"skip|xfail", ast.unparse(d)))
        if isinstance(n, ast.Call) and re.search(r"pytest\.(skip|xfail)", ast.unparse(n.func)):
            skips += 1
    return {"tests": tests, "skips": skips}


def weakening_violations(before: str, after: str) -> list[str]:
    b, a = _strength(before), _strength(after)
    out = [f"test '{t}' was removed" for t in b["tests"] if t not in a["tests"]]
    out += [f"assertions in '{t}' dropped {n}->{a['tests'][t]}"
            for t, n in b["tests"].items() if t in a["tests"] and a["tests"][t] < n]
    if a["skips"] > b["skips"]:
        out.append("skip/xfail marker added")
    return out


def run_repair(root: Path, tests: list[str], proposer: Proposer, *, budget: Budget | None = None,
               allowed_globs: list[str] | None = None, allow_test_edits: bool = False,
               python: str = sys.executable) -> RepairResult:
    budget = budget or Budget()
    allowed = allowed_globs or ["*.py"]
    t0 = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix="a3-repair-"))
    wd = work / "p"
    try:
        shutil.copytree(root, wd, ignore=shutil.ignore_patterns(*SKIP))
        first = run_pytest(wd, tests, python=python)
        if first is None:
            return RepairResult("already_passing", reason="nothing to repair")
        result = RepairResult("exhausted", initial_failure=first)
        failure, seen = first, {first.fingerprint(): 1}
        patch_all: dict[str, str] = {}
        for n in range(1, budget.max_attempts + 1):
            if time.monotonic() - t0 > budget.max_seconds:
                result.status = "timeout"
                break
            proposal = proposer(failure, result.attempts, wd)
            if not proposal:
                result.status, result.reason = "no_proposal", "fixer returned nothing"
                break
            bad = _check_patch(wd, proposal, allowed, allow_test_edits, budget)
            if bad:
                result.attempts.append(Attempt(n, sorted(proposal), "rejected: " + bad))
                result.status, result.reason = "rejected_patch", bad
                break
            backup = {p: (wd / p).read_text() if (wd / p).exists() else None for p in proposal}
            for p, text in proposal.items():
                (wd / p).parent.mkdir(parents=True, exist_ok=True)
                (wd / p).write_text(text)
            nxt = run_pytest(wd, tests, python=python)
            if nxt is None:
                result.attempts.append(Attempt(n, sorted(proposal), "passed"))
                patch_all.update(proposal)
                result.status, result.patch = "fixed", patch_all
                break
            fp = nxt.fingerprint()
            result.attempts.append(Attempt(n, sorted(proposal), "still_failing", fp))
            for p, old in backup.items():             # a non-fix is not kept
                if old is None:
                    (wd / p).unlink(missing_ok=True)
                else:
                    (wd / p).write_text(old)
            seen[fp] = seen.get(fp, 0) + 1
            if seen[fp] >= 3 or (fp == first.fingerprint() and n >= 2):
                result.status, result.reason = "stalled", "same failure fingerprint repeated"
                break
            failure = nxt
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _check_patch(wd: Path, patch: dict[str, str], allowed: list[str], allow_tests: bool,
                 budget: Budget) -> str:
    changed = 0
    for rel, text in patch.items():
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts or not (wd / rel).resolve().is_relative_to(wd.resolve()):
            return f"path escapes workspace: {rel}"
        if not any(fnmatch.fnmatch(rel, g) for g in allowed):
            return f"path not allowed: {rel}"
        is_test = bool(re.search(r"(^|/)(test_[^/]*|[^/]*_test)\.py$|(^|/)tests?/", rel))
        old = (wd / rel).read_text() if (wd / rel).exists() else ""
        if is_test:
            if not allow_tests:
                return f"editing tests is not allowed: {rel}"
            try:
                v = weakening_violations(old, text)
            except SyntaxError:
                return f"test file does not parse: {rel}"
            if v:
                return f"test weakening in {rel}: " + "; ".join(v)
        changed += sum(1 for a, b in zip(old.splitlines(), text.splitlines(), strict=False) if a != b)
        changed += abs(len(old.splitlines()) - len(text.splitlines()))
    if changed > budget.max_patch_lines:
        return f"patch too large ({changed} lines > {budget.max_patch_lines})"
    return ""
