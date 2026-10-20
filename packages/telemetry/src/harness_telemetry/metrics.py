"""Measurable value metrics with transparent denominators.

The single non-negotiable distinction (S4 assignment): a *measured accepted
change* is an observed `pr_opened` event **and** a last independent-review
verdict of passed=True. A model claiming completion (`engine_completed`) is a
different number, reported separately, never folded into acceptance.

Every formula is declared in ``DEFINITIONS`` and rendered verbatim by the
dashboard — no metric may exist without its denominator being public.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .events import CostEntry, RunEvent, scrub_text

STALL_WINDOW = timedelta(hours=24)

DEFINITIONS: dict[str, str] = {
    "planned": "runs with a plan_proposed event",
    "started": "runs with at least one execution_started event",
    "accepted": "runs with a pr_opened event AND last review verdict passed=True",
    "rework_rate": "runs with >1 execution_started OR any review_failed / divided by started",
    "acceptance_rate_of_started": "accepted / started",
    "abandonment_rate": "runs abandoned (planned, never started, older than 24h; or stalled >24h "
    "without verdict/PR/block) / planned",
    "claimed_completions": "runs with an engine_completed event (model-claimed, NOT acceptance)",
    "claimed_but_not_accepted": "runs that claimed completion but never produced an accepted change "
    "(no PR opened, or last review verdict failed)",
    "cost_per_task": "sum of all run costs / planned runs",
    "cost_per_claimed_completion": "sum of costs of engine_completed runs / claimed completions",
    "cost_per_accepted_change": "sum of costs of accepted runs / accepted runs",
    "time_to_first_useful_change": "pr_opened.ts - plan_proposed.ts for accepted runs only",
    "approval_latency": "plan_approved.ts - plan_proposed.ts for approved runs",
}


@dataclass
class RunRecord:
    runId: str
    projectId: str
    teamId: str | None
    goal: str
    planProposedAt: datetime | None = None
    approvedAt: datetime | None = None
    firstExecutionAt: datetime | None = None
    attempts: int = 0
    engineCompletedAt: datetime | None = None
    engineFailedCount: int = 0
    reviewVerdicts: list[tuple[datetime, bool]] = field(default_factory=list)
    prOpenedAt: datetime | None = None
    prId: str | None = None
    blockedAt: datetime | None = None
    doneSignalAt: datetime | None = None
    lastEventAt: datetime | None = None
    costUsd: Decimal = Decimal(0)

    @property
    def accepted(self) -> bool:
        return self.prOpenedAt is not None and bool(self.reviewVerdicts) and self.reviewVerdicts[-1][1]

    @property
    def claimedCompletion(self) -> bool:
        return self.engineCompletedAt is not None

    @property
    def reworked(self) -> bool:
        return self.attempts > 1 or any(not ok for _, ok in self.reviewVerdicts)

    def outcome(self, as_of: datetime, stall_window: timedelta = STALL_WINDOW) -> str:
        if self.accepted:
            return "accepted"
        if self.blockedAt is not None:
            return "blocked"
        if self.reviewVerdicts and not self.reviewVerdicts[-1][1]:
            return "rejected"
        if (
            self.planProposedAt is not None
            and self.firstExecutionAt is None
            and as_of - self.planProposedAt > stall_window
        ):
            return "abandoned"
        if self.lastEventAt is not None and as_of - self.lastEventAt > stall_window:
            return "abandoned"
        return "active"


def build_records(
    events: list[RunEvent],
    costs: list[CostEntry],
    project_team: dict[str, str],
    *,
    as_of: datetime | None = None,
) -> list[RunRecord]:
    records: dict[str, RunRecord] = {}
    for ev in events:
        rec = records.get(ev.runId)
        if rec is None:
            rec = records[ev.runId] = RunRecord(
                runId=ev.runId,
                projectId=ev.projectId,
                teamId=project_team.get(ev.projectId),
                goal="",
            )
        rec.lastEventAt = max(rec.lastEventAt, ev.ts) if rec.lastEventAt else ev.ts
        if ev.type == "plan_proposed":
            rec.planProposedAt = ev.ts
            rec.goal = scrub_text(str(ev.payload.get("goal", "")))[:2000]
        elif ev.type == "plan_approved":
            rec.approvedAt = ev.ts
        elif ev.type == "execution_started":
            rec.attempts += 1
            rec.firstExecutionAt = rec.firstExecutionAt or ev.ts
        elif ev.type == "engine_completed":
            rec.engineCompletedAt = ev.ts
        elif ev.type == "engine_failed":
            rec.engineFailedCount += 1
        elif ev.type in ("review_passed", "review_failed"):
            findings = ev.payload.get("findings") or {}
            rec.reviewVerdicts.append((ev.ts, bool(findings.get("passed"))))
        elif ev.type == "pr_opened":
            rec.prOpenedAt = ev.ts
            rec.prId = ev.payload.get("prId") or rec.prId
        elif ev.type == "blocked_awaiting_clarification":
            rec.blockedAt = ev.ts
        elif ev.type == "done_signal_received":
            rec.doneSignalAt = ev.ts
    costs_by_run: dict[str, Decimal] = {}
    for entry in costs:
        costs_by_run[entry.runId] = costs_by_run.get(entry.runId, Decimal(0)) + entry.costUsd
    for run_id, total in costs_by_run.items():
        if run_id in records:
            records[run_id].costUsd = total
    return sorted(records.values(), key=lambda r: r.runId)


def _filter(
    records: list[RunRecord],
    *,
    from_: datetime | None,
    to: datetime | None,
    team: str | None,
) -> list[RunRecord]:
    out = []
    for rec in records:
        if team is not None and rec.teamId != team:
            continue
        if from_ is not None and (rec.planProposedAt is None or rec.planProposedAt < from_):
            continue
        if to is not None and (rec.planProposedAt is None or rec.planProposedAt >= to):
            continue
        out.append(rec)
    return out


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile (documented, deterministic; no interpolation)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(len(ordered) * q)
    return ordered[max(1, min(rank, len(ordered))) - 1]


def quality_metrics(
    records: list[RunRecord],
    *,
    as_of: datetime,
    from_: datetime | None = None,
    to: datetime | None = None,
    team: str | None = None,
) -> dict:
    recs = _filter(records, from_=from_, to=to, team=team)
    outcomes: dict[str, int] = {}
    for rec in recs:
        key = rec.outcome(as_of)
        outcomes[key] = outcomes.get(key, 0) + 1
    planned = len(recs)
    started = sum(1 for r in recs if r.firstExecutionAt is not None)
    accepted = outcomes.get("accepted", 0)
    reworked = sum(1 for r in recs if r.reworked and r.firstExecutionAt is not None)
    claimed = sum(1 for r in recs if r.claimedCompletion)
    return {
        "tasks": planned,
        "planned": planned,
        **outcomes,
        "started": started,
        "reworked": reworked,
        "reworkRate": round(reworked / started, 4) if started else None,
        "acceptanceRateOfStarted": round(accepted / started, 4) if started else None,
        "acceptanceRateOfPlanned": round(accepted / planned, 4) if planned else None,
        "abandonmentRate": round(outcomes.get("abandoned", 0) / planned, 4) if planned else None,
        "claimedCompletions": claimed,
        "claimedButNotAccepted": sum(
            1 for r in recs if r.claimedCompletion and not r.accepted
        ),
        "engineFailedRuns": sum(1 for r in recs if r.engineFailedCount > 0),
        "definitions": {k: DEFINITIONS[k] for k in (
            "planned", "started", "accepted", "rework_rate",
            "acceptance_rate_of_started", "abandonment_rate",
            "claimed_completions", "claimed_but_not_accepted")},
    }


def _minutes(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 60.0


def productivity_metrics(
    records: list[RunRecord],
    *,
    from_: datetime | None = None,
    to: datetime | None = None,
    team: str | None = None,
) -> dict:
    recs = _filter(records, from_=from_, to=to, team=team)
    ttfuc = [
        _minutes(r.planProposedAt, r.prOpenedAt)
        for r in recs
        if r.accepted and r.planProposedAt and r.prOpenedAt
    ]
    approval = [
        _minutes(r.planProposedAt, r.approvedAt)
        for r in recs
        if r.planProposedAt and r.approvedAt
    ]
    execution = [
        _minutes(r.approvedAt, r.firstExecutionAt)
        for r in recs
        if r.approvedAt and r.firstExecutionAt
    ]
    return {
        "samples": {
            "acceptedRuns": len(ttfuc),
            "approvedRuns": len(approval),
            "executedRuns": len(execution),
        },
        "timeToFirstUsefulChangeMinutes": {
            "p50": percentile(ttfuc, 0.5),
            "p90": percentile(ttfuc, 0.9),
            "values": sorted(round(v, 2) for v in ttfuc),
        },
        "approvalLatencyMinutes": {"p50": percentile(approval, 0.5),
                                   "p90": percentile(approval, 0.9)},
        "approvalToExecutionMinutes": {"p50": percentile(execution, 0.5),
                                       "p90": percentile(execution, 0.9)},
        "definitions": {k: DEFINITIONS[k] for k in (
            "time_to_first_useful_change", "approval_latency")},
    }


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def cost_metrics(
    records: list[RunRecord],
    *,
    from_: datetime | None = None,
    to: datetime | None = None,
    team: str | None = None,
) -> dict:
    recs = _filter(records, from_=from_, to=to, team=team)
    planned = len(recs)
    total = sum((r.costUsd for r in recs), Decimal(0))
    accepted = [r for r in recs if r.accepted]
    claimed = [r for r in recs if r.claimedCompletion]
    accepted_cost = sum((r.costUsd for r in accepted), Decimal(0))
    claimed_cost = sum((r.costUsd for r in claimed), Decimal(0))
    return {
        "currency": "USD",
        "totalCost": _money(total),
        "plannedTasks": planned,
        "acceptedChanges": len(accepted),
        "claimedCompletions": len(claimed),
        "costPerTask": _money(total / planned) if planned else None,
        "costPerClaimedCompletion": _money(claimed_cost / len(claimed)) if claimed else None,
        "costPerAcceptedChange": _money(accepted_cost / len(accepted)) if accepted else None,
        "definitions": {k: DEFINITIONS[k] for k in (
            "cost_per_task", "cost_per_claimed_completion", "cost_per_accepted_change")},
    }
