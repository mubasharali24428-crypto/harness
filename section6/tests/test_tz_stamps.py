"""M13-H4b integration assertions — S6 call-site family writes tz-safe ISO.

Every converted harness_meeting model (Meeting, ChatMessage, Prompt,
MeetingSummary) stamps its createdAt/timestamp default from
``harness_identity.timeutil.now_utc()``. These fields are API-wire strings,
not DB TIMESTAMPTZ binds — the assertion family pins that every stamped
value is an ISO-8601 string carrying an explicit UTC offset (never naive),
so downstream parsers/DB writers never have to guess.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_S1_SRC = _ROOT / "section1" / "src"
if _S1_SRC.exists() and str(_S1_SRC) not in sys.path:
    sys.path.append(str(_S1_SRC))


def _assert_offset_bearing_iso(value: str, label: str) -> None:
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, f"{label}: must carry a tz offset"
    assert parsed.utcoffset() == timedelta(0), f"{label}: must be UTC"


def test_chat_message_timestamp_is_aware_utc_iso():
    from harness_meeting.chat import ChatMessage

    msg = ChatMessage(meetingId="m-1", userId="u-1", content="hello")
    _assert_offset_bearing_iso(msg.timestamp, "ChatMessage.timestamp")


def test_meeting_created_at_is_aware_utc_iso():
    from harness_meeting.service import Meeting

    meeting = Meeting(projectId="p-1", title="standup")
    _assert_offset_bearing_iso(meeting.createdAt, "Meeting.createdAt")


def test_prompt_created_at_is_aware_utc_iso():
    from harness_meeting.prompt_store import Prompt

    prompt = Prompt(meetingId="m-1", content="fix the flake", createdBy="u-1")
    _assert_offset_bearing_iso(prompt.createdAt, "Prompt.createdAt")


def test_summary_created_at_is_aware_utc_iso():
    from harness_meeting.summary_service import MeetingSummary

    summary = MeetingSummary(meetingId="m-1", content="as discussed")
    _assert_offset_bearing_iso(summary.createdAt, "MeetingSummary.createdAt")
