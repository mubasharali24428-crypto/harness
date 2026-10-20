"""Dashboard APIs: fail-closed auth, team scoping, org-wide admin, archive views."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient
from support import Principal, principal_dep

from harness_telemetry import Archive, build_records, fixtures, parse_cost, parse_stream
from harness_telemetry.dashboard import make_dashboard_app

TOKENS = {
    "alice-token": Principal(userId="u-alice", teamId="team-a", role="member"),
    "bob-token": Principal(userId="u-bob", teamId="team-b", role="member"),
    "admin-token": Principal(userId="u-admin", teamId="team-a", role="admin"),
}


@pytest.fixture()
def client(tmp_path):
    events = parse_stream(fixtures.events())
    costs = [parse_cost(c) for c in fixtures.costs()]
    archive = Archive(str(tmp_path / "a.sqlite"))
    archive.ingest(events, costs, fixtures.PROJECT_TEAM, as_of=fixtures.AS_OF)
    records = build_records(events, costs, fixtures.PROJECT_TEAM, as_of=fixtures.AS_OF)
    app = make_dashboard_app(
        archive=archive, records=records, as_of=fixtures.AS_OF,
        get_principal_dep=principal_dep(TOKENS),
    )
    return TestClient(app)


def _get(client, token, path, **params):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get(path, params=params, headers=headers)


def test_auth_fails_closed_when_unwired(tmp_path):
    archive = Archive(str(tmp_path / "a.sqlite"))
    records = build_records(parse_stream(fixtures.events()),
                            [parse_cost(c) for c in fixtures.costs()],
                            fixtures.PROJECT_TEAM, as_of=fixtures.AS_OF)
    app = make_dashboard_app(archive=archive, records=records, as_of=fixtures.AS_OF)
    client = TestClient(app)
    response = client.get("/api/v1/telemetry/quality")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_NOT_CONFIGURED"


def test_missing_or_bad_token_is_401(client):
    assert client.get("/api/v1/telemetry/quality").status_code == 401
    assert _get(client, "nope", "/api/v1/telemetry/quality").status_code == 401


def test_member_sees_only_own_team_and_cannot_ask_for_another(client):
    own = _get(client, "alice-token", "/api/v1/telemetry/quality")
    assert own.status_code == 200
    assert own.json()["tasks"] == 6  # team-a only
    assert own.json()["accepted"] == 2

    forbidden = _get(client, "alice-token", "/api/v1/telemetry/quality", team="team-b")
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "TEAM_FORBIDDEN"


def test_admin_gets_org_wide_and_can_pick_a_team(client):
    org = _get(client, "admin-token", "/api/v1/telemetry/quality")
    assert org.status_code == 200 and org.json()["tasks"] == 8

    team_b = _get(client, "admin-token", "/api/v1/telemetry/quality", team="team-b")
    assert team_b.json()["tasks"] == 2


def test_cost_and_productivity_endpoints_carry_definitions(client):
    cost = _get(client, "admin-token", "/api/v1/telemetry/cost").json()
    assert cost["costPerAcceptedChange"] == "4.3667"
    assert cost["costPerClaimedCompletion"] == "4.2750"
    assert cost["definitions"]["cost_per_accepted_change"]

    prod = _get(client, "admin-token", "/api/v1/telemetry/productivity").json()
    assert prod["timeToFirstUsefulChangeMinutes"]["p50"] == 75.0


def test_archive_search_respects_team_boundaries(client):
    assert _get(client, "alice-token", "/api/v1/telemetry/archive/search", q="login").json()[
        "total"
    ] == 0
    bob = _get(client, "bob-token", "/api/v1/telemetry/archive/search", q="login").json()
    assert bob["total"] == 1 and bob["runs"][0]["runId"] == "run-b2"
    assert "alice@" not in bob["runs"][0]["goal"]

    everyone = _get(client, "admin-token", "/api/v1/telemetry/archive/search").json()
    assert everyone["total"] == 8


def test_run_detail_is_scoped_and_scrubbed(client):
    assert _get(client, "alice-token", "/api/v1/telemetry/archive/runs/run-b1").status_code == 404
    own = _get(client, "alice-token", "/api/v1/telemetry/archive/runs/run-a1")
    assert own.status_code == 200 and own.json()["outcome"] == "accepted"
    assert _get(client, "admin-token", "/api/v1/telemetry/archive/runs/run-b2").status_code == 200


def test_healthz_and_index_page(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    page = client.get("/")
    assert page.status_code == 200 and "Harness telemetry" in page.text
