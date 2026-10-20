"""Shared fixtures: temp-DB settings, fake bearer principals, ASGI client."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from harness_contracts.roles import Principal, Role

from harness_extensions.api import make_app
from harness_extensions.config import Settings

TEAM_A = "team-a"
TEAM_B = "team-b"

_PEOPLE = {
    "tok-admin-a": Principal(userId="u-admin-a", teamId=TEAM_A, role=Role.ADMIN),
    "tok-eng-a": Principal(userId="u-eng-a", teamId=TEAM_A, role=Role.ENGINEER),
    "tok-view-a": Principal(userId="u-view-a", teamId=TEAM_A, role=Role.VIEWER),
    "tok-eng-b": Principal(userId="u-eng-b", teamId=TEAM_B, role=Role.ENGINEER),
}


def make_settings(tmp_path, **over: Any) -> Settings:
    base: dict[str, Any] = dict(
        db_path=str(tmp_path / "registry.db"),
        allow_private_hosts=True,  # fixture servers run on 127.0.0.1
        discover_timeout_s=5.0,
        call_timeout_s=5.0,
    )
    base.update(over)
    return Settings(**base)


async def principal_dep(request) -> Principal:
    authorization = request.headers.get("authorization", "")
    token = authorization.removeprefix("Bearer ").strip()
    person = _PEOPLE.get(token)
    if person is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    return person


def build_app(settings: Settings, **kw: Any):
    return make_app(settings, get_principal_dep=principal_dep, **kw)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def app(settings):
    return build_app(settings)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ext.test") as c:
        yield c


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def api_prefix() -> str:
    return "/api/v1/extensions"


def as_user(client: httpx.AsyncClient, token: str) -> Callable:
    """Small helper returning a request callable bound to one identity."""

    async def request(method: str, path: str, **kw: Any) -> httpx.Response:
        headers = {**kw.pop("headers", {}), **auth(token)}
        return await client.request(method, path, headers=headers, **kw)

    return request
