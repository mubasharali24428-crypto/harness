"""Top-level Meeting Area service."""

import os
import uuid
from datetime import UTC, datetime
from typing import Any

from harness_contracts.brief import EngineeringBrief
from pydantic import BaseModel, Field

from harness_meeting.ai_participant import (
    NO_KEY_CONFIGURED_REPLY,
    AIParticipant,
    MalformedTeamKeyError,
    TeamAIClient,
    pick_team_key,
)
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

    def __init__(
        self,
        team_key_store: Any | None = None,
        vault: Any | None = None,
        models_base_url: str | None = None,
        ai_provider: str = "openai",
    ) -> None:
        self.meetings: dict[str, Meeting] = {}
        self.rooms: dict[str, MeetingRoom] = {}
        self.ai = AIParticipant()
        self.brief_gen = BriefGenerator()
        self.summary_svc = SummaryService()
        self.prompt_store = PromptStore()
        # H-002 wiring (ATLAS sets these at boot; see deploy/runners/services.py):
        #   team_key_store — async session factory over S1's frozen api_keys store
        #   vault          — harness_contracts.KeyVault (decrypt in-process ONLY)
        #   models_base_url — Section 3 model router base URL
        self.team_key_store = team_key_store
        # Vault defaults to the contracts KeyVault under the dev master key so a
        # store-wired-but-vault-less construction still exercises real decrypt;
        # ATLAS wiring passes the production vault explicitly.
        if vault is not None:
            self.vault = vault
        else:
            from harness_contracts.security import KeyVault

            self.vault = KeyVault(b"dev-master-key-change-me-in-prod!")
        self.models_base_url = (
            models_base_url
            or os.environ.get("HARNESS_MODELS_URL")
            or "http://models:8003"
        )
        self.ai_provider = ai_provider
        # Injectable for tests; lazily built per-call otherwise (key can change).
        self.meeting_ai_client: TeamAIClient | None = None

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
        team_id: str | None = None,
    ) -> ChatMessage | None:
        """Process a message that may invoke the AI participant.

        H-002: with a stored team key, the reply is a REAL model completion
        via Section 3 (key stays server-side). Without one, the rules engine
        answers as an EXPLICIT fallback and says so honestly. Returns the AI
        response as a ChatMessage, or None if AI was not invoked.
        """
        if not self.ai.is_ai_invoked(message_content):
            return None

        room = self._get_room(meeting_id)
        command, args = self.ai.extract_ai_command(message_content)
        elaborate = self.ai.should_elaborate(message_content)

        # Deterministic meeting-tooling paths stay deterministic: /summary and
        # /brief are structured artifacts, not free-form completions.
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
        elif command in ("ask", "research", "unknown"):
            response_text = self._llm_or_fallback(
                meeting_id=meeting_id,
                team_id=team_id,
                command=command,
                args=args,
                transcript=room.get_transcript_text(),
            )
        else:  # pragma: no cover - extract_ai_command covers every branch
            response_text = (
                "I didn't understand that command. Try @ai, /research, /summary, or /brief."
            )

        response_text = self.ai.cap_response_length(response_text, elaborate=elaborate)
        return room.post_message(user_id="ai-participant", content=response_text, is_ai=True)

    # ------------------------------------------------------------------
    # H-002: real LLM call with honest fallback ladder
    # ------------------------------------------------------------------

    def _llm_or_fallback(
        self,
        meeting_id: str,
        team_id: str | None,
        command: str,
        args: str,
        transcript: str | None,
    ) -> str:
        """Try the real model; fall back honestly at each rung.

        Rungs:
          1. Team key exists + router answers   -> genuine completion.
          2. Router/key trouble                 -> honest error + rules answer.
          3. No stored key                      -> explicit no-key notice +
                                                   rules answer (never pretends).
          4. Key unreadable (malformed/revoked vault state) -> honest error.
        """
        client = self._resolve_client(team_id)

        if isinstance(client, TeamAIClient):
            question, context = self._compose_prompt(command, args, transcript)
            model_text = client.complete(question, context=context)
            if command == "research":
                model_text = f"Research results for '{args}': {model_text}"
            if self._is_honest_error(model_text):
                # Transport-level trouble: keep the rules-engine substance so
                # the user still gets a real answer, clearly labeled.
                rules_answer = self._rules_engine_reply(command, args)
                if rules_answer:
                    return f"{model_text} ({rules_answer})"
            return model_text

        if client:  # resolver produced honest error copy (e.g. malformed key)
            return str(client)

        # No stored key => rules engine, but HONESTLY labeled (never pretends).
        rules_answer = self._rules_engine_reply(command, args)
        if rules_answer:
            return f"[{NO_KEY_CONFIGURED_REPLY}] {rules_answer}"
        return NO_KEY_CONFIGURED_REPLY

    def _resolve_client(self, team_id: str | None) -> TeamAIClient | str | None:
        """Build a TeamAIClient from the team's stored key, or return an
        honest-fallback sentinel string when no usable key exists."""
        if self.meeting_ai_client is not None:
            return self.meeting_ai_client  # test/injected path
        if self.team_key_store is None or self.vault is None:
            return ""  # nothing wired => no-key fallback
        try:
            import asyncio as _asyncio

            secret = _asyncio.run(
                pick_team_key(self.team_key_store, self.vault, self.ai_provider, team_id)
            )
        except MalformedTeamKeyError:
            return (
                "Your team's stored AI key could not be used "
                "(it looks malformed or was encrypted under a different master key). "
                "Re-add it in Settings → API keys."
            )
        if secret is None:
            return ""
        return TeamAIClient(
            models_base_url=self.models_base_url,
            api_key=secret,
            agent_id=f"ai-participant{f'@{team_id}' if team_id else ''}",
        )

    def _compose_prompt(
        self, command: str, args: str, transcript: str | None
    ) -> tuple[str, str]:
        """Question + system context for the model call."""
        context = (
            "You are the ai-participant in a team meeting. You speak only when "
            "invoked, answer precisely first, and never pad your replies."
        )
        if transcript:
            recent = transcript[-2000:]
            context += f"\n\nRecent meeting transcript (may be partial):\n{recent}"
        if command == "research":
            question = f"Give a precise engineering research brief on: {args}"
        else:
            question = args or "Summarize where the discussion stands and what to decide next."
        return question, context

    def _rules_engine_reply(self, command: str, args: str) -> str | None:
        """The original regex-engine answer, now ONLY a labeled fallback."""
        if command == "research":
            return (
                f"Based on available knowledge, {args} is a well-documented topic. "
                "Key considerations include implementation patterns and best practices."
            )
        if command == "unknown":
            return "I didn't understand that command. Try @ai, /research, /summary, or /brief."
        if args:
            return f"Regarding your question: {args}"
        return None

    @staticmethod
    def _is_honest_error(text: str) -> bool:
        """True when a reply is one of TeamAIClient's honest error copies
        (matched by their stable lead phrases), i.e. NOT genuine model text."""
        leads = (
            "The AI service is temporarily unavailable",
            "The AI provider is rate limiting",
            "The team's stored AI key was rejected",
            "The AI service didn't answer in time",
        )
        return any(text.startswith(lead) for lead in leads)

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
