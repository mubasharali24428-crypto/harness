"""Editor entry points must agree with the real service contract (no host IDE needed)."""

import re
from pathlib import Path

from harness_devworkflow.app import create_app
from harness_devworkflow.config import Config

EDITORS = Path(__file__).resolve().parents[2]


def _templates(tmp_path) -> dict[str, set[str]]:
    app = create_app(Config(None, None, None, None, tmp_path))
    out: dict[str, set[str]] = {}
    for path, ops in app.openapi()["paths"].items():
        out[re.sub(r"\{[^}]+\}", "{}", path)] = {m.upper() for m in ops}
    return out


def test_jetbrains_http_requests_match_the_real_openapi(tmp_path):
    spec = _templates(tmp_path)
    text = (EDITORS / "jetbrains" / "harness.http").read_text()
    reqs = re.findall(r"^(GET|POST|PUT|DELETE)\s+\{\{(\w+)\}\}(\S+)", text, re.MULTILINE)
    assert len(reqs) >= 7
    checked = 0
    for method, svc, raw in reqs:
        path = re.sub(r"\{\{\w+\}\}", "{}", raw.split("?")[0])
        assert path.startswith("/api/v1/"), raw
        if svc == "workflow":
            assert method in spec.get(path, set()), f"{method} {path} not in service OpenAPI"
            checked += 1
    assert checked == 4
    # no request in the shipped file may merge anything
    assert not [p for _, _, p in reqs if p.rstrip("/").endswith("/merge")]


def test_vscode_client_paths_exist_in_openapi(tmp_path):
    spec = _templates(tmp_path)
    src = (EDITORS / "vscode" / "src" / "core" / "protocol.ts").read_text()
    wf = re.findall(r'"/api/v1/developer-workflow[^"`]*|`/api/v1/developer-workflow[^`]*`', src)
    assert wf
    for raw in wf:
        raw = raw.strip('"`').split("?")[0]
        path = re.sub(r"\$\{[^}]+\}", "{}", raw)
        assert path in spec, f"{path} missing from OpenAPI"


def test_no_editor_artifact_ships_a_token_literal():
    pat = re.compile(r"(?i)bearer\s+(?!\{\{|\$\{|\[REDACTED\]|\"|'|\s|\.\.\.)[A-Za-z0-9._-]{12,}")
    for f in EDITORS.rglob("*"):
        shipped = not any(p in f.parts for p in ("node_modules", ".venv", "dist", "test", "spec"))
        if f.is_file() and f.suffix in {".ts", ".lua", ".http", ".json", ".md", ".mjs"} and shipped:
            if f.name == "package-lock.json":
                continue
            assert not pat.search(f.read_text(errors="ignore")), f"credential-looking text in {f}"
