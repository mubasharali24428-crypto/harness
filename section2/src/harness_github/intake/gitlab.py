"""F038: minimal GitLab REST client. Merge-class operations are blocked in code."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import quote, urlsplit

import httpx

_BLOCKED = re.compile(
    r"/merge_requests/\d+/(merge|rebase|approve|unapprove|cancel_merge_when_pipeline_succeeds)/?$"
    r"|/protected_branches|/repository/branches/[^/]+$"
)


class GitLabGuardViolation(RuntimeError):
    pass


def validate_api_base(raw: str) -> str:
    u = urlsplit(raw)
    if u.username or u.password or not u.hostname:
        raise ValueError("api base must be a plain URL without credentials")
    host = u.hostname
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if u.scheme != "https" and not (u.scheme == "http" and loopback):
        raise ValueError("api base must be https (http only for localhost)")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise ValueError("api base address is not allowed")  # cloud metadata etc.
    except ValueError as exc:
        if "not allowed" in str(exc):
            raise
    return f"{u.scheme}://{u.netloc}".rstrip("/")


def _guard(method: str, path: str) -> None:
    if _BLOCKED.search(path.split("?", 1)[0]) or (
        method in {"PUT", "DELETE", "PATCH"} and "/merge_requests/" in path
    ):
        raise GitLabGuardViolation(f"forbidden GitLab operation: {method} {path}")


class GitLabClient:
    def __init__(
        self, api_base: str, token: str, transport: httpx.AsyncBaseTransport | None = None
    ):
        self.base = validate_api_base(api_base)
        self._headers = {"PRIVATE-TOKEN": token, "Accept": "application/json"}
        self._transport = transport

    async def _request(
        self, method: str, path: str, json_body: dict | None = None
    ) -> httpx.Response:
        _guard(method, path)
        async with httpx.AsyncClient(
            base_url=self.base,
            headers=self._headers,
            transport=self._transport,
            timeout=15,
            follow_redirects=False,
        ) as c:
            return await c.request(method, path, json=json_body)

    @staticmethod
    def _proj(project_path: str) -> str:
        return quote(project_path, safe="")

    async def get_issue(self, project_path: str, iid: int) -> dict:
        r = await self._request(
            "GET", f"/api/v4/projects/{self._proj(project_path)}/issues/{int(iid)}"
        )
        r.raise_for_status()
        return r.json()

    async def get_merge_request(self, project_path: str, iid: int) -> dict:
        r = await self._request(
            "GET", f"/api/v4/projects/{self._proj(project_path)}/merge_requests/{int(iid)}"
        )
        r.raise_for_status()
        return r.json()

    async def post_note(self, project_path: str, kind: str, iid: int, body: str) -> None:
        if kind not in ("issues", "merge_requests"):
            raise ValueError("kind must be issues|merge_requests")
        r = await self._request(
            "POST",
            f"/api/v4/projects/{self._proj(project_path)}/{kind}/{int(iid)}/notes",
            {"body": body[:60000]},
        )
        r.raise_for_status()
