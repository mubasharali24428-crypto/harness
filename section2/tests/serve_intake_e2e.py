"""Boots the REAL section2 app (sqlite + intake) with seeded users and two signed-webhook intakes.
Prints one JSON line {base, adminToken, viewerToken}, then serves. Used by section7/e2e-review."""
# ruff: noqa: E501 (seed SQL / payload literals)

import asyncio
import hashlib
import hmac
import json
import sys
import tempfile
from pathlib import Path

import uvicorn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from harness_github.app import make_app  # noqa: E402
from harness_github.config import Settings  # noqa: E402
from harness_github.models import Base  # noqa: E402


def tokhash(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


async def main(origin: str) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="s2-e2e-"))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp / 'db.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        for uid, role, tok in (("u1", "admin", "admin-tok"), ("u2", "viewer", "viewer-tok")):
            await s.execute(
                text(
                    f"INSERT INTO users (id,email,name,is_active) VALUES ('{uid}','{uid}@x.com','{uid}',1)"
                )
            )
            await s.execute(
                text(
                    "INSERT INTO teams (id,name) SELECT 't1','team' WHERE NOT EXISTS (SELECT 1 FROM teams WHERE id='t1')"
                )
            )
            await s.execute(
                text(
                    f"INSERT INTO team_members (id,team_id,user_id,display_name,member_type,role) VALUES ('m{uid}','t1','{uid}','{uid}','human','{role}')"
                )
            )
            await s.execute(
                text(
                    "INSERT INTO auth_sessions (token_hash,user_id,team_id,expires_at) VALUES (:h,:u,'t1','2100-01-01T00:00:00Z')"
                ),
                {"h": tokhash(tok), "u": uid},
            )
        await s.execute(
            text(
                "INSERT INTO projects (id,team_id,name,repo_full_name) VALUES ('p1','t1','demo','org/repo')"
            )
        )
        await s.commit()
    app = make_app(
        factory,
        Settings(
            master_key=b"s2-e2e-key-1234567890abcdef",
            cors_origins=[origin],
            github_client_id="c",
            github_client_secret="s",
        ),
    )
    svc = app.state.intake_service
    reg = svc.register(
        team_id="t1", project_id="p1", user_id="u1", provider="github", external_project="org/repo"
    )
    for n, title in (
        (7, "Login page crashes on empty password"),
        (8, "<script>alert(1)</script> add dark mode"),
    ):
        body = json.dumps(
            {
                "action": "labeled",
                "label": {"name": "harness"},
                "repository": {"full_name": "org/repo"},
                "sender": {"login": "alice", "type": "User"},
                "issue": {
                    "number": n,
                    "title": title,
                    "body": "Steps to reproduce...",
                    "html_url": "javascript:alert(1)",
                },
            }
        ).encode()
        sig = "sha256=" + hmac.new(reg["secret"].encode(), body, hashlib.sha256).hexdigest()
        out = await svc.handle_webhook(
            "github",
            reg["id"],
            {"x-github-event": "issues", "x-github-delivery": f"d{n}", "x-hub-signature-256": sig},
            body,
        )
        assert out.status == 202, out
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    print(json.dumps({"base": f"http://127.0.0.1:{port}"}), flush=True)
    await task


asyncio.run(main(sys.argv[1]))
