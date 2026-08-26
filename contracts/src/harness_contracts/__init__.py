from .api import API_PREFIX, error_body, ws_envelope
from .brief import EngineeringBrief
from .budget import (
    CAP_RESOLUTION_ORDER,
    DEGRADED_FLAG_WIRE,
    DEGRADED_REASON,
    ENV_TEAM_BUDGET_USD,
    MONTH_WINDOW_SQL,
    ResolvedCap,
    env_budget_default,
    parse_env_budget_usd,
    resolve_cap,
    to_cap_decimal,
)
from .events import EVENT_TYPES, EventEnvelope, EventType, event_envelope

try:  # sqlalchemy-dependent modules stay optional at import (S5 has no SA)
    from .fastapi_auth import hash_token, make_bearer_dep
    from .ratelimit import install_rate_limit
except ImportError:  # pragma: no cover — deployments without SA skip these
    hash_token = None  # type: ignore[assignment]
    make_bearer_dep = None  # type: ignore[assignment]
    install_rate_limit = None  # type: ignore[assignment]

from .limits import PLATFORM_CAPS
from .plan import AgentPlan
from .reviewer_thresholds import (
    DEFAULT_REVIEWER_THRESHOLDS,
    REVIEWER_THRESHOLD_KEY,
    ReviewerThresholds,
)
from .roles import Principal, Role, can
from .security import KeyVault

# M22-M2: behavior-bearing plan tiers (caps + upgrade-CTA wire). Additive.
from .tiers import (
    DEFAULT_AUDIT_RETENTION_DAYS,
    DEFAULT_CONTEXT_INDEX_MAX_CHUNKS,
    DEFAULT_RUNS_PER_DAY,
    DEFAULT_SEAT_LIMITS,
    ENV_TIER_CONTEXT_CHUNKS,
    ENV_TIER_RUNS_PER_DAY,
    PLAN_TIERS,
    TIER_CAP_FIELDS,
    TIER_INFO_FIELDS,
    UPGRADE_WIRE,
    TierCaps,
    cap_hit_wire,
    parse_tier,
    tier_caps,
)
from .timeutil import as_utc, db_now, now_utc, utcnow_iso

__version__ = "1.0.0rc1"

__all__ = [
    "Principal",
    "Role",
    "can",
    "EVENT_TYPES",
    "EventType",
    "EventEnvelope",
    "event_envelope",
    "EngineeringBrief",
    "AgentPlan",
    "API_PREFIX",
    "error_body",
    "ws_envelope",
    "KeyVault",
    "hash_token",
    "make_bearer_dep",
    # M17-B3 promotions (additive; see docs/ci-gates.md isolation law):
    "PLATFORM_CAPS",
    "install_rate_limit",
    "DEFAULT_REVIEWER_THRESHOLDS",
    "REVIEWER_THRESHOLD_KEY",
    "ReviewerThresholds",
    "as_utc",
    "db_now",
    "now_utc",
    "utcnow_iso",
    # M18-C2: the one budget truth (cap resolution / month window / posture):
    "CAP_RESOLUTION_ORDER",
    "DEGRADED_FLAG_WIRE",
    "DEGRADED_REASON",
    "ENV_TEAM_BUDGET_USD",
    "MONTH_WINDOW_SQL",
    "ResolvedCap",
    "env_budget_default",
    "parse_env_budget_usd",
    "resolve_cap",
    # M22-M2: behavior-bearing plan tiers + upgrade-CTA wire (additive):
    "DEFAULT_AUDIT_RETENTION_DAYS",
    "DEFAULT_CONTEXT_INDEX_MAX_CHUNKS",
    "DEFAULT_RUNS_PER_DAY",
    "DEFAULT_SEAT_LIMITS",
    "ENV_TIER_CONTEXT_CHUNKS",
    "ENV_TIER_RUNS_PER_DAY",
    "PLAN_TIERS",
    "TIER_CAP_FIELDS",
    "TIER_INFO_FIELDS",
    "UPGRADE_WIRE",
    "TierCaps",
    "cap_hit_wire",
    "parse_tier",
    "tier_caps",
]
