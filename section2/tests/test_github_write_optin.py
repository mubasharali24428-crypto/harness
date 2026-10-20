"""Part 49: GitHub writes stay off per project until a team admin enables them."""

from sqlalchemy import text

from tests.test_service import auth, connect_user, seed

BRANCH = "/api/v1/projects/{pid}/branches?baseRef=main&newBranch=agent/x"
COMMIT = "/api/v1/projects/{pid}/commits"
DRAFT = "/api/v1/projects/{pid}/pulls/draft"
WRITES = [
    (BRANCH, None),
    (COMMIT, {"branch": "agent/x", "message": "m", "files": [{"path": "a.py", "content": "x"}]}),
    (DRAFT, {"branch": "agent/x", "baseBranch": "main", "title": "t", "bodyMd": "b"}),
]
TOGGLE = "/api/v1/projects/{pid}/github/writes"


def _posts(client) -> list[str]:
    return [url for method, url, _ in client.fake.requests if method == "POST"]


async def test_new_project_refuses_every_write_until_enabled(client, db):
    await seed(db)
    await connect_user(db)
    async with db() as s:
        # Raw insert without the flag: the column default must leave writes off.
        await s.execute(
            text(
                "INSERT INTO projects (id, team_id, name, repo_full_name)"
                " VALUES ('p2','t1','fresh','org/fresh')"
            )
        )
        await s.commit()
    for path, body in WRITES:
        response = await client.post(path.format(pid="p2"), headers=auth(), json=body)
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["error"]["code"] == "GITHUB_WRITES_DISABLED"
    assert _posts(client) == []


async def test_admin_turns_writes_on_and_off(client, db):
    await seed(db)
    await connect_user(db)
    off = await client.put(TOGGLE.format(pid="p1"), headers=auth(), json={"enabled": False})
    assert off.status_code == 200, off.text
    refused = await client.post(BRANCH.format(pid="p1"), headers=auth())
    assert refused.status_code == 403, refused.text
    on = await client.put(TOGGLE.format(pid="p1"), headers=auth(), json={"enabled": True})
    assert on.json() == {"projectId": "p1", "githubWritesEnabled": True}
    allowed = await client.post(BRANCH.format(pid="p1"), headers=auth())
    assert allowed.status_code == 200, allowed.text
    assert any(url.endswith("/git/refs") for url in _posts(client))


async def test_only_admin_changes_the_flag_and_everyone_reads_it(client, db):
    await seed(db)
    await connect_user(db)
    async with db() as s:
        await s.execute(text("UPDATE team_members SET role = 'engineer' WHERE id = 'm1'"))
        await s.commit()
    read = await client.get(TOGGLE.format(pid="p1"), headers=auth())
    assert read.json()["githubWritesEnabled"] is True
    denied = await client.put(TOGGLE.format(pid="p1"), headers=auth(), json={"enabled": False})
    assert denied.status_code == 403, denied.text
    assert denied.json()["detail"]["error"]["code"] == "FORBIDDEN"
    still = await client.get(TOGGLE.format(pid="p1"), headers=auth())
    assert still.json()["githubWritesEnabled"] is True


async def test_toggle_is_scoped_to_the_callers_team(client, db):
    await seed(db)
    await connect_user(db)
    async with db() as s:
        await s.execute(text("INSERT INTO teams (id, name) VALUES ('t2','other')"))
        await s.execute(
            text(
                "INSERT INTO projects (id, team_id, name, repo_full_name)"
                " VALUES ('p9','t2','theirs','org/theirs')"
            )
        )
        await s.commit()
    response = await client.put(TOGGLE.format(pid="p9"), headers=auth(), json={"enabled": True})
    assert response.status_code == 404, response.text
