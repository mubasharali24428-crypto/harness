"""Goal 1 — GitHub OAuth web application flow (wave 5).

Flow under test:
  GET /api/v1/github/oauth/start   (bearer) -> 302 github.com/login/oauth/authorize?state=…
  GET /api/v1/github/oauth/callback?code&state  (no auth; state IS the CSRF binding)
      -> token exchange -> encrypted at rest -> 302 {frontend_base}/settings?connected=<login>
  GET /api/v1/github/connection    (bearer) -> {connected, login, scopes} — never a token

Security invariants pinned here:
- start requires a session; state binds user_id server-side (CSRF).
- callback rejects unknown/expired/replayed state with 400.
- access_token NEVER appears in any response body, log-shaped field, or plaintext column.
"""


import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from harness_github.app import make_app
from harness_github.config import Settings


def auth() -> dict:
    return {"Authorization": "Bearer tok1"}


async def _seed(db):
    from conftest import seed_user_and_project

    await seed_user_and_project(db)


async def _extract_state(location: str) -> str:
    from urllib.parse import parse_qs, urlparse

    return parse_qs(urlparse(location).query)["state"][0]


@pytest.fixture
async def anon_client(db):
    """App WITHOUT github creds — proves the NOT_CONFIGURED path."""
    settings = Settings(
        master_key=b"s2-test-key-1234567890abcdef",
        cors_origins=[],
        github_client_id="",
        github_client_secret="",
    )
    app = make_app(db, settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        c.app = app
        yield c


async def test_start_requires_session(db, client):
    await _seed(db)
    r = await client.get("/api/v1/github/oauth/start")
    assert r.status_code == 401


async def test_start_redirects_to_github_with_bound_state(db, client):
    await _seed(db)
    r = await client.get("/api/v1/github/oauth/start", headers=auth(), follow_redirects=False)
    assert r.status_code in (301, 302, 303, 307)
    loc = r.headers["location"]
    assert loc.startswith("https://github.com/login/oauth/authorize")
    assert "client_id=cid" in loc
    assert "scope=repo%20read:user" in loc
    assert "redirect_uri=" in loc
    state = await _extract_state(loc)
    # The state must be bound server-side to THIS user (CSRF anchor).
    assert state in client.app.state.oauth_states
    assert client.app.state.oauth_states[state][0] == "u1"


async def test_start_without_creds_is_clean_not_configured(db, anon_client):
    await _seed(db)
    r = await anon_client.get("/api/v1/github/oauth/start", headers=auth())
    assert r.status_code == 503
    body = r.json()
    err = body.get("error") or (body.get("detail") or {}).get("error") or {}
    assert err.get("code") == "NOT_CONFIGURED"


async def test_full_browser_flow_connects_and_persists(db, client):
    await _seed(db)
    started = await client.get(
        "/api/v1/github/oauth/start", headers=auth(), follow_redirects=False
    )
    state = await _extract_state(started.headers["location"])

    cb = await client.get(
        f"/api/v1/github/oauth/callback?code=abc&state={state}", follow_redirects=False
    )
    assert cb.status_code in (301, 302, 303, 307)
    assert cb.headers["location"].startswith("http://localhost:3000/settings")
    assert "connected=founder-dev" in cb.headers["location"]

    async with db() as s:
        row = (
            await s.execute(
                text(
                    "SELECT github_login, access_token_encrypted, scopes "
                    "FROM github_connections WHERE user_id='u1'"
                )
            )
        ).mappings().first()
    assert row["github_login"] == "founder-dev"
    assert row["access_token_encrypted"] != "gho_fake_token_123"
    assert "gho_fake_token_123" not in str(row["access_token_encrypted"])
    assert KeyVault(b"s2-test-key-1234567890abcdef").decrypt(row["access_token_encrypted"]) == (
        "gho_fake_token_123"
    )


async def test_callback_rejects_unknown_state(db, client):
    await _seed(db)
    r = await client.get("/api/v1/github/oauth/callback?code=abc&state=bogus")
    assert r.status_code == 400


async def test_callback_rejects_replayed_state(db, client):
    await _seed(db)
    started = await client.get(
        "/api/v1/github/oauth/start", headers=auth(), follow_redirects=False
    )
    state = await _extract_state(started.headers["location"])
    first = await client.get(
        f"/api/v1/github/oauth/callback?code=abc&state={state}", follow_redirects=False
    )
    assert first.status_code in (301, 302, 303, 307)
    replay = await client.get(f"/api/v1/github/oauth/callback?code=abc&state={state}")
    assert replay.status_code == 400


async def test_connection_reports_connected_without_token(db, client):
    await _seed(db)
    before = await client.get("/api/v1/github/connection", headers=auth())
    assert before.status_code == 200
    assert before.json()["connected"] is False

    started = await client.get(
        "/api/v1/github/oauth/start", headers=auth(), follow_redirects=False
    )
    state = await _extract_state(started.headers["location"])
    await client.get(f"/api/v1/github/oauth/callback?code=abc&state={state}")

    after = await client.get("/api/v1/github/connection", headers=auth())
    body = after.json()
    assert body["connected"] is True
    assert body["login"] == "founder-dev"
    assert isinstance(body["scopes"], list) and body["scopes"]
    assert "gho_fake_token_123" not in after.text


from harness_contracts import KeyVault  # noqa: E402  (used above)
