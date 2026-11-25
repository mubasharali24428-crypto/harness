"""Sprint 5 Task 37 (I-08 UI): GET /runs/{runId}/learned-rule — the stop's lesson, read-only.

The route only reads case_memory (migration 0080). It never writes a project rule (CD-02):
the response says ``status: "proposed"`` and the test asserts no rule table is touched.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from harness_orchestrator import collab
from harness_orchestrator.case_memory import record_case
from tests.unit.test_collab_backend import P, q
from tests.unit.test_collab_backend import env as env  # noqa: F401

NOW = datetime(2026, 11, 25, 3, tzinfo=UTC)
CASE_DDL = (
    "CREATE TABLE case_memory (case_id VARCHAR(36) PRIMARY KEY, team_id TEXT NOT NULL,"
    " project_id TEXT, run_id TEXT NOT NULL, failure_class TEXT, tool TEXT, paths TEXT,"
    " fingerprint TEXT, outcome TEXT, weight REAL, suggested_rule TEXT,"
    " status TEXT CHECK (status = 'proposed'), recorded_at TIMESTAMP)"
)


def run(f, coro_fn):
    async def go():
        async with f() as db:
            await coro_fn(db)
            await db.commit()

    asyncio.run(go())


def add_case(f, team, run_id, when=NOW, paths=("deploy/.env",), tool="bash"):
    c = record_case(run_id, "denials_repeated", tool, list(paths), "denied", when)

    async def ins(db):
        await db.execute(text(
            "INSERT INTO case_memory (case_id, team_id, project_id, run_id, failure_class,"
            " tool, paths, fingerprint, outcome, weight, suggested_rule, status, recorded_at)"
            " VALUES (:c, :t, 'p', :r, :fc, :tool, :paths, :fp, :o, :w, :s, 'proposed', :at)"),
            {"c": c["caseId"], "t": team, "r": run_id, "fc": c["failureClass"], "tool": tool,
             "paths": json.dumps(c["paths"]), "fp": c["fingerprint"], "o": c["outcome"],
             "w": c["weight"], "s": c["suggestedRule"], "at": when})

    run(f, ins)
    return c


@pytest.fixture()
def cases(env):
    as_, f = env

    async def ddl(db):
        await db.execute(text(CASE_DDL))

    run(f, ddl)
    return as_, f


def test_a_denial_stop_offers_its_rule_read_only(cases):
    as_, f = cases
    mine = add_case(f, "team-a", "r1")
    add_case(f, "team-a", "r-old", NOW - timedelta(days=1))  # similar earlier case, same team
    add_case(f, "team-b", "r-foreign", NOW)  # same path, other team: never shown
    before = q(f, "SELECT count(*) AS n FROM case_memory")[0].n
    body = as_("view").get(f"{P}/r1/learned-rule").json()  # any reader of the run
    offer = body["offer"]
    assert offer["status"] == "proposed" and offer["failureClass"] == "denials_repeated"
    assert offer["paths"] == ["deploy/.env"]
    assert offer["suggestedRule"] == mine["suggestedRule"]
    assert offer["suggestedRule"].startswith("LEARNED FROM DENIALS (run r1)")
    assert [c["runId"] for c in body["similarCases"]] == ["r-old"]
    assert "r-foreign" not in json.dumps(body)
    assert q(f, "SELECT count(*) AS n FROM case_memory")[0].n == before  # read-only


def test_a_run_without_a_denial_stop_has_no_offer(cases):
    as_, _ = cases
    assert as_("lead").get(f"{P}/r1/learned-rule").json() == {"offer": None, "similarCases": []}


def test_foreign_team_gets_the_same_404_as_any_run_read(cases):
    as_, f = cases
    add_case(f, "team-a", "r1")
    r = as_("evil").get(f"{P}/r1/learned-rule")
    assert r.status_code == 404 and r.json()["detail"]["error"]["code"] == "NOT_FOUND"


def test_case_rows_of_another_team_never_answer_for_this_run(cases):
    _, f = cases
    add_case(f, "team-b", "r1")  # a mis-attributed row: team filter applies on top of authz
    assert asyncio.run(collab.learned_rule_for_run(f, "r1", "team-a")) == collab.NO_LEARNED_RULE


def test_pre_0080_schema_reads_as_no_lesson(env):
    _, f = env  # the shared DDL has no case_memory table
    assert asyncio.run(collab.learned_rule_for_run(f, "r1", "team-a")) == collab.NO_LEARNED_RULE
