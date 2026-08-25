"""M12-G3 — platform-wide request-size caps (S1's slice).

M17-B3: the canonical registry now lives in ``harness_contracts.limits``
(promoted so S4/S6 stop importing this module across sections via runtime
sys.path injection). Kept as a re-export for Section 1's internal callers
and the G3 parity tests; new code should import from harness_contracts.
"""

from __future__ import annotations

from harness_contracts.limits import (
    LOGIN_EMAIL_MAX,
    LOGIN_TEAM_ID_MAX,
    LOGIN_TEAM_NAME_MAX,
    OTP_CODE_MAX,
    PLATFORM_CAPS,
    PROJECT_NAME_MAX,
    PROJECT_REPO_FULL_NAME_MAX,
    WS_TICKET_PROJECT_ID_MAX,
)

__all__ = [
    "LOGIN_EMAIL_MAX",
    "LOGIN_TEAM_ID_MAX",
    "LOGIN_TEAM_NAME_MAX",
    "OTP_CODE_MAX",
    "PLATFORM_CAPS",
    "PROJECT_NAME_MAX",
    "PROJECT_REPO_FULL_NAME_MAX",
    "WS_TICKET_PROJECT_ID_MAX",
]
