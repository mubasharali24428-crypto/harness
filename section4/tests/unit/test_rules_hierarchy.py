"""F019 — hierarchical instruction tiers: precedence, labels, bounds, absence.

Unit proofs for the pure assembly + env/workspace resolution, plus one
propose_plan integration slice proving the hierarchy lands on the run and
reaches the execution prompt (with the flat legacy block as fallback).
"""

import asyncio
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.instruction_hierarchy import (
    INSTRUCTION_BLOCK_HEADER,
    INSTRUCTION_PRECEDENCE_NOTE,
    MAX_TIER_BYTES,
    TIER_ORDER,
    load_instruction_hierarchy,
    resolve_instruction_hierarchy,
)
from harness_orchestrator.service import OrchestratorService

ORG, TEAM, PROJ, DIR = "org says X", "team says Y", "project says Z", "dir says W"


class TestPrecedence:
    def test_all_tiers_render_in_precedence_order(self):
        h = load_instruction_hierarchy(
            org_text=ORG, team_text=TEAM, project_text=PROJ, directory_text=DIR
        )
        assert h.tiers == TIER_ORDER
        rendered = h.rendered
        first, last = rendered.index(ORG), rendered.index(DIR)
        positions = [rendered.index(p) for p in (ORG, TEAM, PROJ, DIR)]
        assert positions == sorted(positions) and first < last
        assert INSTRUCTION_BLOCK_HEADER in rendered
        assert "closest tier wins" in INSTRUCTION_BLOCK_HEADER.lower()
        assert INSTRUCTION_PRECEDENCE_NOTE in rendered

    def test_each_tier_is_labelled_with_its_source(self):
        h = load_instruction_hierarchy(
            org_text=ORG, org_source="/etc/org-rules.md",
            team_text=TEAM, team_source="/etc/team-rules.md",
            project_text=PROJ, project_source="project_rules:p-1",
            directory_text=DIR, directory_source="/ws/AGENTS.md",
        )
        rendered = h.rendered
        for label in ("ORG", "TEAM", "PROJECT", "DIRECTORY"):
            assert f"[{label}" in rendered
        assert "/etc/org-rules.md" in rendered and "/ws/AGENTS.md" in rendered

    def test_absent_tiers_are_recorded_not_silent(self):
        h = load_instruction_hierarchy(project_text=PROJ)
        assert h.tiers == ("project",)
        assert set(h.absent) >= {"org", "team", "directory"}
        meta = h.meta()
        json.dumps(meta)
        assert meta["absent"] and meta["tiers"][0]["tier"] == "project"

    def test_no_tiers_render_nothing(self):
        h = load_instruction_hierarchy()
        assert h.rendered == ""
        assert h.blocks == ()


class TestBounds:
    def test_oversize_tier_clamped(self):
        h = load_instruction_hierarchy(org_text="x" * (MAX_TIER_BYTES * 3))
        assert len(h.blocks[0].text) <= MAX_TIER_BYTES

    def test_total_cap_marks_overflow_tiers(self):
        big = "y" * MAX_TIER_BYTES
        h = load_instruction_hierarchy(
            org_text=big, team_text=big, project_text=big, directory_text=big
        )
        assert len(h.blocks) >= 2
        assert any("total cap" in a for a in h.absent)


class TestResolution:
    def test_env_files_and_workspace_agents_md(self, tmp_path):
        org_file = tmp_path / "org.md"
        org_file.write_text(ORG)
        team_file = tmp_path / "team.md"
        team_file.write_text(TEAM)
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "AGENTS.md").write_text(DIR)

        h = asyncio.run(
            resolve_instruction_hierarchy(
                None,
                "p-1",
                str(ws),
                env={"ORG_RULES_FILE": str(org_file), "TEAM_RULES_FILE": str(team_file)},
            )
        )
        assert h.tiers == ("org", "team", "directory")  # no project rules source
        rendered = h.rendered
        assert ORG in rendered and TEAM in rendered and DIR in rendered

    def test_unreadable_tier_file_degrades_to_absent(self, tmp_path):
        h = asyncio.run(
            resolve_instruction_hierarchy(
                None,
                None,
                None,
                env={
                    "ORG_RULES_FILE": str(tmp_path),  # a directory, not a file
                    "TEAM_RULES_FILE": str(tmp_path / "missing.md"),
                },
            )
        )
        assert h.rendered == ""
        assert set(h.absent) == set(TIER_ORDER)


DDL = """
CREATE TABLE teams (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) NOT NULL, budget_usd REAL NULL);
CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL);
CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL);
CREATE TABLE agent_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id VARCHAR(36) NOT NULL,
    type VARCHAR(80) NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00'
);
CREATE TABLE agent_plans (
    id VARCHAR(36) PRIMARY KEY,
    run_id VARCHAR(36) NOT NULL,
    goal TEXT NOT NULL,
    files_likely_to_change TEXT NOT NULL DEFAULT '[]',
    steps TEXT NOT NULL DEFAULT '[]',
    tests TEXT NOT NULL DEFAULT '[]',
    risks TEXT NOT NULL DEFAULT '[]',
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    forbidden_paths TEXT NOT NULL DEFAULT '[]',
    expected_result TEXT NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL,
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00'
);
CREATE TABLE project_rules (project_id VARCHAR(36) PRIMARY KEY, rules_text TEXT NOT NULL);
"""


class _Bus:
    def __init__(self, factory) -> None:
        self._factory = factory

    async def publish(self, event_type, project_id, payload=None, run_id=None) -> None:
        name = event_type.value if hasattr(event_type, "value") else str(event_type)
        async with self._factory() as db:
            await db.execute(
                text(
                    "INSERT INTO agent_events (run_id, type, payload)"
                    " VALUES (:r, :t, :payload)"
                ),
                {"r": run_id, "t": name, "payload": json.dumps(payload or {}, default=str)},
            )
            await db.commit()

    async def ensure_run(self, project_id: str, run_id: str) -> None:
        pass


async def test_propose_plan_stores_hierarchy_and_execution_uses_it(
    tmp_path, monkeypatch
):
    """Integration slice: project tier + workspace AGENTS.md reach the
    assembled execution prompt through the real propose path."""
    monkeypatch.delenv("CONTEXT_SEARCH_BASE_URL", raising=False)
    monkeypatch.delenv("ORG_RULES_FILE", raising=False)
    monkeypatch.delenv("TEAM_RULES_FILE", raising=False)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "AGENTS.md").write_text(DIR)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/h.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        for stmt in DDL.split(";"):
            if stmt.strip():
                await conn.execute(text(stmt))
        await conn.execute(text("INSERT INTO teams VALUES ('team-a', 'A', NULL)"))
        await conn.execute(text("INSERT INTO projects VALUES ('p-1', 'team-a')"))
        await conn.execute(
            text("INSERT INTO project_rules VALUES ('p-1', :r)"), {"r": PROJ}
        )
    try:
        svc = OrchestratorService(
            engine=DeepSeekHarnessWrapper(
                backend_runner=lambda g: "ok", workspace_dir=str(ws)
            ),
            session_factory=factory,
            run_queue_factory=factory,
            bus=_Bus(factory),
        )
        await svc.propose_plan("run-h", goal="do the thing", project_id="p-1")
        run_data = svc.runs["run-h"]
        assert run_data["instruction_hierarchy"]["tiers"] == [
            {"tier": "project", "source": "project_rules:p-1", "bytes": len(PROJ.encode())},
            {"tier": "directory", "source": str(ws / "AGENTS.md"), "bytes": len(DIR.encode())},
        ]
        block = run_data["instructions_block"]
        assert PROJ in block and DIR in block
        # precedence: project before directory (directory closest, rendered last)
        assert block.index(PROJ) < block.index(DIR)
    finally:
        await engine.dispose()
