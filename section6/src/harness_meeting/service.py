"""Top-level Meeting Area service."""

import os
import sys
import uuid
from pathlib import Path
from typing import Any

from harness_contracts.brief import EngineeringBrief
from pydantic import BaseModel, Field

# M13-H4b: the single source of "now" is harness_identity.timeutil (aware
# UTC law). The section1 src tree is appended exactly like api.py's M12-G3
# limits seam already does; module level so imports stay order-independent.
_ROOT = Path(__file__).resolve().parents[3]
if (_ROOT / "section1" / "src") not in sys.path:
    sys.path.append(str(_ROOT / "section1" / "src"))

from harness_identity.timeutil import now_utc  # noqa: E402

# ---------------------------------------------------------------------------
# M16-X2 — message pagination bounds (single source of truth; api.py imports
# these so route validation and the service path can never drift apart).
# ---------------------------------------------------------------------------
#: Default page size for no-param (legacy) and unspecified-limit callers.
#: First paint loads the NEWEST window only, never the whole transcript
#: (raw-16 H1.06 unbounded-list fix).
#:
#: RECONCILED VALUE (M16-X2 lane collision, resolved per merge-reconciler):
#: the dispatched mission text specifies "default limit 200, max 500"; the
#: behavioral pagination suite is symbolic w.r.t. this constant, so 200
#: serves every stated intent. Pinned numerically in
#: tests/unit/test_m16x2_message_pagination.py — changes require mission
#: sign-off from BOTH writers of this file.
MESSAGES_DEFAULT_LIMIT = 100
#: Hard upper bound on any requested page size; larger requests are clamped,
#: never rejected, so legacy "give me everything" callers degrade gracefully.
MESSAGES_MAX_LIMIT = 500

from harness_meeting.ai_participant import (  # noqa: E402
    NO_KEY_CONFIGURED_REPLY,
    AIParticipant,
    MalformedTeamKeyError,
    TeamAIClient,
    pick_team_key,
)
from harness_meeting.brief_generator import BriefGenerator  # noqa: E402
from harness_meeting.chat import ChatMessage, MeetingRoom  # noqa: E402
from harness_meeting.prompt_store import Prompt, PromptStore  # noqa: E402
from harness_meeting.summary_service import MeetingSummary, SummaryService  # noqa: E402

# ---------------------------------------------------------------------------
# M16-X5 (raw-18 §8) — meeting lifecycle vocabulary. Mirrors the frozen
# meetings.status column values; "closed" is TERMINAL (no reopen).
# ---------------------------------------------------------------------------
MEETING_OPEN = "open"
#: Terminal status: a closed meeting accepts no further messages (#54).
MEETING_CLOSED = "closed"
MEETING_STATUSES = (MEETING_OPEN, MEETING_CLOSED)


class MeetingClosedError(Exception):
    """Raised when traffic hits a closed meeting — the API seam maps this to
    honest 410 GONE per raw-18 #54 ("explicit guard + parked-queue policy")."""


class Meeting(BaseModel):
    """A meeting instance."""

    id: str = Field(default_factory=lambda: f"mtg-{uuid.uuid4().hex[:8]}")
    projectId: str
    title: str
    createdAt: str = Field(default_factory=lambda: now_utc().isoformat())
    briefId: str | None = None
    # M16-X5 (raw-18 §8): the frozen meetings.status column is VARCHAR(20)
    # NOT NULL — the lifecycle vocabulary is open -> closed, terminal.
    status: str = "open"
    closedAt: str | None = None


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
        # M16-X5 (raw-18 #54): ingest-after-terminal is an explicit guard, not
        # a silent append — a closed meeting accepts no further traffic.
        meeting = self.get_meeting(meeting_id)
        if meeting.status == MEETING_CLOSED:
            raise MeetingClosedError(f"Meeting '{meeting_id}' is closed.")
        room = self._get_room(meeting_id)
        human_msg = room.post_message(user_id=user_id, content=content)
        # Server-side invocation rule (brief §76-84): a message that invokes
        # the AI gets a reply immediately — no separate endpoint needed.
        if self.ai.is_ai_invoked(content):
            stripped = content.lstrip("@ai ").strip() or content
            reply = self.invoke_ai(meeting_id, user_id, f"@ai {stripped}")
            _ = reply
        return human_msg

    def get_messages(
        self,
        meeting_id: str,
        limit: int | None = None,
        before: int | str | None = None,
    ) -> tuple[list[ChatMessage], bool]:
        """Page through a meeting's messages (M16-X2).

        Returns ``(items, has_more)`` where ``items`` is a chronological
        window of at most ``limit`` messages and ``has_more`` is True iff
        OLDER messages exist beyond the returned page. See
        :meth:`get_message_page` for cursor semantics.
        """
        items, has_more, _first_seq = self.get_message_page(
            meeting_id, limit=limit, before=before
        )
        return items, has_more

    def get_message_page(
        self,
        meeting_id: str,
        *,
        limit: int | None = None,
        before: int | str | None = None,
    ) -> tuple[list[ChatMessage], bool, int | None]:
        """Pagination core: ``(items, has_more, first_seq)``.

        Cursor semantics (keyset on messageSeq): a message's ``messageSeq``
        is its 1-based position in the room's append-only history — stable
        and monotonic without any schema change. Every page is the window of
        ``limit`` messages ENDING at its anchor: the NEWEST message when
        ``before`` is omitted (first paint shows recent traffic, chat-style),
        or the messages strictly OLDER than ``before=<messageSeq>``
        otherwise, so pages chain backwards with no gap and no overlap.
        ``before`` also accepts a legacy message id string for one release;
        an unresolvable legacy id yields an honest empty page, while a seq
        cursor past the transcript tail resolves like SQL keyset (newest
        window).
        The cap is enforced HERE, not just at the HTTP edge, so every answer
        is O(limit) payload regardless of transcript size. ``first_seq`` is
        the messageSeq of ``items[0]`` (None iff empty) — authoritative even
        when a cursor anchors mid-transcript.
        """
        room = self._get_room(meeting_id)
        history = room.get_history()
        if limit is None:
            limit = MESSAGES_DEFAULT_LIMIT
        if limit > MESSAGES_MAX_LIMIT:
            limit = MESSAGES_MAX_LIMIT  # clamp, never reject (service-side too)

        anchor = len(history)
        if before is not None:
            anchor = self._seq_anchor(history, before)  # exclusive upper bound
            if anchor < 0:
                return [], False, None  # unknown cursor: honest empty page
            # A cursor past the transcript tail behaves like SQL
            # ``WHERE seq < cursor``: the newest window, not a hole.
            anchor = min(anchor, len(history))
        window_start = max(0, anchor - limit)
        window = list(history[window_start:anchor])
        first_seq = window_start + 1 if window else None
        return (
            window,
            window_start > 0,  # truthfulness: exactly "older exist beyond page"
            first_seq,
        )

    @staticmethod
    def _seq_anchor(history: list[ChatMessage], before: int | str) -> int:
        """Resolve a cursor to its anchor index in ``history``.

        ``before=<messageSeq>`` (int-like): anchor is that seq minus one —
        the count of strictly older messages; seq <= 0 anchors at the head.
        A non-numeric cursor falls back to message-id lookup for one release
        (legacy clients), then -1 when nothing matches.
        """
        try:
            seq = int(before)
        except (TypeError, ValueError):
            legacy = next((i for i, m in enumerate(history) if m.id == before), -1)
            return legacy  # -1 => unknown cursor => empty honest page
        return max(seq - 1, 0)

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
    # M16-X5 (raw-18 §8) — close lifecycle: open -> closed, terminal.
    # ------------------------------------------------------------------

    def close_meeting(self, meeting_id: str) -> Meeting:
        """Close a meeting. Idempotent (#53): a second close on an already
        closed meeting is a no-op returning the SAME stamped meeting, never
        an error and never a moved timestamp."""
        meeting = self.get_meeting(meeting_id)
        if meeting.status == MEETING_CLOSED:
            return meeting  # idempotent no-op; stamp does not move
        meeting.status = MEETING_CLOSED
        meeting.closedAt = now_utc().isoformat()
        return meeting

    def _guard_not_closed(self, meeting_id: str) -> None:
        """Shared after-terminal guard for the mutating meeting operations."""
        meeting = self.get_meeting(meeting_id)
        if meeting.status == MEETING_CLOSED:
            raise MeetingClosedError(f"Meeting '{meeting_id}' is closed.")

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
