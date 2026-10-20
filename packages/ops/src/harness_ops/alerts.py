"""F119 — Alerts for stalled, risky, expensive, or policy-violating runs.

Rules evaluate the same telemetry records the dashboards use; the engine adds
statefulness on top: a dedupe key + notification window so an ongoing condition
pages once, not every evaluation, and resolution is a first-class event.

Notification semantics (tested, deterministic):
  * condition appears            -> alert.opened
  * still true within window     -> suppressed (count increments, no send)
  * still true after window      -> alert.reminder (at most once per window)
  * condition gone               -> alert.resolved (exactly once)

``stall_hours`` default (12h) is deliberately BELOW the telemetry abandonment
window (24h): the point of the alert is to page while the run can still be
rescued, before it would be classified abandoned.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from harness_telemetry import RunRecord

from .store import OpsStore


@dataclass(frozen=True)
class AlertThresholds:
    stall_hours: float = 12.0
    block_hours: float = 24.0
    per_run_usd: Decimal = Decimal("5.00")
    team_budget_usd: Decimal = Decimal("20.00")
    team_budget_window_hours: float = 24.0
    repeated_engine_failures: int = 2
    dedupe_window: timedelta = timedelta(hours=6)


@dataclass(frozen=True)
class AlertDraft:
    rule: str
    severity: str
    dedupeKey: str
    subject: dict
    message: str

    @property
    def alertId(self) -> str:
        digest = hashlib.sha256(f"{self.rule}:{self.dedupeKey}".encode()).hexdigest()
        return f"alr-{digest[:12]}"


def evaluate_alerts(
    records: list[RunRecord],
    *,
    as_of: datetime,
    thresholds: AlertThresholds | None = None,
) -> list[AlertDraft]:
    t = thresholds or AlertThresholds()
    drafts: list[AlertDraft] = []

    for rec in records:
        outcome = rec.outcome(as_of)
        if outcome == "active" and rec.lastEventAt is not None:
            idle = (as_of - rec.lastEventAt).total_seconds() / 3600.0
            if idle > t.stall_hours:
                drafts.append(
                    AlertDraft(
                        rule="stalled_run",
                        severity="medium",
                        dedupeKey=f"stalled_run:{rec.runId}",
                        subject={"runId": rec.runId, "teamId": rec.teamId},
                        message=f"run {rec.runId} idle for {idle:.1f}h (> "
                        f"{t.stall_hours:.0f}h) with no terminal outcome",
                    )
                )
        if rec.blockedAt is not None and outcome == "blocked":
            blocked_h = (as_of - rec.blockedAt).total_seconds() / 3600.0
            if blocked_h > t.block_hours:
                drafts.append(
                    AlertDraft(
                        rule="blocked_run",
                        severity="medium",
                        dedupeKey=f"blocked_run:{rec.runId}",
                        subject={"runId": rec.runId, "teamId": rec.teamId},
                        message=f"run {rec.runId} blocked awaiting clarification for "
                        f"{blocked_h:.1f}h",
                    )
                )
        if rec.costUsd > t.per_run_usd:
            drafts.append(
                AlertDraft(
                    rule="expensive_run",
                    severity="low",
                    dedupeKey=f"expensive_run:{rec.runId}",
                    subject={"runId": rec.runId, "teamId": rec.teamId},
                    message=f"run {rec.runId} cost ${rec.costUsd} (> ${t.per_run_usd})",
                )
            )
        if rec.claimedCompletion and outcome == "rejected":
            drafts.append(
                AlertDraft(
                    rule="claimed_but_rejected",
                    severity="high",
                    dedupeKey=f"claimed_but_rejected:{rec.runId}",
                    subject={"runId": rec.runId, "teamId": rec.teamId},
                    message=f"run {rec.runId}: engine claimed completion but independent "
                    "review rejected the change (risky: claim/reality gap)",
                )
            )
        if rec.engineFailedCount >= t.repeated_engine_failures:
            drafts.append(
                AlertDraft(
                    rule="repeated_engine_failure",
                    severity="high",
                    dedupeKey=f"repeated_engine_failure:{rec.runId}",
                    subject={"runId": rec.runId, "teamId": rec.teamId},
                    message=f"run {rec.runId}: engine failed {rec.engineFailedCount} times",
                )
            )

    window = timedelta(hours=t.team_budget_window_hours)
    by_team: dict[str, Decimal] = {}
    for rec in records:
        if rec.teamId is None or rec.lastEventAt is None:
            continue
        if as_of - rec.lastEventAt <= window:
            by_team[rec.teamId] = by_team.get(rec.teamId, Decimal("0")) + rec.costUsd
    for team_id, spend in sorted(by_team.items()):
        if spend > t.team_budget_usd:
            drafts.append(
                AlertDraft(
                    rule="team_budget_exceeded",
                    severity="high",
                    dedupeKey=f"team_budget_exceeded:{team_id}",
                    subject={"teamId": team_id},
                    message=f"team {team_id} spent ${spend} in the last "
                    f"{t.team_budget_window_hours:.0f}h (> ${t.team_budget_usd})",
                )
            )
    return drafts


class AlertEngine:
    """Stateful dedupe/resolution over the store's alert table."""

    def __init__(self, store: OpsStore, *, thresholds: AlertThresholds | None = None) -> None:
        self._store = store
        self._thresholds = thresholds or AlertThresholds()

    @property
    def thresholds(self) -> AlertThresholds:
        return self._thresholds

    def sync(self, drafts: list[AlertDraft], *, now: datetime) -> dict:
        """Returns {"opened": [...], "reminders": [...], "suppressed": n,
        "resolved": [...]} — each entry is a webhook-ready notification."""
        opened, reminders, suppressed, resolved = [], [], 0, []

        current_keys = {d.dedupeKey for d in drafts}
        for draft in drafts:
            existing = self._store.active_alert(draft.dedupeKey)
            window = self._thresholds.dedupe_window
            if existing is not None and existing["lastNotifiedAt"] is not None:
                last = datetime.fromisoformat(existing["lastNotifiedAt"])
                if now - last < window:
                    suppressed += 1
                    self._store.upsert_alert(
                        alert_id=draft.alertId, rule=draft.rule,
                        dedupe_key=draft.dedupeKey, severity=draft.severity,
                        subject=draft.subject, message=draft.message,
                        now=now, notified=False,
                    )
                    continue
                self._store.upsert_alert(
                    alert_id=draft.alertId, rule=draft.rule,
                    dedupe_key=draft.dedupeKey, severity=draft.severity,
                    subject=draft.subject, message=draft.message,
                    now=now, notified=True,
                )
                reminders.append(self._notification("alert.reminder", draft))
                continue
            self._store.upsert_alert(
                alert_id=draft.alertId, rule=draft.rule, dedupe_key=draft.dedupeKey,
                severity=draft.severity, subject=draft.subject, message=draft.message,
                now=now, notified=True,
            )
            opened.append(self._notification("alert.opened", draft))

        for row in self._store.alerts(status="active"):
            key = row["dedupeKey"]
            if key in current_keys:
                continue
            resolved_row = self._store.resolve_alert(key, now)
            if resolved_row is not None:
                resolved.append(
                    {
                        "event": "alert.resolved",
                        "alertId": resolved_row["alertId"],
                        "rule": resolved_row["rule"],
                        "severity": resolved_row["severity"],
                        "subject": resolved_row["subject"],
                        "message": resolved_row["message"],
                        "count": resolved_row["count"],
                    }
                )
        return {"opened": opened, "reminders": reminders,
                "suppressed": suppressed, "resolved": resolved}

    @staticmethod
    def _notification(event: str, draft: AlertDraft) -> dict:
        return {
            "event": event,
            "alertId": draft.alertId,
            "rule": draft.rule,
            "severity": draft.severity,
            "subject": draft.subject,
            "message": draft.message,
        }
