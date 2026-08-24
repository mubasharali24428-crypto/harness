"""M12-G10 — rules_loader tests: prompt assembly + propose_plan injection.

Unit-level assertions on the assembled planner prompt block (the lane's TESTS
requirement), plus a sqlite-backed integration slice proving propose_plan
actually loads the stored document and stores the block on the run. The
advisory boundary is pinned too: no rules content may ever mutate
forbidden_paths or claim enforcement power.
"""

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.rules_loader import (
    MAX_RULES_BYTES,
    PROJECT_RULES_BLOCK_HEADER,
    RULES_ADVISORY_NOTE,
    build_rules_block,
    load_project_rules,
)
from harness_orchestrator.service import OrchestratorService

# ----------------------------------------------------- build_rules_block


def test_empty_and_whitespace_rules_yield_no_block():
    assert build_rules_block("") == ""
    assert build_rules_block(None) == ""
    assert build_rules_block("   \n\t ") == ""


def test_block_contains_header_verbatim_text_and_advisory_note():
    text_value = "Always use pnpm.\nNo default exports."
    block = build_rules_block(text_value)
    lines = block.splitlines()
    # exactly one header line, first in the block
    assert sum(1 for ln in lines if ln.startswith(PROJECT_RULES_BLOCK_HEADER)) == 1
    assert lines[0] == PROJECT_RULES_BLOCK_HEADER
    assert "Always use pnpm." in block and "No default exports." in block
    # advisory disclaimer present: forbidden_paths stay hard-blocked by S5
    assert "forbidden_paths" in block and "hard block" in block
    assert RULES_ADVISORY_NOTE.strip().splitlines()[0] in block


def test_rules_text_is_never_altered_by_assembly():
    weird = "tabs\tkept\n\nblank lines kept  "
    body = build_rules_block(weird)
    assert weird.strip() in body  # verbatim (stripped only at the edges)


def test_oversized_document_cannot_produce_unbounded_prompt():
    huge = "x" * (MAX_RULES_BYTES * 2)
    block = build_rules_block(huge)
    assert len(block) < MAX_RULES_BYTES + 1000  # bounded even if loader missed it


# --------------------------------------------------- load_project_rules


@pytest.fixture
async def rules_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.execute(
            sql_text(
                "CREATE TABLE project_rules ("
                "id VARCHAR(36) PRIMARY KEY, "
                "project_id VARCHAR(36) NOT NULL UNIQUE, "
                "rules_text TEXT NOT NULL, "
                "updated_by VARCHAR(36), "
                "created_at TIMESTAMP, updated_at TIMESTAMP)"
            )
        )
        await conn.execute(
            sql_text(
                "INSERT INTO project_rules (id, project_id, rules_text) VALUES "
                "('r1', 'proj-rules', 'Always run section1 pytest before PR.'),"
                "('r2', 'proj-empty', '')"
            )
        )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_load_returns_stored_text(rules_factory):
    assert (
        await load_project_rules(rules_factory, "proj-rules")
        == "Always run section1 pytest before PR."
    )


async def test_load_missing_row_or_blank_is_no_rules(rules_factory):
    assert await load_project_rules(rules_factory, "proj-unknown") == ""
    assert await load_project_rules(rules_factory, "proj-empty") == ""


async def test_load_degrades_without_factory_or_project():
    assert await load_project_rules(None, "proj-x") == ""
    factory = object()  # non-callable/absent project id short-circuits first
    assert await load_project_rules(factory, None) == ""
    assert await load_project_rules(factory, "") == ""


async def test_load_survives_broken_db():
    class Boom:
        def __call__(self):
            raise RuntimeError("db gone")

    assert await load_project_rules(Boom(), "proj-x") == ""


# --------------------------------------- propose_plan injection (service)


async def test_propose_plan_injects_rules_block_into_run_context(rules_factory):
    service = OrchestratorService(session_factory=rules_factory)
    plan_id = await service.propose_plan("run-g10", project_id="proj-rules")
    assert plan_id
    run_data = service.runs["run-g10"]
    block = run_data["rules_prompt_block"]
    assert block.startswith(f"{PROJECT_RULES_BLOCK_HEADER}\n")
    assert "Always run section1 pytest before PR." in block

    # a project with no rules produces NO block (nothing injected)
    await service.propose_plan("run-g10-bare", project_id="proj-empty")
    assert service.runs["run-g10-bare"]["rules_prompt_block"] == ""

    # a run with no project at all likewise stays clean
    await service.propose_plan("run-g10-noproj")
    assert service.runs["run-g10-noproj"]["rules_prompt_block"] == ""


async def test_rules_are_advisory_only_forbidden_paths_untouched(rules_factory):
    """Hard-rule boundary: loading/injecting rules never mutates plan
    constraints. Section 5 remains the sole enforcement layer."""
    service = OrchestratorService(session_factory=rules_factory)
    await service.propose_plan("run-g10-guard", project_id="proj-rules")
    plan = service.planner.get_plan(service.runs["run-g10-guard"]["plan_id"])
    assert plan.forbiddenPaths == []  # rules text did NOT leak into constraints
    block = service.runs["run-g10-guard"]["rules_prompt_block"]
    assert "hard block" in block  # ...and the prompt says so itself
