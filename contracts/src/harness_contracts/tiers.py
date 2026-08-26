"""M22-M2 — behavior-bearing plan tiers: definitions, cap resolution, CTA wire.

Commander's intent (mission22-commercial-graph.md, lane M2): PLAN_TIERS stop
being behavior-free strings — hitting a cap produces an upgrade CTA, not a
dead end. This module is the ONE definition of what each tier buys:

* ``free``  — capped runs/day + capped context index size
* ``pro``   — full runs + full index (no platform caps of either kind)
* ``team``  — pro, plus seats and longer audit retention

Cap sources (per value, first non-None wins):

1. ``HARNESS_TIER_RUNS_PER_DAY`` / ``HARNESS_TIER_CONTEXT_CHUNKS``
   deploy-wide env overrides over the tier default (tuning knobs; unset by
   default so shipped defaults ARE the pricing).
2. :data:`DEFAULT_*` tier defaults above.

A cap value of ``None`` means UNLIMITED for that dimension — pro/team are
genuinely uncapped, not zero. Garbage/unset env values are ABSENT (fall
through to the tier default), never runtime errors — same posture as
``budget.parse_env_budget_usd``.

CTA contract (:func:`cap_hit_wire`): every enforcement site that refuses work
because a TIER cap was reached answers with HTTP 402 and this exact body
shape — ``error`` (frozen envelope) + ``upgrade`` block carrying
``{tier, cap, capField, used, upgradeTo, cta}``. Clients render an upgrade
call-to-action from ``upgrade`` alone; a bare denial without it is the dead
end this module exists to prevent. Money/budget exhaustion stays a separate
concern (section4's budget gate, M18-C2) — tier caps gate VOLUME, budgets
gate SPEND.

Seat limit and audit-retention days are DESCRIPTIVE here (informational wire
fields; retention is consumed by deploy/runners/events_retention.py config,
seats by roster provisioning) — they never gate requests from this module.
Per-team non-default values may be stored on ``billing_plans``
(migration 0031 columns); NULL column = inherit the tier default.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: The canonical tier ladder, cheapest first. Exported verbatim from here;
#: section1.billing re-exports for backwards compatibility (it owned the bare
#: tuple since Mission 10).
PLAN_TIERS = ("free", "pro", "team")

_DEFAULT_TIER = "free"

# --------------------------------------------------------------------------
# Env knobs — deploy-wide overrides over the tier defaults below
# --------------------------------------------------------------------------

ENV_TIER_RUNS_PER_DAY = "HARNESS_TIER_RUNS_PER_DAY"
ENV_TIER_CONTEXT_CHUNKS = "HARNESS_TIER_CONTEXT_CHUNKS"

# --------------------------------------------------------------------------
# Tier defaults. None = unlimited. These ARE the published pricing (M1's
# PRICING.md describes exactly this mapping) — change them there too.
# --------------------------------------------------------------------------

#: Runs per UTC calendar day per team. free is capped; pro/team are not.
DEFAULT_RUNS_PER_DAY: dict[str, int | None] = {"free": 20, "pro": None, "team": None}

#: Context index size ceiling per project (stored chunks). free is capped;
#: pro/team are not.
DEFAULT_CONTEXT_INDEX_MAX_CHUNKS: dict[str, int | None] = {
    "free": 2000,
    "pro": None,
    "team": None,
}

#: Team seats (roster members). Informational — enforced at invite time.
DEFAULT_SEAT_LIMITS: dict[str, int] = {"free": 1, "pro": 5, "team": 25}

#: Audit-log retention window (days). team buys compliance-grade history
#: (mirrors events_retention.py's DEFAULT_AUDIT_RETENTION_DAYS = 365);
#: free/pro ride the operator's default sweep window (30).
DEFAULT_AUDIT_RETENTION_DAYS: dict[str, int] = {"free": 30, "pro": 30, "team": 365}

#: The upgrade ladder: what each tier advertises when a cap bites.
#: team is the top — its entry is None (nothing to sell; the CTA is omitted).
UPGRADE_WIRE: dict[str, str | None] = {"free": "pro", "pro": "team", "team": None}

#: Machine-checkable statement of WHICH wire fields are enforced caps vs
#: descriptive info — consumers pin against these tuples so a rename cannot
#: happen silently in one consumer.
TIER_CAP_FIELDS = ("runsPerDay", "contextIndexMaxChunks")
TIER_INFO_FIELDS = ("seatLimit", "auditRetentionDays")


def parse_tier(value: Any, *, default: str = _DEFAULT_TIER) -> str:
    """Normalize any stored/requested tier spelling onto the canonical ladder.

    Unknown/None values fall back to ``default`` (never raise) — an unmigrated
    row or a stale client string must not crash enforcement; it lands on the
    safe (most-capped) answer.
    """
    name = str(value or "").strip().lower()
    return name if name in PLAN_TIERS else default


def _parse_env_int(raw: str | None) -> int | None:
    """Env value -> non-negative int, or None when absent/garbage/negative."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    return value if value >= 0 else None


@dataclass(frozen=True)
class TierCaps:
    """Resolved behavior for one tier — the answer to 'what does this buy?'."""

    tier: str
    runs_per_day: int | None
    context_index_max_chunks: int | None
    seat_limit: int
    audit_retention_days: int
    upgrade_to: str | None

    def to_wire(self) -> dict[str, Any]:
        """camelCase wire shape (api-conventions.md); snake_case stays Python.

        ``runsPerDay``/``contextIndexMaxChunks`` are the ENFORCED caps
        (TIER_CAP_FIELDS); ``seatLimit``/``auditRetentionDays`` are the
        descriptive tier facts (TIER_INFO_FIELDS).
        """
        return {
            "tier": self.tier,
            "runsPerDay": self.runs_per_day,
            "contextIndexMaxChunks": self.context_index_max_chunks,
            "seatLimit": self.seat_limit,
            "auditRetentionDays": self.audit_retention_days,
            "upgradeTo": self.upgrade_to,
        }


def tier_caps(
    tier: Any,
    *,
    env: Mapping[str, str] | None = None,
    runs_per_day_override: int | None = None,
    context_index_max_chunks_override: int | None = None,
) -> TierCaps:
    """Resolve one tier's effective caps against env + per-team overrides.

    Law of precedence:

    * A PER-TEAM override argument (billing_plans 0031 columns — a deliberate
      operator action contracted with that specific team) ALWAYS wins.
    * Otherwise, a CAPPED dimension resolves to env-knob value if present,
      else the tier default.
    * UNLIMITED stays unlimited against ENV: the deploy-wide knobs TUNE an
      existing tier cap, never INTRODUCE one — a stray
      ``HARNESS_TIER_RUNS_PER_DAY=50`` must not silently downgrade every Pro
      team from unlimited to 50/day (PRICING.md honesty; cf. "no forced
      model downgrade"). Only the per-team contractual override can bound an
      unlimited dimension.

    The env map is injectable for tests; ``None`` reads ``os.environ``.
    """
    name = parse_tier(tier)
    env_map = os.environ if env is None else env

    def _resolve(default: int | None, env_name: str, override: int | None) -> int | None:
        if override is not None:
            return override
        if default is None:
            return None
        env_value = _parse_env_int(env_map.get(env_name))
        return env_value if env_value is not None else default

    return TierCaps(
        tier=name,
        runs_per_day=_resolve(
            DEFAULT_RUNS_PER_DAY[name], ENV_TIER_RUNS_PER_DAY, runs_per_day_override
        ),
        context_index_max_chunks=_resolve(
            DEFAULT_CONTEXT_INDEX_MAX_CHUNKS[name],
            ENV_TIER_CONTEXT_CHUNKS,
            context_index_max_chunks_override,
        ),
        seat_limit=DEFAULT_SEAT_LIMITS[name],
        audit_retention_days=DEFAULT_AUDIT_RETENTION_DAYS[name],
        upgrade_to=UPGRADE_WIRE[name],
    )


def cap_hit_wire(
    *,
    tier: str,
    cap: int,
    cap_field: str,
    used: int,
    message: str | None = None,
) -> dict[str, Any]:
    """THE cap-hit response body: frozen error envelope + structured upgrade CTA.

    ``cap_field`` uses the wire spelling from TIER_CAP_FIELDS. The ``upgrade``
    block is what the UI renders — every tier-cap denial MUST carry it
    (graph exit criterion: an upgrade CTA, not a dead end). At the top tier
    ``upgradeTo`` is null and the CTA degrades honestly to a contact-the-
    operator message instead of pretending another plan exists.
    """
    target = UPGRADE_WIRE[parse_tier(tier)]
    label = f"Upgrade to {target.capitalize()}" if target else "Contact your operator"
    body: dict[str, Any] = {
        "error": {
            "code": "TIER_CAP_REACHED",
            "message": (
                message
                or f"{parse_tier(tier)} plan cap reached: {cap_field} limit is {cap}"
            ),
        },
        "upgrade": {
            "tier": parse_tier(tier),
            "cap": cap,
            "capField": cap_field,
            "used": used,
            "upgradeTo": target,
            "cta": {"action": "upgrade", "label": label},
        },
    }
    return body
