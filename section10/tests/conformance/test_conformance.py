"""Conformance suite: every endpoint's response must equal its golden fixture.

A failure here means the Section 10 contract surface changed — regen fixtures
only as part of an intentional, documented contract change (AGENTS.md rule).
"""

import json
from pathlib import Path

import httpx
import pytest

from harness_onboarding.api import Settings, make_app
from harness_onboarding.store import StateStore

from .cases import CASES

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture()
def client(tmp_path):
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        identity_url="",
        github_url="",
        meeting_url="",
        orchestrator_url="",
    )
    app = make_app(settings=settings, store=StateStore(settings.state_path))
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://conf")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["fixture"])
async def test_golden(client, case):
    if case["method"] == "GET":
        resp = await client.get(case["path"])
    elif case["method"] == "POST":
        resp = await client.post(case["path"], json=case.get("body"))
    else:
        resp = await client.put(case["path"], json=case.get("body"))

    golden = json.loads((FIXTURES_DIR / case["fixture"]).read_text(encoding="utf-8"))
    assert resp.status_code == golden["status"], case["fixture"]
    assert resp.json() == golden["body"], f"response drifted from golden {case['fixture']}"


async def test_every_endpoint_has_a_golden():
    """Every deterministic endpoint must be covered by the fixture manifest.

    /api/v1/onboarding/state is intentionally excluded: its service probes are
    nondeterministic, so it is pinned by unit tests with a mock transport instead.
    """
    openapi_paths = set(make_app().openapi()["paths"].keys())
    covered = {c["path"] for c in CASES}
    deterministic = openapi_paths - {
        "/api/v1/onboarding/state",
    }
    assert covered == deterministic
