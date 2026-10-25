"""Phase 7: Meeting Area state survives a service/process rebuild."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_meeting.service import MEETING_CLOSED, MeetingService


async def _schema(factory):
    async with factory() as db:
        await db.execute(text("CREATE TABLE projects (id TEXT PRIMARY KEY, team_id TEXT NOT NULL)"))
        await db.execute(
            text(
                "CREATE TABLE meetings (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
                "title TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, "
                "closed_at TEXT, brief_id TEXT)"
            )
        )
        await db.execute(
            text(
                "CREATE TABLE meeting_messages (id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, "
                "sender_id TEXT, sender_member_id TEXT, kind TEXT NOT NULL, body TEXT NOT NULL, "
                "is_ai_generated BOOLEAN NOT NULL DEFAULT FALSE, created_at TEXT NOT NULL)"
            )
        )
        await db.execute(
            text(
                "CREATE TABLE engineering_briefs (id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, "
                "project_id TEXT NOT NULL, requirements JSON NOT NULL, constraints JSON NOT NULL, "
                "acceptance_criteria JSON NOT NULL, risks JSON NOT NULL, "
                "open_questions JSON NOT NULL, "
                "status TEXT NOT NULL, approved_by TEXT, created_at TEXT NOT NULL)"
            )
        )
        await db.execute(
            text(
                "CREATE TABLE meeting_summaries (id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, "
                "content TEXT NOT NULL, format TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
        )
        await db.execute(
            text(
                "CREATE TABLE meeting_prompts (id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, "
                "version INTEGER NOT NULL, content TEXT NOT NULL, created_by TEXT NOT NULL, "
                "linked_task_id TEXT, linked_branch TEXT, created_at TEXT NOT NULL)"
            )
        )
        await db.execute(text("INSERT INTO projects (id, team_id) VALUES ('p1', 'team-1')"))
        await db.commit()


async def test_meeting_state_round_trips_after_service_rebuild(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'meeting.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _schema(factory)

    first = MeetingService(db_factory=factory)
    meeting = await first.acreate_meeting("p1", "Durable room", team_id="team-1")
    await first.apost_message(meeting.id, "member-1", "We need requirement persistence.")
    brief = await first.agenerate_brief(meeting.id)
    summary = await first.agenerate_summary(meeting.id)
    prompt = await first.acreate_prompt(meeting.id, "member-1", "Keep the transcript.")
    await first.aclose_meeting(meeting.id)

    second = MeetingService(db_factory=factory)
    restored = await second.aget_meeting(meeting.id)
    messages, has_more, _ = await second.aget_message_page(meeting.id)
    restored_brief = await second.aget_brief(meeting.id)
    prompts = await second.alist_prompts(meeting.id)

    assert restored.title == meeting.title
    assert restored.teamId == "team-1"
    assert restored.status == MEETING_CLOSED
    assert restored_brief is not None and restored_brief.id == brief.id
    assert summary.meetingId == restored.id
    assert [m.content for m in messages] == ["We need requirement persistence."]
    assert has_more is False
    assert [p.id for p in prompts] == [prompt.id]

    await engine.dispose()
