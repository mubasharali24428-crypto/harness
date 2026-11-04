"""M2-W12 — skills_loader tests: block assembly + propose_plan injection.

Unit-level assertions on the assembled prompt block, plus a sqlite-backed
integration slice proving propose_plan loads reviewed team skills through the
project's team and stores the block on the run. The advisory boundary is
pinned too: no skill content may ever mutate forbidden_paths or claim
enforcement power, and the digest pin travels byte-identically to Section 1.
"""

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.service import OrchestratorService
from harness_orchestrator.skills_loader import (
    MAX_TOTAL_BYTES,
    SKILL_BLOCK_HEADER,
    SKILLS_ADVISORY_NOTE,
    build_skills_block,
    load_team_skills,
    project_team_id,
    skill_digest,
)

# The golden vector shared with Section 1: same canonical JSON, same sha256.
GOLDEN = {
    "name": "deploy-guide",
    "version": "1.0.0",
    "description": "Deployment checklist",
    "instructions": "Run make static-gates before PR.",
    "source": "team-authored",
    "author": "mubashar",
    "teamId": "team-a",
}
GOLDEN_DIGEST = "74e80712b547a590700d84dbf94dfd8c1ad61298eae19433ffd0a5e50492e93e"


def _skill_row(name="deploy-guide", version="1.0.0", status="reviewed", enabled=1,
               instructions="Run make static-gates before PR.", team="team-a",
               pin=GOLDEN_DIGEST, source="team-authored", author="mubashar",
               reviewer="reviewer-1", expires=None):
    return {
        "name": name, "version": version, "description": "Deployment checklist",
        "instructions": instructions, "provenance_source": source,
        "provenance_author": author, "reviewed_by": reviewer, "pinned_sha256": pin,
        "expires_at": expires,
    }


# --------------------------------------------------------- digest identity


def test_golden_digest_matches_section1():
    assert skill_digest(
        GOLDEN["name"], GOLDEN["version"], GOLDEN["description"],
        GOLDEN["instructions"], GOLDEN["source"], GOLDEN["author"], GOLDEN["teamId"],
    ) == GOLDEN_DIGEST


# ------------------------------------------------------- build_skills_block


def test_empty_skills_yield_no_block():
    assert build_skills_block([]) == ""
    assert build_skills_block(None) == ""


def test_block_contains_header_advisory_note_and_verbatim_instructions():
    skills = [{
        "name": "deploy-guide", "version": "1.0.0", "description": "Deployment checklist",
        "instructions": "Run make static-gates before PR.", "source": "team-authored",
        "author": "mubashar", "reviewedBy": "reviewer-1", "digest": GOLDEN_DIGEST,
    }]
    block = build_skills_block(skills)
    lines = block.splitlines()
    assert sum(1 for ln in lines if ln.startswith(SKILL_BLOCK_HEADER)) == 1
    assert lines[0] == SKILL_BLOCK_HEADER
    assert "Run make static-gates before PR." in block
    assert "deploy-guide@1.0.0" in block and "74e80712b547" in block
    assert "reviewed by: reviewer-1" in block
    assert "advisory" in block and "hard block" in block
    assert SKILLS_ADVISORY_NOTE.strip().splitlines()[0] in block


def test_block_is_bounded_even_if_loader_missed_the_cap():
    skills = [{
        "name": f"skill-{i}", "version": "1.0.0", "description": "d",
        "instructions": "x" * 4096, "source": "s", "author": "a",
        "reviewedBy": "r", "digest": "0" * 64,
    } for i in range(40)]
    block = build_skills_block(skills)
    assert len(block.encode("utf-8")) < MAX_TOTAL_BYTES + 4096
    assert "truncated" in block


# --------------------------------------------------------- load_team_skills


@pytest.fixture
async def skills_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.execute(
            sql_text(
                "CREATE TABLE team_skills ("
                "id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL, "
                "name VARCHAR(32) NOT NULL, version VARCHAR(16) NOT NULL, "
                "description VARCHAR(500) NOT NULL, instructions TEXT NOT NULL, "
                "provenance_source VARCHAR(200) NOT NULL, "
                "provenance_author VARCHAR(200) NOT NULL, "
                "status VARCHAR(16) NOT NULL, enabled BOOLEAN NOT NULL, "
                "reviewed_by VARCHAR(36), reviewed_at TIMESTAMP, "
                "pinned_sha256 VARCHAR(64), expires_at TIMESTAMP, "
                "created_by VARCHAR(36) NOT NULL, "
                "created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL)"
            )
        )
        await conn.execute(
            sql_text(
                "INSERT INTO team_skills "
                "(id, team_id, name, version, description, instructions, "
                " provenance_source, provenance_author, status, enabled, "
                " reviewed_by, pinned_sha256, created_by, created_at, updated_at) VALUES "
                "('s1','team-a','deploy-guide','1.0.0','Deployment checklist',"
                "'Run make static-gates before PR.','team-authored','mubashar',"
                "'reviewed',1,'reviewer-1','" + GOLDEN_DIGEST + "',"
                "'u1','2026-10-28T10:00:00','2026-10-28T10:00:00'),"
                "('s2','team-a','draft-skill','1.0.0','d','x','team-authored','a',"
                "'draft',0,NULL,'" + GOLDEN_DIGEST + "',"
                "'u1','2026-10-28T10:00:00','2026-10-28T10:00:00'),"
                "('s3','team-a','disabled-skill','1.0.0','d','x','team-authored','a',"
                "'disabled',0,'reviewer-1','" + GOLDEN_DIGEST + "',"
                "'u1','2026-10-28T10:00:00','2026-10-28T10:00:00'),"
                "('s4','team-a','tampered','1.0.0','d','x','team-authored','a',"
                "'reviewed',1,'reviewer-1','deadbeef',"
                "'u1','2026-10-28T10:00:00','2026-10-28T10:00:00'),"
                "('s5','team-b','beta','1.0.0','d','x','team-authored','a',"
                "'reviewed',1,'reviewer-1','e02ed7080224321bbdb4eac2516aaaac0db46ce5fc9cf4fe64f6cbea059483e1',"
                "'u2','2026-10-28T10:00:00','2026-10-28T10:00:00'),"
                "('s6','team-a','expired-skill','1.0.0','d','x','team-authored','a',"
                "'reviewed',1,'reviewer-1','" + GOLDEN_DIGEST + "',"
                "'u1','2026-10-28T10:00:00','2026-10-28T10:00:00')"
            )
        )
        await conn.execute(
            sql_text("UPDATE team_skills SET expires_at='2026-10-27T10:00:00' WHERE id='s6'")
        )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_load_returns_only_reviewed_enabled_unexpired_unpinned(skills_factory):
    skills = await load_team_skills(skills_factory, "team-a")
    assert [s["name"] for s in skills] == ["deploy-guide"]
    assert skills[0]["digest"] == GOLDEN_DIGEST
    assert skills[0]["reviewedBy"] == "reviewer-1"


async def test_load_other_team_sees_its_own(skills_factory):
    skills = await load_team_skills(skills_factory, "team-b")
    assert [s["name"] for s in skills] == ["beta"]


async def test_load_degrades_without_factory_or_team(skills_factory):
    assert await load_team_skills(None, "team-a") == []
    assert await load_team_skills(skills_factory, None) == []
    assert await load_team_skills(skills_factory, "") == []


async def test_load_survives_broken_db():
    class Boom:
        def __call__(self):
            raise RuntimeError("db gone")

    assert await load_team_skills(Boom(), "team-a") == []


# ------------------------------------------------- project_team_id + service


@pytest.fixture
async def full_factory(skills_factory):
    async with skills_factory() as db:
        await db.execute(
            sql_text(
                "CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, "
                "team_id VARCHAR(36) NOT NULL)"
            )
        )
        await db.execute(
            sql_text(
                "INSERT INTO projects (id, team_id) VALUES "
                "('proj-a', 'team-a'), ('proj-b', 'team-b'), ('proj-c', 'team-c')"
            )
        )
        await db.commit()
    return skills_factory


async def test_project_team_id_lookup(full_factory):
    assert await project_team_id(full_factory, "proj-a") == "team-a"
    assert await project_team_id(full_factory, "proj-missing") is None
    assert await project_team_id(full_factory, None) is None


async def test_propose_plan_injects_skills_through_the_projects_team(full_factory):
    service = OrchestratorService(session_factory=full_factory)
    await service.propose_plan("run-w12-skills", project_id="proj-a")
    block = service.runs["run-w12-skills"]["skills_prompt_block"]
    assert block.startswith(f"{SKILL_BLOCK_HEADER}\n")
    assert "Run make static-gates before PR." in block
    assert "draft-skill" not in block

    # a project whose team has no skills produces NO block
    await service.propose_plan("run-w12-noskills", project_id="proj-c")
    assert service.runs["run-w12-noskills"]["skills_prompt_block"] == ""

    # a run with no project at all likewise stays clean
    await service.propose_plan("run-w12-noproj")
    assert service.runs["run-w12-noproj"]["skills_prompt_block"] == ""


async def test_skills_are_advisory_only_forbidden_paths_untouched(full_factory):
    """Hard-rule boundary: injecting a skill never mutates plan constraints.
    Even a skill whose text demands a wider scope stays text."""
    service = OrchestratorService(session_factory=full_factory)
    await service.propose_plan("run-w12-guard", project_id="proj-a")
    plan = service.planner.get_plan(service.runs["run-w12-guard"]["plan_id"])
    assert plan.forbiddenPaths == []
    block = service.runs["run-w12-guard"]["skills_prompt_block"]
    assert "hard block" in block
