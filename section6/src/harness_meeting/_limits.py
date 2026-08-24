"""M12-G3 (Mission 12 Wave 2.2, lane G3) — platform-wide request-size caps.

raw-06 #9 hardening sweep: every JSON request body field that carries
unbounded free text now dies at Pydantic validation (FastAPI → 422) instead
of reaching handlers, the DB, logs, or model context.

Rules this module encodes:

* **Additive only.** Caps are generous upper bounds chosen well above any
  legitimate payload observed in fixtures, golden flows, or schema columns
  (emails ≤ RFC 5321's 320, titles/names ≤ the VARCHAR(200) convention,
  ids ≤ 36-char UUIDs rounded up). No existing valid payload can trip them.
* **One shared source of truth.** Section-owned request models reference
  these values; per-section copies (where a section's venv does not carry
  ``harness_identity``, e.g. S4/S6) MUST mirror them byte-for-value — the
  G3 parity tests in each section pin the numbers so drift fails CI.
* **Uniform rejection surface.** Violations surface as FastAPI 422s; where
  lane F1's ``install_error_envelope`` middleware is installed the body is
  the frozen ``{"error": {"code": "VALIDATION_ERROR", ...}}`` envelope.

Constants are named ``<FIELD>_<MAX>`` and grouped per section.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Section 1 — identity/auth/projects (app.py request models)
# --------------------------------------------------------------------------
LOGIN_EMAIL_MAX = 320  # RFC 5321 maximum email length; column is VARCHAR(320)
LOGIN_TEAM_ID_MAX = 64  # team ids are 36-char UUIDs; headroom for legacy forms
LOGIN_TEAM_NAME_MAX = 200  # teams.name convention (VARCHAR(200))
OTP_CODE_MAX = 12  # shipped codes are numeric OTP_CODE_LENGTH digits
PROJECT_NAME_MAX = 200  # projects.name convention (VARCHAR(200))
PROJECT_REPO_FULL_NAME_MAX = 200  # "owner/repo" GitHub slug
WS_TICKET_PROJECT_ID_MAX = 64  # project ids are 36-char UUIDs

# --------------------------------------------------------------------------
# Section 4 — orchestrator (api.py request models)
# --------------------------------------------------------------------------
PROPOSE_GOAL_MAX = 20_000  # agent goal text; largest legitimate brief ≈ pages
PROPOSE_PROJECT_ID_MAX = 64
PLAN_ACTION_PLAN_ID_MAX = 64
HUMAN_EDIT_PATH_MAX = 512  # repo-relative file path (mission-specified)
HUMAN_EDIT_CONTENT_MAX = 100_000  # full-file edit payload (mission-specified)
DONE_DIFF_TEXT_MAX = 200_000  # evidence diff attached to a done-signal

# --------------------------------------------------------------------------
# Section 6 — meeting area (api.py request models)
# --------------------------------------------------------------------------
MEETING_TITLE_MAX = 200  # meeting title (mission-specified)
MEETING_PROJECT_ID_MAX = 64
MEETING_USER_ID_MAX = 64
MESSAGE_CONTENT_MAX = 10_000  # chat/AI/prompt message bodies (mission-specified)

#: Single flat registry consumed by the per-section parity tests: each entry
#: pins a cap so a drive-by edit here (or in a mirrored section copy) that
#: desyncs the sections fails pytest instead of shipping a split platform.
PLATFORM_CAPS: dict[str, int] = {
    "S1.LOGIN_EMAIL_MAX": LOGIN_EMAIL_MAX,
    "S1.LOGIN_TEAM_ID_MAX": LOGIN_TEAM_ID_MAX,
    "S1.LOGIN_TEAM_NAME_MAX": LOGIN_TEAM_NAME_MAX,
    "S1.OTP_CODE_MAX": OTP_CODE_MAX,
    "S1.PROJECT_NAME_MAX": PROJECT_NAME_MAX,
    "S1.PROJECT_REPO_FULL_NAME_MAX": PROJECT_REPO_FULL_NAME_MAX,
    "S1.WS_TICKET_PROJECT_ID_MAX": WS_TICKET_PROJECT_ID_MAX,
    "S4.PROPOSE_GOAL_MAX": PROPOSE_GOAL_MAX,
    "S4.PROPOSE_PROJECT_ID_MAX": PROPOSE_PROJECT_ID_MAX,
    "S4.PLAN_ACTION_PLAN_ID_MAX": PLAN_ACTION_PLAN_ID_MAX,
    "S4.HUMAN_EDIT_PATH_MAX": HUMAN_EDIT_PATH_MAX,
    "S4.HUMAN_EDIT_CONTENT_MAX": HUMAN_EDIT_CONTENT_MAX,
    "S4.DONE_DIFF_TEXT_MAX": DONE_DIFF_TEXT_MAX,
    "S6.MEETING_TITLE_MAX": MEETING_TITLE_MAX,
    "S6.MEETING_PROJECT_ID_MAX": MEETING_PROJECT_ID_MAX,
    "S6.MEETING_USER_ID_MAX": MEETING_USER_ID_MAX,
    "S6.MESSAGE_CONTENT_MAX": MESSAGE_CONTENT_MAX,
}
