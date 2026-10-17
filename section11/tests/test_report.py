"""F014 report proofs: standalone HTML dependency-graph report.

The report is a single self-contained HTML document (no JS, no CDN) with
every interpolated string escaped — file names are attacker-adjacent input
(they come from a repo being indexed), so escaping is a security property
here, not cosmetics.
"""

import asyncio

from conftest import AUTH_HEADERS, seed_project

from harness_context.graph import DependencyGraph, GraphEdge, GraphNode
from harness_context.report import render_dependency_graph_html

PROJECT = "p-report"


def _graph_wire():
    g = DependencyGraph(project_id=PROJECT)
    g.nodes = [
        GraphNode(path="pkg/app.py", lang="python"),
        GraphNode(path="pkg/util.py", lang="python"),
    ]
    g.edges = [
        GraphEdge(source="pkg/app.py", target="pkg/util.py", kind="import")
    ]
    g.external = [
        {"specifier": "os", "count": 1, "importedBy": ["pkg/app.py"]}
    ]
    g.unresolved = 2
    return g.to_wire()


def test_report_renders_svg_and_escapes_names():
    wire = {
        "projectId": "p<&>",
        "nodes": [{"path": "<script>alert(1)</script>.py", "lang": "python"}],
        "edges": [],
        "external": [
            {"specifier": "os<script>", "count": 1, "importedBy": ["<img>.py"]}
        ],
        "unresolved": 1,
    }
    page = render_dependency_graph_html(wire, title="Graph <for> p")
    assert page.lower().startswith("<!doctype html>")
    assert "<svg" in page
    # No UNESCAPED tag survives from any interpolated string...
    assert "<script>" not in page
    assert "<img>" not in page
    # ...the payload is rendered inert as escaped text instead.
    assert "&lt;script&gt;alert(1)&lt;/script&gt;.py" in page
    assert "os&lt;script&gt;" in page
    assert "&lt;img&gt;.py" in page
    assert "Graph &lt;for&gt; p" in page


def test_report_is_deterministic():
    wire = _graph_wire()
    assert render_dependency_graph_html(wire, title="t") == render_dependency_graph_html(
        wire, title="t"
    )


def test_report_handles_empty_graph():
    page = render_dependency_graph_html(
        {"projectId": "p", "nodes": [], "edges": [], "external": [], "unresolved": 0},
        title="empty",
    )
    assert "<svg" in page  # still a valid (empty) drawing


def test_report_shows_unresolved_banner():
    page = render_dependency_graph_html(_graph_wire(), title="t")
    assert "2 unresolved relative reference(s)" in page


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


async def test_report_route_serves_html(client, db, mini_repo):
    await seed_project(db, PROJECT)
    await _index(client, mini_repo)

    res = await client.get(
        f"/api/v1/context/report/graph/{PROJECT}", headers=AUTH_HEADERS
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert PROJECT in res.text


async def test_report_route_requires_ownership(client, db):
    await seed_project(db, "p-foreign", team_id="team-other")
    res = await client.get(
        "/api/v1/context/report/graph/p-foreign", headers=AUTH_HEADERS
    )
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "NOT_FOUND"
