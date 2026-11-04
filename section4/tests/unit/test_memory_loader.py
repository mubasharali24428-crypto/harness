"""M2-W12 — memory_loader tests: block assembly + propose_plan injection.

Unit-level assertions on the assembled prompt block, plus a sqlite-backed
integration slice proving propose_plan loads reviewed, unexpired project
memory and stores the block on the run. The advisory boundary is pinned:
memory cannot override rules or the plan scope, and it says so itself.
"""

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.memory_loader import (
    MAX_TOTAL_BYTES,
    MEMORY_ADVISORY_NOTE,
    MEMORY_BLOCK_HEADER,
    build_memory_block,
    load_project_memory,
)
from harness_orchestrator.service import OrchestratorService

# ------------------------------------------------------- build_memory_block


def test_empty_entries_yield_no_block():
    assert build_memory_block([]) == ""
    assert build_memory_block(None) == ""


def test_block_contains_header_advisory_note_and_verbatim_text():
    entries = [{
        "text": "The API prefers camelCase projections.", "source": "user",
        "reviewedBy": "reviewer-1", "createdBy": "u1", "createdAt": "2026-10-28T10:00:00",
    }]
    block = build_memory_block(entries)
    lines = block.splitlines()
    assert sum(1 for ln in lines if ln.startswith(MEMORY_BLOCK_HEADER)) == 1
    assert lines[0] == MEMORY_BLOCK_HEADER
    assert "The API prefers camelCase projections." in block
    assert "[1] source: user" in block and "author: u1" in block
    assert "reviewed by: reviewer-1" in block
    # the note names what it cannot override: rules, scope, policy
    assert "do NOT override" in block and "forbidden_paths" in block
    assert MEMORY_ADVISORY_NOTE.strip().splitlines()[0] in block


def test_block_is_bounded_even_if_loader_missed_the_cap():
    entries = [{
        "text": "y" * 4096, "source": "user", "reviewedBy": "r",
        "createdBy": "u1", "createdAt": "",
    } for _ in range(40)]
    block = build_memory_block(entries)
    assert len(block.encode("utf-8")) < MAX_TOTAL_BYTES + 4096
    assert "truncated" in block


# ------------------------------------------------------- load_project_memory


@pytest.fixture
async def memory_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.execute(
            sql_text(
                "CREATE TABLE project_memory ("
                "id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL, "
                "memory_text TEXT NOT NULL, source VARCHAR(16) NOT NULL, "
                "status VARCHAR(16) NOT NULL, reviewed_by VARCHAR(36), "
                "reviewed_at TIMESTAMP, expires_at TIMESTAMP, "
                "created_by VARCHAR(36) NOT NULL, "
                "created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL)"
            )
        )
        await conn.execute(
            sql_text(
                "INSERT INTO project_memory "
                "(id, project_id, memory_text, source, status, reviewed_by, "
                " created_by, created_at, updated_at) VALUES "
                "('m1','proj-a','The API prefers camelCase projections.','user','reviewed',"
                "'reviewer-1','u1','2026-10-28T10:00:00','2026-10-28T10:00:00'),"
                "('m2','proj-a','A draft nobody reviewed yet.','user','draft',"
                "NULL,'u1','2026-10-28T10:00:00','2026-10-28T10:00:00'),"
                "('m3','proj-a','Expired note.','user','reviewed',"
                "'reviewer-1','u1','2026-10-28T10:00:00','2026-10-28T10:00:00'),"
                "('m4','proj-b','Other project note.','user','reviewed',"
                "'reviewer-2','u2','2026-10-28T10:00:00','2026-10-28T10:00:00')"
            )
        )
        await conn.execute(
            sql_text("UPDATE project_memory SET expires_at='2026-10-27T10:00:00' WHERE id='m3'")
        )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_load_returns_only_reviewed_unexpired_of_the_project(memory_factory):
    entries = await load_project_memory(memory_factory, "proj-a")
    assert [e["text"] for e in entries] == ["The API prefers camelCase projections."]


async def test_load_never_crosses_projects(memory_factory):
    entries = await load_project_memory(memory_factory, "proj-b")
    assert [e["text"] for e in entries] == ["Other project note."]
    assert await load_project_memory(memory_factory, "proj-missing") == []


async def test_load_degrades_without_factory_or_project(memory_factory):
    assert await load_project_memory(None, "proj-a") == []
    assert await load_project_memory(memory_factory, None) == []
    assert await load_project_memory(memory_factory, "") == []


async def test_load_survives_broken_db():
    class Boom:
        def __call__(self):
            raise RuntimeError("db gone")

    assert await load_project_memory(Boom(), "proj-a") == []


# ------------------------------------------------- service injection


async def test_propose_plan_injects_memory_into_run_context(memory_factory):
    service = OrchestratorService(session_factory=memory_factory)
    await service.propose_plan("run-w12-mem", project_id="proj-a")
    block = service.runs["run-w12-mem"]["memory_prompt_block"]
    assert block.startswith(f"{MEMORY_BLOCK_HEADER}\n")
    assert "The API prefers camelCase projections." in block
    assert "A draft nobody reviewed yet." not in block

    # a project with no notes produces NO block
    await service.propose_plan("run-w12-mem-other", project_id="proj-other")
    assert service.runs["run-w12-mem-other"]["memory_prompt_block"] == ""


async def test_memory_is_advisory_only_forbidden_paths_untouched(memory_factory):
    """Hard-rule boundary: memory that demands a wider scope stays text and
    never mutates plan constraints."""
    service = OrchestratorService(session_factory=memory_factory)
    await service.propose_plan("run-w12-mem-guard", project_id="proj-a")
    plan = service.planner.get_plan(service.runs["run-w12-mem-guard"]["plan_id"])
    assert plan.forbiddenPaths == []
    block = service.runs["run-w12-mem-guard"]["memory_prompt_block"]
    assert "hard block" in block
