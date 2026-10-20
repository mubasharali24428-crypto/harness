"""Capture A5-S1 interop artifacts: OpenAPI snapshot + normalized golden fixtures.

What it does (all REAL — no mocks):
  * boots the extensions ASGI app on a temp SQLite DB with a real bearer dep;
  * runs a scripted journey against a genuine MCP fixture server (official SDK,
    streamable HTTP, ephemeral port) — register → discover → grant → call,
    skills, custom tools, catalog install, plus one error path;
  * normalizes volatile fields (uuids, digests, timestamps, ports, latencies);
  * writes ``openapi.json`` and ``golden/NN_*.json`` under
    ``docs/agent-program/a5/session-1/interop/``.

Run from the repo root:
    ./packages/extensions/.venv/bin/python packages/extensions/scripts/capture_golden.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import sys
import tempfile
from typing import Any

import httpx
from fastapi import HTTPException
from harness_contracts.roles import Principal, Role

ROOT = pathlib.Path(__file__).resolve().parents[3]
ROOT_SRC = ROOT / "packages" / "extensions" / "src"
ROOT_TESTS = ROOT / "packages" / "extensions" / "tests"
for p in (str(ROOT_SRC), str(ROOT_TESTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from fixture_server import default_tools, http_mcp_server  # noqa: E402

from harness_extensions.api import make_app  # noqa: E402
from harness_extensions.config import Settings  # noqa: E402

OUT = ROOT / "docs" / "agent-program" / "a5" / "session-1" / "interop"
PREFIX = "/api/v1/extensions"

TEAM_A = "team-a"
PEOPLE = {
    "tok-admin-a": Principal(userId="u-admin-a", teamId=TEAM_A, role=Role.ADMIN),
    "tok-eng-a": Principal(userId="u-eng-a", teamId=TEAM_A, role=Role.ENGINEER),
    "tok-view-a": Principal(userId="u-view-a", teamId=TEAM_A, role=Role.VIEWER),
}

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SHA_RE = re.compile(r"\b[0-9a-f]{64}\b")
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(\+00:00|Z)")
PORT_RE = re.compile(r"http://127\.0\.0\.1:\d+/(mcp|hook)")


def normalize(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, val in value.items():
            if key.endswith("Ms") and isinstance(val, (int, float)):
                out[key] = "<ms>"
            else:
                out[key] = normalize(val)
        return out
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if isinstance(value, str):
        s = UUID_RE.sub("<uuid>", value)
        s = SHA_RE.sub("<sha256>", s)
        s = TS_RE.sub("<timestamp>", s)
        s = PORT_RE.sub(lambda m: f"http://127.0.0.1:<port>/{m.group(1)}", s)
        return s
    return value


class Recorder:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    async def step(
        self,
        name: str,
        client: httpx.AsyncClient,
        token: str,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
    ) -> httpx.Response:
        res = await client.request(
            method, path, json=json_body, headers={"Authorization": f"Bearer {token}"}
        )
        try:
            body = res.json()
        except ValueError:
            body = res.text
        identity = {
            "u-admin-a": "admin@team-a",
            "u-eng-a": "engineer@team-a",
            "u-view-a": "viewer@team-a",
        }[PEOPLE[token].userId]
        request_obj = {
            "method": method,
            "path": path,
            "identity": identity,
            **({"body": json_body} if json_body is not None else {}),
        }
        self.entries.append(
            {
                "name": name,
                "request": normalize(request_obj),
                "response": {"status": res.status_code, "body": normalize(body)},
            }
        )
        return res


async def principal_dep(request) -> Principal:
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    person = PEOPLE.get(token)
    if person is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    return person


async def main() -> None:
    tmp = tempfile.TemporaryDirectory(prefix="a5-s1-capture-")
    settings = Settings(
        db_path=str(pathlib.Path(tmp.name) / "registry.db"),
        allow_private_hosts=True,
        discover_timeout_s=5.0,
        call_timeout_s=5.0,
    )
    app = make_app(settings, get_principal_dep=principal_dep)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "openapi.json").write_text(
        json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    )
    print("wrote", OUT / "openapi.json")

    rec = Recorder()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ext.test") as c:
        async with http_mcp_server(default_tools()) as url:
            reg = await rec.step(
                "register_server",
                c,
                "tok-eng-a",
                "POST",
                f"{PREFIX}/servers",
                json_body={"name": "fixture-echo", "url": url},
            )
            server_id = reg.json()["serverId"]
            await rec.step(
                "list_tool_discovery",
                c,
                "tok-eng-a",
                "GET",
                f"{PREFIX}/servers/{server_id}/tools",
            )
            await rec.step(
                "create_grant",
                c,
                "tok-eng-a",
                "POST",
                f"{PREFIX}/grants",
                json_body={"serverId": server_id, "toolName": "echo"},
            )
            await rec.step(
                "call_tool",
                c,
                "tok-eng-a",
                "POST",
                f"{PREFIX}/calls",
                json_body={"serverId": server_id, "toolName": "echo", "arguments": {"text": "hi"}},
            )
            custom = await rec.step(
                "create_custom_tool",
                c,
                "tok-eng-a",
                "POST",
                f"{PREFIX}/custom-tools",
                json_body={
                    "name": "echo-narrow",
                    "serverId": server_id,
                    "sourceTool": "echo",
                    "allowedArguments": ["text"],
                },
            )
            custom_id = custom.json()["customToolId"]
            await rec.step(
                "call_custom_tool",
                c,
                "tok-eng-a",
                "POST",
                f"{PREFIX}/custom-tools/{custom_id}/call",
                json_body={"arguments": {"text": "wrapped"}},
            )
            await rec.step(
                "call_unknown_tool_error",
                c,
                "tok-eng-a",
                "POST",
                f"{PREFIX}/calls",
                json_body={"serverId": server_id, "toolName": "nope", "arguments": {}},
            )

        skill = await rec.step(
            "create_skill",
            c,
            "tok-eng-a",
            "POST",
            f"{PREFIX}/skills",
            json_body={"name": "release-checklist", "content": "1. freeze\n2. verify\n3. ship"},
        )
        skill_id = skill.json()["skill"]["skillId"]
        await rec.step(
            "add_skill_version",
            c,
            "tok-eng-a",
            "POST",
            f"{PREFIX}/skills/{skill_id}/versions",
            json_body={"content": "1. freeze\n2. verify\n3. ship\n4. postmortem notes"},
        )
        await rec.step(
            "get_skill_latest", c, "tok-eng-a", "GET", f"{PREFIX}/skills/{skill_id}"
        )
        await rec.step("catalog_list", c, "tok-eng-a", "GET", f"{PREFIX}/catalog")
        await rec.step(
            "install_plugin",
            c,
            "tok-eng-a",
            "POST",
            f"{PREFIX}/catalog/incident-response/install",
        )
        await rec.step("plugins_list", c, "tok-eng-a", "GET", f"{PREFIX}/plugins")
        await rec.step("skills_list", c, "tok-eng-a", "GET", f"{PREFIX}/skills")
        await rec.step(
            "viewer_write_denied",
            c,
            "tok-view-a",
            "POST",
            f"{PREFIX}/skills",
            json_body={"name": "nope", "content": "x"},
        )

    golden = OUT / "golden"
    golden.mkdir(exist_ok=True)
    for i, entry in enumerate(rec.entries, start=1):
        path = golden / f"{i:02d}_{entry['name']}.json"
        path.write_text(json.dumps(entry, indent=2) + "\n")
        print("wrote", path.name, entry["response"]["status"])
    tmp.cleanup()
    print(f"{len(rec.entries)} golden fixtures + openapi written")


if __name__ == "__main__":
    asyncio.run(main())
