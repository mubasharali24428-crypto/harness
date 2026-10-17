"""Shared decision vocabulary for the A1 outbound-security policy engine.

Every evaluator returns a :class:`Decision` with a stable machine-readable
``reason`` and structured findings. Findings NEVER carry matched secret
material — only a rule id, severity, character span and a message that names
the rule class — so decisions are safe to log, persist in audit detail, or
return over the wire.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Outcome(StrEnum):
    """Ordered by strength: DENY > QUARANTINE > REDACT > ALLOW."""

    ALLOW = "allow"
    REDACT = "redact"
    QUARANTINE = "quarantine"
    DENY = "deny"


_RANK = {
    Outcome.ALLOW: 0,
    Outcome.REDACT: 1,
    Outcome.QUARANTINE: 2,
    Outcome.DENY: 3,
}

SEVERITIES = ("low", "medium", "high", "critical")


def strongest(outcomes: Iterable[Outcome]) -> Outcome:
    """Worst (most restrictive) outcome in ``outcomes``; ALLOW when empty."""
    result = Outcome.ALLOW
    for outcome in outcomes:
        if _RANK[outcome] > _RANK[result]:
            result = outcome
    return result


@dataclass(frozen=True)
class Finding:
    """One policy rule hit. Carries no secret material by construction."""

    rule_id: str
    severity: str
    start: int
    end: int
    message: str
    action: Outcome = Outcome.REDACT

    def as_dict(self) -> dict[str, Any]:
        return {
            "ruleId": self.rule_id,
            "severity": self.severity,
            "start": self.start,
            "end": self.end,
            "message": self.message,
            "action": self.action.value,
        }


@dataclass(frozen=True)
class Decision:
    """Outcome plus evidence. ``text`` is the safe/redacted payload if any."""

    outcome: Outcome
    reason: str
    findings: tuple[Finding, ...] = ()
    text: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.outcome is Outcome.ALLOW

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "decision": self.outcome.value,
            "reason": self.reason,
            "findings": [f.as_dict() for f in self.findings],
            "details": dict(self.details),
        }
        if self.text is not None:
            out["text"] = self.text
        return out


def redact_spans(text: str, spans: Iterable[tuple[int, int, str]]) -> str:
    """Replace ``(start, end, label)`` spans with ``[REDACTED:<label>]``.

    Overlapping spans merge into one replacement (the first label wins) so a
    secret can never be partially re-exposed by a nested match.
    """
    ordered = sorted((s, e, label) for s, e, label in spans if 0 <= s < e <= len(text))
    merged: list[list[Any]] = []
    for start, end, label in ordered:
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end, label])
    pieces: list[str] = []
    cursor = 0
    for start, end, label in merged:
        pieces.append(text[cursor:start])
        pieces.append(f"[REDACTED:{label}]")
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)
