"""T14 (docs/briefs/threat-model.md): POST /auth/logout revokes the presenting session, and only it.

Also pins the fixation half: a bearer the client invents is never adopted at login.
"""

EMAIL = "t14@example.com"


async def _login(c) -> str:
    sent: list[tuple[str, str]] = []
    c.app.state.notifier = lambda email, code: sent.append((email, code))
    assert (await c.post("/api/v1/auth/request-login", json={"email": EMAIL})).status_code == 200
    r = await c.post("/api/v1/auth/verify-otp", json={"email": EMAIL, "code": sent[-1][1]})
    assert r.status_code == 200
    return r.json()["token"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_logout_revokes_that_token_but_not_the_users_other_session(client):
    first, second = await _login(client), await _login(client)
    assert first != second
    assert (await client.get("/api/v1/me", headers=_h(first))).status_code == 200
    assert (await client.post("/api/v1/auth/logout", headers=_h(first))).json() == {"ok": True}
    assert (await client.get("/api/v1/me", headers=_h(first))).status_code == 401
    assert (await client.post("/api/v1/auth/logout", headers=_h(first))).status_code == 401  # no replay
    assert (await client.get("/api/v1/me", headers=_h(second))).status_code == 200


async def test_a_client_chosen_bearer_is_never_adopted_at_login(client):
    chosen = "attacker-chosen-session-token"
    sent: list[tuple[str, str]] = []
    client.app.state.notifier = lambda email, code: sent.append((email, code))
    await client.post("/api/v1/auth/request-login", json={"email": EMAIL})
    r = await client.post(
        "/api/v1/auth/verify-otp", json={"email": EMAIL, "code": sent[-1][1]}, headers=_h(chosen)
    )
    assert r.status_code == 200 and r.json()["token"] != chosen
    assert (await client.get("/api/v1/me", headers=_h(chosen))).status_code == 401
