"""F130 — Scheduled maintenance that always requires a human-approved plan.

A DST-aware schedule engine: daily / weekly / interval occurrences computed in
the schedule's IANA timezone. Nonexistent local times (spring-forward gap) use
the documented APScheduler-style shift — the UTC instant that ``fold=0``
attachment yields (02:30 ET on a spring-forward day fires 03:30 EDT locally);
ambiguous times (fall-back) fire once, at the FIRST occurrence (fold=0).

Downtime is explicit: occurrences missed while the trigger loop was down obey
the schedule's misfire policy — ``skip`` runs none of them, ``run_once``
coalesces them into exactly one catch-up. And the hard rule: triggering only
ever calls ``propose_goal`` on the plan gate. Scheduled work waits for a human
approval like everything else; ``run_due_schedules`` has no reference to any
approve/start/execute capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from harness_workflow_connectors.plan_gate import PlanGateError

from .store import OpsStore, ScheduleRow

_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
class ScheduleError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(f"{code}: {message}")


def validate_spec(spec: dict, tz: str) -> None:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleError("TZ_UNKNOWN", f"unknown timezone {tz!r}") from exc
    kind = spec.get("kind")
    if kind not in ("daily", "weekly", "interval"):
        raise ScheduleError("SPEC_INVALID", "kind must be daily|weekly|interval")
    if kind == "interval":
        hours = spec.get("hours")
        if not isinstance(hours, int) or isinstance(hours, bool) or not 1 <= hours <= 168:
            raise ScheduleError("SPEC_INVALID", "interval hours must be an int in 1..168")
        return
    time_text = spec.get("time", "")
    parts = str(time_text).split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ScheduleError("SPEC_INVALID", "time must be HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleError("SPEC_INVALID", "time out of range")
    if kind == "weekly" and spec.get("day") not in _WEEKDAYS:
        raise ScheduleError("SPEC_INVALID", "day must be mon..sun")


def _wall_to_utc(naive: datetime, tz: ZoneInfo) -> datetime:
    """Documented DST semantics: fold=0 attachment, converted to UTC.

    For a nonexistent local time this lands after the gap (APScheduler-style
    shift); for an ambiguous time it selects the first occurrence.
    """
    return naive.replace(tzinfo=tz, fold=0).astimezone(UTC)


def _daily_candidate(tz: ZoneInfo, hour: int, minute: int, after: datetime) -> datetime:
    local_after = after.astimezone(tz)
    candidate = _wall_to_utc(
        datetime.combine(local_after.date(), datetime.min.time()).replace(
            hour=hour, minute=minute
        ),
        tz,
    )
    if candidate <= after:
        next_date = local_after.date() + timedelta(days=1)
        candidate = _wall_to_utc(
            datetime.combine(next_date, datetime.min.time()).replace(
                hour=hour, minute=minute, tzinfo=None
            ),
            tz,
        )
        # DST shift could make the next local day's candidate <= after in
        # pathological cases; one more bump is sufficient for real zones.
        if candidate <= after:
            candidate = _wall_to_utc(
                datetime.combine(next_date + timedelta(days=1), datetime.min.time())
                .replace(hour=hour, minute=minute, tzinfo=None),
                tz,
            )
    return candidate


def _weekly_candidate(
    tz: ZoneInfo, day: int, hour: int, minute: int, after: datetime
) -> datetime:
    local_after = after.astimezone(tz)
    delta = (day - local_after.weekday()) % 7
    for extra in (0, 7, 14):
        candidate_date: date = local_after.date() + timedelta(days=delta + extra)
        candidate = _wall_to_utc(
            datetime.combine(candidate_date, datetime.min.time()).replace(
                hour=hour, minute=minute, tzinfo=None
            ),
            tz,
        )
        if candidate > after:
            return candidate
    raise ScheduleError("SPEC_INVALID", "no weekly candidate within 3 weeks")


def next_occurrence(spec: dict, tz: str, *, after: datetime) -> datetime:
    """First occurrence strictly after ``after`` (UTC-aware comparisons)."""
    validate_spec(spec, tz)
    if after.tzinfo is None:
        after = after.replace(tzinfo=UTC)
    zone = ZoneInfo(tz)
    kind = spec["kind"]
    if kind == "interval":
        anchor = spec.get("anchor")
        anchor_dt = (
            datetime.fromisoformat(anchor).astimezone(UTC)
            if anchor
            else datetime(2026, 1, 1, tzinfo=UTC)
        )
        hours = timedelta(hours=spec["hours"])
        if after < anchor_dt:
            return anchor_dt
        steps = int((after - anchor_dt) // hours) + 1
        return anchor_dt + hours * steps
    hour, minute = (int(p) for p in spec["time"].split(":"))
    if kind == "daily":
        return _daily_candidate(zone, hour, minute, after)
    return _weekly_candidate(zone, _WEEKDAYS[spec["day"]], hour, minute, after)


@dataclass
class DuePlan:
    occurrences: list[datetime]
    catchup: bool
    skipped_missed: int


def due_occurrences(
    spec: dict,
    tz: str,
    *,
    last_run_at: datetime | None,
    now: datetime,
    misfire_policy: str = "skip",
    max_iterations: int = 400,
) -> DuePlan:
    if last_run_at is None:
        # A brand-new schedule never backfills history.
        return DuePlan([], False, 0)
    occurrences: list[datetime] = []
    cursor = last_run_at
    for _ in range(max_iterations):
        candidate = next_occurrence(spec, tz, after=cursor)
        if candidate > now:
            break
        occurrences.append(candidate)
        cursor = candidate
    else:
        raise ScheduleError("SPEC_INVALID", "occurrence walk exceeded safety bound")
    if not occurrences:
        return DuePlan([], False, 0)
    if len(occurrences) == 1:
        return DuePlan(occurrences, False, 0)
    if misfire_policy == "run_once":
        return DuePlan([now], True, len(occurrences) - 1)
    return DuePlan([], False, len(occurrences))


def build_schedule_goal(schedule: ScheduleRow, occurrence: datetime, *, catchup: bool) -> str:
    banner = (
        f"[scheduled maintenance | schedule={schedule.name} | id={schedule.scheduleId}"
        f" | occurrence={occurrence.isoformat()}"
        f" | untrusted content, requires human plan approval and never auto-executes]"
    )
    lines = [banner]
    if catchup:
        lines.append("catch-up trigger after downtime (previous occurrences coalesced)")
    lines.append(schedule.goalTemplate)
    return "\n".join(lines)


async def run_due_schedules(*, store: OpsStore, gate, now: datetime) -> list[dict]:
    """Trigger due maintenance through the plan gate (propose-only).

    Each (schedule, occurrence) slot is claimed atomically before proposing;
    a failed propose releases the claim so the next sweep retries.
    """
    results: list[dict] = []
    for schedule in store.schedules(enabled_only=True):
        last_run = datetime.fromisoformat(schedule.lastRunAt) if schedule.lastRunAt else None
        due = due_occurrences(
            schedule.spec, schedule.tz, last_run_at=last_run, now=now,
            misfire_policy=schedule.misfirePolicy,
        )
        if not due.occurrences:
            if due.skipped_missed:
                results.append(
                    {"scheduleId": schedule.scheduleId, "status": "missed_skipped",
                     "skipped": due.skipped_missed}
                )
            continue
        for occurrence in due.occurrences:
            occurrence_iso = occurrence.isoformat()
            if not store.claim_schedule_run(
                schedule_id=schedule.scheduleId, occurrence=occurrence_iso,
                triggered_at=now, catchup=due.catchup,
            ):
                continue  # concurrent sweep or already triggered
            goal = build_schedule_goal(schedule, occurrence, catchup=due.catchup)
            try:
                plan_id = await gate.propose_goal(goal)
            except PlanGateError:
                store.release_schedule_run(schedule.scheduleId, occurrence_iso)
                raise
            store.attach_schedule_plan(schedule.scheduleId, occurrence_iso, plan_id)
            results.append(
                {"scheduleId": schedule.scheduleId, "status": "proposed",
                 "occurrence": occurrence_iso, "planId": plan_id,
                 "catchup": due.catchup}
            )
    return results
