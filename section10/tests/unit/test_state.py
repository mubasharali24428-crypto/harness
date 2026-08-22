"""GET /state service discovery: probes degrade gracefully, steps reflect availability."""

import httpx
import pytest

from harness_onboarding.api import Settings, make_app
from harness_onboarding.store import StateStore

# URL -> availability, exercised through the api module's probe seam.
PROBE_RESULTS = {
    "http://identity.local:8001": True,  # healthy 200
    "http://github.local:8100": False,  # 500
    "http://orchestrator.local:8004": False,  # connection refused
}


@pytest.fixture()
def app(tmp_path, monkeypatch):
    async def fake_probe(client, url, health_path):
        return PROBE_RESULTS.get(url, False)

    monkeypatch.setattr("harness_onboarding.api.probe_available", fake_probe)
    settings = Settings(
        state_path=str(tmp_path / "state.json"),
        identity_url="http://identity.local:8001",
        github_url="http://github.local:8100",
        meeting_url="",  # section 6 not deployed
        orchestrator_url="http://orchestrator.local:8004",
    )
    return make_app(settings=settings, store=StateStore(settings.state_path))


async def test_state_reports_service_availability(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/onboarding/state")
    assert resp.status_code == 200
    body = resp.json()
    by_key = {s["key"]: s for s in body["services"]}
    assert by_key["identity"]["available"] is True
    assert by_key["github"]["available"] is False
    assert by_key["meeting"]["available"] is False  # unset URL
    assert by_key["orchestrator"]["available"] is False
    assert by_key["onboarding"]["available"] is True  # self


async def test_step_availability_follows_service(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get("/api/v1/onboarding/state")).json()
    steps = {s["id"]: s for s in body["steps"]}
    assert steps["connect_github"]["available"] is False
    assert steps["create_team"]["available"] is True
    assert steps["start_meeting"]["available"] is False
    assert steps["guided_demo"]["available"] is True


async def test_state_includes_ack_and_telemetry_defaults(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get("/api/v1/onboarding/state")).json()
    assert body["securityChecklistAcknowledged"] is False
    assert body["telemetry"]["fullPromptText"] is False
    assert body["telemetry"]["baselineEnabled"] is True


async def test_acknowledge_persists(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/onboarding/security-checklist/acknowledge")
        assert resp.status_code == 200
        body = (await client.get("/api/v1/onboarding/state")).json()
    assert body["securityChecklistAcknowledged"] is True
