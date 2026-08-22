"""Meeting summary generation and storage."""

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field


class MeetingSummary(BaseModel):
    """A stored meeting summary."""

    id: str = Field(default_factory=lambda: f"summary-{uuid.uuid4().hex[:8]}")
    meetingId: str
    content: str
    createdAt: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    format: str = "markdown"


class SummaryService:
    """Generates and stores meeting summaries."""

    def __init__(self) -> None:
        self.summaries: dict[str, list[MeetingSummary]] = {}

    def generate_summary(
        self,
        meeting_id: str,
        transcript: str,
    ) -> MeetingSummary:
        """Generates a meeting summary from the chat transcript.

        In production this would use Section 3 callModel.
        For now, produces a structured markdown summary.
        """
        lines = transcript.strip().splitlines()
        participants = set()
        for line in lines:
            if line.startswith("[") and "]:" in line:
                name = line.split("]")[0].lstrip("[")
                if name != "AI":
                    participants.add(name)

        summary_text = (
            f"# Meeting Summary\n\n"
            f"**Participants:** {', '.join(sorted(participants)) or 'N/A'}\n\n"
            f"**Total Messages:** {len(lines)}\n\n"
            f"## Discussion Points\n\n"
        )

        for i, line in enumerate(lines[:10], 1):
            summary_text += f"{i}. {line}\n"

        if len(lines) > 10:
            summary_text += f"\n... and {len(lines) - 10} more messages.\n"

        summary = MeetingSummary(
            meetingId=meeting_id,
            content=summary_text,
        )

        if meeting_id not in self.summaries:
            self.summaries[meeting_id] = []
        self.summaries[meeting_id].append(summary)

        return summary

    def get_summaries(self, meeting_id: str) -> list[MeetingSummary]:
        return self.summaries.get(meeting_id, [])

    def export_markdown(self, summary: MeetingSummary) -> str:
        """Exports summary as markdown string (for PDF/DOC conversion)."""
        return summary.content
