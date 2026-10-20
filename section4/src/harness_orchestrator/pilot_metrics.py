"""P3.3 pilot metrics for one team, derived only from product tables.

    python -m harness_orchestrator.pilot_metrics TEAM_ID [--baseline FILE]
        [--since YYYY-MM-DD] [--until YYYY-MM-DD] (needs HARNESS_DATABASE_URL)

Date bounds select runs by creation time at UTC midnight: since inclusive, until
exclusive. Related events/audits for those runs are not independently time-filtered.
Omit both for all history. Accepted counts audit_log rows whose action is
review.approved and target_id belongs to a selected run; it counts DISTINCT runs that
are also finished, so the accept rate cannot exceed 1. It is still an audit-row proxy,
not independent confirmation that a human accepted the task.

``--baseline`` compares against the team's pre-pilot workflow, measured by people before
day 1 (template: docs/briefs/pilot-baseline-template.json). Below MIN_N tasks on either
side the comparison is reported as insufficient and no claim is made.

Counts and timings of what happened. It makes no productivity or ROI claim: comparing
against the team's current workflow needs a baseline measured separately.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from harness_orchestrator.runs_index import _FINISHED_EVENT_TYPES

NOTE = (
    "Counts and timings only. No baseline of the team's current workflow has been "
    "measured, so these numbers support no speed-up or ROI claim."
)


MIN_N = 5


def validate_baseline(baseline: Any, team_id: str | None = None) -> dict[str, Any]:
    """Validate the human-entered baseline before it can be compared with a team report."""
    if not isinstance(baseline, dict):
        raise ValueError("baseline must be a JSON object")
    extra = set(baseline) - {"teamId", "capturedFrom", "capturedTo", "method", "tasks"}
    if extra:
        raise ValueError(f"baseline has unsupported fields: {', '.join(sorted(extra))}")
    recorded_team = baseline.get("teamId")
    if not isinstance(recorded_team, str) or not recorded_team.strip():
        raise ValueError("baseline.teamId must be a non-empty string")
    if team_id is not None and recorded_team != team_id:
        raise ValueError("baseline.teamId must match the requested team id")

    dates: list[datetime] = []
    for name in ("capturedFrom", "capturedTo"):
        value = baseline.get(name)
        if not isinstance(value, str):
            raise ValueError(f"baseline.{name} must be a valid YYYY-MM-DD date")
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError(f"baseline.{name} must be a valid YYYY-MM-DD date") from exc
        if parsed.isoformat() != value:
            raise ValueError(f"baseline.{name} must be a valid YYYY-MM-DD date")
        dates.append(datetime.combine(parsed, datetime.min.time(), tzinfo=UTC))
    if dates[0] > dates[1]:
        raise ValueError("baseline.capturedFrom must be on or before capturedTo")

    method = baseline.get("method")
    if not isinstance(method, str) or not method.strip():
        raise ValueError("baseline.method must describe how the observations were measured")
    tasks = baseline.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("baseline.tasks must contain at least one task")

    seen: set[str] = set()
    for index, task in enumerate(tasks):
        prefix = f"baseline.tasks[{index}]"
        if not isinstance(task, dict):
            raise ValueError(f"{prefix} must be an object")
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError(f"{prefix}.id must be a non-empty string")
        if task_id in seen:
            raise ValueError(f"baseline task ids must be unique (duplicate {task_id!r})")
        seen.add(task_id)
        minutes = task.get("minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
            raise ValueError(f"{prefix}.minutes must be a finite non-negative number")
        try:
            finite = math.isfinite(minutes)
        except OverflowError:
            finite = False
        if not finite or minutes < 0:
            raise ValueError(f"{prefix}.minutes must be a finite non-negative number")
        task_extra = set(task) - {"id", "minutes", "accepted"}
        if task_extra:
            raise ValueError(f"{prefix} has unsupported fields: {', '.join(sorted(task_extra))}")
        if not isinstance(task.get("accepted"), bool):
            raise ValueError(f"{prefix}.accepted must be a boolean")
    return baseline


def compare(report: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Pilot vs hand-measured baseline. ``claimable`` is False unless both sides have MIN_N."""
    tasks = baseline.get("tasks") or []
    minutes = [float(t["minutes"]) for t in tasks if float(t.get("minutes", -1)) >= 0]
    base_acc = (sum(1 for t in tasks if t.get("accepted")) / len(tasks)) if tasks else None
    el = report["elapsedSeconds"]
    runs = report["runs"]
    pilot_min = el["median"] / 60 if el.get("n") else None
    pilot_acc = (runs["accepted"] / runs["finished"]) if runs["finished"] else None
    out: dict[str, Any] = {
        "baselineN": len(minutes), "pilotN": el.get("n", 0),
        "baselineMedianMinutes": statistics.median(minutes) if minutes else None,
        "pilotMedianMinutes": pilot_min,
        "baselineAcceptRate": base_acc, "pilotAcceptRate": pilot_acc,
        "claimable": len(minutes) >= MIN_N and el.get("n", 0) >= MIN_N,
    }
    if out["claimable"]:
        out["medianTimeChangePct"] = round(
            (pilot_min - out["baselineMedianMinutes"]) / out["baselineMedianMinutes"] * 100, 1
        ) if out["baselineMedianMinutes"] else None
        out["verdict"] = "descriptive only: different tasks, not a controlled comparison"
    else:
        out["verdict"] = f"insufficient data (need at least {MIN_N} tasks on each side); no claim"
    return out


def _ts(v: Any) -> datetime:
    d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _summary(values: list[float]) -> dict[str, float]:
    values = [v for v in values if v >= 0]  # negative = clock skew / bad data, not a duration
    if not values:
        return {"n": 0}
    return {"n": len(values), "median": statistics.median(values), "max": max(values)}


async def collect(
    factory: Any, team_id: str, since: datetime | None = None, until: datetime | None = None,
) -> dict[str, Any]:
    window = ""
    p: dict[str, Any] = {"t": team_id}
    if since is not None:
        window += " AND r.created_at >= :since"
        p["since"] = since
    if until is not None:
        window += " AND r.created_at < :until"
        p["until"] = until
    scope = (
        "SELECT r.id FROM agent_runs r JOIN projects p ON p.id = r.project_id"
        " WHERE p.team_id = :t" + window
    )
    async with factory() as db:
        runs = (await db.execute(text(
            "SELECT r.id, r.created_at,"
            " (SELECT MAX(e.ts) FROM agent_events e WHERE e.run_id = r.id"
            f" AND e.type IN {_FINISHED_EVENT_TYPES!r}) AS event_ts,"
            " (SELECT MAX(a.ts) FROM audit_log a WHERE a.target_id = r.id"
            " AND a.action = 'run.stopped') AS stop_ts"
            " FROM agent_runs r JOIN projects p ON p.id = r.project_id"
            " WHERE p.team_id = :t" + window), p)).all()
        redirects = (await db.execute(text(
            f"SELECT kind, count(*) AS n FROM run_collab_events WHERE kind LIKE 'redirect.%'"
            f" AND run_id IN ({scope}) GROUP BY kind"), p)).all()
        approvals = (await db.execute(text(
            f"SELECT state, created_at, decided_at FROM high_impact_approvals"
            f" WHERE run_id IN ({scope})"), p)).all()
        audit = (await db.execute(text(
            f"SELECT action, count(*) AS n FROM audit_log WHERE target_id IN ({scope})"
            f" GROUP BY action"), p)).all()
        approved = {r.target_id for r in (await db.execute(text(
            f"SELECT DISTINCT target_id FROM audit_log WHERE action = 'review.approved'"
            f" AND target_id IN ({scope})"), p)).all()}
    acts = {r.action: r.n for r in audit}
    # agent_runs.state is not maintained; finished = completion/failure/PR event or a stop.
    finished = {
        r.id: max(_ts(t) for t in (r.event_ts, r.stop_ts) if t)
        for r in runs
        if r.event_ts or r.stop_ts
    }
    return {
        "teamId": team_id,
        "runs": {
            "created": len(runs),
            "finished": len(finished),
            "accepted": len(approved & finished.keys()),
            "stopped": acts.get("run.stopped", 0),
        },
        "elapsedSeconds": _summary(
            [(finished[r.id] - _ts(r.created_at)).total_seconds() for r in runs if r.id in finished]
        ),
        "redirects": {r.kind: r.n for r in redirects},
        "approvals": {
            "byState": dict(Counter(a.state for a in approvals)),
            "decisionLatencySeconds": _summary(
                [(_ts(a.decided_at) - _ts(a.created_at)).total_seconds()
                 for a in approvals if a.decided_at is not None and a.state != "expired"]
            ),
        },
        "cancelConfirmed": acts.get("run.cancel_confirmed", 0),
        "note": NOTE,
    }


def main() -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    def utc_date(value: str) -> datetime:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
            if parsed.date().isoformat() != value:
                raise ValueError
            return parsed
        except ValueError as exc:
            raise argparse.ArgumentTypeError("expected a valid date in YYYY-MM-DD format") from exc

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("team_id")
    parser.add_argument("--baseline")
    parser.add_argument("--since", type=utc_date)
    parser.add_argument("--until", type=utc_date)
    args = parser.parse_args()
    if args.since is not None and args.until is not None and args.since > args.until:
        parser.error("--since must be on or before --until")
    baseline = None
    if args.baseline is not None:
        try:
            with open(args.baseline, encoding="utf-8") as fh:
                baseline = validate_baseline(json.load(fh), team_id=args.team_id)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            parser.error(f"invalid baseline: {exc}")

    async def run() -> dict[str, Any]:
        engine = create_async_engine(os.environ["HARNESS_DATABASE_URL"])
        try:
            return await collect(
                async_sessionmaker(engine, expire_on_commit=False), args.team_id,
                since=args.since, until=args.until,
            )
        finally:
            await engine.dispose()

    report = asyncio.run(run())
    if baseline is not None:
        report["baselineComparison"] = compare(report, baseline)
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
