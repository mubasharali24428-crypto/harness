"""A1-S2 — pure spend intelligence: token forecast (F087), anomalies (F088), alerts (F144).

No I/O. Every function states its uncertainty instead of faking precision:
too little history => ``calibrated=False`` with a wide interval, or
``insufficient_history`` for anomalies (never a made-up "all clear").
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

MIN_CALIBRATED_SAMPLES = 8
STALE_AFTER = timedelta(days=14)
WIDE_FACTOR = 4  # uncalibrated interval multiplier on the median
MIN_ANOMALY_HISTORY_DAYS = 7
ANOMALY_Z = Decimal("3.5")
ANOMALY_FLOOR_USD = Decimal("1")  # ignore moves too small to matter


def _quantile(sorted_vals: list[int], q: float) -> int:
    idx = min(len(sorted_vals) - 1, max(0, round(q * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


@dataclass(frozen=True)
class Sample:
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    at: datetime


def predict_tokens(
    samples: list[Sample], prompt_tokens: int, *, now: datetime | None = None
) -> dict:
    """Empirical forecast of output tokens/cost for a request of ``prompt_tokens``.

    Output tokens use the sample quantiles (p50, p90); cost uses the observed
    $/token over the same samples. With fewer than MIN_CALIBRATED_SAMPLES the
    interval is widened and ``calibrated`` is False; samples older than
    STALE_AFTER set ``stale``. No samples at all => ``available=False``.
    """
    now = now or datetime.now(UTC)
    if prompt_tokens < 0:
        raise ValueError("prompt_tokens must be >= 0")
    usable = [s for s in samples if s.output_tokens >= 0 and s.input_tokens >= 0]
    if not usable:
        return {"available": False, "calibrated": False, "reason": "no_history"}
    outs = sorted(s.output_tokens for s in usable)
    total_tokens = sum(s.input_tokens + s.output_tokens for s in usable)
    total_cost = sum((s.cost_usd for s in usable), Decimal("0"))
    rate = (total_cost / total_tokens) if total_tokens else Decimal("0")
    calibrated = len(usable) >= MIN_CALIBRATED_SAMPLES
    p50, p90 = _quantile(outs, 0.5), _quantile(outs, 0.9)
    low, high = _quantile(outs, 0.1), max(p90, _quantile(outs, 0.95))
    if not calibrated:
        low, high = 0, max(high, p50 * WIDE_FACTOR)
    newest = max(s.at for s in usable)
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=UTC)

    def cost(out_tokens: int) -> str:
        return str((rate * (prompt_tokens + out_tokens)).quantize(Decimal("0.000001")))

    return {
        "available": True,
        "calibrated": calibrated,
        "stale": now - newest > STALE_AFTER,
        "sampleCount": len(usable),
        "promptTokens": prompt_tokens,
        "outputTokens": {"low": low, "p50": p50, "p90": p90, "high": high},
        "costUsd": {"low": cost(low), "p50": cost(p50), "p90": cost(p90), "high": cost(high)},
    }


def detect_spend_anomaly(daily: dict[str, Decimal], today: str) -> dict:
    """Robust (median/MAD) check of ``today`` against the prior days.

    ``daily`` maps ISO date -> USD. Days with no spend must be passed as 0 by
    the caller so quiet periods count. Fewer than MIN_ANOMALY_HISTORY_DAYS
    prior days => ``insufficient_history`` (unknown, not "normal").
    """
    prior = [v for d, v in sorted(daily.items()) if d < today]
    value = daily.get(today, Decimal("0"))
    if len(prior) < MIN_ANOMALY_HISTORY_DAYS:
        return {"status": "insufficient_history", "priorDays": len(prior), "todayUsd": str(value)}
    med = Decimal(str(statistics.median(prior)))
    mad = Decimal(str(statistics.median([abs(v - med) for v in prior])))
    # 1.4826*MAD ~ sigma; floor avoids divide-by-zero on flat history.
    scale = max(mad * Decimal("1.4826"), ANOMALY_FLOOR_USD / 10)
    z = (value - med) / scale
    anomalous = z > ANOMALY_Z and (value - med) >= ANOMALY_FLOOR_USD
    return {
        "status": "anomaly" if anomalous else "normal",
        "priorDays": len(prior),
        "todayUsd": str(value),
        "medianUsd": str(med),
        "score": str(z.quantize(Decimal("0.01"))),
    }


def budget_alert(
    cap: Decimal | None,
    used: Decimal,
    reserved: Decimal,
    *,
    warn_fraction: Decimal = Decimal("0.8"),
) -> dict:
    """Level for a monthly cap counting in-flight holds (used + reserved)."""
    if cap is None:
        return {"level": "unlimited", "capUsd": None}
    committed = used + reserved
    remaining = max(cap - committed, Decimal("0"))
    if committed >= cap:
        level = "ceiling"
    elif cap > 0 and committed >= cap * warn_fraction:
        level = "warning"
    else:
        level = "ok"
    return {
        "level": level,
        "capUsd": str(cap),
        "committedUsd": str(committed),
        "remainingUsd": str(remaining),
    }


def reconcile_usage(
    ledger_usd: Decimal, provider_usd: Decimal | None, *, tolerance_usd: Decimal = Decimal("0.01")
) -> dict:
    """Compare our ledger to provider-reported usage; unknown stays unknown."""
    if provider_usd is None:
        return {"status": "unsettled", "ledgerUsd": str(ledger_usd), "providerUsd": None}
    diff = provider_usd - ledger_usd
    status = "matched" if abs(diff) <= tolerance_usd else "discrepancy"
    return {
        "status": status,
        "ledgerUsd": str(ledger_usd),
        "providerUsd": str(provider_usd),
        "differenceUsd": str(diff),
    }
