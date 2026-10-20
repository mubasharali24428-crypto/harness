"""F119 acceptance: the alert rules fire on real telemetry records, and the
engine dedupes (one page per condition per window), reminds, and resolves."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from harness_telemetry import RunRecord
from support import fixture_records

from harness_ops.alerts import AlertEngine, AlertThresholds, evaluate_alerts
from harness_ops.store import OpsStore

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def drafts_for(records, **kw):
    return evaluate_alerts(records, as_of=NOW, **kw)


def test_fixture_records_produce_expected_drafts():
    drafts = drafts_for(fixture_records())
    keys = sorted(d.dedupeKey for d in drafts)
    assert keys == [
        "blocked_run:run-a5",
        "claimed_but_rejected:run-a3",
        "expensive_run:run-a2",
        "expensive_run:run-b2",
    ]
    by_key = {d.dedupeKey: d for d in drafts}
    assert by_key["claimed_but_rejected:run-a3"].severity == "high"
    assert "claim/reality gap" in by_key["claimed_but_rejected:run-a3"].message


def test_stalled_fires_before_abandonment_window():
    idle_15h = RunRecord(
        runId="run-x", projectId="proj-a", teamId="team-a", goal="g",
        lastEventAt=NOW - timedelta(hours=15), costUsd=Decimal("1.00"),
    )
    idle_26h = RunRecord(
        runId="run-y", projectId="proj-a", teamId="team-a", goal="g",
        lastEventAt=NOW - timedelta(hours=26), costUsd=Decimal("1.00"),
    )
    rules = {(d.dedupeKey, d.rule) for d in drafts_for([idle_15h, idle_26h])}
    assert ("stalled_run:run-x", "stalled_run") in rules
    assert idle_26h.outcome(NOW) == "abandoned"
    assert not any(
        d.rule == "stalled_run" and d.subject["runId"] == "run-y"
        for d in drafts_for([idle_26h])
    )


def test_repeated_engine_failures_and_team_budget():
    flaky = RunRecord(
        runId="run-f", projectId="proj-x", teamId="team-x", goal="g",
        engineFailedCount=2, lastEventAt=NOW - timedelta(hours=1),
        costUsd=Decimal("2.00"),
    )
    big = RunRecord(
        runId="run-b", projectId="proj-x", teamId="team-x", goal="g",
        lastEventAt=NOW - timedelta(hours=1), costUsd=Decimal("25.00"),
    )
    drafts = drafts_for([flaky, big])
    rules = {d.rule for d in drafts}
    assert rules == {"repeated_engine_failure", "team_budget_exceeded", "expensive_run"}
    budget = next(d for d in drafts if d.rule == "team_budget_exceeded")
    assert budget.subject == {"teamId": "team-x"}


def test_thresholds_are_overridable():
    record = RunRecord(
        runId="run-x", projectId="proj-a", teamId="team-a", goal="g",
        lastEventAt=NOW - timedelta(hours=15), costUsd=Decimal("1.00"),
    )
    default = drafts_for([record])
    raised = drafts_for([record], thresholds=AlertThresholds(stall_hours=24.0))
    assert any(d.rule == "stalled_run" for d in default)
    assert not any(d.rule == "stalled_run" for d in raised)


def test_engine_dedupes_reminds_and_resolves(tmp_path):
    store = OpsStore(str(tmp_path / "ops.sqlite"))
    engine = AlertEngine(store)
    records = fixture_records()

    first = engine.sync(drafts_for(records), now=NOW)
    assert len(first["opened"]) == 4
    assert first["suppressed"] == 0
    assert all(n["event"] == "alert.opened" for n in first["opened"])

    second = engine.sync(drafts_for(records), now=NOW + timedelta(hours=1))
    assert second["opened"] == [] and second["reminders"] == []
    assert second["suppressed"] == 4
    row = store.active_alert("expensive_run:run-a2")
    assert row["count"] == 2

    later = engine.sync(drafts_for(records), now=NOW + timedelta(hours=7))
    assert len(later["reminders"]) == 4
    assert all(n["event"] == "alert.reminder" for n in later["reminders"])
    assert store.active_alert("expensive_run:run-a2")["count"] == 3

    healthy_records = [r for r in records if r.runId != "run-a3"]
    resolved = engine.sync(drafts_for(healthy_records), now=NOW + timedelta(hours=8))
    assert [n["rule"] for n in resolved["resolved"]] == ["claimed_but_rejected"]
    assert store.active_alert("claimed_but_rejected:run-a3") is None
    again = engine.sync(drafts_for(healthy_records), now=NOW + timedelta(hours=9))
    assert again["resolved"] == []


def test_every_open_has_stable_identity(tmp_path):
    store = OpsStore(str(tmp_path / "ops.sqlite"))
    engine = AlertEngine(store)
    records = fixture_records()
    first = engine.sync(drafts_for(records), now=NOW)
    ids = {n["alertId"] for n in first["opened"]}
    for round_no in (1, 2, 20):
        engine.sync(drafts_for(records), now=NOW + timedelta(hours=7 * round_no))
    rows = {row["alertId"] for row in store.alerts(status="active")}
    assert rows == ids
