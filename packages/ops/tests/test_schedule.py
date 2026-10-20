"""F130 acceptance: DST wall-time semantics are exact (spring-forward shift,
fall-back first occurrence), downtime misfires follow the explicit policy, and
scheduled work can ONLY become a proposed plan awaiting human approval."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from harness_workflow_connectors.plan_gate import PlanGateError, ProposePlanGate
from support import fake_orchestrator

from harness_ops.schedule import (
    ScheduleError,
    build_schedule_goal,
    due_occurrences,
    next_occurrence,
    run_due_schedules,
    validate_spec,
)
from harness_ops.store import OpsStore, ScheduleRow

NY = "America/New_York"


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


# ------------------------------------------------------------------- DST math


def test_spring_forward_nonexistent_time_shifts_like_apscheduler():
    spec = {"kind": "daily", "time": "02:30"}
    occurrence = next_occurrence(spec, NY, after=utc("2026-03-07T07:31:00"))
    assert occurrence == utc("2026-03-08T07:30:00")  # 03:30 EDT — the shifted wall time
    local = occurrence.astimezone(__import__("zoneinfo").ZoneInfo(NY))
    assert local.strftime("%H:%M") == "03:30"
    assert local.tzname() == "EDT"


def test_fall_back_ambiguous_time_takes_first_occurrence():
    spec = {"kind": "daily", "time": "01:30"}
    occurrence = next_occurrence(spec, NY, after=utc("2026-10-31T05:31:00"))
    assert occurrence == utc("2026-11-01T05:30:00")  # first pass (EDT)
    import zoneinfo

    local = occurrence.astimezone(zoneinfo.ZoneInfo(NY))
    assert local.fold == 0
    assert local.tzname() in ("EDT", "EST")


def test_normal_days_and_weekly():
    daily = next_occurrence({"kind": "daily", "time": "09:00"}, NY,
                            after=utc("2026-10-17T12:00:00"))
    assert daily == utc("2026-10-17T13:00:00")  # same day: 09:00 EDT is 13:00Z
    assert next_occurrence({"kind": "daily", "time": "09:00"}, NY,
                           after=utc("2026-10-17T13:01:00")) == utc("2026-10-18T13:00:00")
    weekly = next_occurrence({"kind": "weekly", "day": "mon", "time": "09:00"},
                             NY, after=utc("2026-10-17T12:00:00"))
    assert weekly == utc("2026-10-19T13:00:00")


def test_interval_anchor_grid():
    spec = {"kind": "interval", "hours": 6, "anchor": "2026-01-01T00:00:00+00:00"}
    assert next_occurrence(spec, "UTC", after=utc("2026-01-01T13:00:00")) == \
        utc("2026-01-01T18:00:00")


# ------------------------------------------------------------------ misfires


def test_downtime_skip_policy():
    spec = {"kind": "daily", "time": "02:30"}
    plan = due_occurrences(
        spec, NY, last_run_at=utc("2026-10-12T06:30:00"),
        now=utc("2026-10-15T12:00:00"), misfire_policy="skip",
    )
    assert plan.occurrences == []
    assert plan.skipped_missed == 3


def test_downtime_run_once_policy_coalesces_catchup():
    spec = {"kind": "daily", "time": "02:30"}
    plan = due_occurrences(
        spec, NY, last_run_at=utc("2026-10-12T06:30:00"),
        now=utc("2026-10-15T12:00:00"), misfire_policy="run_once",
    )
    assert plan.occurrences == [utc("2026-10-15T12:00:00")]
    assert plan.catchup is True
    assert plan.skipped_missed == 2


def test_single_due_occurrence_is_not_catchup():
    spec = {"kind": "daily", "time": "02:30"}
    plan = due_occurrences(spec, NY, last_run_at=utc("2026-10-14T06:30:00"),
                           now=utc("2026-10-15T06:45:00"))
    assert plan.occurrences == [utc("2026-10-15T06:30:00")]
    assert plan.catchup is False


def test_brand_new_schedule_never_backfills():
    plan = due_occurrences({"kind": "daily", "time": "02:30"}, NY, last_run_at=None,
                           now=utc("2026-10-15T12:00:00"))
    assert plan.occurrences == []


# -------------------------------------------------------------- spec hygiene


def test_validate_spec_rejections():
    with pytest.raises(ScheduleError) as exc:
        validate_spec({"kind": "daily", "time": "09:00"}, "Mars/Olympus")
    assert exc.value.code == "TZ_UNKNOWN"
    for bad in (
        {"kind": "hourly", "time": "09:00"},
        {"kind": "daily", "time": "25:00"},
        {"kind": "daily", "time": "0900"},
        {"kind": "weekly", "day": "monday", "time": "09:00"},
        {"kind": "interval", "hours": 0},
        {"kind": "interval", "hours": 200},
    ):
        with pytest.raises(ScheduleError):
            validate_spec(bad, "UTC")


# ------------------------------------------------------------ store + engine


def insert_schedule(store, schedule_id="sch-1", *, last_run_at, misfire="skip"):
    store.insert_schedule(ScheduleRow(
        scheduleId=schedule_id, name="Nightly maintenance", teamId="team-a",
        spec={"kind": "daily", "time": "02:30"}, tz=NY,
        goalTemplate="Run nightly maintenance checks",
        misfirePolicy=misfire, enabled=True, createdBy="admin",
        createdAt=utc("2026-01-01T00:00:00").isoformat(), lastRunAt=last_run_at,
    ))


def test_claim_is_atomic_and_releasable(tmp_path):
    store = OpsStore(str(tmp_path / "ops.sqlite"))
    insert_schedule(store, last_run_at=utc("2026-10-14T06:30:00").isoformat())
    occurrence = "2026-10-15T06:30:00+00:00"
    before = store.schedule("sch-1").lastRunAt
    assert store.claim_schedule_run(schedule_id="sch-1", occurrence=occurrence,
                                    triggered_at=utc("2026-10-15T12:00:00"),
                                    catchup=False) is True
    assert store.claim_schedule_run(schedule_id="sch-1", occurrence=occurrence,
                                    triggered_at=utc("2026-10-15T12:01:00"),
                                    catchup=False) is False
    assert store.schedule("sch-1").lastRunAt == before  # claim is not progress
    store.attach_schedule_plan("sch-1", occurrence, "plan-77")
    runs = store.schedule_runs("sch-1")
    assert runs == [{"occurrence": occurrence,
                     "triggeredAt": "2026-10-15T12:00:00+00:00",
                     "planId": "plan-77", "catchup": False}]
    assert store.schedule("sch-1").lastRunAt == occurrence

    other = "2026-10-16T06:30:00+00:00"
    assert store.claim_schedule_run(schedule_id="sch-1", occurrence=other,
                                    triggered_at=utc("2026-10-16T12:00:00"),
                                    catchup=False) is True
    store.release_schedule_run("sch-1", other)
    assert [r["occurrence"] for r in store.schedule_runs("sch-1")] == [occurrence]
    assert store.schedule("sch-1").lastRunAt == occurrence

    # lease takeover: a crashed trigger's stale unfilled claim is re-claimable
    assert store.claim_schedule_run(schedule_id="sch-1", occurrence=other,
                                    triggered_at=utc("2026-10-16T12:00:00"),
                                    catchup=False) is True
    assert store.claim_schedule_run(schedule_id="sch-1", occurrence=other,
                                    triggered_at=utc("2026-10-16T12:03:00"),
                                    catchup=False) is False
    assert store.claim_schedule_run(schedule_id="sch-1", occurrence=other,
                                    triggered_at=utc("2026-10-16T12:06:00"),
                                    catchup=False) is True


class RecordingGate:
    def __init__(self):
        self.goals: list[str] = []
        self.fail_next = False

    async def propose_goal(self, goal: str) -> str:
        if self.fail_next:
            self.fail_next = False
            from harness_workflow_connectors.plan_gate import PlanGateError

            raise PlanGateError("PLAN_GATE_UNAVAILABLE", "simulated", 502)
        self.goals.append(goal)
        return f"plan-{len(self.goals)}"


async def test_run_due_proposes_only_and_is_idempotent(tmp_path):
    store = OpsStore(str(tmp_path / "ops.sqlite"))
    insert_schedule(store, last_run_at=utc("2026-10-14T06:30:00").isoformat())
    gate = RecordingGate()
    now = utc("2026-10-15T12:00:00")

    first = await run_due_schedules(store=store, gate=gate, now=now)
    assert first == [{"scheduleId": "sch-1", "status": "proposed",
                      "occurrence": "2026-10-15T06:30:00+00:00",
                      "planId": "plan-1", "catchup": False}]
    assert len(gate.goals) == 1
    assert "requires human plan approval" in gate.goals[0]
    assert "never auto-executes" in gate.goals[0]

    second = await run_due_schedules(store=store, gate=gate, now=now)
    assert second == []
    assert len(gate.goals) == 1


async def test_gate_failure_releases_claim_for_retry(tmp_path):
    store = OpsStore(str(tmp_path / "ops.sqlite"))
    insert_schedule(store, last_run_at=utc("2026-10-14T06:30:00").isoformat())
    gate = RecordingGate()
    gate.fail_next = True
    now = utc("2026-10-15T12:00:00")
    with pytest.raises(PlanGateError):
        await run_due_schedules(store=store, gate=gate, now=now)
    assert store.schedule_runs("sch-1") == []
    retry = await run_due_schedules(store=store, gate=gate, now=now)
    assert retry[0]["status"] == "proposed"


async def test_disabled_schedules_are_skipped(tmp_path):
    store = OpsStore(str(tmp_path / "ops.sqlite"))
    insert_schedule(store, last_run_at=utc("2026-10-14T06:30:00").isoformat())
    store.set_schedule_enabled("sch-1", False)
    gate = RecordingGate()
    result = await run_due_schedules(store=store, gate=gate,
                                     now=utc("2026-10-15T12:00:00"))
    assert result == [] and gate.goals == []


async def test_real_gate_proposes_and_never_approves(tmp_path):
    async with fake_orchestrator() as (base, state):
        store = OpsStore(str(tmp_path / "ops.sqlite"))
        insert_schedule(store, last_run_at=utc("2026-10-14T06:30:00").isoformat())
        gate = ProposePlanGate(base, bearer_token="ops-token")
        result = await run_due_schedules(store=store, gate=gate,
                                         now=utc("2026-10-15T12:00:00"))
        assert result[0]["planId"] == "plan-1"
        assert "Run nightly maintenance checks" in state["plans"]["plan-1"]["goal"]
        assert state["approve_calls"] == []  # propose-only, enforced
        await gate.aclose()


def test_goal_banner_names_schedule_and_occurrence():
    row = ScheduleRow(
        scheduleId="sch-9", name="Rotate credentials", teamId="team-a",
        spec={"kind": "weekly", "day": "sun", "time": "03:00"}, tz="UTC",
        goalTemplate="Rotate service credentials", misfirePolicy="skip",
        enabled=True, createdBy="admin", createdAt="2026-01-01T00:00:00+00:00",
        lastRunAt=None,
    )
    goal = build_schedule_goal(row, utc("2026-10-18T03:00:00"), catchup=True)
    assert "[scheduled maintenance" in goal
    assert "id=sch-9" in goal
    assert "occurrence=2026-10-18T03:00:00+00:00" in goal
    assert "catch-up trigger after downtime" in goal
    assert goal.endswith("Rotate service credentials")


def test_asyncio_gather_claim_race_is_single_winner(tmp_path):
    store = OpsStore(str(tmp_path / "ops.sqlite"))
    insert_schedule(store, last_run_at=utc("2026-10-14T06:30:00").isoformat())
    occurrence = "2026-10-15T06:30:00+00:00"

    async def claim():
        return store.claim_schedule_run(
            schedule_id="sch-1", occurrence=occurrence,
            triggered_at=utc("2026-10-15T12:00:00"), catchup=False,
        )

    async def run():
        return await asyncio.gather(*(claim() for _ in range(20)))

    winners = asyncio.run(run())
    assert sum(winners) == 1
