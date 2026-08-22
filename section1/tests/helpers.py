import httpx


async def login(client: httpx.AsyncClient, email="dev@example.com", team_id=None):
    body = {"email": email}
    if team_id:
        body["teamId"] = team_id
    r = await client.post("/api/v1/auth/login", json=body)
    assert r.status_code == 200, r.text
    return r.json()

def auth(token: str):
    return {"Authorization": f"Bearer {token}"}
