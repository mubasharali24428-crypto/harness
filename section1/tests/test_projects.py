
from tests.helpers import auth, login


async def test_create_and_list_projects_scoped_to_team(client):
    a = await login(client, "pa@x.com")
    b = await login(client, "pb@x.com")

    r = await client.post(
        "/api/v1/projects",
        json={"name": "demo-repo"},
        headers=auth(a["token"]),
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["name"] == "demo-repo"

    r = await client.get("/api/v1/projects", headers=auth(a["token"]))
    a_ids = {i["id"] for i in r.json()["items"]}
    assert created["id"] in a_ids

    # teams are isolated: b never sees a's project ids
    r = await client.get("/api/v1/projects", headers=auth(b["token"]))
    b_ids = {i["id"] for i in r.json()["items"]}
    assert created["id"] not in b_ids

    # unauthenticated rejected
    r = await client.get("/api/v1/projects")
    assert r.status_code == 401

    # blank name rejected
    r = await client.post("/api/v1/projects", json={"name": "  "}, headers=auth(a["token"]))
    assert r.status_code == 422
