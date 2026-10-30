"""W16 slice 4 — the reader law a SIEM cursor depends on.

`latest_seq` decides where a freshly created collector starts. Get it wrong and
the failure is silent in both directions: point it at another team's newest row
and this team's own un-pushed rows are skipped forever; fall back to 0 and the
"latest" choice becomes a full history dump. So it is tested as the primitive it
is, not only through the HTTP layer.
"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.audit import write_audit
from harness_identity.audit_rows import latest_seq, read_audit_page
from harness_identity.models import Base, Team, TeamMember, User


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/rows.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        s.add(User(id="u1", email="a@x.z", name="a"))
        s.add(User(id="u2", email="b@x.z", name="b"))
        s.add(Team(id="t1", name="A"))
        s.add(Team(id="t2", name="B"))
        s.add(TeamMember(id="m1", team_id="t1", user_id="u1", display_name="a", role="admin"))
        s.add(TeamMember(id="m2", team_id="t2", user_id="u2", display_name="b", role="admin"))
        await s.commit()
    yield maker
    await engine.dispose()


async def _write(factory, team, member, n):
    async with factory() as s:
        for i in range(n):
            await write_audit(
                s,
                "member.removed",
                actor_member_id=member,
                target_type="agent_run",
                target_id="r1",
                detail={"teamId": team, "n": i},
            )
        await s.commit()


async def test_a_team_with_no_rows_has_no_latest_seq(factory):
    async with factory() as db:
        assert await latest_seq(db, "t1") == 0


async def test_latest_seq_ignores_a_newer_row_belonging_to_another_team(factory):
    await _write(factory, "t1", "m1", 3)
    await _write(factory, "t2", "m2", 4)  # seq 4..7
    async with factory() as db:
        assert await latest_seq(db, "t1") == 3
        assert await latest_seq(db, "t2") == 7


async def test_latest_seq_is_not_capped_by_the_page_size(factory):
    await _write(factory, "t1", "m1", 250)
    async with factory() as db:
        items, has_more = await read_audit_page(db, "t1", after_seq=0, limit=200)
        assert len(items) == 200 and has_more is True
        assert await latest_seq(db, "t1") == 250


async def test_latest_seq_includes_an_unbound_denial_that_names_the_team(factory):
    await _write(factory, "t2", "m2", 2)  # seq 1..2, other team
    async with factory() as s:
        await write_audit(
            s,
            "policy.denied",
            actor_member_id=None,
            actor_kind="system",
            target_type="team",
            target_id="t1",
            detail={"teamId": "t1", "reason": "not a member"},
        )
        await s.commit()
    async with factory() as db:
        assert await latest_seq(db, "t1") == 3
        assert await latest_seq(db, "t2") == 2
