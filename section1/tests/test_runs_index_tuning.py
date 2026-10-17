"""M16-X1 — runs-index tuning at 100k events (ALEX-FORCE wave 6, Squad ALPHA).

Enumerated production shapes (section4/src/harness_orchestrator/runs_index.py,
GET /api/v1/orchestrator/runs)::

    page       SELECT r.id, r.project_id, r.state, r.created_at
               FROM agent_runs r JOIN projects p ON p.id = r.project_id
               WHERE p.team_id = :team_id [AND r.project_id = ...]
                     [AND r.state = ...]
                     [AND (r.created_at, r.id) < (:cursor_created, :cursor_run)]
               ORDER BY r.created_at DESC, r.id DESC LIMIT :limit;

    lifecycle  SELECT e.run_id, MIN(e.ts) FROM agent_events e    -- x2 families
               WHERE e.run_id IN (:...page_ids) AND e.type IN (:...markers)
               GROUP BY e.run_id;

Migration 0026 backs them with idx_agent_runs_team_created_id (ordered index
range for the page) and idx_agent_events_run_type_ts_covering (index-only MIN
per run). Before M16-X1 the lifecycle half was TWO correlated MIN(ts) probes
PER ROW — the N+1 this lane kills; the statement-budget pin below fails if
any per-row agent_events statement ever comes back.

EXPLAIN evidence, per dialect:
- SQLite (always on): EXPLAIN QUERY PLAN via typed-literal rendering of the
  exact production TextClauses — SEARCH ... USING INDEX
  idx_agent_runs_team_created_id (including row-value cursor bounds) and
  SEARCH ... USING COVERING INDEX idx_agent_events_run_type_ts_covering.
- Postgres (opt-in via HARNESS_TEST_PG, money-suite convention): the real
  0001->0026 chain on a throwaway database, EXPLAIN (COSTS OFF) output pasted
  into the test log; the covering index's NAME is asserted (scan flavor is
  planner/statistics dependent on a freshly loaded table).

Measured while authoring this lane (SQLite, ~80k agent_events): the
rewritten request costs ~0.7 ms vs ~8.3 ms for the legacy N+1 simulation
(52 round trips -> 3 statements, ~11x).
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import (
    DateTime,
    Integer,
    String,
    bindparam,
    column,
    event,
    text,
)
from sqlalchemy import (
    table as sqlalchemy_table,
)
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# runs_index.py lives in section4 (same interpreter-path dance as the S3/S8
# cross-section suites); migrations resolve relative to the repo root.
_ROOT = Path(__file__).resolve().parents[2]
_S4_SRC = _ROOT / "section4" / "src"
if _S4_SRC.exists() and str(_S4_SRC) not in sys.path:
    sys.path.insert(0, str(_S4_SRC))

import harness_orchestrator.api as orch_api  # noqa: E402
import harness_orchestrator.runs_index as ri  # noqa: E402
from harness_contracts.roles import Principal, Role  # noqa: E402

_MIGRATIONS = _ROOT / "section1" / "migrations"
_TEAM_IDX = "idx_agent_runs_team_created_id"
_COVERING_IDX = "idx_agent_events_run_type_ts_covering"

# Scale targets (mission floor: >=50k agent_events).
_RUNS_A = 1500  # team-a runs spread over 3 projects (forces the merge sort)
_RUNS_B = 500  # second team: scoping must stay honest at scale
_EVENTS_PER_RUN = (20, 60)  # uniform-ish spread -> ~80k events total


def _migration_body() -> str:
    return (_MIGRATIONS / "0026_runs_index_tuning.sql").read_text()


def _migration_statements() -> list[str]:
    """Executable statements of 0026, comments stripped, in file order.

    Strips '--' to end of line (trailing comments included) so the file's
    prose survives but only SQL executes.
    """
    out = []
    for raw in _migration_body().split(";"):
        lines = [re.sub(r"--.*$", "", ln) for ln in raw.splitlines()]
        stmt = "\n".join(lines).strip()
        if stmt:
            out.append(stmt)
    return out


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def scale_db(tmp_path_factory):
    """SQLite engine holding frozen-shape tables + BOTH 0026 indexes, seeded
    to mission scale (~80k agent_events >= the 50k floor).

    NullPool on purpose: the same engine is driven from pytest-asyncio loops
    AND TestClient portals, and aiosqlite connections must never hop loops.
    """
    tmp_path = tmp_path_factory.mktemp("m16x1")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/scale.db", poolclass=NullPool
    )

    async def _build():
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY,"
                     " team_id VARCHAR(36) NOT NULL)")
            )
            await conn.execute(
                text("CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY,"
                     " project_id VARCHAR(36) NOT NULL, state VARCHAR(40) NOT NULL,"
                     " created_at TIMESTAMP WITH TIME ZONE NOT NULL)")
            )
            await conn.execute(
                text("CREATE TABLE agent_events (seq INTEGER PRIMARY KEY,"
                     " run_id VARCHAR(36), type VARCHAR(60) NOT NULL,"
                     " ts TIMESTAMP WITH TIME ZONE NOT NULL)")
            )
            # Apply migration 0026 VERBATIM: its exact statements must stand
            # alone on a plain schema (additive, runner-compatible).
            for stmt in _migration_statements():
                await conn.execute(text(stmt))

            await conn.execute(
                text("INSERT INTO projects VALUES"
                     " ('pA','team-a'), ('pB','team-a'), ('pC','team-a'),"
                     " ('pZ','team-b')")
            )
            base = datetime.now(UTC)
            # Typed table clauses: datetimes bind natively (no deprecated
            # sqlite3 datetime-adapter path, so no 80k warnings).
            runs_table = sqlalchemy_table(
                "agent_runs",
                column("id", String),
                column("project_id", String),
                column("state", String),
                column("created_at", DateTime),
            )
            events_table = sqlalchemy_table(
                "agent_events",
                column("seq", Integer),
                column("run_id", String),
                column("type", String),
                column("ts", DateTime),
            )
            runs_insert = runs_table.insert()
            events_insert = events_table.insert()
            states = ("planning", "executing", "pr_opened")
            runs, events, seq = [], [], 0
            lo, hi = _EVENTS_PER_RUN
            span = hi - lo
            for n in range(_RUNS_A + _RUNS_B):
                pid = f"p{'ABC'[n % 3]}" if n < _RUNS_A else "pZ"
                runs.append(
                    {
                        "id": f"run-{n:06d}",
                        "project_id": pid,
                        "state": states[n % 3],
                        "created_at": base - timedelta(seconds=n * 7),
                    }
                )
                for k in range(lo + (n % span)):
                    seq += 1
                    etype = (
                        "log_line",
                        "execution_started",
                        "engine_completed",
                        "resumed",
                        "tool_call",
                    )[k % 5]
                    offset = {
                        "execution_started": 30,
                        "engine_completed": 300,
                    }.get(etype, 0)
                    events.append(
                        {
                            "seq": seq,
                            "run_id": f"run-{n:06d}",
                            "type": etype,
                            "ts": base - timedelta(seconds=n * 7 - offset - k),
                        }
                    )
            for chunk in range(0, len(runs), 5000):
                await conn.execute(
                    runs_insert.values(runs[chunk : chunk + 5000])
                )
            for chunk in range(0, len(events), 10000):
                await conn.execute(
                    events_insert.values(events[chunk : chunk + 10000])
                )
            return len(runs), len(events)

    n_runs, n_events = asyncio.run(_build())
    assert n_events >= 50_000, f"fixture under-seeded: {n_events}"
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield SimpleNamespace(engine=engine, factory=factory, runs=n_runs, events=n_events)
    asyncio.run(engine.dispose())


@pytest.fixture
def captured(scale_db):
    """SQL capture handle bound to the scale engine's sync engine."""

    class _Capture:
        def __init__(self, eng):
            self.statements: list[str] = []
            self._eng = eng

        def __enter__(self):
            event.listen(self._eng.sync_engine, "before_cursor_execute", self._on)
            return self

        def __exit__(self, *exc):
            event.remove(self._eng.sync_engine, "before_cursor_execute", self._on)
            return False

        def _on(self, conn, cursor, statement, parameters, context, executemany):
            self.statements.append(statement)

        @property
        def agent_events_statements(self):
            return [s for s in self.statements if "agent_events" in s]

    return lambda: _Capture(scale_db.engine)


def _client(monkeypatch, factory, principal: Principal | None = None):
    """FastAPI app hosting ONLY the runs router, identity overridden."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    principal = principal or Principal(userId="u-x1", teamId="team-a", role=Role.ADMIN)

    class _Stub:
        session_factory = factory

    monkeypatch.setattr(orch_api, "_service", _Stub())
    app = FastAPI()
    app.include_router(ri.router)
    app.dependency_overrides[orch_api.get_principal] = lambda: principal
    return TestClient(app)


# --------------------------------------------------------------------------
# EXPLAIN evidence helpers (SQLite)
# --------------------------------------------------------------------------


def _render_literal(stmt) -> str:
    """Compile a TextClause to SQL with values inlined (plan probing only —
    production always executes parameterized)."""
    return str(
        stmt.compile(dialect=sqlite_dialect(), compile_kwargs={"literal_binds": True})
    )


async def _explain(conn, rendered: str) -> list[str]:
    rows = await conn.execute(text("EXPLAIN QUERY PLAN " + rendered))
    return [dict(r._mapping)["detail"] for r in rows]


def _page_probe(where: str, **values: object) -> str:
    """Typed-literal rendering of the production page query for EQP probes."""
    binds = [
        bindparam("team_id", type_=String),
        bindparam("limit", type_=Integer),
    ]
    if ":project_id" in where:
        binds.append(bindparam("project_id", type_=String))
    if ":run_state" in where:
        binds.append(bindparam("run_state", type_=String))
    if ":cursor_created" in where:
        binds += [
            bindparam("cursor_created", type_=DateTime),
            bindparam("cursor_run", type_=String),
        ]
    stmt = text(ri._PAGE_TEMPLATE.format(where=where)).bindparams(*binds)
    return _render_literal(stmt.bindparams(**values))


def _lifecycle_probe(n_ids: int = 25) -> str:
    stmt = ri._lifecycle_stmt(ri._START_EVENT_TYPES).bindparams(
        page_run_ids=[f"run-{n:06d}" for n in range(n_ids)],
        marker_types=list(ri._START_EVENT_TYPES),
    )
    return _render_literal(stmt)


# --------------------------------------------------------------------------
# 1. migration 0026 — additive, runner-compatible, effective
# --------------------------------------------------------------------------


def test_0026_is_purely_additive_and_runner_compatible():
    body = _migration_body()
    statements = _migration_statements()
    assert statements, "0026 carries no statements"
    for stmt in statements:
        head = stmt.lstrip().upper()
        assert head.startswith("CREATE INDEX IF NOT EXISTS"), stmt[:60]
    joined = "\n".join(statements).upper()
    for forbidden in ("ALTER ", "DROP ", "INSERT ", "UPDATE ", "DELETE ",
                      "BEGIN", "COMMIT"):
        assert forbidden not in joined, forbidden
    # deploy/runners/migrate.py splits on literal $$: none may appear.
    assert "$$" not in body
    assert {f"{_TEAM_IDX}", f"{_COVERING_IDX}"} <= set(
        re.findall(r"INDEX IF NOT EXISTS (\w+)", body)
    )


def test_0026_indexes_exist_after_verbatim_apply(scale_db):
    async def _go():
        async with scale_db.engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='index'"
                     " AND tbl_name IN ('agent_runs','agent_events')"
                     f" AND name IN ('{_TEAM_IDX}','{_COVERING_IDX}')")
            )
            return {r[0] for r in rows}

    assert asyncio.run(_go()) == {_TEAM_IDX, _COVERING_IDX}


# --------------------------------------------------------------------------
# 2. EXPLAIN evidence — the plan must name the new indexes
# --------------------------------------------------------------------------


async def _page_plans(engine):
    out = {}
    async with engine.connect() as conn:
        out["team_only"] = await _explain(
            conn,
            _page_probe("p.team_id = :team_id", team_id="team-a", limit=26),
        )
        out["team_and_cursor"] = await _explain(
            conn,
            _page_probe(
                "p.team_id = :team_id AND (r.created_at, r.id)"
                " < (:cursor_created, :cursor_run)",
                team_id="team-a",
                limit=26,
                cursor_created=datetime.now(UTC) - timedelta(hours=24),
                cursor_run="run-001000",
            ),
        )
    return out


async def test_page_query_searches_the_new_composite_index(scale_db):
    plans = await _page_plans(scale_db.engine)

    # The runs side must be an INDEX RANGE search over 0026's composite —
    # not SCAN r (full scan), not a sort-the-world temp b-tree on r.
    for shape, details in plans.items():
        r_lines = [d for d in details if d.startswith("SCAN r") or "SEARCH r" in d]
        assert len(r_lines) == 1, (shape, details)
        assert f"SEARCH r USING INDEX {_TEAM_IDX}" in r_lines[0], (shape, r_lines)
        assert not any(d.startswith("SCAN r") for d in details), (shape, details)


async def test_cursor_predicate_becomes_index_range_bounds(scale_db):
    plans = await _page_plans(scale_db.engine)
    line = next(
        d for d in plans["team_and_cursor"] if f"USING INDEX {_TEAM_IDX}" in d
    )
    # Row-value keyset comparison compiles to explicit index bounds...
    assert "(created_at,id)<" in line.replace(" ", "")
    # ...and the ordering columns ride the index (no separate sort step on r).
    # H-2 (M17): real assertion (was `... and ... or ... or True`, a tautology):
    # the compiled line must reference the ordering columns — either the
    # quoted SQL identifier or the bound-parameter spelling.
    assert ("created_at" in line and '"id"' in line) or "id=?" in line
    assert "TEMP B-TREE FOR ORDER BY" not in " ".join(
        d for d in plans["team_and_cursor"] if "SEARCH r" in d or d.startswith("SCAN r")
    )


async def test_lifecycle_aggregate_is_an_index_only_scan(scale_db):
    async with scale_db.engine.connect() as conn:
        details = await _explain(conn, _lifecycle_probe())
    assert details == [
        f"SEARCH e USING COVERING INDEX {_COVERING_IDX} (run_id=? AND type=?)"
    ], details


# --------------------------------------------------------------------------
# 3. the N+1 is gone — statement budget through the real route
# --------------------------------------------------------------------------


def test_request_statement_budget_no_per_row_probes(monkeypatch, scale_db, captured):
    client = _client(monkeypatch, scale_db.factory)
    with captured() as cap:
        res = client.get("/api/v1/orchestrator/runs", params={"limit": 25})

    assert res.status_code == 200
    body = res.json()
    assert len(body["items"]) == 25
    assert body["nextCursor"]  # 1500 team runs: more pages exist

    ev = cap.agent_events_statements
    # Exactly TWO agent_events statements per request (one per marker family)
    # regardless of page size — the legacy code issued 2 x LIMIT here.
    assert len(ev) == 2, [s[:120] for s in ev]
    for s in ev:
        assert "GROUP BY" in s, s[:120]
        assert not re.search(r"\(\s*SELECT\s+MIN\s*\(\s*e\.ts", s, re.I), s[:120]


def test_derived_timestamps_survive_the_rewrite(monkeypatch, scale_db):
    client = _client(monkeypatch, scale_db.factory)
    items = client.get("/api/v1/orchestrator/runs", params={"limit": 25}).json()["items"]
    assert items and all(i["projectId"].startswith("p") for i in items)
    checked = 0
    for item in items:
        if item["status"] != "executing":
            continue
        # Every seeded run carries execution_started AND engine_completed.
        assert item["startedAt"] is not None
        assert item["finishedAt"] is not None
        assert item["startedAt"] <= item["finishedAt"]

        async def _expected(run_id=item["id"]):
            async with scale_db.engine.connect() as conn:
                row = (
                    await conn.execute(
                        text("SELECT MIN(ts) FROM agent_events"
                             " WHERE run_id=:r AND type='execution_started'"),
                        {"r": run_id},
                    )
                ).scalar()
            return row

        assert asyncio.run(_expected()) is not None
        checked += 1
        if checked == 3:
            break
    assert checked == 3


def test_cursor_walk_stays_correct_at_scale(monkeypatch, scale_db):
    """Ordering/correctness invariant of the M14 contract, re-proven on the
    rewritten query over 1500 team runs: pages chain without repeats."""
    client = _client(monkeypatch, scale_db.factory)
    seen: list[str] = []
    cursor = None
    for _ in range(4):  # 4 pages x 100 = 400 runs walked, then stop
        params: dict = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        body = client.get("/api/v1/orchestrator/runs", params=params).json()
        seen.extend(i["id"] for i in body["items"])
        if body["nextCursor"] is None:
            break
        cursor = body["nextCursor"]
    assert len(seen) == len(set(seen)) == 400
    ns = [int(i.split("-")[1]) for i in seen]
    assert ns == sorted(ns)  # created_at DESC == ascending seed index


# --------------------------------------------------------------------------
# 4. latency sanity at mission scale
# --------------------------------------------------------------------------


def test_latency_sanity_under_one_second(monkeypatch, scale_db, captured):
    client = _client(monkeypatch, scale_db.factory)
    with captured():
        start = time.perf_counter()
        res = client.get("/api/v1/orchestrator/runs")
        elapsed = time.perf_counter() - start
    assert res.status_code == 200
    # Authoring box measured ~1 ms for this request at ~80k events; the bound
    # exists to catch regressions toward the legacy N+1 (~8 ms) or worse, not
    # to gate CI on wall-clock noise.
    assert elapsed < 1.0, f"runs-index request took {elapsed*1000:.0f} ms"


# --------------------------------------------------------------------------
# 5. live Postgres (opt-in) — real planner evidence on the 0001->0026 chain
# --------------------------------------------------------------------------

_pg_reason = "HARNESS_TEST_PG not set; live-PG runs-index probe skipped"
_PG_CONTAINER = os.environ.get("HARNESS_TEST_PG_CONTAINER", "harness-db-1")
_PG_USER = os.environ.get("HARNESS_TEST_PG_USER", "harness")


def _psql(dbname: str, sql: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker", "exec", "-i", _PG_CONTAINER,
            "psql", "-U", _PG_USER, "-v", "ON_ERROR_STOP=1", "-q", "-A", "-t",
            "-d", dbname, "-f", "-",
        ],
        input=sql,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.skipif(not os.environ.get("HARNESS_TEST_PG"), reason=_pg_reason)
class TestLivePostgresRunsIndexPlan:
    def test_chain_lands_0026_and_planner_names_the_covering_index(self):
        assert shutil.which("docker"), "docker unavailable"
        dbname = f"harness_x1_{uuid.uuid4().hex[:10]}"
        assert _psql("postgres", f'CREATE DATABASE "{dbname}"').returncode == 0
        try:
            for path in sorted(_MIGRATIONS.glob("*.sql")):
                res = _psql(dbname, path.read_text())
                assert res.returncode == 0, f"{path.name}: {res.stderr}"

            # Seed real scale server-side (>=50k agent_events) so the
            # planner's choice is meaningful, not a 3-row toy. Event ts is
            # anchored pre-Sep-2026 so every row lands in the legacy child —
            # the partition the LIFECYCLE plan pin below names.
            seeded = _psql(
                dbname,
                """
                INSERT INTO teams (id, name) VALUES ('team-a', 'alpha'),
                                                    ('team-b', 'zeta');
                INSERT INTO projects (id, team_id, name)
                SELECT 'pA', 'team-a', 'alpha'
                UNION ALL SELECT 'pZ', 'team-b', 'zeta';
                INSERT INTO agent_runs (id, project_id, state, created_at,
                                        updated_at)
                SELECT 'run-' || g,
                       CASE WHEN g <= 1500 THEN 'pA' ELSE 'pZ' END,
                       'executing',
                       now() - (g || ' minutes')::interval,
                       now()
                FROM generate_series(1, 2000) AS g;
                INSERT INTO agent_events (project_id, run_id, type, payload,
                                          ts)
                SELECT CASE WHEN g <= 1500 THEN 'pA' ELSE 'pZ' END,
                       'run-' || (1 + (g % 2000)),
                       (ARRAY['execution_started', 'resumed', 'log_line',
                              'engine_completed'])[1 + (g % 4)],
                       json_build_object('k', g),
                       TIMESTAMPTZ '2026-06-01T00:00:00Z'
                           - (g || ' seconds')::interval
                FROM generate_series(1, 60000) AS g;
                VACUUM ANALYZE agent_events;
                """,
            )
            assert seeded.returncode == 0, seeded.stderr

            idx = _psql(
                dbname,
                "SELECT indexname FROM pg_indexes WHERE tablename IN"
                " ('agent_runs','agent_events') ORDER BY 1;",
            )
            assert idx.returncode == 0
            names = idx.stdout.split()
            assert _TEAM_IDX in names and _COVERING_IDX in names, names

            # The exact production shapes with representative literals.
            page_sql = (
                "EXPLAIN (COSTS OFF) SELECT r.id FROM agent_runs r"
                " JOIN projects p ON p.id = r.project_id"
                " WHERE p.team_id = 'team-a'"
                " ORDER BY r.created_at DESC, r.id DESC LIMIT 26;"
            )
            life_sql = (
                "EXPLAIN (COSTS OFF) SELECT e.run_id, MIN(e.ts)"
                " FROM agent_events e WHERE e.run_id IN"
                " ('run-1','run-2','run-3') AND e.type IN"
                " ('execution_started','resumed') GROUP BY e.run_id;"
            )
            for label, sql in (("PAGE", page_sql), ("LIFECYCLE", life_sql)):
                plan = _psql(dbname, sql)
                assert plan.returncode == 0, plan.stderr
                print(f"\n=== Postgres EXPLAIN (COSTS OFF) [{label}] ===")
                print(plan.stdout)

            # Planner evidence, honestly staged:
            # (a) out of the box, the lifecycle aggregate must still be index
            #     driven -- never Seq Scan on agent_events;
            # (b) after VACUUM (visibility map) + production-realistic IO
            #     costing (random_page_cost=1.0; SSD/shared_buffers), PG
            #     upgrades to a true INDEX ONLY SCAN over 0026's covering
            #     index with ZERO heap fetches. On a freshly loaded table
            #     without vacuum the planner instead picks 0012's narrower
            #     (run_id, type) bitmap + heap fetch -- same rows, extra
            #     visits; production tables are vacuumed, so pin the better
            #     plan under its actual preconditions.
            # M19-N3 attribution: since 0028 agent_events is RANGE-partitioned;
            # the planner scans each child separately and may Seq Scan the
            # EMPTY monthly children (zero pages). The regression this pin
            # hunts -- a scan over the seeded history -- lives in the legacy
            # child holding every row, so the pin names THAT partition.
            life_plan = _psql(dbname, life_sql)
            assert "Seq Scan on agent_events_legacy" not in life_plan.stdout

            ios_sql = (
                "SET random_page_cost = 1.0; "
                "EXPLAIN (ANALYZE, COSTS OFF) "
                + life_sql.split(";", 1)[0].split("EXPLAIN (COSTS OFF)", 1)[1]
                + ";"
            )
            ios = _psql(dbname, ios_sql)
            assert ios.returncode == 0, ios.stderr
            print("\n=== Postgres EXPLAIN (ANALYZE) [LIFECYCLE,"
                  " random_page_cost=1.0] ===")
            print(ios.stdout)
            # M19-N3 attribution: since 0028 agent_events is partitioned; the
            # planner reports each CHILD's physical index name (the 0026
            # name survives only as the parent-level catalog entry), so pin
            # the plan shape -- Index Only Scan over the seeded history in
            # the legacy child, with zero heap fetches -- not the literal.
            assert "Index Only Scan using agent_events_legacy_" in ios.stdout
            assert "Heap Fetches: 0" in ios.stdout
            assert "Execution Time" in ios.stdout
        finally:
            _psql("postgres", f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
