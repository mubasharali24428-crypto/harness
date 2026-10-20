"""Skills/playbooks: versioning, bounds, isolation."""

from __future__ import annotations

from conftest import as_user


async def test_create_and_version_cycle(client):
    req = as_user(client, "tok-eng-a")
    created = await req(
        "POST",
        "/api/v1/extensions/skills",
        json={"name": "deploy-runbook", "content": "step 1\nstep 2"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["skill"]["latestVersion"] == 1
    assert body["version"]["digest"]
    skill_id = body["skill"]["skillId"]

    v2 = await req(
        "POST",
        f"/api/v1/extensions/skills/{skill_id}/versions",
        json={"content": "step 1\nstep 2 improved"},
    )
    assert v2.status_code == 201
    assert v2.json()["version"] == 2

    latest = await req("GET", f"/api/v1/extensions/skills/{skill_id}")
    assert latest.json()["version"]["version"] == 2
    old = await req("GET", f"/api/v1/extensions/skills/{skill_id}?version=1")
    assert old.json()["version"]["content"] == "step 1\nstep 2"

    listed = await req("GET", "/api/v1/extensions/skills")
    assert [s["name"] for s in listed.json()["items"]] == ["deploy-runbook"]


async def test_duplicate_name_conflict(client):
    req = as_user(client, "tok-eng-a")
    first = await req(
        "POST", "/api/v1/extensions/skills", json={"name": "dup", "content": "a"}
    )
    assert first.status_code == 201
    second = await req(
        "POST", "/api/v1/extensions/skills", json={"name": "dup", "content": "b"}
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "SKILL_NAME_CONFLICT"


async def test_invalid_name_and_oversized_content(client, settings):
    req = as_user(client, "tok-eng-a")
    bad = await req(
        "POST", "/api/v1/extensions/skills", json={"name": "Bad Name!", "content": "x"}
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "SKILL_NAME_INVALID"
    huge = await req(
        "POST",
        "/api/v1/extensions/skills",
        json={"name": "big", "content": "x" * (settings.max_skill_bytes + 10)},
    )
    assert huge.status_code == 400
    assert huge.json()["error"]["code"] == "SKILL_CONTENT_TOO_LARGE"


async def test_cross_tenant_skill_no_oracle(client):
    req_a = as_user(client, "tok-eng-a")
    req_b = as_user(client, "tok-eng-b")
    created = await req_a(
        "POST", "/api/v1/extensions/skills", json={"name": "a-only", "content": "secret"}
    )
    skill_id = created.json()["skill"]["skillId"]
    assert (await req_b("GET", f"/api/v1/extensions/skills/{skill_id}")).status_code == 404


async def test_viewer_cannot_write(client):
    req = as_user(client, "tok-view-a")
    res = await req(
        "POST", "/api/v1/extensions/skills", json={"name": "nope", "content": "x"}
    )
    assert res.status_code == 403
