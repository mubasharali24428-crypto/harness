"""Local markdown tree connector: one ``*.md`` file per page."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from .connector import WikiError, WikiPage


class LocalMarkdownWiki:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        if not self.root.is_dir():
            raise WikiError("WIKI_ROOT_MISSING", f"not a directory: {self.root}")

    def _files(self):
        return sorted(p for p in self.root.rglob("*.md") if p.is_file())

    def _page_id(self, path: Path) -> str:
        return path.relative_to(self.root).with_suffix("").as_posix()

    @staticmethod
    def _title(path: Path, text: str) -> str:
        for line in text.splitlines():
            if line.startswith("# "):
                return line[2:].strip() or path.stem
        return path.stem

    async def list_pages(self) -> list[WikiPage]:
        pages = []
        for path in self._files():
            text = path.read_text(encoding="utf-8")
            mtime = _dt.datetime.fromtimestamp(path.stat().st_mtime, tz=_dt.UTC)
            pages.append(
                WikiPage(
                    pageId=self._page_id(path),
                    title=self._title(path, text),
                    content=text,
                    updatedAt=mtime.isoformat(),
                )
            )
        return pages

    async def get_page(self, page_id: str) -> WikiPage:
        path = self.root / f"{page_id}.md"
        if not path.is_file():
            raise WikiError("WIKI_PAGE_NOT_FOUND", f"no page {page_id!r} under {self.root}")
        text = path.read_text(encoding="utf-8")
        mtime = _dt.datetime.fromtimestamp(path.stat().st_mtime, tz=_dt.UTC)
        return WikiPage(
            pageId=page_id,
            title=self._title(path, text),
            content=text,
            updatedAt=mtime.isoformat(),
        )
