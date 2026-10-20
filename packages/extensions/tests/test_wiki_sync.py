"""F125 end to end: wiki pages -> real extensions service -> versioned skills.

Runs the actual ASGI service, the real ExtensionsClient, the real connector,
and a real local wiki HTTP server. Nothing about this path is mocked.
"""

from __future__ import annotations

import pathlib
import sys

import httpx
from conftest import build_app, make_settings
from harness_wiki_connector import HttpWikiConnector, LocalMarkdownWiki, sync_wiki_to_skills

from harness_extensions.client import ExtensionsClient

# The wiki fixture server lives next to its connector (integrations/...); make
# it importable here so the integration test drives the REAL server, not a copy.
sys.path.insert(
    0,
    str(
        pathlib.Path(__file__).resolve().parents[3]
        / "integrations"
        / "connectors"
        / "wiki"
        / "tests"
    ),
)
from wiki_fixture import wiki_server  # noqa: E402


async def _run(tmp_path, connector):
    settings = make_settings(tmp_path)
    app = build_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ext.test") as http:
        client = ExtensionsClient("http://ext.test", token="tok-eng-a", client=http)
        return await sync_wiki_to_skills(client, connector)


async def test_local_markdown_pages_sync_into_registry(tmp_path):
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    (wiki_root / "deploys.md").write_text("# Deploy Runbook\n\nfreeze, ship, unfreeze.\n")

    report = await _run(tmp_path, LocalMarkdownWiki(wiki_root))
    assert report.counts == {"created": 1, "updated": 0, "unchanged": 0}

    settings2 = make_settings(tmp_path)
    # Same DB file: read back through a fresh app instance.
    app2 = build_app(settings2)
    transport2 = httpx.ASGITransport(app=app2)
    async with httpx.AsyncClient(transport=transport2, base_url="http://ext.test") as http2:
        client2 = ExtensionsClient("http://ext.test", token="tok-view-a", client=http2)
        skills = await client2.list_skills()
        assert [s["name"] for s in skills] == ["wiki/deploys"]
        detail = await client2.get_skill(skills[0]["skillId"])
        assert detail["version"]["content"] == "# Deploy Runbook\n\nfreeze, ship, unfreeze.\n"


async def test_http_wiki_sync_is_idempotent_and_versions_changes(tmp_path):
    pages = {
        "eng/oncall": {
            "title": "On-call 101",
            "content": "# On-call 101\n\npage the primary.\n",
            "updatedAt": "2026-10-02T00:00:00Z",
        }
    }
    async with wiki_server(pages) as base:
        wiki = HttpWikiConnector(base)

        first = await _run(tmp_path, wiki)
        assert first.counts == {"created": 1, "updated": 0, "unchanged": 0}

        second = await _run(tmp_path, wiki)
        assert second.counts == {"created": 0, "updated": 0, "unchanged": 1}

        pages["eng/oncall"]["content"] = "# On-call 101\n\npage primary, then backup.\n"
        third = await _run(tmp_path, wiki)
        assert third.counts == {"created": 0, "updated": 1, "unchanged": 0}

        settings = make_settings(tmp_path)
        app = build_app(settings)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ext.test") as http:
            client = ExtensionsClient("http://ext.test", token="tok-view-a", client=http)
            (skill,) = await client.list_skills()
            assert skill["latestVersion"] == 2
            v1 = await client.get_skill(skill["skillId"], version=1)
            v2 = await client.get_skill(skill["skillId"], version=2)
            assert "backup" not in v1["version"]["content"]
            assert "backup" in v2["version"]["content"]
        await wiki.aclose()
