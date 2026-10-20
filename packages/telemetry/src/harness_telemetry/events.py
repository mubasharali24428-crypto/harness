"""Ingestion models: the frozen EventEnvelope stream + cost-ledger rows.

Everything downstream (spans, metrics, archive) is derived from these two
inputs. Parsing is lenient on payload contents (payload keys are payload
conventions, not schema), strict on envelope identity (type/ts/seq/runId).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

# ---------------------------------------------------------------------------
# PII / secret scrubbing (applies to every text that can reach storage, spans
# or dashboards). Rule-based, deterministic; false negatives possible, false
# positives cheap. Patterns mirror the connector intake scrubber.
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"sk_(live|test)_[A-Za-z0-9]{10,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[=:]\s*\S{8,}"),
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def scrub_text(text: str) -> str:
    """Redact secrets then emails; deterministic, idempotent."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return _EMAIL.sub("[EMAIL]", text)


# ---------------------------------------------------------------------------
# Event stream
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunEvent:
    type: str
    ts: datetime
    seq: int
    projectId: str
    runId: str
    payload: dict[str, Any] = field(default_factory=dict)


def parse_event(raw: dict[str, Any]) -> RunEvent:
    run_id = raw.get("runId")
    if not run_id:
        raise ValueError("event without runId cannot be attributed to a run")
    return RunEvent(
        type=str(raw["type"]),
        ts=datetime.fromisoformat(str(raw["ts"])),
        seq=int(raw["seq"]),
        projectId=str(raw["projectId"]),
        runId=str(run_id),
        payload=raw.get("payload") or {},
    )


def parse_stream(raws: list[dict[str, Any]]) -> list[RunEvent]:
    return sorted((parse_event(r) for r in raws), key=lambda e: (e.runId, e.seq))


# ---------------------------------------------------------------------------
# Cost ledger rows (adapter shape of the frozen cost_ledger table)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostEntry:
    runId: str
    costUsd: Decimal
    inputTokens: int
    outputTokens: int
    latencyMs: int
    model: str


def parse_cost(raw: dict[str, Any]) -> CostEntry:
    try:
        cost = Decimal(str(raw.get("costUsd", "0")))
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise ValueError(f"unparsable costUsd: {raw.get('costUsd')!r}") from exc
    return CostEntry(
        runId=str(raw["runId"]),
        costUsd=cost,
        inputTokens=int(raw.get("inputTokens", 0)),
        outputTokens=int(raw.get("outputTokens", 0)),
        latencyMs=int(raw.get("latencyMs", 0)),
        model=str(raw.get("model", "unknown")),
    )


def now_utc() -> datetime:
    return datetime.now(UTC)
