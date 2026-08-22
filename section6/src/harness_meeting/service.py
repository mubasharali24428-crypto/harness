"""Top-level Meeting Area service."""

import uuid
from datetime import UTC, datetime

from harness_contracts.brief import EngineeringBrief
from pydantic import BaseModel, Field

from harness_meeting.ai_participant import AIParticipant
from harness_meeting.brief_generator import BriefGenerator
from harness_meeting.chat import ChatMessage, MeetingRoom
from harness_meeting.prompt_store import Prompt, PromptStore
from harness_meeting.summary_service import MeetingSummary, SummaryService


class Meeting(BaseModel):
    """A meeting instance."""

    id: str = Field(default_factory=lambda: f"mtg-{uuid.uuid4().hex[:8]}")
    projectId: str
    title: str
    createdAt: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    briefId: str | None = None


class MeetingService:
    """Section 6 Meeting Area top-level service."""

    def __init__(self) -> None:
        self.meetings: dict[str, Meeting] = {}
        self.rooms: dict[str, MeetingRoom] = {}
        self.ai = AIParticipant()
        self.brief_gen = BriefGenerator()
        self.summary_svc = SummaryService()
        self.prompt_store = PromptStore()

    def create_meeting(self, project_id: str, title: str) -> Meeting:
        meeting = Meeting(projectId=project_id, title=title)
        self.meetings[meeting.id] = meeting
        self.rooms[meeting.id] = MeetingRoom(meeting.id)
        return meeting

    def get_meeting(self, meeting_id: str) -> Meeting:
        if meeting_id not in self.meetings:
            raise KeyError(f"Meeting '{meeting_id}' not found.")
        return self.meetings[meeting_id]

    def post_message(
        self,
        meeting_id: str,
        user_id: str,
        content: str,
    ) -> ChatMessage:
        room = self._get_room(meeting_id)
        human_msg = room.post_message(user_id=user_id, content=content)
        # Server-side invocation rule (brief §76-84): a message that invokes
        # the AI gets a reply immediately — no separate endpoint needed.
        if self.ai.is_ai_invoked(content):
            stripped = content.lstrip("@ai ").strip() or content
            reply = self.invoke_ai(meeting_id, user_id, f"@ai {stripped}")
            _ = reply
        return human_msg

    def get_messages(self, meeting_id: str) -> list[ChatMessage]:
        room = self._get_room(meeting_id)
        return room.get_history()

    def invoke_ai(
        self,
        meeting_id: str,
        user_id: str,
        message_content: str,
    ) -> ChatMessage | None:
        """Process a message that may invoke the AI participant.

        Returns the AI response as a ChatMessage, or None if AI was not invoked.
        """
        if not self.ai.is_ai_invoked(message_content):
            return None

        room = self._get_room(meeting_id)
        command, args = self.ai.extract_ai_command(message_content)
        elaborate = self.ai.should_elaborate(message_content)

        if command == "summary":
            summary = self.summary_svc.generate_summary(
                meeting_id=meeting_id,
                transcript=room.get_transcript_text(),
            )
            response_text = f"Meeting summary generated:\n{summary.content}"
        elif command == "brief":
            meeting = self.get_meeting(meeting_id)
            brief = self.brief_gen.generate_brief(
                meeting_id=meeting_id,
                project_id=meeting.projectId,
                transcript=room.get_transcript_text(),
            )
            self.meetings[meeting_id].briefId = brief.id
            response_text = (
                f"Engineering brief generated (DRAFT):\n"
                f"- Requirements: {len(brief.requirements)}\n"
                f"- Constraints: {len(brief.constraints)}\n"
                f"- Acceptance Criteria: {len(brief.acceptanceCriteria)}\n"
                f"- Risks: {len(brief.risks)}\n"
                f"- Open Questions: {len(brief.openQuestions)}"
            )
        elif command == "research":
            response_text = (
                f"Research results for '{args}': "
                f"Based on available knowledge, {args} is a well-documented topic. "
                f"Key considerations include implementation patterns and best practices."
            )
        elif command == "ask":
            response_text = f"Regarding your question: {args}"
        else:
            response_text = (
                "I didn't understand that command. Try @ai, /research, /summary, or /brief."
            )

        response_text = self.ai.cap_response_length(response_text, elaborate=elaborate)
        return room.post_message(user_id="ai-participant", content=response_text, is_ai=True)

    def generate_brief(self, meeting_id: str) -> EngineeringBrief:
        room = self._get_room(meeting_id)
        meeting = self.get_meeting(meeting_id)
        brief = self.brief_gen.generate_brief(
            meeting_id=meeting_id,
            project_id=meeting.projectId,
            transcript=room.get_transcript_text(),
        )
        self.meetings[meeting_id].briefId = brief.id
        return brief

    def approve_brief(
        self,
        meeting_id: str,
        brief_id: str,
        user_id: str,
    ) -> EngineeringBrief:
        return self.brief_gen.approve_brief(brief_id, user_id)

    def reject_brief(
        self,
        meeting_id: str,
        brief_id: str,
        user_id: str,
    ) -> EngineeringBrief:
        return self.brief_gen.reject_brief(brief_id, user_id)

    def get_brief(self, meeting_id: str) -> EngineeringBrief | None:
        meeting = self.get_meeting(meeting_id)
        if meeting.briefId:
            return self.brief_gen.get_brief(meeting.briefId)
        return None

    def generate_summary(self, meeting_id: str) -> MeetingSummary:
        room = self._get_room(meeting_id)
        return self.summary_svc.generate_summary(
            meeting_id=meeting_id,
            transcript=room.get_transcript_text(),
        )

    def create_prompt(
        self,
        meeting_id: str,
        user_id: str,
        content: str,
    ) -> Prompt:
        return self.prompt_store.create_prompt(meeting_id, user_id, content)

    def list_prompts(self, meeting_id: str) -> list[Prompt]:
        return self.prompt_store.list_versions(meeting_id)

    def _get_room(self, meeting_id: str) -> MeetingRoom:
        if meeting_id not in self.rooms:
            raise KeyError(f"Meeting room '{meeting_id}' not found.")
        return self.rooms[meeting_id]
