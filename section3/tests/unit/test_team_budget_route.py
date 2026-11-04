"""W15 slice 2 — the model-call route consumes a TEAM budget after auth.

Per-IP limits cannot see the team. The route therefore consumes a team bucket
once the principal is known; a saturated team is answered 429 RATE_LIMITED with
Retry-After before any provider work, and other teams keep their own budget.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[3]
if (_ROOT / "section1" / "src") not in sys.path:
    sys.path.append(str(_ROOT / "section1" / "src"))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from harness_contracts.signatures import ModelRole  # noqa: E402
from harness_identity.envelope_middleware import install_error_envelope  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from harness_router.api import get_principal, router  # noqa: E402

RATE_BUCKETS_DDL = (
    "CREATE TABLE rate_buckets (bucket_key VARCHAR(200) PRIMARY KEY, "
    "window_start TIMESTAMPTZ NOT NULL, count INTEGER NOT NULL DEFAULT 0)"
)

CALL_BODY = {
    "agentId": "agent-w15",
    "role": ModelRole.PRIMARY.value,
    "messages": [{"role": "user", "content": "team budget probe"}],
}


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.execute(text(RATE_BUCKETS_DDL))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _app(factory, team_id: str) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    install_error_envelope(app)
    app.state.session_factory = factory
    app.dependency_overrides[get_principal] = lambda: SimpleNamespace(
        userId="u-w15", teamId=team_id, role="engineer"
    )
    return app


async def _post(app: FastAPI):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post("/api/v1/models/call", json=CALL_BODY)


@pytest.mark.asyncio
async def test_call_over_team_budget_answers_429_before_the_provider(factory, monkeypatch):
    monkeypatch.setenv("HARNESS_TEAM_MODEL_CALL_LIMIT", "2")
    app = _app(factory, "team-busy")
    assert (await _post(app)).status_code == 200
    assert (await _post(app)).status_code == 200

    blocked = await _post(app)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    retry_after = blocked.headers["retry-after"]
    assert retry_after.isdigit() and 1 <= int(retry_after) <= 60


@pytest.mark.asyncio
async def test_another_team_keeps_its_budget(factory, monkeypatch):
    monkeypatch.setenv("HARNESS_TEAM_MODEL_CALL_LIMIT", "1")
    busy = _app(factory, "team-busy")
    idle = _app(factory, "team-idle")
    assert (await _post(busy)).status_code == 200
    assert (await _post(busy)).status_code == 429
    assert (await _post(idle)).status_code == 200
