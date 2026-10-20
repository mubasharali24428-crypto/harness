"""Sync semantics: create / unchanged / updated, driven by real content, not mocks."""

from __future__ import annotations

import itertools

from harness_wiki_connector import LocalMarkdownWiki, sync_wiki_to_skills


class StubSink:
    """In-memory WikiSkillSink; mirrors the registry's versioned model."""

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.skills: dict[str, dict] = {}  # name -> {"skillId", "latestVersion"}

    async def list_skills(self) -> list[dict]:
        return [
            {"skillId": s["skillId"], "name": name, "latestVersion": s["latestVersion"]}
            for name, s in self.skills.items()
        ]

    async def get_skill(self, skill_id: str, version: int | None = None) -> dict:
        for name, s in self.skills.items():
            if s["skillId"] == skill_id:
                return {
                    "skill": {"skillId": skill_id, "name": name},
                    "version": {"version": version, "content": s["versions"][version]},
                }
        raise AssertionError("unknown skill id")

    async def create_skill(self, *, name: str, content: str) -> dict:
        self.skills[name] = {
            "skillId": f"s{next(self._ids)}",
            "latestVersion": 1,
            "versions": {1: content},
        }
        return {"skill": {"name": name}, "version": {"version": 1}}

    async def add_skill_version(self, skill_id: str, *, content: str) -> dict:
        for s in self.skills.values():
            if s["skillId"] == skill_id:
                v = s["latestVersion"] + 1
                s["latestVersion"] = v
                s["versions"][v] = content
                return {"version": v}
        raise AssertionError("unknown skill id")


async def test_sync_create_then_unchanged_then_updated(tmp_path):
    (tmp_path / "eng").mkdir()
    page = tmp_path / "eng" / "deploys.md"
    page.write_text("# Deploy Runbook\n\nstep one\n")

    sink = StubSink()
    wiki = LocalMarkdownWiki(tmp_path)

    first = await sync_wiki_to_skills(sink, wiki)
    assert first.counts == {"created": 1, "updated": 0, "unchanged": 0}
    assert first.created == ["wiki/eng-deploys"]
    assert sink.skills["wiki/eng-deploys"]["versions"][1] == (
        "# Deploy Runbook\n\nstep one\n"
    )

    second = await sync_wiki_to_skills(sink, wiki)
    assert second.counts == {"created": 0, "updated": 0, "unchanged": 1}

    page.write_text("# Deploy Runbook\n\nstep one\nstep two\n")
    third = await sync_wiki_to_skills(sink, wiki)
    assert third.counts == {"created": 0, "updated": 1, "unchanged": 0}
    assert sink.skills["wiki/eng-deploys"]["latestVersion"] == 2
    assert "step two" in sink.skills["wiki/eng-deploys"]["versions"][2]
    # History is preserved — the registry never edits v1 in place.
    assert "step two" not in sink.skills["wiki/eng-deploys"]["versions"][1]


async def test_sync_adds_new_pages_and_respects_namespace(tmp_path):
    (tmp_path / "a.md").write_text("# A\n\nbody a\n")
    sink = StubSink()
    wiki = LocalMarkdownWiki(tmp_path)

    await sync_wiki_to_skills(sink, wiki, namespace="handbook")
    (tmp_path / "b.md").write_text("no heading\n")
    report = await sync_wiki_to_skills(sink, wiki, namespace="handbook")

    assert report.created == ["handbook/b"]
    assert set(sink.skills) == {"handbook/a", "handbook/b"}
    # Title-less page still gets a heading in the skill body.
    assert sink.skills["handbook/b"]["versions"][1].startswith("# b\n")
