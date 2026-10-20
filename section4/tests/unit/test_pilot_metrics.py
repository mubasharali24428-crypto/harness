"""P3.3: pilot metrics derived only from product tables (no traction claims)."""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import pilot_metrics
from tests.unit.test_collab_chaos_live_pg import factory as pg_factory  # noqa: F401

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

DDL = (
    "CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36))",
    "CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36),"
    " state VARCHAR(40), created_at TIMESTAMP, updated_at TIMESTAMP)",
    "CREATE TABLE run_collab_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR(36),"
    " kind VARCHAR(40), ts TIMESTAMP)",
    "CREATE TABLE high_impact_approvals (id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36),"
    " state VARCHAR(16), created_at TIMESTAMP, decided_at TIMESTAMP)",
    "CREATE TABLE audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, action VARCHAR(80),"
    " target_id VARCHAR(80), ts TIMESTAMP)",
    "CREATE TABLE agent_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR(36),"
    " type VARCHAR(60), ts TIMESTAMP)",
)


@pytest.fixture()
def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/m.db")

    async def seed():
        async with engine.begin() as c:
            for d in DDL:
                await c.execute(text(d))
            for sql, p in [
                ("INSERT INTO projects VALUES ('pa','A'), ('pb','B')", {}),
                ("INSERT INTO agent_runs VALUES ('r1','pa','idle',:a,:a)", {"a": T0}),
                ("INSERT INTO agent_runs VALUES ('r2','pa','idle',:a,:a)", {"a": T0}),
                ("INSERT INTO agent_runs VALUES ('r3','pa','idle',:a,:a)", {"a": T0}),
                ("INSERT INTO agent_runs VALUES ('rx','pb','idle',:a,:a)", {"a": T0}),
                ("INSERT INTO agent_events (run_id,type,ts) VALUES"
                 " ('r1','engine_completed',:b), ('rx','engine_completed',:c)",
                 {"b": T0 + timedelta(minutes=30), "c": T0 + timedelta(hours=9)}),
                ("INSERT INTO run_collab_events (run_id,kind,ts) VALUES"
                 " ('r1','redirect.guidance',:a), ('r1','redirect.guidance',:a),"
                 " ('r1','redirect.scope_change',:a), ('r1','guidance.delivered',:a),"
                 " ('rx','redirect.guidance',:a)", {"a": T0}),
                ("INSERT INTO high_impact_approvals VALUES ('a1','r1','completed',:a,:b),"
                 " ('a2','r1','rejected',:a,:c), ('a3','r2','pending',:a,NULL),"
                 " ('ax','rx','completed',:a,:a)",
                 {"a": T0, "b": T0 + timedelta(seconds=60), "c": T0 + timedelta(seconds=180)}),
                ("INSERT INTO audit_log (action,target_id,ts) VALUES"
                 " ('run.stopped','r3',:s), ('run.cancel_confirmed','r3',:s),"
                 " ('review.approved','r1',:s), ('run.stopped','rx',:s)",
                 {"s": T0 + timedelta(minutes=10)}),
            ]:
                await c.execute(text(sql), p)

    asyncio.run(seed())
    yield async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(engine.dispose())


def test_metrics_are_team_scoped_and_computed_from_tables(factory):
    m = asyncio.run(pilot_metrics.collect(factory, "A"))
    # r1 finished by event (30 min), r3 finished by a stop (10 min), r2 still open.
    assert m["runs"] == {"created": 3, "finished": 2, "accepted": 1, "stopped": 1}
    assert m["elapsedSeconds"] == {"n": 2, "median": 1200.0, "max": 1800.0}
    assert m["redirects"] == {"redirect.guidance": 2, "redirect.scope_change": 1}
    assert m["approvals"]["byState"] == {"completed": 1, "rejected": 1, "pending": 1}
    assert m["approvals"]["decisionLatencySeconds"] == {"n": 2, "median": 120.0, "max": 180.0}
    assert m["cancelConfirmed"] == 1
    assert "baseline" in m["note"].lower()  # no ROI claims without a measured baseline


def test_summary_drops_negative_durations():
    from harness_orchestrator.pilot_metrics import _summary

    assert _summary([-5.0, 2.0, 4.0]) == {"n": 2, "median": 3.0, "max": 4.0}
    assert _summary([-1.0]) == {"n": 0}


def _rep(n, med=600.0, accepted=3, finished=4):
    return {"elapsedSeconds": {"n": n, "median": med, "max": med},
            "runs": {"accepted": accepted, "finished": finished}}


def test_compare_refuses_to_claim_without_enough_baseline():
    from harness_orchestrator.pilot_metrics import compare

    few = {"tasks": [{"minutes": 30, "accepted": True}] * 2}
    out = compare(_rep(10), few)
    assert out["claimable"] is False and "insufficient" in out["verdict"]
    assert "medianTimeChangePct" not in out
    assert compare(_rep(2), {"tasks": [{"minutes": 30}] * 9})["claimable"] is False


def test_compare_reports_change_when_both_sides_have_enough():
    from harness_orchestrator.pilot_metrics import compare

    base = {"tasks": [{"minutes": 20, "accepted": True}] * 4 + [{"minutes": 20, "accepted": False}]}
    out = compare(_rep(6, med=600.0), base)  # pilot 10 min vs baseline 20 min
    assert out["claimable"] and out["medianTimeChangePct"] == -50.0
    assert out["baselineAcceptRate"] == 0.8 and out["pilotAcceptRate"] == 0.75
    assert "not a controlled" in out["verdict"]
    assert compare(_rep(0, accepted=0, finished=0), {"tasks": []})["pilotAcceptRate"] is None


def _baseline_document(**changes):
    baseline = {
        "teamId": "A",
        "capturedFrom": "2026-09-01",
        "capturedTo": "2026-09-30",
        "method": "Consecutive comparable maintenance tasks; reviewer timed through acceptance.",
        "tasks": [
            {"id": "task-1", "minutes": 25.5, "accepted": True},
            {"id": "task-2", "minutes": 40, "accepted": False},
        ],
    }
    baseline.update(changes)
    return baseline


def test_baseline_validation_accepts_template_shape_and_matching_team():
    assert pilot_metrics.validate_baseline(_baseline_document(), team_id="A")["teamId"] == "A"


@pytest.mark.parametrize(("changes", "message"), [
    ({"teamId": "B"}, "match the requested team"),
    ({"capturedFrom": "2026-02-30"}, "valid YYYY-MM-DD"),
    ({"capturedFrom": "2026-10-01", "capturedTo": "2026-09-30"}, "on or before"),
    ({"method": " "}, "method must describe"),
    ({"secret": "do-not-keep"}, "unsupported fields"),
    ({"tasks": []}, "at least one task"),
    ({"tasks": [{"id": "x", "minutes": -1, "accepted": True}]}, "finite non-negative"),
    ({"tasks": [{"id": "x", "minutes": float("nan"), "accepted": True}]}, "finite non-negative"),
    ({"tasks": [{"id": "x", "minutes": 10**1000, "accepted": True}]}, "finite non-negative"),
    ({"tasks": [{"id": "x", "minutes": True, "accepted": True}]}, "finite non-negative"),
    ({"tasks": [{"id": "x", "minutes": 2, "accepted": 1}]}, "accepted must be a boolean"),
    ({"tasks": [
        {"id": "same", "minutes": 2, "accepted": True},
        {"id": "same", "minutes": 3, "accepted": False},
    ]}, "must be unique"),
])
def test_baseline_validation_rejects_invalid_or_mismatched_data(changes, message):
    with pytest.raises(ValueError, match=message):
        pilot_metrics.validate_baseline(_baseline_document(**changes), team_id="A")


def test_no_window_preserves_report(factory):
    assert asyncio.run(pilot_metrics.collect(factory, 'A')) == asyncio.run(
        pilot_metrics.collect(factory, 'A', since=None, until=None)
    )


@pytest.mark.parametrize('since,until,created', [
    (T0, None, 3), (None, T0, 0), (T0, T0, 0),
    (T0 + timedelta(days=1), None, 0),
    (None, T0 + timedelta(days=1), 3),
])
def test_window_boundaries(factory, since, until, created):
    m = asyncio.run(pilot_metrics.collect(factory, 'A', since, until))
    assert m['runs']['created'] == created
    if not created:
        assert m['runs'] == dict(created=0, finished=0, accepted=0, stopped=0)
        assert m['redirects'] == {} and m['approvals']['byState'] == {}
        assert m['elapsedSeconds'] == {'n': 0} and m['cancelConfirmed'] == 0


def test_window_excludes_older_runs_but_keeps_later_activity(factory):
    async def go():
        async with factory() as db:
            await db.execute(text("UPDATE agent_runs SET created_at = :t WHERE id = 'r1'"),
                             {'t': T0 - timedelta(days=10)})
            await db.execute(text("UPDATE audit_log SET ts = :t WHERE target_id = 'r3'"),
                             {'t': T0 + timedelta(days=10)})
            await db.commit()
        return await pilot_metrics.collect(factory, 'A', T0, T0 + timedelta(days=1))
    m = asyncio.run(go())
    assert m['runs'] == dict(created=2, finished=1, accepted=0, stopped=1)
    assert m['redirects'] == {} and m['approvals']['byState'] == {'pending': 1}
    assert m['cancelConfirmed'] == 1  # activity is not filtered independently


@pytest.mark.parametrize('args', [
    ['--since', '2026-02-30'], ['--until', 'bad'], ['--since', '20261001'],
    ['--until', '2026-1-1'], ['--since', '2026-10-02', '--until', '2026-10-01'],
])
def test_invalid_window_is_clear_cli_error(monkeypatch, capsys, args):
    monkeypatch.setattr('sys.argv', ['pilot_metrics', 'A', *args])
    monkeypatch.delenv('HARNESS_DATABASE_URL', raising=False)
    with pytest.raises(SystemExit) as exc:
        pilot_metrics.main()
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert 'YYYY-MM-DD' in error or '--since must be on or before --until' in error


def test_cli_window_and_baseline_preserved(factory, monkeypatch, tmp_path, capsys):
    import json

    baseline = _baseline_document(
        tasks=[{"id": f"task-{i}", "minutes": 20, "accepted": True} for i in range(5)]
    )
    path = tmp_path / 'baseline.json'
    path.write_text(json.dumps(baseline))
    seen = []
    real_collect = pilot_metrics.collect

    async def collect(_factory, team, since, until):
        seen.append((since, until))
        return await real_collect(factory, team, since, until)

    monkeypatch.setattr(pilot_metrics, 'collect', collect)
    monkeypatch.setenv('HARNESS_DATABASE_URL', 'sqlite+aiosqlite:///:memory:')
    for flags in ([], ['--since', '2026-10-01', '--until', '2026-10-02']):
        monkeypatch.setattr('sys.argv', ['pilot_metrics', 'A', '--baseline', str(path), *flags])
        assert pilot_metrics.main() == 0
        output = json.loads(capsys.readouterr().out)
        expected = asyncio.run(real_collect(factory, 'A'))
        expected['baselineComparison'] = pilot_metrics.compare(expected, baseline)
        assert output == expected
    assert seen == [(None, None), (T0.replace(hour=0), (T0 + timedelta(days=1)).replace(hour=0))]


def test_accepted_counts_distinct_finished_runs_only(factory):
    async def go():
        async with factory() as db:
            # duplicate approval rows for finished r1, and one for still-open r2
            await db.execute(text(
                "INSERT INTO audit_log (action,target_id,ts) VALUES"
                " ('review.approved','r1',:s), ('review.approved','r1',:s),"
                " ('review.approved','r2',:s)"), {"s": T0 + timedelta(minutes=11)})
            await db.commit()
        return await pilot_metrics.collect(factory, "A")

    m = asyncio.run(go())
    assert m["runs"]["finished"] == 2 and m["runs"]["accepted"] == 1
    assert m["runs"]["accepted"] / m["runs"]["finished"] <= 1


@pytest.mark.skipif(not os.environ.get("HARNESS_TEST_PG"), reason="HARNESS_TEST_PG not set")
async def test_window_and_distinct_accepted_on_real_postgres(pg_factory):  # noqa: F811
    """Same semantics on real timestamptz/jsonb: bound params, window, distinct accepted."""
    day = datetime(2026, 10, 10, tzinfo=UTC)
    async with pg_factory() as db:
        for rid, created in (("old", day - timedelta(days=5)), ("in1", day), ("in2", day)):
            await db.execute(text(
                "INSERT INTO agent_runs (id, project_id, state, created_at)"
                " VALUES (:i, 'p', 'idle', :c)"), {"i": rid, "c": created})
        await db.execute(text(
            "INSERT INTO agent_events (project_id, run_id, type, payload, ts) VALUES"
            " ('p','old','engine_completed','{}',:a), ('p','in1','engine_completed','{}',:b)"),
            {"a": day - timedelta(days=5, hours=-1), "b": day + timedelta(minutes=30)})
        for rid in ("old", "in1", "in1", "in2"):
            await db.execute(text(
                "INSERT INTO audit_log (actor_kind, action, target_type, target_id, detail, ts)"
                " VALUES ('human','review.approved','agent_run',:r,'{}',:ts)"),
                {"r": rid, "ts": day + timedelta(hours=1)})
        await db.commit()
    all_ = await pilot_metrics.collect(pg_factory, "t1")
    assert all_["runs"]["created"] == 4  # old, in1, in2 + the fixture's r1
    assert all_["runs"]["finished"] == 2 and all_["runs"]["accepted"] == 2
    win = await pilot_metrics.collect(
        pg_factory, "t1", since=day, until=day + timedelta(days=1))
    assert win["runs"] == {"created": 2, "finished": 1, "accepted": 1, "stopped": 0}
    assert win["elapsedSeconds"]["n"] == 1
