"""F049: mutation testing for Python sources (test-strength analysis).

AST mutants (comparison/boolean/arithmetic/constant/return operators) are applied one at a
time in a scratch copy of the project and the given tests are run against each. A surviving
mutant is concrete evidence of a behaviour the tests do not pin down. Executed, not inferred.
"""
from __future__ import annotations

import ast
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build"}
_CMP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq,
        ast.NotEq: ast.Eq, ast.Is: ast.IsNot, ast.IsNot: ast.Is, ast.In: ast.NotIn,
        ast.NotIn: ast.In}
_BIN = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult,
        ast.FloorDiv: ast.Mult, ast.Mod: ast.Mult}


@dataclass
class Mutant:
    file: str
    index: int
    line: int
    op: str
    detail: str


@dataclass
class MutantResult:
    mutant: Mutant
    outcome: str   # killed | survived | timeout | error


@dataclass
class MutationReport:
    status: str    # executed | blocked
    baseline_ok: bool
    results: list[MutantResult] = field(default_factory=list)
    total_sites: int = 0
    sampled: int = 0
    seconds: float = 0.0
    note: str = ""

    @property
    def killed(self) -> int:
        return sum(r.outcome in ("killed", "timeout") for r in self.results)

    @property
    def survived(self) -> list[MutantResult]:
        return [r for r in self.results if r.outcome == "survived"]

    @property
    def score(self) -> float | None:
        valid = [r for r in self.results if r.outcome != "error"]
        return round(self.killed / len(valid), 4) if valid else None


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = n.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant):
                ids.add(id(b[0].value))
    return ids


class _Mutator(ast.NodeTransformer):
    """Counts mutation sites; applies the one whose ordinal == target (or none to just count)."""

    def __init__(self, target: int | None, skip_ids: set[int], noskip_lines: set[int]):
        self.target, self.n, self.skip_ids, self.noskip = target, 0, skip_ids, noskip_lines
        self.applied: tuple[int, str, str] | None = None

    def _site(self, node: ast.AST, op: str, detail: str) -> bool:
        if getattr(node, "lineno", 0) in self.noskip:
            return False
        hit = self.target == self.n
        if hit:
            self.applied = (node.lineno, op, detail)
        self.n += 1
        return hit

    def visit_Compare(self, node: ast.Compare):
        self.generic_visit(node)
        for i, op in enumerate(node.ops):
            if type(op) in _CMP and self._site(node, "compare", f"{type(op).__name__}->{_CMP[type(op)].__name__}"):
                node.ops[i] = _CMP[type(op)]()
        return node

    def visit_BinOp(self, node: ast.BinOp):
        self.generic_visit(node)
        if type(node.op) in _BIN and self._site(node, "arith", f"{type(node.op).__name__}->{_BIN[type(node.op)].__name__}"):
            node.op = _BIN[type(node.op)]()
        return node

    def visit_BoolOp(self, node: ast.BoolOp):
        self.generic_visit(node)
        if self._site(node, "bool", type(node.op).__name__):
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        return node

    def visit_UnaryOp(self, node: ast.UnaryOp):
        self.generic_visit(node)
        if isinstance(node.op, ast.Not) and self._site(node, "not", "drop not"):
            return node.operand
        return node

    def visit_Constant(self, node: ast.Constant):
        if id(node) in self.skip_ids:
            return node
        if isinstance(node.value, bool) and self._site(
                node, "const", f"{node.value}->{not node.value}"):
            return ast.copy_location(ast.Constant(not node.value), node)
        if (isinstance(node.value, int) and not isinstance(node.value, bool)
                and self._site(node, "const", f"{node.value}->{node.value + 1}")):
            return ast.copy_location(ast.Constant(node.value + 1), node)
        return node

    def visit_Return(self, node: ast.Return):
        self.generic_visit(node)
        returns_none = isinstance(node.value, ast.Constant) and node.value.value is None
        if node.value is not None and not returns_none and self._site(node, "return", "return None"):
            node.value = ast.copy_location(ast.Constant(None), node.value)
        return node


def _noskip_lines(src: str) -> set[int]:
    return {i for i, line in enumerate(src.splitlines(), 1) if "pragma: no mutate" in line}


def enumerate_mutants(path: Path, rel: str) -> list[Mutant]:
    src = path.read_text()
    tree = ast.parse(src)
    probe = _Mutator(None, _docstring_nodes(tree), _noskip_lines(src))
    probe.visit(tree)
    out = []
    for i in range(probe.n):
        t = ast.parse(src)
        m = _Mutator(i, _docstring_nodes(t), _noskip_lines(src))
        m.visit(t)
        if m.applied:
            out.append(Mutant(rel, i, *m.applied))
    return out


def _apply(src: str, index: int) -> str:
    t = ast.parse(src)
    _Mutator(index, _docstring_nodes(t), _noskip_lines(src)).visit(t)
    return ast.unparse(ast.fix_missing_locations(t))


def _run_tests(workdir: Path, tests: list[str], timeout: float, python: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "COV_"))}
    env.update(PYTHONPATH=os.pathsep.join([str(workdir), str(workdir / "src")]),
               PYTHONDONTWRITEBYTECODE="1")
    try:
        r = subprocess.run([python, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *tests],
                           cwd=workdir, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return "timeout"
    if r.returncode == 0:
        return "pass"
    return "no_tests" if r.returncode == 5 else "fail"


def run_mutation(root: Path, sources: list[str], tests: list[str], *, max_mutants: int = 40,
                 seed: int = 0, per_mutant_timeout: float = 30.0, budget_seconds: float = 300.0,
                 python: str = sys.executable) -> MutationReport:
    root = root.resolve()
    t0 = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix="a3-mut-"))
    try:
        shutil.copytree(root, work / "p", ignore=shutil.ignore_patterns(*SKIP), dirs_exist_ok=True)
        wd = work / "p"
        base = _run_tests(wd, tests, per_mutant_timeout * 3, python)
        if base != "pass":
            return MutationReport("blocked", False, note=f"baseline tests did not pass ({base}); "
                                  "mutation results would be meaningless")
        all_m: list[Mutant] = []
        for rel in sources:
            all_m += enumerate_mutants(wd / rel, rel)
        picked = sorted(random.Random(seed).sample(all_m, min(max_mutants, len(all_m))),
                        key=lambda m: (m.file, m.index))
        rep = MutationReport("executed", True, total_sites=len(all_m), sampled=len(picked))
        originals: dict[str, str] = {}
        for m in picked:
            if time.monotonic() - t0 > budget_seconds:
                rep.note = f"time budget reached after {len(rep.results)} of {len(picked)} mutants"
                break
            f = wd / m.file
            originals.setdefault(m.file, f.read_text())
            try:
                f.write_text(_apply(originals[m.file], m.index))
                out = _run_tests(wd, tests, per_mutant_timeout, python)
            except SyntaxError:
                out = "error"
            finally:
                f.write_text(originals[m.file])
            rep.results.append(MutantResult(m, {"pass": "survived", "fail": "killed",
                                                "timeout": "timeout"}.get(out, "error")))
        rep.seconds = round(time.monotonic() - t0, 2)
        return rep
    finally:
        shutil.rmtree(work, ignore_errors=True)
