"""M23-Z2 bet A — AST-aware .py chunking (chunking.py upgrade).

Contract under test:

- Parseable .py splits at TOP-LEVEL STATEMENT boundaries (docstrings,
  imports, assignments, functions, classes — decorators and bodies stay
  attached to their statement instead of being cut by a line window).
- Any .py that fails to parse (and every non-.py language) keeps the exact
  M21-X1 overlapping line-window geometry — fallback can never regress.
- Whitespace-only gaps between statements never become chunks.
- An oversize statement (> MAX_AST_CHUNK_LINES) is re-split into overlapping
  windows so a monster function cannot balloon one embedding.
- Same bytes -> same chunk hashes (incremental law), and a legacy line-window
  index migrates to AST geometry through the normal incremental re-index.
"""

from pathlib import Path

import pytest
from sqlalchemy import text

import harness_context.service as service_module
from harness_context.chunking import (
    DEFAULT_OVERLAP,
    DEFAULT_WINDOW,
    MAX_AST_CHUNK_LINES,
    _py_statement_spans,
    _window_chunks,
    chunk_file,
    chunk_python_text,
    chunk_text,
    iter_repo_files,
    lang_for,
)
from harness_context.embeddings import HashingEmbedding
from harness_context.service import ContextService, IndexJob

PROJECT = "p-ast"

STRUCTURED_SRC = '''\
"""Widget inventory utilities."""
import os

MAX_WIDGETS = 42


class WidgetStore:
    """Hard per-team quota storage."""

    def __init__(self, quota: int = MAX_WIDGETS):
        self.quota = quota
        self.widgets: dict[str, dict] = {}

    def put(self, widget_id: str, payload: dict) -> bool:
        """Store a widget; refuse once the quota is exhausted."""
        if len(self.widgets) >= self.quota:
            return False
        self.widgets[widget_id] = payload
        return True


@dataclasses.dataclass(frozen=True)
class WidgetRecord:
    widget_id: str
    payload: dict


def sync_widget_inventory(store, catalog) -> list:
    """Synchronize widget inventory against the catalog."""
    diffs: list[tuple] = []
    for widget_id, desired in catalog.items():
        current = store.widgets.get(widget_id)
        if current is None:
            diffs.append(("insert", widget_id, desired))
        elif current != desired:
            diffs.append(("update", widget_id, desired))
    return diffs
'''

# Long enough that NO default window can span header and tail together.
LONG_FN_SRC_HEADER = 'def reconcile_team_quotas(ledger):\n'
LONG_FN_BODY = [
    '    """Check every quota entry in the ledger."""\n',
    '    checks = []\n',
]
_LONG_FILLER_WORDS = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]
for _i in range(30):
    LONG_FN_BODY.append(
        f'    checks.append("{_LONG_FILLER_WORDS[_i % len(_LONG_FILLER_WORDS)]}"\n'
        f'                 "-{_LONG_FILLER_WORDS[(_i * 2) % len(_LONG_FILLER_WORDS)]}")\n'
    )
LONG_FN_TAIL = '    vacuum_stale_entries(ledger)\n    return checks\n'
LONG_FN_SRC = (
    '"""Plan reconciliation helpers."""\n\n'
    + LONG_FN_SRC_HEADER
    + "".join(LONG_FN_BODY)
    + LONG_FN_TAIL
)


def _bounds(chunks) -> list[tuple[int, int]]:
    return [(c.start_line, c.end_line) for c in chunks]


def _legacy_bounds(rel_path: str, src: str, *, window: int, overlap: int):
    """The M21-X1 geometry, computed directly (pre-bet reference)."""
    return _bounds(
        _window_chunks(
            rel_path, "python", src.splitlines(), window=window, overlap=overlap
        )
    )


# --------------------------------------------------------------------- shapes


def test_top_level_statements_stay_intact():
    chunks = chunk_text("store.py", STRUCTURED_SRC, window=120, overlap=20)
    contents = [c.content for c in chunks]

    # Every top-level statement survives verbatim inside exactly one chunk.
    for marker in (
        '"""Widget inventory utilities."""',
        "import os",
        "MAX_WIDGETS = 42",
        "@dataclasses.dataclass(frozen=True)",
    ):
        owners = [c for c in contents if marker in c]
        assert len(owners) == 1, marker

    cls = next(c for c in contents if "class WidgetStore:" in c)
    # Decorator-free class: header, BOTH methods, and the last return line.
    assert "class WidgetStore:" in cls
    assert "def put(self" in cls
    assert "return True" in cls

    deco = next(c for c in contents if c.startswith("@"))
    assert deco.startswith("@dataclasses.dataclass(frozen=True)")
    assert "class WidgetRecord:" in deco

    fn = next(c for c in contents if "def sync_widget_inventory" in c)
    assert fn.startswith("def sync_widget_inventory")
    assert 'diffs.append(("update", widget_id, desired))' in fn

    # Chunks do not overlap and never leave gaps over non-blank lines.
    ordered = sorted(chunks, key=lambda c: c.start_line)
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        assert nxt.start_line > prev.end_line
        gap = STRUCTURED_SRC.splitlines()[prev.end_line : nxt.start_line - 1]
        assert not "".join(gap).strip(), (prev, nxt)


def test_decorator_lines_are_part_of_the_statement_span():
    spans = _py_statement_spans(STRUCTURED_SRC.splitlines())
    lines = STRUCTURED_SRC.splitlines()
    deco_start = next(i for i, ln in enumerate(lines) if ln.startswith("@"))
    owner = [s for s in spans if s[0] <= deco_start <= s[1]]
    assert owner and owner[0][0] == deco_start  # span STARTS at the @ line


def test_whitespace_only_gaps_yield_no_chunks():
    src = "A = 1\n\n\n\n\nB = 2\n"
    chunks = chunk_text("gaps.py", src)
    assert _bounds(chunks) == [(1, 1), (6, 6)]
    assert all(c.content.strip() for c in chunks)


def test_empty_and_whitespace_files_yield_nothing():
    assert chunk_text("empty.py", "") == []
    assert chunk_text("blank.py", "\n\n\n") == []


def test_syntax_error_py_falls_back_to_exact_legacy_windows():
    broken = "def broken(:\n    pass\nx = \n"
    tagged = chunk_python_text("broken.py", broken)
    assert [t for _, t in tagged] == ["window"]
    assert _bounds([c for c, _ in tagged]) == _legacy_bounds(
        "broken.py", broken, window=DEFAULT_WINDOW, overlap=DEFAULT_OVERLAP
    )
    # chunk_text agrees with the explicit fallback API.
    assert chunk_text("broken.py", broken) == [c for c, _ in tagged]


def test_non_python_languages_keep_window_geometry():
    md = "\n".join(f"line {i}" for i in range(1, 251))  # 250 lines
    chunks = chunk_text("big.md", md, window=120, overlap=20)
    assert _bounds(chunks) == [(1, 120), (101, 220), (201, 250)]


def test_oversize_statement_is_windowed_not_one_blob():
    n = MAX_AST_CHUNK_LINES + 100  # force the oversize branch
    src = "def blob():\n" + "".join("    pass\n" for _ in range(n - 1))
    tagged = chunk_python_text("blob.py", src)
    assert any(t == "window" for _, t in tagged)
    # Window slices sit INSIDE the statement span; no chunk crosses another.
    spans = _py_statement_spans(src.splitlines())
    assert spans == [(0, n - 1)]
    for c, tag in tagged:
        if tag == "window":
            assert c.end_line - c.start_line + 1 <= MAX_AST_CHUNK_LINES


def test_same_bytes_same_hashes_changed_bytes_change_them():
    a = [c.chunk_hash for c in chunk_text("s.py", STRUCTURED_SRC)]
    b = [c.chunk_hash for c in chunk_text("s.py", STRUCTURED_SRC)]
    assert a == b
    edited = STRUCTURED_SRC.replace("return True", "return False")
    c = [x.chunk_hash for x in chunk_text("s.py", edited)]
    assert len(a) == len(c)
    assert a != c  # the touched statement's identity moved


def test_chunk_file_matches_chunk_text(tmp_path):
    (tmp_path / "f.py").write_text(STRUCTURED_SRC, encoding="utf-8")
    assert chunk_file(tmp_path, "f.py") == chunk_text("f.py", STRUCTURED_SRC)


# ------------------------------------------------------------------ service


@pytest.fixture()
def ast_repo(tmp_path: Path) -> Path:
    root = tmp_path / "astrepo"
    root.mkdir(parents=True)
    (root / "pkg").mkdir()
    (root / "pkg" / "store.py").write_text(STRUCTURED_SRC, encoding="utf-8")
    (root / "notes.md").write_text(
        "# Notes\n\nplain markdown rides line windows as always.\n", encoding="utf-8"
    )
    return root


def _service(db) -> ContextService:
    return ContextService(db, HashingEmbedding(dims=256), window=6, overlap=2)


async def _stored_bounds(db, path: str) -> list[tuple[int, int]]:
    async with db() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT start_line, end_line FROM context_chunks"
                    " WHERE project_id = :p AND path = :path ORDER BY start_line"
                ),
                {"p": PROJECT, "path": path},
            )
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


async def test_legacy_window_index_migrates_to_ast_geometry_on_reindex(
    db, ast_repo, monkeypatch
):
    """Incremental law across the CHUNKER UPGRADE itself: seed the index with
    pre-bet line-window geometry, then reindex with AST chunking — every .py
    file's chunk_hash set changed, so it is re-embedded and stale windows are
    pruned; the untouched .md skips; a third pass is a full skip."""
    svc = _service(db)

    real_chunk_file = service_module.chunk_file

    def legacy_chunk_file(root, rel_path, *, window, overlap):
        src = Path(root, rel_path).read_text(encoding="utf-8", errors="replace")
        return _window_chunks(
            rel_path, lang_for(rel_path), src.splitlines(), window=window, overlap=overlap
        )

    monkeypatch.setattr(service_module, "chunk_file", legacy_chunk_file)
    await svc.reindex(IndexJob(id="j1", project_id=PROJECT, repo_path=str(ast_repo)), ast_repo)
    legacy_py_bounds = await _stored_bounds(db, "pkg/store.py")
    monkeypatch.setattr(service_module, "chunk_file", real_chunk_file)

    # Sanity: the seeded geometry really was the legacy window shape.
    assert legacy_py_bounds != _bounds(chunk_file(ast_repo, "pkg/store.py"))

    job2 = IndexJob(id="j2", project_id=PROJECT, repo_path=str(ast_repo))
    await svc.reindex(job2, ast_repo)
    assert job2.files_skipped == 1  # notes.md — hash unchanged
    assert job2.files_indexed == 1  # pkg/store.py — chunk_hash set changed
    assert job2.chunks_indexed == len(chunk_file(ast_repo, "pkg/store.py"))
    assert await _stored_bounds(db, "pkg/store.py") == _bounds(
        chunk_file(ast_repo, "pkg/store.py")
    )

    embeds_after_migration = svc.embed_calls
    job3 = IndexJob(id="j3", project_id=PROJECT, repo_path=str(ast_repo))
    await svc.reindex(job3, ast_repo)
    assert job3.files_indexed == 0 and job3.files_skipped == 2
    assert svc.embed_calls == embeds_after_migration  # zero re-embeds


async def test_symbol_query_improves_on_ast_geometry(db, ast_repo, monkeypatch):
    """Before/after on the SAME repo + backend. Under legacy windows the
    function header and its far-away body sit >window lines apart, so NO
    stored chunk can ever return them together; under AST the whole function
    is retrievable as ONE scored hit."""
    import harness_context.service as svc_mod

    svc = _service(db)

    async def hits_for(project_id: str, query: str):
        async with db() as session:
            return await svc.search(await session.connection(), query, project_id, top_k=8)

    # BEFORE: force the legacy chunker.
    def legacy_chunk_file(root, rel_path, *, window, overlap):
        src = Path(root, rel_path).read_text(encoding="utf-8", errors="replace")
        return _window_chunks(
            rel_path, lang_for(rel_path), src.splitlines(), window=window, overlap=overlap
        )

    monkeypatch.setattr(svc_mod, "chunk_file", legacy_chunk_file)
    await svc.reindex(IndexJob(id="b1", project_id="p-before", repo_path=str(ast_repo)), ast_repo)
    before = await hits_for("p-before", "sync widget inventory")
    assert before, "legacy index returned nothing"
    # Geometry law: header (line 26) and its last body statement (>6 lines
    # below) cannot share a window=6 chunk — intact function UNRETRIEVABLE.
    assert not any(
        "def sync_widget_inventory" in h.content and 'diffs.append(("update"' in h.content
        for h in before
    )

    monkeypatch.undo()
    # AFTER: the shipped AST chunker.
    await svc.reindex(IndexJob(id="a1", project_id="p-after", repo_path=str(ast_repo)), ast_repo)
    after = await hits_for("p-after", "sync widget inventory")
    intact = [
        h
        for h in after
        if h.content.startswith("def sync_widget_inventory")
        and 'diffs.append(("update"' in h.content
    ]
    assert intact, [h.content[:60] for h in after]


async def test_broken_py_still_indexes_through_service(db, tmp_path):
    root = tmp_path / "oddrepo"
    root.mkdir()
    (root / "tragic.py").write_text("def oops(:\n", encoding="utf-8")
    (root / "fine.py").write_text("OK = True\n", encoding="utf-8")
    svc = _service(db)
    job = IndexJob(id="j1", project_id=PROJECT, repo_path=str(root))
    await svc.reindex(job, root)
    assert sorted(iter_repo_files(root)) == ["fine.py", "tragic.py"]
    assert job.files_indexed == 2  # fallback kept the odd bytes indexable
