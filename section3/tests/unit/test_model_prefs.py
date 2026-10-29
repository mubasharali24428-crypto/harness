"""Per-team model preferences: durability shape, authz, and wire safety."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness_router import api, prefs
from harness_router.config import MODEL_PRICING_CATALOG


class FakePrefsPool:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.execute_calls: list[tuple] = []
        self.fail = False

    async def fetchrow(self, sql: str, team_id: str):
        if self.fail:
            raise RuntimeError("storage down")
        row = self.rows.get(team_id)
        if row is None:
            return None
        return {**row, "updated_at": None}

    async def execute(self, sql: str, *params):
        if self.fail:
            raise RuntimeError("storage down")
        self.execute_calls.append(params)
        team_id, primary, reviewer, effort, updated_by = params
        self.rows[team_id] = {
            "primary_model": primary,
            "reviewer_model": reviewer,
            "reasoning_effort": effort,
            "updated_by": updated_by,
        }
        return "INSERT 0 1"


def _client(role: str = "admin"):
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_principal] = lambda: SimpleNamespace(
        userId="u-prefs", teamId="t-prefs", role=role
    )
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset():
    api.set_ledger_pool(None)
    yield
    api.set_ledger_pool(None)


def test_get_without_storage_returns_defaults_and_says_so():
    res = _client().get("/api/v1/models/prefs")
    assert res.status_code == 200
    body = res.json()
    assert body["persisted"] is False
    assert body["primaryModel"] == "deepseek/deepseek-v4-flash"
    assert body["reasoningEffort"] == "off"


def test_put_is_admin_only():
    pool = FakePrefsPool()
    api.set_ledger_pool(pool)
    res = _client("engineer").put(
        "/api/v1/models/prefs",
        json={
            "primaryModel": "deepseek/deepseek-chat",
            "reviewerModel": "deepseek/deepseek-chat",
            "reasoningEffort": "low",
        },
    )
    assert res.status_code == 403
    assert pool.execute_calls == []


def test_put_persists_and_reports_the_saved_choice():
    pool = FakePrefsPool()
    api.set_ledger_pool(pool)
    client = _client()
    res = client.put(
        "/api/v1/models/prefs",
        json={
            "primaryModel": "deepseek/deepseek-r1-0528",
            "reviewerModel": "deepseek/deepseek-chat",
            "reasoningEffort": "high",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["persisted"] is True
    assert body["primaryModel"] == "deepseek/deepseek-r1-0528"
    assert body["reasoningEffort"] == "high"
    assert pool.execute_calls and pool.execute_calls[0][0] == "t-prefs"

    again = client.get("/api/v1/models/prefs").json()
    assert again["reviewerModel"] == "deepseek/deepseek-chat"
    assert again["persisted"] is True


def test_put_rejects_unknown_models():
    api.set_ledger_pool(FakePrefsPool())
    res = _client().put(
        "/api/v1/models/prefs",
        json={
            "primaryModel": "not/a-model",
            "reviewerModel": "deepseek/deepseek-chat",
            "reasoningEffort": "off",
        },
    )
    assert res.status_code == 400
    assert res.json()["detail"]["error"]["code"] == "UNKNOWN_MODEL"


def test_put_fails_honestly_without_storage():
    res = _client().put(
        "/api/v1/models/prefs",
        json={
            "primaryModel": "deepseek/deepseek-chat",
            "reviewerModel": "deepseek/deepseek-chat",
            "reasoningEffort": "off",
        },
    )
    assert res.status_code == 503
    assert res.json()["detail"]["error"]["code"] == "PREFS_UNAVAILABLE"


def test_reasoning_is_only_added_for_aggregator_endpoints(monkeypatch):
    opts = api.ModelCallOpts()
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    assert api._with_reasoning(opts, "high", "deepseek/deepseek-r1-0528") is opts

    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://openrouter.ai/api/v1")
    wired = api._with_reasoning(opts, "high", "deepseek/deepseek-r1-0528")
    assert wired is not None and wired.extra == {"reasoning": {"effort": "high"}}

    # Non-reasoning models and "off" keep the historical request shape.
    assert api._with_reasoning(opts, "high", "deepseek/deepseek-chat") is opts
    assert api._with_reasoning(opts, "off", "deepseek/deepseek-r1-0528") is opts


def test_every_default_is_in_the_catalog():
    assert prefs.config.default_primary_model in MODEL_PRICING_CATALOG
    assert prefs.config.default_reviewer_model in MODEL_PRICING_CATALOG
