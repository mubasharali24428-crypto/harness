"""Connector core: the WikiPage shape, the connector protocol, and errors."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class WikiError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class WikiPage:
    pageId: str
    title: str
    content: str = ""
    updatedAt: str | None = None


@runtime_checkable
class WikiConnector(Protocol):
    """Anything that can enumerate and fetch wiki pages.

    ``list_pages`` may omit ``content`` (metadata-only listings are cheaper
    over the network); consumers must call ``get_page`` for the body.
    """

    async def list_pages(self) -> list[WikiPage]: ...

    async def get_page(self, page_id: str) -> WikiPage: ...


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, max_len: int = 60) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug[:max_len].strip("-")
