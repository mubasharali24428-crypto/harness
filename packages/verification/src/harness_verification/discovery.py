"""F041: dependency-aware test discovery (static inference over the import graph).

Given changed files, find the tests that could observe them: the test files themselves,
tests that import a changed module directly or transitively, and name-convention matches.
Everything here is *static inference*; the scorecard labels it that way.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", ".tox"}
TS_EXT = (".ts", ".tsx", ".js", ".mjs", ".jsx")
MAX_DEPTH = 4


def _files(root: Path, suffixes: tuple[str, ...]):
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix in suffixes and not (set(p.relative_to(root).parts) & SKIP_DIRS):
            yield p


def is_test_file(rel: str) -> bool:
    name = Path(rel).name
    return (name.startswith("test_") and name.endswith(".py")) or name.endswith("_test.py") or bool(
        re.search(r"\.(test|spec)\.(ts|tsx|js|mjs|jsx)$", name))


def detect_runners(root: Path) -> list[str]:
    runners = []
    if any(_files(root, (".py",))) and (
        (root / "pytest.ini").exists() or (root / "conftest.py").exists()
        or "pytest" in (root / "pyproject.toml").read_text(errors="ignore") if (root / "pyproject.toml").exists()
        else any(is_test_file(p.relative_to(root).as_posix()) for p in _files(root, (".py",)))):
        runners.append("pytest")
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text())
        except ValueError:
            data = {}
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        for r in ("vitest", "jest"):
            if r in deps:
                runners.append(r)
    return runners


def _py_module_name(root: Path, rel: str) -> list[str]:
    """Possible dotted names for a python file (with and without a src/ prefix)."""
    parts = list(Path(rel).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    names = {".".join(parts)} if parts else set()
    if parts and parts[0] == "src" and len(parts) > 1:
        names.add(".".join(parts[1:]))
    return sorted(names)


def _py_imports(path: Path, rel: str) -> set[str]:
    try:
        tree = ast.parse(path.read_text(errors="ignore"))
    except SyntaxError:
        return set()
    out: set[str] = set()
    pkg = list(Path(rel).parent.parts)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                base = ".".join([*anchor, *([base] if base else [])])
            out.add(base)
            out.update(f"{base}.{a.name}" if base else a.name for a in node.names)
    return out


_TS_IMPORT = re.compile(r"""(?:from|import|require\()\s*['"](\.{1,2}/[^'"]+)['"]""")


def _ts_imports(path: Path, root: Path) -> set[str]:
    out = set()
    for spec in _TS_IMPORT.findall(path.read_text(errors="ignore")):
        base = (path.parent / spec).resolve()
        stem = base.with_suffix("") if base.suffix in (".js", ".mjs") else base
        for cand in [base, *(Path(f"{stem}{e}") for e in TS_EXT), *(stem / f"index{e}" for e in TS_EXT)]:
            if cand.is_file() and cand.is_relative_to(root.resolve()):
                out.add(cand.relative_to(root.resolve()).as_posix())
                break
    return out


@dataclass
class Selected:
    path: str
    reason: str
    rank: int
    evidence: str = "static_inference"


@dataclass
class DiscoveryResult:
    runners: list[str]
    tests: list[Selected] = field(default_factory=list)
    unmatched_changed: list[str] = field(default_factory=list)
    run_all: bool = False
    notes: list[str] = field(default_factory=list)

    def paths(self) -> list[str]:
        return [t.path for t in self.tests]


def discover(root: Path, changed: list[str]) -> DiscoveryResult:
    root = root.resolve()
    res = DiscoveryResult(runners=detect_runners(root))
    rel_all = [p.relative_to(root).as_posix() for p in _files(root, (".py", *TS_EXT))]
    # reverse import graph over files
    importers: dict[str, set[str]] = {}
    py_names: dict[str, str] = {}
    for rel in rel_all:
        if rel.endswith(".py"):
            for n in _py_module_name(root, rel):
                py_names[n] = rel
    for rel in rel_all:
        if rel.endswith(".py"):
            for imp in _py_imports(root / rel, rel):
                # longest known module prefix
                parts = imp.split(".")
                for i in range(len(parts), 0, -1):
                    tgt = py_names.get(".".join(parts[:i]))
                    if tgt:
                        if tgt != rel:
                            importers.setdefault(tgt, set()).add(rel)
                        break
        else:
            for tgt in _ts_imports(root / rel, root):
                importers.setdefault(tgt, set()).add(rel)

    chosen: dict[str, Selected] = {}

    def add(path: str, reason: str, rank: int) -> None:
        if path not in chosen or chosen[path].rank > rank:
            chosen[path] = Selected(path, reason, rank)

    for ch in changed:
        ch = Path(ch).as_posix()
        if Path(ch).name in ("package.json", "pyproject.toml", "conftest.py", "pytest.ini",
                             "vitest.config.ts", "tsconfig.json"):
            res.run_all = True
            res.notes.append(f"{ch}: configuration change - run everything")
            continue
        if not (root / ch).exists():
            res.notes.append(f"{ch}: deleted or missing; dependants unknown")
            res.unmatched_changed.append(ch)
            continue
        if is_test_file(ch):
            add(ch, "changed test", 0)
            continue
        if not ch.endswith((".py", *TS_EXT)):
            res.unmatched_changed.append(ch)
            continue
        found = False
        # BFS over reverse edges
        frontier, seen, depth = {ch}, {ch}, 0
        while frontier and depth < MAX_DEPTH:
            depth += 1
            nxt: set[str] = set()
            for f in frontier:
                for imp in importers.get(f, ()):
                    if imp in seen:
                        continue
                    seen.add(imp)
                    nxt.add(imp)
                    if is_test_file(imp):
                        add(imp, f"imports {Path(f).name}" if depth == 1
                            else f"transitively depends on {Path(ch).name} ({depth} hops)", depth)
                        found = True
            frontier = nxt
        stem = Path(ch).stem
        for rel in rel_all:
            n = Path(rel).name
            if is_test_file(rel) and stem in {n.removeprefix("test_").split(".")[0].removesuffix("_test"),
                                              n.split(".")[0]}:
                add(rel, f"name matches {Path(ch).name}", 5)
                found = True
        if not found:
            res.unmatched_changed.append(ch)
    res.tests = sorted(chosen.values(), key=lambda s: (s.rank, s.path))
    return res
