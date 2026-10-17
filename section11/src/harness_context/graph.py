"""Dependency-graph extraction from indexed content (F014).

Builds a file-level dependency graph (nodes = indexed files, edges = imports /
requires / relative links) from what is ALREADY in the index — no second
filesystem walk, no new persistence. Two honesty rules, both enforced here:

* an edge is emitted only when it resolves to a file that is itself indexed;
  imports of external packages are aggregated as ``external`` specifiers with
  a count and importers list — never invented as nodes;
* unresolvable relative references (a ``./missing.py``, a link out of the
  repo) increment ``unresolved`` instead of being silently dropped.

Extraction per language:

* Python — ``ast.parse`` on the stitched chunk content (chunks cover the
  whole file, so imports parse with real line fidelity); supports
  ``import pkg.mod``, ``from pkg import name``, and relative imports
  (``from .x import y``, ``from . import z``).
* TS/JS/TSX/JSX — static import/export-from/re-export regexes plus
  ``require("…")`` and dynamic ``import("…")`` calls.
* Markdown — relative ``[text](path.md)`` links.

Per-file chunk text is STITCHED (ordered by start line, first write wins on
overlap), so window-chunked files reconstruct without duplicated overlap
lines.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

# Candidate suffixes tried when resolving a relative import without a known
# extension, in code-language order.
_TS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")
_PY_EXTENSIONS = (".py",)

#: Fallback when a path has no stored lang tag (e.g. direct library use).
_EXT_LANGS = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "jsx",
    ".md": "markdown",
}


def _lang_of_path(path: str) -> str:
    suffix = path[path.rfind(".") :] if "." in path else ""
    return _EXT_LANGS.get(suffix, path.rsplit(".", 1)[-1])

_IMPORT_RE = re.compile(
    r"""
    (?:import|export)\s        # import / export-from statement
    (?:type\s+)?               # TS type-only imports
    [^'"]*?\sfrom\s*['"]([^'"]+)['"]
    | import\s*\(\s*['"]([^'"]+)['"]\s*\)   # dynamic import("…")
    | require\s*\(\s*['"]([^'"]+)['"]\s*\) # require("…")
    | ^\s*import\s*['"]([^'"]+)['"]        # side-effect import "…"
    """,
    re.VERBOSE | re.MULTILINE,
)
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+['\"][^)]*['\"])?\)")


@dataclass(frozen=True)
class GraphNode:
    path: str
    lang: str


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    kind: str  # "import" | "link"


@dataclass
class DependencyGraph:
    project_id: str
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    external: list[dict] = field(default_factory=list)  # {specifier, count, importedBy[]}
    unresolved: int = 0  # relative refs that resolve to nothing indexed

    def to_wire(self) -> dict:
        return {
            "projectId": self.project_id,
            "nodes": [{"path": n.path, "lang": n.lang} for n in self.nodes],
            "edges": [
                {"source": e.source, "target": e.target, "kind": e.kind}
                for e in self.edges
            ],
            "external": self.external,
            "unresolved": self.unresolved,
        }


def stitch_chunks(rows: list[dict]) -> str:
    """Reassemble one file's text from its stored chunk rows.

    Rows are ``{startLine, endLine, content}`` (1-indexed inclusive); overlap
    (window chunking) is resolved first-write-wins, which is exact for
    identical content and harmless for the pathological divergent case.
    Positions are preserved, so line numbers from parsing match the file.
    """
    ordered = sorted(rows, key=lambda r: (r["startLine"], r["endLine"]))
    out: list[str | None] = []
    for row in ordered:
        lines = row["content"].split("\n")
        pos = int(row["startLine"]) - 1
        if pos < 0:
            continue
        for i, line in enumerate(lines):
            idx = pos + i
            if idx >= len(out):
                out.extend([None] * (idx + 1 - len(out)))
            if out[idx] is None:
                out[idx] = line
    return "\n".join(line or "" for line in out)


def _resolve_relative(
    base_dir: str, spec: str, known: set[str], exts: tuple[str, ...]
) -> str | None:
    """Resolve ``./x`` / ``../x`` from the importing file's directory."""
    joined = posixpath.normpath(posixpath.join(base_dir, spec)) if base_dir else spec
    if joined.startswith(".."):
        return None  # escapes the repo — the caller counts it unresolved
    candidates = [joined]
    if not any(joined.endswith(e) for e in exts + (".md",)):
        candidates += [f"{joined}{e}" for e in exts]
        candidates += [f"{joined}/__init__.py", f"{joined}/index.ts", f"{joined}/index.tsx"]
    for cand in candidates:
        if cand in known:
            return cand
    return None


def _resolve_bare_python(module: str, known: set[str]) -> str | None:
    """Absolute python import ``pkg.util`` → ``pkg/util.py`` / package init."""
    path = module.replace(".", "/")
    for cand in (f"{path}.py", f"{path}/__init__.py"):
        if cand in known:
            return cand
    return None


def _python_refs(text: str) -> list[tuple[str, int]]:
    """(specifier, relative_level) pairs from a stitched python file."""
    import ast

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    refs: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            refs.extend((alias.name, 0) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                refs.append((node.module, node.level or 0))
            else:
                # ``from . import a, b`` — each alias may be a submodule.
                refs.extend((alias.name, node.level) for alias in node.names)
    return refs


def _script_refs(text: str) -> list[str]:
    out: list[str] = []
    for match in _IMPORT_RE.finditer(text):
        spec = next((g for g in match.groups() if g), None)
        if spec:
            out.append(spec)
    return out


def _md_refs(text: str) -> list[str]:
    out: list[str] = []
    for match in _MD_LINK_RE.finditer(text):
        target = match.group(1)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = target.split("#", 1)[0]
        if target:
            out.append(target)
    return out


def build_dependency_graph(
    project_id: str,
    contents: dict[str, list[dict]],
    *,
    nodes_lang: dict[str, str] | None = None,
) -> DependencyGraph:
    """Assemble the graph from ``{path: [chunk rows]}``.

    ``nodes_lang`` optionally carries the stored lang tag per path (from the
    same fetch); it is cosmetic metadata on nodes only.
    """
    known = set(contents)
    graph = DependencyGraph(project_id=project_id)
    graph.nodes = [
        GraphNode(path=p, lang=(nodes_lang or {}).get(p) or _lang_of_path(p))
        for p in sorted(known)
    ]

    external_counts: dict[str, set[str]] = {}
    seen_edges: set[tuple[str, str, str]] = set()

    for path in sorted(contents):
        text = stitch_chunks(contents[path])
        lang = (nodes_lang or {}).get(path) or _lang_of_path(path)
        base_dir = posixpath.dirname(path)

        if lang == "python":
            for spec, level in _python_refs(text):
                if level and level > 0:
                    # Relative: level 1 = same package dir; level N = N-1 up.
                    anchor = base_dir
                    for _ in range(level - 1):
                        anchor = posixpath.dirname(anchor)
                    target = _resolve_relative(anchor, spec, known, _PY_EXTENSIONS)
                else:
                    target = _resolve_bare_python(spec, known)
                if target is None:
                    if level and level > 0:
                        # A RELATIVE import that misses the index is a broken
                        # intra-repo reference — count it loudly.
                        graph.unresolved += 1
                    else:
                        # Absolute spec that is not an indexed path: stdlib /
                        # installed package — external, aggregated per name.
                        external_counts.setdefault(spec, set()).add(path)
                    continue
                key = (path, target, "import")
                if key not in seen_edges:
                    seen_edges.add(key)
                    graph.edges.append(GraphEdge(source=path, target=target, kind="import"))

        elif lang in {"typescript", "tsx", "javascript", "jsx"}:
            for spec in _script_refs(text):
                if spec.startswith((".", "/")):
                    target = _resolve_relative(base_dir, spec, known, _TS_EXTENSIONS)
                else:
                    # monorepo-style alias to an indexed path (rare) else external
                    target = spec if spec in known else None
                if target is None:
                    if spec.startswith("."):
                        graph.unresolved += 1
                    else:
                        external_counts.setdefault(spec, set()).add(path)
                    continue
                key = (path, target, "import")
                if key not in seen_edges:
                    seen_edges.add(key)
                    graph.edges.append(GraphEdge(source=path, target=target, kind="import"))

        elif lang == "markdown":
            for spec in _md_refs(text):
                target = _resolve_relative(base_dir, spec, known, ())
                if target is None:
                    graph.unresolved += 1
                    continue
                key = (path, target, "link")
                if key not in seen_edges:
                    seen_edges.add(key)
                    graph.edges.append(GraphEdge(source=path, target=target, kind="link"))

    graph.external = [
        {"specifier": spec, "count": len(importers), "importedBy": sorted(importers)}
        for spec, importers in sorted(external_counts.items())
    ]
    return graph
