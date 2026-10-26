"""M06: which goal images reach the model, and what is reported when they don't."""

import asyncio
import base64

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.engine import _prompt_input
from harness_orchestrator.goal_images import (
    canonical_b64,
    resolve_goal_images,
    vision_allowlist,
)

DDL = """
CREATE TABLE artifacts (
    id VARCHAR(64) PRIMARY KEY,
    team_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) NOT NULL,
    name VARCHAR(200) NOT NULL,
    media_type VARCHAR(120) NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    storage_ref VARCHAR(200) NOT NULL,
    kind VARCHAR(20) NOT NULL,
    state VARCHAR(20) NOT NULL,
    scan TEXT NOT NULL DEFAULT '{}',
    request_id VARCHAR(64) NULL,
    created_by VARCHAR(64) NOT NULL,
    created_at TEXT NOT NULL,
    retention_until TEXT NULL,
    deleted_at TEXT NULL
);
"""

PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 8  # a real PNG magic prefix


def _insert(db, **kw):
    row = {
        "id": "a1",
        "team_id": "team-a",
        "run_id": "run-1",
        "name": "shot.png",
        "media_type": "image/png",
        "size_bytes": len(PNG),
        "sha256": "x",
        "storage_ref": "team-a/x",
        "kind": "upload",
        "state": "available",
        "created_by": "u",
        "created_at": "2026-10-26T00:00:00+00:00",
        "retention_until": None,
        **kw,
    }
    return db.execute(
        text(
            "INSERT INTO artifacts (id, team_id, run_id, name, media_type, size_bytes, sha256,"
            " storage_ref, kind, state, created_by, created_at, retention_until)"
            " VALUES (:id, :team_id, :run_id, :name, :media_type, :size_bytes, :sha256,"
            " :storage_ref, :kind, :state, :created_by, :created_at, :retention_until)"
        ),
        row,
    )


@pytest.fixture
def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/art.db")
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as db:
            for stmt in DDL.split(";"):
                if stmt.strip():
                    await db.execute(text(stmt))

    asyncio.new_event_loop().run_until_complete(_setup())
    return maker


def _seed(factory, *specs):
    async def _go():
        async with factory() as db:
            for spec in specs:
                await _insert(db, **spec)
            await db.commit()

    asyncio.new_event_loop().run_until_complete(_go())


def _read(ref):
    return PNG if ref == "team-a/x" else None


def test_an_available_image_upload_becomes_a_canonical_block(factory):
    _seed(factory, {})
    out = asyncio.new_event_loop().run_until_complete(
        resolve_goal_images(factory, "run-1", "team-a", read=_read)
    )
    assert out.refused is None and out.skipped == []
    assert out.blocks == [{"type": "image", "data": canonical_b64(PNG), "mimeType": "image/png"}]


def test_other_teams_and_other_runs_images_are_never_selected(factory):
    _seed(
        factory,
        {"id": "other-team", "team_id": "team-b"},
        {"id": "other-run", "run_id": "run-2"},
        {"id": "quarantined", "state": "quarantined"},
        {"id": "agent-output", "kind": "output"},
        {"id": "not-an-image", "media_type": "text/plain"},
    )
    out = asyncio.new_event_loop().run_until_complete(
        resolve_goal_images(factory, "run-1", "team-a", read=lambda ref: PNG)
    )
    assert out.blocks == [] and out.skipped == []
    assert out.team_scoped is True


def test_an_unknown_team_still_stays_inside_the_run(factory):
    """teamId is a process stamp, not a durable column: a replica that never
    saw the bind must degrade to run-scoped selection and SAY so, not silently
    attach nothing."""
    _seed(
        factory,
        {"id": "mine", "team_id": "team-a"},
        {"id": "theirs", "run_id": "run-2", "team_id": "team-b"},
    )
    out = asyncio.new_event_loop().run_until_complete(
        resolve_goal_images(factory, "run-1", None, read=lambda ref: PNG)
    )
    assert len(out.blocks) == 1
    assert out.team_scoped is False
    assert out.summary()["teamScoped"] is False


def test_an_expired_image_is_not_sent_even_before_the_purge(factory):
    """The artifacts API 410s this row on read; the model call must not keep
    serving bytes somebody decided to retain for less time."""
    _seed(factory, {"retention_until": "2020-01-01T00:00:00+00:00"})
    out = asyncio.new_event_loop().run_until_complete(
        resolve_goal_images(factory, "run-1", "team-a", read=lambda ref: PNG)
    )
    assert out.blocks == []
    assert out.skipped[0]["reason"] == "retention expired"


def test_an_unparseable_retention_stamp_is_treated_as_expired(factory):
    _seed(factory, {"retention_until": "not-a-date"})
    out = asyncio.new_event_loop().run_until_complete(
        resolve_goal_images(factory, "run-1", "team-a", read=lambda ref: PNG)
    )
    assert out.blocks == [] and out.skipped[0]["reason"] == "retention expired"


def test_oversized_image_is_skipped_with_a_reason(factory, monkeypatch):
    monkeypatch.setenv("HARNESS_GOAL_IMAGE_MAX_BYTES", "8")
    _seed(factory, {"size_bytes": 99})
    out = asyncio.new_event_loop().run_until_complete(
        resolve_goal_images(factory, "run-1", "team-a", read=lambda ref: b"z" * 99)
    )
    assert out.blocks == []
    assert out.skipped[0]["reason"] == "larger than 8 bytes"


def test_count_limit_keeps_the_oldest_images(factory, monkeypatch):
    monkeypatch.setenv("HARNESS_GOAL_IMAGE_MAX_COUNT", "2")
    _seed(
        factory,
        {"id": "a", "storage_ref": "r/a", "created_at": "2026-10-26T01:00:00+00:00"},
        {"id": "b", "storage_ref": "r/b", "created_at": "2026-10-26T02:00:00+00:00"},
        {"id": "c", "storage_ref": "r/c", "created_at": "2026-10-26T03:00:00+00:00"},
    )
    out = asyncio.new_event_loop().run_until_complete(
        resolve_goal_images(factory, "run-1", "team-a", read=lambda ref: PNG)
    )
    assert [b["mimeType"] for b in out.blocks] == ["image/png", "image/png"]
    assert out.skipped == [{"artifactId": "c", "name": "shot.png", "reason": "image count limit"}]


def test_total_budget_is_enforced_after_the_first_image(factory, monkeypatch):
    monkeypatch.setenv("HARNESS_GOAL_IMAGE_TOTAL_BYTES", str(len(PNG) * 2 - 1))
    _seed(
        factory,
        {"id": "a", "storage_ref": "r/a"},
        {"id": "b", "storage_ref": "r/b"},
    )
    seen = []

    def read(ref):
        seen.append(ref)
        return PNG

    out = asyncio.new_event_loop().run_until_complete(
        resolve_goal_images(factory, "run-1", "team-a", read=read)
    )
    # Ordering is oldest-first and the budget applies in that order; whichever
    # row landed second is the one reported as over budget.
    assert len(out.blocks) == 1
    assert "total image budget" in out.skipped[0]["reason"]


def test_missing_bytes_are_reported_not_guessed(factory):
    _seed(factory, {})
    out = asyncio.new_event_loop().run_until_complete(
        resolve_goal_images(factory, "run-1", "team-a", read=lambda ref: None)
    )
    assert out.blocks == []
    assert out.skipped[0]["reason"] == "stored bytes missing"


def test_a_raising_reader_degrades_to_a_skip(factory):
    _seed(factory, {})

    def boom(_ref):
        raise OSError("disk gone")

    out = asyncio.new_event_loop().run_until_complete(
        resolve_goal_images(factory, "run-1", "team-a", read=boom)
    )
    assert out.blocks == [] and out.skipped[0]["reason"] == "stored bytes missing"


def test_vision_allowlist_refuses_before_the_call(factory, monkeypatch):
    monkeypatch.setenv("HARNESS_VISION_MODELS", "deepseek-vision,other-model")
    assert vision_allowlist() == frozenset({"deepseek-vision", "other-model"})
    _seed(factory, {})
    out = asyncio.new_event_loop().run_until_complete(
        resolve_goal_images(factory, "run-1", "team-a", "text-only-model", read=_read)
    )
    assert out.blocks == [] and "not in HARNESS_VISION_MODELS" in out.refused
    ok = asyncio.new_event_loop().run_until_complete(
        resolve_goal_images(factory, "run-1", "team-a", "deepseek-vision", read=_read)
    )
    assert ok.refused is None and len(ok.blocks) == 1


def test_unset_allowlist_leaves_the_runtime_as_authority(monkeypatch):
    monkeypatch.delenv("HARNESS_VISION_MODELS", raising=False)
    assert vision_allowlist() is None


def test_kill_switch_sends_nothing(factory, monkeypatch):
    monkeypatch.setenv("HARNESS_GOAL_IMAGES", "off")
    _seed(factory, {})
    out = asyncio.new_event_loop().run_until_complete(
        resolve_goal_images(factory, "run-1", "team-a", read=_read)
    )
    assert out.blocks == [] and out.refused is None


def test_no_session_factory_is_a_text_only_run():
    out = asyncio.new_event_loop().run_until_complete(
        resolve_goal_images(None, "run-1", "team-a", read=_read)
    )
    assert out.blocks == []


def test_missing_artifacts_table_does_not_break_the_run(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/empty.db")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    out = asyncio.new_event_loop().run_until_complete(
        resolve_goal_images(maker, "run-1", "team-a", read=_read)
    )
    assert out.blocks == [] and out.skipped == [] and out.refused is None


def test_canonical_b64_roundtrip_is_the_runtime_admission_test():
    assert canonical_b64(PNG) == base64.b64encode(PNG).decode()
    # non-canonical padding is what the runtime rejects; b64encode never emits
    # it, so the guard is proven by the decode side instead.
    assert base64.b64decode(canonical_b64(PNG), validate=True) == PNG


def test_prompt_input_keeps_the_bare_string_when_there_is_no_image():
    assert _prompt_input("fix the parser", None) == "fix the parser"
    assert _prompt_input("fix the parser", []) == "fix the parser"


def test_prompt_input_puts_the_text_block_first_with_images():
    block = {"type": "image", "data": "AA==", "mimeType": "image/png"}
    assert _prompt_input("why does this look wrong", [block]) == [
        {"type": "text", "text": "why does this look wrong"},
        block,
    ]
