"""M12-G3 — platform-wide request-size caps (S4's slice).

M17-B3: the canonical registry lives in ``harness_contracts.limits``.
Previously this was a byte-equal copy of section1's limits.py kept only so
this venv didn't need harness_identity; the copy (and its sys.path-imported
original) are gone — everything imports the frozen contracts module now.
"""

from __future__ import annotations

from harness_contracts.limits import (
    DONE_DIFF_TEXT_MAX,
    HUMAN_EDIT_CONTENT_MAX,
    HUMAN_EDIT_PATH_MAX,
    PLAN_ACTION_PLAN_ID_MAX,
    PROPOSE_GOAL_MAX,
    PROPOSE_PROJECT_ID_MAX,
)

__all__ = [
    "DONE_DIFF_TEXT_MAX",
    "HUMAN_EDIT_CONTENT_MAX",
    "HUMAN_EDIT_PATH_MAX",
    "PLAN_ACTION_PLAN_ID_MAX",
    "PROPOSE_GOAL_MAX",
    "PROPOSE_PROJECT_ID_MAX",
]
