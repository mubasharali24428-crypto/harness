"""AI Participant Rules Engine — server-side enforcement."""

import re

AI_INVOKE_PATTERN = re.compile(
    r"(^@ai\b|/ask-ai\b|^/research\b|^/summary\b|^/brief\b)", re.IGNORECASE
)
ELABORATE_PATTERN = re.compile(
    r"\b(elaborate|explain more|go deeper|more detail|expand on)\b",
    re.IGNORECASE,
)

DEFAULT_MAX_WORDS = 150
ELABORATE_MAX_WORDS = 500


class AIParticipant:
    """Server-side AI participant that is muted by default.

    Rules (from brief §76-84):
    - Muted by default; speaks only when explicitly invoked.
    - Never interrupts.
    - Answers precisely first; elaborates only on request.
    - Avoids long default answers.
    """

    @staticmethod
    def is_ai_invoked(message_content: str) -> bool:
        """Returns True only if the message explicitly invokes the AI."""
        return bool(AI_INVOKE_PATTERN.search(message_content))

    @staticmethod
    def should_elaborate(message_content: str) -> bool:
        """Returns True only if user explicitly asks for elaboration."""
        return bool(ELABORATE_PATTERN.search(message_content))

    @staticmethod
    def extract_ai_command(message_content: str) -> tuple[str, str]:
        """Extracts an AI command and its arguments from a message.

        Supported commands:
        - @ai <question>        -> ("ask", "<question>")
        - /ask-ai <question>    -> ("ask", "<question>")
        - /research <topic>     -> ("research", "<topic>")
        - /summary              -> ("summary", "")
        - /brief                -> ("brief", "")

        Returns ("unknown", "") for unrecognised commands.
        """
        content = message_content.strip()

        if content.startswith("/research"):
            args = content[len("/research") :].strip()
            return ("research", args)
        if content.startswith("/summary"):
            return ("summary", "")
        if content.startswith("/brief"):
            return ("brief", "")
        if content.lower().startswith("@ai"):
            args = content[3:].strip()
            return ("ask", args)
        if content.lower().startswith("/ask-ai"):
            args = content[len("/ask-ai") :].strip()
            return ("ask", args)

        return ("unknown", "")

    @staticmethod
    def cap_response_length(text: str, elaborate: bool = False) -> str:
        """Enforces response length limits."""
        max_words = ELABORATE_MAX_WORDS if elaborate else DEFAULT_MAX_WORDS
        words = text.split()
        if len(words) <= max_words:
            return text
        return " ".join(words[:max_words]) + "..."
