from .api import API_PREFIX, error_body, ws_envelope
from .brief import EngineeringBrief
from .events import EVENT_TYPES, EventEnvelope, EventType, event_envelope
from .fastapi_auth import hash_token, make_bearer_dep
from .limits import PLATFORM_CAPS
from .plan import AgentPlan
from .ratelimit import install_rate_limit
from .reviewer_thresholds import (
    DEFAULT_REVIEWER_THRESHOLDS,
    REVIEWER_THRESHOLD_KEY,
    ReviewerThresholds,
)
from .roles import Principal, Role, can
from .security import KeyVault
from .timeutil import as_utc, db_now, now_utc, utcnow_iso

__version__ = "0.4.0"

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
]
