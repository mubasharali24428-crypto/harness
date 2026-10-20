"""HTTP wiki connector: talks to a JSON wiki API.

API contract (documented in the package README):

    GET {base}/pages            -> {"pages": [{"id", "title", "updatedAt"}]}
    GET {base}/pages/{id}       -> {"id", "title", "content", "updatedAt"}

URL vetting is injected (``url_guard``) so the same connector works in the
extensions service (which passes its url-policy check) and standalone (default
guard: http/https only)."""
from __future__ import annotations

from collections.abc import Callable
from urllib.parse import quote, urlparse

import httpx

from .connector import WikiError, WikiPage

UrlGuard = Callable[[str], None]


def default_url_guard(url: str) -> None:
    scheme = urlparse(url).scheme
    if scheme not in ("http", "https"):
        raise WikiError("WIKI_URL_INVALID", f"refusing non-http(s) url: {url!r}")


class HttpWikiConnector:
    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 15.0,
        url_guard: UrlGuard | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        base_url = base_url.rstrip("/")
        (url_guard or default_url_guard)(base_url)
        self._base = base_url
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = client or httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=timeout
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str) -> dict:
        try:
            res = await self._client.get(path)
        except httpx.HTTPError as exc:
            raise WikiError("WIKI_UNREACHABLE", f"{self._base}{path}: {exc}") from exc
        if res.status_code >= 400:
            raise WikiError(
                "WIKI_HTTP_ERROR", f"{path} returned HTTP {res.status_code}"
            )
        try:
            return res.json()
        except ValueError as exc:
            raise WikiError("WIKI_BAD_RESPONSE", f"{path} did not return JSON") from exc

    async def list_pages(self) -> list[WikiPage]:
        body = await self._get("/pages")
        raw = body.get("pages") if isinstance(body, dict) else body
        if not isinstance(raw, list):
            raise WikiError("WIKI_BAD_RESPONSE", "expected a list of pages")
        return [
            WikiPage(
                pageId=str(item["id"]),
                title=str(item.get("title") or item["id"]),
                updatedAt=item.get("updatedAt"),
            )
            for item in raw
        ]

    async def get_page(self, page_id: str) -> WikiPage:
        # Page ids may be hierarchical ("eng/deploys"); keep "/" literal so
        # both path-style and encoded server routes work.
        body = await self._get(f"/pages/{quote(page_id, safe='/')}")
        if not isinstance(body, dict) or "id" not in body:
            raise WikiError("WIKI_BAD_RESPONSE", f"page {page_id!r} missing fields")
        return WikiPage(
            pageId=str(body["id"]),
            title=str(body.get("title") or body["id"]),
            content=str(body.get("content") or ""),
            updatedAt=body.get("updatedAt"),
        )
