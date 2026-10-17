"""Self-contained dependency-graph HTML report (F014 companion surface).

Renders a :class:`harness_context.graph.DependencyGraph` (wire dict) as ONE
standalone HTML document:

* inline SVG only — no JavaScript, no external assets, no CDN links, so the
  page renders identically offline and in a sandboxed viewer;
* every string from the graph is ``html.escape``-d before interpolation —
  file paths, specifiers and language tags are attacker-influenced input
  (they come from repo content) and this page is opened in a browser;
* deterministic layout: nodes sorted by path, placed on an evenly-spaced
  grid (columns = ceil(sqrt(n))), edges drawn between node centers. Same
  graph in -> byte-identical HTML out, which is what the test asserts.

Summary blocks answer the operator questions directly: node/edge counts,
unresolved references (loudly, since they mean indexing/parsing gaps), and
the external-dependency table with importer counts.
"""

from __future__ import annotations

import html
import math

_GRID = 170  # px between node centers
_NODE_W = 150
_NODE_H = 44
_MARGIN = 40


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _layout(nodes: list[dict]) -> dict[str, tuple[float, float]]:
    """path -> (x, y) node top-left on an even grid, sorted by path."""
    ordered = sorted(nodes, key=lambda n: n["path"])
    cols = max(1, math.ceil(math.sqrt(len(ordered))))
    positions: dict[str, tuple[float, float]] = {}
    for idx, node in enumerate(ordered):
        col, row = idx % cols, idx // cols
        positions[node["path"]] = (
            _MARGIN + col * _GRID,
            _MARGIN + row * _GRID,
        )
    return positions


def render_dependency_graph_html(graph: dict, *, title: str = "Dependency graph") -> str:
    """One standalone HTML document for ``graph`` (see graph.to_wire())."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    external = graph.get("external", [])
    unresolved = graph.get("unresolved", 0)
    project_id = graph.get("projectId", "")

    positions = _layout(nodes)
    cols = max(1, math.ceil(math.sqrt(len(nodes)))) if nodes else 1
    rows = math.ceil(len(nodes) / cols) if nodes else 0
    width = _MARGIN * 2 + max(1, cols) * _GRID
    height = _MARGIN * 2 + max(1, rows) * _GRID

    edge_lines = []
    for edge in sorted(edges, key=lambda e: (e["source"], e["target"], e["kind"])):
        src = positions.get(edge["source"])
        dst = positions.get(edge["target"])
        if src is None or dst is None:
            continue
        x1, y1 = src[0] + _NODE_W / 2, src[1] + _NODE_H / 2
        x2, y2 = dst[0] + _NODE_W / 2, dst[1] + _NODE_H / 2
        dash = "" if edge["kind"] == "import" else ' stroke-dasharray="5,4"'
        edge_lines.append(
            f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}"'
            f' stroke="#8a94a6" stroke-width="1"{dash} />'
        )

    node_boxes = []
    for node in sorted(nodes, key=lambda n: n["path"]):
        x, y = positions[node["path"]]
        label = node["path"]
        node_boxes.append(
            f'<g><title>{_esc(label)}</title>'
            f'<rect x="{x:.0f}" y="{y:.0f}" width="{_NODE_W}" height="{_NODE_H}"'
            f' rx="6" fill="#f4f6fb" stroke="#4a5568" stroke-width="1.2" />'
            f'<text x="{x + _NODE_W / 2:.0f}" y="{y + 18:.0f}" text-anchor="middle"'
            f' font-family="ui-monospace,monospace" font-size="11">{_esc(label[:22])}</text>'
            f'<text x="{x + _NODE_W / 2:.0f}" y="{y + 34:.0f}" text-anchor="middle"'
            f' font-family="ui-monospace,monospace" font-size="9" fill="#718096">'
            f"{_esc(node.get('lang', ''))}</text></g>"
        )

    external_rows = "".join(
        f"<tr><td><code>{_esc(dep['specifier'])}</code></td>"
        f"<td>{_esc(dep['count'])}</td>"
        f"<td>{_esc(', '.join(dep['importedBy'][:5]))}"
        f"{' …' if len(dep['importedBy']) > 5 else ''}</td></tr>"
        for dep in external
    )

    unresolved_banner = (
        f'<p class="warn">{_esc(unresolved)} unresolved relative reference(s) —'
        " targets not present in the current index.</p>"
        if unresolved
        else '<p class="ok">No unresolved relative references.</p>'
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{_esc(title)} — {_esc(project_id)}</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 24px; color: #1a202c; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  h2 {{ font-size: 15px; margin: 28px 0 8px; }}
  .muted {{ color: #718096; font-size: 13px; margin: 0 0 16px; }}
  .warn {{ color: #975a16; background: #fffaf0; border: 1px solid #f6e05e;
         padding: 8px 12px; border-radius: 6px; font-size: 13px; }}
  .ok {{ color: #276749; background: #f0fff4; border: 1px solid #9ae6b4;
       padding: 8px 12px; border-radius: 6px; font-size: 13px; }}
  svg {{ border: 1px solid #e2e8f0; border-radius: 8px; background: #fff;
        max-width: 100%; height: auto; }}
  table {{ border-collapse: collapse; font-size: 13px; }}
  td, th {{ border: 1px solid #e2e8f0; padding: 4px 10px; text-align: left; }}
  code {{ background: #edf2f7; padding: 1px 4px; border-radius: 3px; }}
</style>
</head>
<body>
<h1>{_esc(title)}</h1>
<p class="muted">project {_esc(project_id)} — {_esc(len(nodes))} files,
{_esc(len(edges))} internal edges, {_esc(len(external))} external specifiers</p>
{unresolved_banner}
<h2>Graph</h2>
<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"
     role="img" aria-label="dependency graph">
{''.join(edge_lines)}
{''.join(node_boxes)}
</svg>
<h2>External dependencies</h2>
<table>
<thead><tr><th>Specifier</th><th>Importers</th><th>Files</th></tr></thead>
<tbody>{external_rows or '<tr><td colspan="3">none</td></tr>'}</tbody>
</table>
</body>
</html>
"""
