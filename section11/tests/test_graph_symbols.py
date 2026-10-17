"""F014/F015 proofs: dependency graph + symbol map/references.

Graph: python imports resolved to in-repo nodes (ast), TS/JS import-require
regexes, markdown relative links, externals aggregated, unresolved counted.
Symbols: python definitions with exact spans, regex-based TS/JS (endLine
null — honest about its precision), reference search bounded and
identifier-validated.
"""

import asyncio

from conftest import AUTH_HEADERS, seed_project

from harness_context.graph import build_dependency_graph, stitch_chunks
from harness_context.symbols import find_symbol, list_symbols

PROJECT = "p-graph"


def _rows(text: str, start: int = 1, lang: str = "python") -> list[dict]:
    return [
        {
            "lang": lang,
            "startLine": start,
            "endLine": start + text.count("\n"),
            "content": text,
        }
    ]


# ------------------------------------------------------------------- graph


def test_python_imports_resolve_to_repo_nodes_and_externals():
    contents = {
        "app/main.py": _rows("import os\nfrom pkg import util\nimport pkg.util\n"),
        "pkg/util.py": _rows("def helper():\n    return 1\n"),
        "pkg/__init__.py": _rows(""),
    }
    g = build_dependency_graph("p", contents)
    wire = g.to_wire()
    edges = {(e["source"], e["target"], e["kind"]) for e in wire["edges"]}
    assert ("app/main.py", "pkg/util.py", "import") in edges
    assert any(d["specifier"] == "os" for d in wire["external"])
    node_paths = {n["path"] for n in wire["nodes"]}
    assert {"app/main.py", "pkg/util.py", "pkg/__init__.py"} <= node_paths


def test_js_and_markdown_edges():
    contents = {
        "web/index.ts": _rows(
            "import { x } from './lib/thing';\nconst y = require('./util');\n",
            lang="typescript",
        ),
        "web/lib/thing.ts": _rows("export const x = 1;\n", lang="typescript"),
        "web/util.js": _rows("module.exports = {};\n", lang="javascript"),
        "docs/guide.md": _rows("[api](../web/index.ts)\n", lang="markdown"),
    }
    wire = build_dependency_graph("p", contents).to_wire()
    edges = {(e["source"], e["target"]) for e in wire["edges"]}
    assert ("web/index.ts", "web/lib/thing.ts") in edges
    assert ("web/index.ts", "web/util.js") in edges
    assert ("docs/guide.md", "web/index.ts") in edges
    assert wire["unresolved"] == 0


def test_unresolved_relative_import_is_counted_not_faked():
    contents = {"a.py": _rows("from . import missing\n")}
    wire = build_dependency_graph("p", contents).to_wire()
    assert wire["edges"] == []
    assert wire["unresolved"] == 1


def test_stitch_chunks_first_write_wins_on_overlap():
    """Overlapping line 3 diverges: the earlier chunk's line wins (exact for
    identical content, harmless in the pathological case), later lines fill in."""
    rows = [
        {"startLine": 1, "endLine": 3, "content": "a\nb\nc"},
        {"startLine": 3, "endLine": 5, "content": "c2\nd\ne"},
    ]
    assert stitch_chunks(rows) == "a\nb\nc\nd\ne"


# ----------------------------------------------------------------- symbols


def test_python_definitions_have_exact_spans():
    contents = {
        "svc.py": _rows(
            "class Store:\n"
            "    def put(self):\n"
            "        pass\n"
            "\n"
            "    class Inner:\n"
            "        pass\n"
            "\n"
            "def top():\n"
            "    pass\n"
        )
    }
    defs, truncated = list_symbols(contents)
    assert truncated is False
    by_name = {d.name: d for d in defs}
    assert by_name["Store"].kind == "class"
    assert by_name["Store"].start_line == 1
    assert by_name["Store"].end_line == 6
    assert by_name["put"].qualname == "Store.put"
    assert by_name["put"].kind == "method"
    assert by_name["Inner"].qualname == "Store.Inner"
    assert by_name["top"].kind == "function"
    assert by_name["top"].start_line == 8


def test_find_symbol_separates_definitions_from_references():
    contents = {
        "svc.py": _rows("def widget():\n    return 1\n"),
        "use.py": _rows("from svc import widget\n\nwidget()\n"),
    }
    defs, refs, truncated = find_symbol(contents, None, "widget")
    assert truncated is False
    assert [(d.path, d.start_line) for d in defs] == [("svc.py", 1)]
    kinds = {(r.path, r.start_line): r.kind for r in refs}
    assert kinds[("svc.py", 1)] == "definition"
    assert kinds[("use.py", 1)] == "reference"
    assert kinds[("use.py", 3)] == "reference"
    # Substring safety: 'widgets' inside another identifier must not match.
    _, refs2, _ = find_symbol({"x.py": _rows("widgets = 1\n")}, None, "widget")
    assert refs2 == []


def test_find_symbol_rejects_non_identifier():
    try:
        find_symbol({}, None, "not a name!")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_ts_definition_endline_is_honestly_null():
    contents = {"a.ts": _rows("export function alpha() {\n}\n", lang="typescript")}
    defs, _ = list_symbols(contents)
    alpha = next(d for d in defs if d.name == "alpha")
    assert alpha.start_line == 1
    assert alpha.end_line is None


# ------------------------------------------------------------- HTTP level


async def _index(client, repo) -> None:
    res = await client.post(
        "/api/v1/context/index",
        json={"repoPath": str(repo), "projectId": PROJECT},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 200, res.text
    job_id = res.json()["jobId"]
    for _ in range(200):
        st = (
            await client.get(f"/api/v1/context/index/{job_id}", headers=AUTH_HEADERS)
        ).json()
        if st["status"] in ("completed", "failed"):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("index job did not finish")


async def test_graph_and_symbols_over_http(client, db, mini_repo):
    await seed_project(db, PROJECT)
    await _index(client, mini_repo)

    res = await client.get(f"/api/v1/context/graph/{PROJECT}", headers=AUTH_HEADERS)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["projectId"] == PROJECT
    assert "pkg/widget_store.py" in {n["path"] for n in body["nodes"]}
    assert "pkg/telemetry.py" in {n["path"] for n in body["nodes"]}
    assert isinstance(body["external"], list)

    res = await client.get(f"/api/v1/context/symbols/{PROJECT}", headers=AUTH_HEADERS)
    assert res.status_code == 200
    names = {d["name"] for d in res.json()["definitions"]}
    assert {"WidgetStore", "put", "emit"} <= names

    res = await client.get(
        f"/api/v1/context/symbols/{PROJECT}?name=WidgetStore", headers=AUTH_HEADERS
    )
    assert res.status_code == 200
    body = res.json()
    assert [d["name"] for d in body["definitions"]] == ["WidgetStore"]
    assert body["references"][0]["kind"] == "definition"

    res = await client.get(
        f"/api/v1/context/symbols/{PROJECT}?name=bad%20name", headers=AUTH_HEADERS
    )
    assert res.status_code == 422


async def test_graph_requires_ownership(client, db):
    await seed_project(db, "p-foreign", team_id="team-other")
    res = await client.get("/api/v1/context/graph/p-foreign", headers=AUTH_HEADERS)
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "NOT_FOUND"
