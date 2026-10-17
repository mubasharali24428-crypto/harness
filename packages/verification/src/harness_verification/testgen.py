"""F042: test-generation workflow with rationale and acceptance gates.

Generators are pluggable (``Generator`` protocol - a model-backed one fits here). The shipped
baseline is a deterministic *characterization* generator: it derives boundary-ish inputs from
the function's own AST, records what the current code does, and emits pytest cases. That pins
CURRENT behaviour (including current bugs) - it is a regression net, not a correctness oracle,
and each proposal says so. Whatever generates a test, the same gates decide acceptance:
(1) passes on the current code, (2) adds line coverage or kills >=1 mutant of the target.
"""
from __future__ import annotations

import ast
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .mutation import run_mutation

SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}
SAFE_EXC = {"ValueError", "TypeError", "ZeroDivisionError", "KeyError", "IndexError",
            "AttributeError", "OverflowError"}


@dataclass
class FunctionInfo:
    file: str
    name: str
    lineno: int
    params: list[str]
    constants: list[int]
    ast_hints: dict[str, set[str]] = field(default_factory=dict)


@dataclass
class TestProposal:
    __test__ = False
    target: str
    rationale: str
    source: str
    kind: str = "characterization"
    cases: int = 0


@dataclass
class Evaluation:
    accepted: bool
    passes_on_current: bool
    coverage_gained_lines: int | None
    mutants_killed: int | None
    mutants_total: int | None
    reasons: list[str]


class Generator(Protocol):
    def propose(self, root: Path, fn: FunctionInfo) -> list[TestProposal]: ...


def module_name(rel: str) -> str:
    parts = list(Path(rel).with_suffix("").parts)
    if parts[0] == "src":
        parts = parts[1:]
    return ".".join(parts)


def list_functions(root: Path, rel: str) -> list[FunctionInfo]:
    tree = ast.parse((root / rel).read_text())
    out = []
    for n in tree.body:
        if not isinstance(n, ast.FunctionDef) or n.name.startswith("_"):
            continue
        a = n.args
        if a.vararg or a.kwarg or a.kwonlyargs:
            continue
        params = [x.arg for x in a.args]
        hints: dict[str, set[str]] = {p: set() for p in params}
        consts: set[int] = set()
        for node in ast.walk(n):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id in ("len", "sum", "max", "min", "sorted", "list", "set"):
                for arg in node.args:
                    if isinstance(arg, ast.Name) and arg.id in hints:
                        hints[arg.id].add("seq")
            if isinstance(node, ast.For) and isinstance(node.iter, ast.Name) and node.iter.id in hints:
                hints[node.iter.id].add("seq")
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                    and node.value.id in hints and node.attr in ("lower", "upper", "strip", "split"):
                hints[node.value.id].add("str")
            if isinstance(node, ast.Compare):
                for c in [node.left, *node.comparators]:
                    if isinstance(c, ast.Constant) and isinstance(c.value, int) and not isinstance(c.value, bool):
                        consts.add(c.value)
        out.append(FunctionInfo(rel, n.name, n.lineno, params, sorted(consts), hints))
    return out


def _candidates(fn: FunctionInfo, p: str) -> list:
    h = fn.ast_hints.get(p, set())
    if "seq" in h:
        return [[], [1], [1, 2, 3]]
    if "str" in h:
        return ["", "Ab c"]
    ints = sorted({0, 1, -1, *fn.constants, *(c + 1 for c in fn.constants), *(c - 1 for c in fn.constants)})
    return ints[:7]


_RUNNER = textwrap.dedent('''
    import ast, importlib, json, sys
    sys.path[:0] = [sys.argv[1], sys.argv[1] + "/src"]
    fn = getattr(importlib.import_module(sys.argv[2]), sys.argv[3])
    out = []
    for raw in json.loads(sys.argv[4]):
        args = ast.literal_eval(raw)
        try:
            v = fn(*args)
            r = repr(v)
            try:
                same = ast.literal_eval(r) == v
            except Exception:
                same = False
            out.append({"args": raw, "ok": r} if same else None)
        except BaseException as e:
            out.append({"args": raw, "raises": type(e).__name__})
    print(json.dumps(out))
''')


class CharacterizationGenerator:
    max_cases = 12

    def __init__(self, python: str = sys.executable) -> None:
        self.python = python

    def propose(self, root: Path, fn: FunctionInfo) -> list[TestProposal]:
        if not fn.params:
            return []
        combos = list(itertools.islice(itertools.product(*[_candidates(fn, p) for p in fn.params]),
                                       self.max_cases))
        raw = [repr(list(c)) for c in combos]
        r = subprocess.run([self.python, "-c", _RUNNER, str(root), module_name(fn.file), fn.name,
                            json.dumps(raw)], capture_output=True, text=True, timeout=60, check=False,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        if r.returncode != 0:
            return []
        rows = [x for x in json.loads(r.stdout) if x]
        lines, ok_cases = [], 0
        for row in rows:
            args = ast.literal_eval(row["args"])
            call = f"{fn.name}(*{args!r})"
            if "ok" in row:
                lines.append(f"    assert {call} == {row['ok']}")
                ok_cases += 1
            elif row["raises"] in SAFE_EXC:
                lines.append(f"    with pytest.raises({row['raises']}):\n        {call}")
                ok_cases += 1
        if not lines:
            return []
        src = (f"import pytest\nfrom {module_name(fn.file)} import {fn.name}\n\n\n"
               f"def test_{fn.name}_characterization():\n" + "\n".join(lines) + "\n")
        why = (f"{fn.file}::{fn.name} has no pinning test for {ok_cases} boundary inputs derived from "
               f"its own comparisons/usage ({', '.join(map(repr, fn.constants)) or 'no int constants'}). "
               "CHARACTERIZATION: records what the code does today - it detects behaviour change, "
               "it does not prove the behaviour is correct.")
        return [TestProposal(f"{fn.file}::{fn.name}", why, src, cases=ok_cases)]


def _coverage_lines(wd: Path, tests: list[str], target: str, python: str) -> set[int] | None:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(wd), str(wd / "src")]),
           "PYTHONDONTWRITEBYTECODE": "1"}
    cov = wd / ".cov"
    r = subprocess.run([python, "-m", "coverage", "run", f"--data-file={cov}", "-m", "pytest", "-q",
                        "-p", "no:cacheprovider", *tests], cwd=wd, env=env, capture_output=True,
                       text=True, timeout=120, check=False)
    if r.returncode not in (0, 1, 5):
        return None
    j = wd / "cov.json"
    rr = subprocess.run([python, "-m", "coverage", "json", f"--data-file={cov}", "-o", str(j)],
                        cwd=wd, capture_output=True, text=True, check=False)
    if rr.returncode != 0 or not j.exists():
        return None
    files = json.loads(j.read_text())["files"]
    for k, v in files.items():
        if Path(k).as_posix().endswith(target):
            return set(v["executed_lines"])
    return set()


def evaluate(root: Path, proposal: TestProposal, existing_tests: list[str], *,
             python: str = sys.executable, mutants: int = 20) -> Evaluation:
    root = root.resolve()
    target_file = proposal.target.split("::")[0]
    work = Path(tempfile.mkdtemp(prefix="a3-tg-"))
    try:
        wd = work / "p"
        shutil.copytree(root, wd, ignore=shutil.ignore_patterns(*SKIP))
        new_rel = f"tests/test_generated_{proposal.target.split('::')[1]}.py"
        (wd / new_rel).parent.mkdir(exist_ok=True)
        (wd / new_rel).write_text(proposal.source)
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(wd), str(wd / "src")]),
               "PYTHONDONTWRITEBYTECODE": "1"}
        run = subprocess.run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", new_rel],
                             cwd=wd, env=env, capture_output=True, text=True, timeout=120, check=False)
        reasons: list[str] = []
        if run.returncode != 0:
            return Evaluation(False, False, None, None, None,
                              ["generated test does not pass on the current code"])
        before = _coverage_lines(wd, existing_tests, target_file, python) if existing_tests else set()
        after = _coverage_lines(wd, [*existing_tests, new_rel], target_file, python)
        gained = None if before is None or after is None else len(after - before)
        if gained is None:
            reasons.append("coverage measurement unavailable (coverage.py missing?) - blocked")
        mut = run_mutation(wd, [target_file], [new_rel], max_mutants=mutants, python=python)
        killed = mut.killed if mut.status == "executed" else None
        total = len(mut.results) if mut.status == "executed" else None
        if mut.status != "executed":
            reasons.append(f"mutation blocked: {mut.note}")
        useful = (gained or 0) > 0 or (killed or 0) > 0
        if not useful:
            reasons.append("adds no coverage and kills no mutants - rejected as redundant")
        return Evaluation(useful, True, gained, killed, total, reasons)
    finally:
        shutil.rmtree(work, ignore_errors=True)
