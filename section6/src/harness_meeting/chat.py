"""Chat room and message model for multi-human text chat."""

import sys
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

# M13-H4b: the single source of "now" is harness_identity.timeutil (aware
# UTC law). The section1 src tree is appended exactly like api.py's M12-G3
# limits seam already does; module level so imports stay order-independent.
_ROOT = Path(__file__).resolve().parents[3]
if (_ROOT / "section1" / "src") not in sys.path:
    sys.path.append(str(_ROOT / "section1" / "src"))

from harness_identity.timeutil import now_utc  # noqa: E402


class ChatMessage(BaseModel):
    """A single message in a meeting chat."""

    id: str = Field(default_factory=lambda: f"msg-{uuid.uuid4().hex[:8]}")
    meetingId: str
    userId: str
    content: str
    timestamp: str = Field(default_factory=lambda: now_utc().isoformat())
    isAiGenerated: bool = False


class MeetingRoom:
    """In-memory chat room managing message history per meeting."""

    def __init__(self, meeting_id: str) -> None:
        self.meeting_id = meeting_id
        self.messages: list[ChatMessage] = []

    def post_message(
        self,
        user_id: str,
        content: str,
        is_ai: bool = False,
    ) -> ChatMessage:
        msg = ChatMessage(
            meetingId=self.meeting_id,
            userId=user_id,
            content=content,
            isAiGenerated=is_ai,
        )
        self.messages.append(msg)
        return msg

    def get_history(self) -> list[ChatMessage]:
        return list(self.messages)

    def get_transcript_text(self) -> str:
        """Returns the full chat transcript as plain text."""
        lines = []
        for m in self.messages:
            prefix = "[AI]" if m.isAiGenerated else f"[{m.userId}]"
            lines.append(f"{prefix}: {m.content}")
        return "\n".join(lines)
