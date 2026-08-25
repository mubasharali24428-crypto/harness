"""M12-G3 — platform-wide request-size caps (S6's slice).

M17-B3: the canonical registry lives in ``harness_contracts.limits``.
Previously this was a byte-equal copy of section1's limits.py kept only so
this venv didn't need harness_identity; the copy (and its sys.path-imported
original) are gone — everything imports the frozen contracts module now.
"""

from __future__ import annotations

from harness_contracts.limits import (
    MEETING_PROJECT_ID_MAX,
    MEETING_TITLE_MAX,
    MEETING_USER_ID_MAX,
    MESSAGE_CONTENT_MAX,
)

__all__ = [
    "MEETING_PROJECT_ID_MAX",
    "MEETING_TITLE_MAX",
    "MEETING_USER_ID_MAX",
    "MESSAGE_CONTENT_MAX",
]
