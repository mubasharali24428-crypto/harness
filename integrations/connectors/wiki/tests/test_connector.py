"""Local + HTTP wiki connector behavior, against real files and a real server."""

from __future__ import annotations

import pytest
from wiki_fixture import wiki_server

from harness_wiki_connector import (
    HttpWikiConnector,
    LocalMarkdownWiki,
    WikiError,
    default_url_guard,
    slugify,
)

PAGES = {
    "eng/deploys": {
        "title": "Deploy Runbook",
        "content": "# Deploy Runbook\n\n1. freeze\n2. ship\n",
        "updatedAt": "2026-10-01T00:00:00Z",
    },
    "eng/oncall": {
        "title": "On-call 101",
        "content": "# On-call 101\n\npage the primary.\n",
        "updatedAt": "2026-10-02T00:00:00Z",
    },
}


def test_slugify():
    assert slugify("Deploy Runbook!") == "deploy-runbook"
    assert slugify("  --weird//name-- ") == "weird-name"


async def test_local_markdown_connector(tmp_path):
    (tmp_path / "eng").mkdir()
    (tmp_path / "eng" / "deploys.md").write_text("# Deploy Runbook\n\nstep one\n")
    (tmp_path / "notes.md").write_text("no heading here\n")

    wiki = LocalMarkdownWiki(tmp_path)
    pages = await wiki.list_pages()
    assert [p.pageId for p in pages] == ["eng/deploys", "notes"]
    assert pages[0].title == "Deploy Runbook"
    assert pages[1].title == "notes"  # falls back to the file stem

    page = await wiki.get_page("eng/deploys")
    assert page.title == "Deploy Runbook"
    assert "step one" in page.content
    assert page.updatedAt is not None

    with pytest.raises(WikiError) as err:
        await wiki.get_page("nope")
    assert err.value.code == "WIKI_PAGE_NOT_FOUND"


def test_local_markdown_requires_a_real_root(tmp_path):
    with pytest.raises(WikiError) as err:
        LocalMarkdownWiki(tmp_path / "missing")
    assert err.value.code == "WIKI_ROOT_MISSING"


async def test_http_connector_against_real_server():
    async with wiki_server(PAGES) as base:
        wiki = HttpWikiConnector(base)
        try:
            metas = await wiki.list_pages()
            assert [m.pageId for m in metas] == ["eng/deploys", "eng/oncall"]
            assert all(m.content == "" for m in metas)  # listings are metadata-only

            page = await wiki.get_page("eng/deploys")
            assert page.title == "Deploy Runbook"
            assert "1. freeze" in page.content
            assert page.updatedAt == "2026-10-01T00:00:00Z"

            with pytest.raises(WikiError) as err:
                await wiki.get_page("missing")
            assert err.value.code == "WIKI_HTTP_ERROR"
        finally:
            await wiki.aclose()


def test_url_guard_runs_before_any_request():
    with pytest.raises(WikiError) as err:
        HttpWikiConnector("ftp://wiki.internal")
    assert err.value.code == "WIKI_URL_INVALID"

    def deny_private(url: str) -> None:
        raise WikiError("WIKI_URL_REFUSED", "private host")

    with pytest.raises(WikiError) as err2:
        HttpWikiConnector("http://127.0.0.1:9", url_guard=deny_private)
    assert err2.value.code == "WIKI_URL_REFUSED"

    assert default_url_guard("https://wiki.example.com") is None


async def test_unreachable_server_is_a_typed_error():
    wiki = HttpWikiConnector("http://127.0.0.1:9", timeout=0.5)
    try:
        with pytest.raises(WikiError) as err:
            await wiki.list_pages()
        assert err.value.code == "WIKI_UNREACHABLE"
    finally:
        await wiki.aclose()
