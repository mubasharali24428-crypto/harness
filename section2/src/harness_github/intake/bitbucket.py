"""F039: minimal Bitbucket Cloud client. Merge/approve/decline are blocked in code."""

from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from .gitlab import validate_api_base

_BLOCKED = re.compile(r"/pullrequests/\d+/(merge|approve|decline|request-changes)/?$")
API_BASE = "https://api.bitbucket.org"


class BitbucketGuardViolation(RuntimeError):
    pass


def _guard(method: str, path: str) -> None:
    if _BLOCKED.search(path.split("?", 1)[0]) or (
        method in {"PUT", "DELETE"} and "/pullrequests/" in path
    ):
        raise BitbucketGuardViolation(f"forbidden Bitbucket operation: {method} {path}")


class BitbucketClient:
    def __init__(
        self,
        token: str,
        api_base: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base = validate_api_base(api_base or API_BASE)
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        self._transport = transport

    async def post_comment(self, full_name: str, kind: str, number: int, text: str) -> None:
        if kind not in ("issues", "pullrequests"):
            raise ValueError("kind must be issues|pullrequests")
        ws, _, repo = full_name.partition("/")
        slug = f"{quote(ws, safe='')}/{quote(repo, safe='')}"
        path = f"/2.0/repositories/{slug}/{kind}/{int(number)}/comments"
        _guard("POST", path)
        async with httpx.AsyncClient(
            base_url=self.base,
            headers=self._headers,
            transport=self._transport,
            timeout=15,
            follow_redirects=False,
        ) as c:
            r = await c.post(path, json={"content": {"raw": text[:30000]}})
        r.raise_for_status()
