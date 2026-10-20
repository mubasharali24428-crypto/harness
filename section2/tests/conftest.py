import pytest
from httpx import ASGITransport, AsyncClient, MockTransport, Response
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_github.app import make_app
from harness_github.config import Settings
from harness_github.models import Base


@pytest.fixture(scope="session")
def event_loop():
    import asyncio

    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        from sqlalchemy import text

        await conn.execute(
            text(
                "CREATE TABLE high_impact_approvals (id TEXT, run_id TEXT, "
                "requester_user_id TEXT, state TEXT)"
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


class FakeGitHub:
    """Records every request; answers the endpoints our service uses."""

    def __init__(self):
        self.requests: list[tuple[str, str, dict | None]] = []
        self.refs = {"heads/main": "abc123base"}
        self.prs: dict[int, dict] = {}
        self._next_pr = 1

    def handler(self, method: str, url, request_body=None) -> Response:
        path = str(url)
        body = request_body or {}
        self.requests.append((method, path, body))

        if path.endswith("/login/oauth/access_token"):
            return Response(
                200, json={"access_token": "gho_fake_token_123", "token_type": "bearer"}
            )
        if path == "https://api.github.com/user":
            return Response(200, json={"login": "founder-dev"})
        if "/git/trees/" in path:
            return Response(
                200,
                json={"tree": [{"path": "src/app.py", "type": "blob", "sha": "f00d", "size": 10}]},
            )
        if "/contents/" in path:
            import base64

            return Response(
                200,
                json={
                    "path": "src/app.py",
                    "sha": "f00d",
                    "content": base64.b64encode(b"print(1)").decode(),
                },
            )
        if "/git/ref/heads/" in path:
            branch = path.rsplit("/git/ref/heads/", 1)[-1]
            key = f"heads/{branch}"
            if key in self.refs:
                return Response(200, json={"object": {"type": "commit", "sha": self.refs[key]}})
            return Response(404, json={"message": "no such ref"})
        if path.endswith("/git/refs") and method == "POST":
            self.refs[body["ref"].removeprefix("refs/")] = body["sha"]
            return Response(201, json={"ref": body["ref"]})
        if "/git/blobs" in path:
            return Response(201, json={"sha": f"blob-{len(self.requests)}"})
        if "/git/trees" in path and method == "POST":
            return Response(201, json={"sha": "tree-1"})
        if "/git/commits" in path and method == "POST":
            sha = f"commit-{len(self.requests)}"
            return Response(201, json={"sha": sha})
        if "/git/refs/heads/" in path and method == "PATCH":
            branch = path.split("/git/refs/heads/", 1)[-1]
            self.refs[f"heads/{branch}"] = body["sha"]
            return Response(200, json={"ok": True})
        if "/pulls" in path and method == "POST":
            n = self._next_pr
            self._next_pr += 1
            pr = {
                "number": n,
                "html_url": f"https://github.com/org/repo/pull/{n}",
                "draft": body.get("draft", False),
                "state": "open",
                "merged": False,
                "mergeable": True,
                "head": {"ref": body["head"]},
                "base": {"ref": body["base"]},
            }
            self.prs[n] = pr
            return Response(201, json=pr)
        if "/pulls/" in path and method == "GET":
            n = int(path.rstrip("/").rsplit("/", 1)[-1])
            return Response(200, json=self.prs[n])
        if path.split("?")[0].endswith("/issues") and method == "GET":
            return Response(
                200,
                json=[
                    {"number": 1, "title": "bug", "state": "open",
                     "html_url": "https://github.com/org/repo/issues/1"},
                    {"number": 2, "title": "pr", "state": "open",
                     "html_url": "https://github.com/org/repo/pull/2", "pull_request": {}},
                ],
            )
        if path.endswith("/issues") and method == "POST":
            n = sum(1 for m, u, _ in self.requests if m == "POST" and u.endswith("/issues"))
            return Response(
                201,
                json={"number": n, "title": body["title"], "state": "open",
                      "html_url": f"https://github.com/org/repo/issues/{n}"},
            )
        return Response(404, json={"message": "not found"})

    def transport(self) -> MockTransport:
        import json as _json

        def adapt(request) -> Response:
            try:
                body = _json.loads(request.content) if request.content else None
            except Exception:
                body = None
            return self.handler(request.method, str(request.url), body)

        return MockTransport(adapt)

    def merge_attempted(self) -> bool:
        return any("/merge" in u for _, u, _ in self.requests)


@pytest.fixture
async def client(db):
    settings = Settings(
        master_key=b"s2-test-key-1234567890abcdef",
        cors_origins=[],
        github_client_id="cid",
        github_client_secret="secret",
    )
    app = make_app(db, settings)
    fake = FakeGitHub()
    app.state.transport = fake.transport()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        c.fake = fake
        c.app = app
        yield c


async def seed_user_and_project(db):
    from sqlalchemy import text

    async with db() as s:
        await s.execute(
            text("INSERT INTO users (id, email, name, is_active) VALUES ('u1','dev@x.com','D',1)")
        )
        await s.execute(text("INSERT INTO teams (id, name) VALUES ('t1','team')"))
        await s.execute(
            text(
                "INSERT INTO team_members (id, team_id, user_id, display_name, member_type, role)"
                " VALUES ('m1','t1','u1','D','human','admin')"
            )
        )
        await s.execute(
            text(
                "INSERT INTO auth_sessions (token_hash, user_id, team_id, expires_at)"
                " VALUES (:h,'u1','t1','2100-01-01T00:00:00Z')"
            ),
            {"h": __import__("hashlib").sha256(b"tok1").hexdigest()},
        )
        await s.execute(
            text(
                "INSERT INTO projects (id, team_id, name, repo_full_name)"
                " VALUES ('p1','t1','demo','org/repo')"
            )
        )
        await s.commit()


def auth():
    return {"Authorization": "Bearer tok1"}
