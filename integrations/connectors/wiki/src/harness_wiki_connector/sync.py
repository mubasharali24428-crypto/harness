"""Sync wiki pages into the skills registry as versioned skills.

The sink is duck-typed against the extensions service's REST client
(``ExtensionsClient`` satisfies ``WikiSkillSink`` structurally), so this
package stays service-agnostic and testable with any stub.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .connector import WikiConnector, slugify


class WikiSkillSink(Protocol):
    async def list_skills(self) -> list[dict]: ...

    async def get_skill(self, skill_id: str, version: int | None = None) -> dict: ...

    async def create_skill(self, *, name: str, content: str) -> dict: ...

    async def add_skill_version(self, skill_id: str, *, content: str) -> dict: ...


@dataclass
class SyncReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return {
            "created": len(self.created),
            "updated": len(self.updated),
            "unchanged": len(self.unchanged),
        }


def _render(page_title: str, body: str) -> str:
    body = body.rstrip("\n")
    if body.startswith("# "):
        return body + "\n"
    return f"# {page_title}\n\n{body}\n"


async def sync_wiki_to_skills(
    sink: WikiSkillSink,
    connector: WikiConnector,
    *,
    namespace: str = "wiki",
) -> SyncReport:
    """Idempotent pull sync.

    Skill name = ``{namespace}/{slug(pageId)}``. Content changes add a NEW
    version (the registry's versioned-skills model), never an in-place edit;
    unchanged pages are left alone. Returns counts, honestly.
    """
    report = SyncReport()
    existing: dict[str, dict[str, Any]] = {
        s["name"]: s for s in await sink.list_skills()
    }

    for meta in await connector.list_pages():
        page = await connector.get_page(meta.pageId)
        name = f"{namespace}/{slugify(page.pageId)}"
        content = _render(page.title, page.content)

        target = existing.get(name)
        if target is None:
            await sink.create_skill(name=name, content=content)
            report.created.append(name)
            continue

        detail = await sink.get_skill(target["skillId"], version=target["latestVersion"])
        if detail["version"]["content"] == content:
            report.unchanged.append(name)
        else:
            await sink.add_skill_version(target["skillId"], content=content)
            report.updated.append(name)

    return report
