"""W16 slice 4 — the export a SIEM collector can append without parsing a document.

`format=ndjson` answers one self-contained JSON object per line (`application/x-
ndjson`), so a collector that does `curl | while read line` is enough — no JSON
document to hold in memory, no nesting to walk, chain fields preserved so the
hashes survive the trip. Cursor metadata moves to headers, because a stream of
lines has no place to put it and dropping it would make the stream unresumable.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.app import make_app
from harness_identity.audit import write_audit
from harness_identity.auth import hash_token
from harness_identity.config import Settings
from harness_identity.models import AuthSession, Base, Team, TeamMember, User

KEY = b"test-master-key-0123456789abcdef"


@pytest.fixture
async def world(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/nd.db", connect_args={"timeout": 15}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(User(id="u1", email="a@x.z", name="a"))
        s.add(Team(id="t1", name="A"))
        s.add(TeamMember(id="m1", team_id="t1", user_id="u1", display_name="a", role="admin"))
        s.add(
            AuthSession(
                token_hash=hash_token("goodtok"),
                user_id="u1",
                team_id="t1",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await s.commit()
        for action in ("member.removed", "apikey.added", "account.erased"):
            await write_audit(
                s,
                action,
                actor_member_id="m1",
                target_type="agent_run",
                target_id="r1",
                detail={"teamId": "t1", "why": action},
            )
        await s.commit()
    app = make_app(factory, Settings(master_key=KEY, cors_origins=[]))
    yield {"app": app, "factory": factory}
    await engine.dispose()


async def request_raw(world, query, token="goodtok"):
    async with AsyncClient(
        transport=ASGITransport(app=world["app"]), base_url="http://t"
    ) as c:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return await c.get(f"/api/v1/audit/export?{query}", headers=headers)


async def test_ndjson_answers_one_self_contained_object_per_line(world):
    r = await request_raw(world, "format=ndjson")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/x-ndjson")

    lines = [ln for ln in r.text.splitlines() if ln.strip()]
    assert len(lines) == 3
    first = json.loads(lines[0])
    # Every field a collector needs to store, and every field the chain needs.
    assert set(first) >= {
        "seq",
        "action",
        "actorKind",
        "targetType",
        "detail",
        "ts",
        "chainPos",
        "prevHash",
        "rowHash",
    }
    assert first["detail"]["why"] == "member.removed"
    assert [json.loads(ln)["seq"] for ln in lines] == sorted(
        json.loads(ln)["seq"] for ln in lines
    )
    # No wrapper object: a line must stand alone in a log aggregator.
    assert "items" not in r.text


async def test_cursor_travels_in_headers_because_lines_have_no_footer(world):
    """A collector that resumes at the header cursor sees every row exactly once —
    including the `audit.exported` row the first page itself wrote."""
    r = await request_raw(world, "format=ndjson&limit=2")
    assert r.status_code == 200
    first = [json.loads(ln) for ln in r.text.splitlines() if ln.strip()]
    assert len(first) == 2
    assert r.headers["x-harness-more"] == "true"
    assert r.headers["x-harness-next-cursor"] == str(first[-1]["seq"])

    rest = []
    cursor = r.headers["x-harness-next-cursor"]
    while True:
        page = await request_raw(world, f"format=ndjson&afterSeq={cursor}")
        rows = [json.loads(ln) for ln in page.text.splitlines() if ln.strip()]
        rest.extend(rows)
        if page.headers["x-harness-more"] != "true" or not rows:
            break
        cursor = page.headers["x-harness-next-cursor"]

    seqs = [row["seq"] for row in first + rest]
    assert len(seqs) == len(set(seqs)), "a row was delivered twice"
    assert seqs == sorted(seqs)
    actions = [row["action"] for row in first + rest]
    assert {"member.removed", "apikey.added", "account.erased"} <= set(actions)


async def test_an_empty_window_is_an_empty_body_not_a_null_line(world):
    async with world["factory"]() as db:
        last = (await db.execute(text("SELECT max(seq) FROM audit_log"))).scalar_one()
    r = await request_raw(world, f"format=ndjson&afterSeq={last}")
    assert r.status_code == 200
    assert r.text == ""
    assert r.headers["x-harness-next-cursor"] == str(last)
    assert r.headers["x-harness-more"] == "false"


async def test_the_json_shape_is_untouched_by_the_new_format(world):
    r = await request_raw(world, "")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"stream", "items", "limit", "hasMore", "nextCursor"}
    assert len(body["items"]) == 3


async def test_an_unknown_format_is_refused_not_silently_json(world):
    r = await request_raw(world, "format=csv")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
