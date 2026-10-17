"""Symbol map + find-references over indexed content (F015).

Two questions, both answered from the index only:

* ``list_symbols`` — the definition map of a repo (functions/classes/methods
  for Python via ``ast`` with exact line spans; functions/classes/const
  arrow-assignments for TS/JS via anchored regexes, where ``endLine`` is
  ``None`` because a regex cannot honestly track a closing brace through
  strings/comments).
* ``find_symbol(name)`` — every occurrence of ``name``: definitions plus
  reference lines with one-line snippets. Occurrence detection is
  word-boundary and regex-escaped; ``name`` itself must be a plain
  identifier, so query text can never behave as a pattern.

References are text-occurrence based (no scope analysis) — this is a "where
does this name appear" map, not a type-checker. That limit is reported in the
wire payload (``note``) rather than hidden.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .graph import stitch_chunks

MAX_SYMBOLS = 500
MAX_REFERENCES = 200
SNIPPET_CHARS = 160

_IDENT_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")

_TS_FUNC_RE = re.compile(
    r"^[ \t]*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(",
    re.MULTILINE,
)
_TS_CLASS_RE = re.compile(
    r"^[ \t]*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_TS_CONST_FN_RE = re.compile(
    r"^[ \t]*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
    r"(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)",
    re.MULTILINE,
)

_CODE_LANGS = {"python", "typescript", "tsx", "javascript", "jsx"}


@dataclass(frozen=True)
class SymbolDefinition:
    path: str
    name: str
    qualname: str
    kind: str  # function | method | class | const
    start_line: int
    end_line: int | None  # None when the extractor cannot track spans

    def to_wire(self) -> dict:
        return {
            "path": self.path,
            "name": self.name,
            "qualname": self.qualname,
            "kind": self.kind,
            "startLine": self.start_line,
            "endLine": self.end_line,
        }


@dataclass(frozen=True)
class SymbolReference:
    path: str
    start_line: int
    kind: str  # definition | reference
    snippet: str


#: Fallback when a path has no stored lang tag (e.g. direct library use).
_EXT_LANGS = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "jsx",
}


def _lang_of(path: str, lang: str | None) -> str:
    if lang:
        return lang
    suffix = path[path.rfind(".") :] if "." in path else ""
    return _EXT_LANGS.get(suffix, path.rsplit(".", 1)[-1])


def _python_defs(path: str, text: str) -> list[SymbolDefinition]:
    import ast

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    defs: list[SymbolDefinition] = []

    def visit(node: ast.AST, prefix: str, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                starts = [child.lineno] + [d.lineno for d in child.decorator_list]
                defs.append(
                    SymbolDefinition(
                        path=path,
                        name=child.name,
                        qualname=qual,
                        kind="method" if in_class else "function",
                        start_line=min(starts),
                        end_line=getattr(child, "end_lineno", None),
                    )
                )
                visit(child, qual, in_class=False)
            elif isinstance(child, ast.ClassDef):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                starts = [child.lineno] + [d.lineno for d in child.decorator_list]
                defs.append(
                    SymbolDefinition(
                        path=path,
                        name=child.name,
                        qualname=qual,
                        kind="class",
                        start_line=min(starts),
                        end_line=getattr(child, "end_lineno", None),
                    )
                )
                visit(child, qual, in_class=True)
            else:
                visit(child, prefix, in_class)

    visit(tree, "", in_class=False)
    return defs


def _script_defs(path: str, text: str) -> list[SymbolDefinition]:
    defs: list[SymbolDefinition] = []
    for regex, kind in (
        (_TS_FUNC_RE, "function"),
        (_TS_CLASS_RE, "class"),
        (_TS_CONST_FN_RE, "const"),
    ):
        for match in regex.finditer(text):
            line = text.count("\n", 0, match.start(1)) + 1
            defs.append(
                SymbolDefinition(
                    path=path,
                    name=match.group(1),
                    qualname=match.group(1),
                    kind=kind,
                    start_line=line,
                    end_line=None,  # regex cannot track spans honestly
                )
            )
    return defs


def extract_definitions(
    contents: dict[str, list[dict]], lang_by_path: dict[str, str] | None = None
) -> list[SymbolDefinition]:
    """Definition map for every code file in the index."""
    defs: list[SymbolDefinition] = []
    for path in sorted(contents):
        lang = _lang_of(path, (lang_by_path or {}).get(path))
        if lang not in _CODE_LANGS:
            continue
        text = stitch_chunks(contents[path])
        defs.extend(_python_defs(path, text) if lang == "python" else _script_defs(path, text))
    return defs


def list_symbols(
    contents: dict[str, list[dict]], lang_by_path: dict[str, str] | None = None
) -> tuple[list[SymbolDefinition], bool]:
    """All definitions, bounded; ``(defs, truncated)``."""
    defs = extract_definitions(contents, lang_by_path)
    if len(defs) > MAX_SYMBOLS:
        return defs[:MAX_SYMBOLS], True
    return defs, False


def find_symbol(
    contents: dict[str, list[dict]],
    lang_by_path: dict[str, str] | None,
    name: str,
) -> tuple[list[SymbolDefinition], list[SymbolReference], bool]:
    """Definitions of ``name`` plus bounded text references, ``(defs, refs, truncated)``."""
    if not _IDENT_RE.match(name):
        raise ValueError(f"symbol name must be a plain identifier: {name!r}")

    defs = [d for d in extract_definitions(contents, lang_by_path) if d.name == name]
    def_lines = {(d.path, d.start_line) for d in defs}
    pattern = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")

    refs: list[SymbolReference] = []
    truncated = False
    for path in sorted(contents):
        text = stitch_chunks(contents[path])
        if name not in text:
            continue
        for line_no, line in enumerate(text.split("\n"), start=1):
            if not pattern.search(line):
                continue
            if len(refs) >= MAX_REFERENCES:
                truncated = True
                break
            refs.append(
                SymbolReference(
                    path=path,
                    start_line=line_no,
                    kind="definition" if (path, line_no) in def_lines else "reference",
                    snippet=line.strip()[:SNIPPET_CHARS],
                )
            )
        if truncated:
            break
    return defs, refs, truncated
