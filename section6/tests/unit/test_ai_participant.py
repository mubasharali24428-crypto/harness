"""Unit tests for AI participant rules engine."""

from harness_meeting.ai_participant import AIParticipant


class TestAIInvocation:
    def test_at_ai_prefix_invokes(self):
        assert AIParticipant.is_ai_invoked("@ai what is this?") is True

    def test_at_ai_uppercase_invokes(self):
        assert AIParticipant.is_ai_invoked("@AI explain JWT") is True

    def test_slash_ask_ai_invokes(self):
        assert AIParticipant.is_ai_invoked("/ask-ai how does this work?") is True

    def test_slash_research_invokes(self):
        assert AIParticipant.is_ai_invoked("/research OAuth2 best practices") is True

    def test_slash_summary_invokes(self):
        assert AIParticipant.is_ai_invoked("/summary") is True

    def test_slash_brief_invokes(self):
        assert AIParticipant.is_ai_invoked("/brief") is True

    def test_normal_message_does_not_invoke(self):
        assert AIParticipant.is_ai_invoked("I think we should refactor") is False

    def test_mentioning_ai_without_prefix_does_not_invoke(self):
        assert AIParticipant.is_ai_invoked("the AI should help us") is False

    def test_empty_message_does_not_invoke(self):
        assert AIParticipant.is_ai_invoked("") is False


class TestElaboration:
    def test_elaborate_keyword(self):
        assert AIParticipant.should_elaborate("elaborate on that") is True

    def test_explain_more_keyword(self):
        assert AIParticipant.should_elaborate("can you explain more?") is True

    def test_go_deeper_keyword(self):
        assert AIParticipant.should_elaborate("go deeper into the architecture") is True

    def test_normal_question_no_elaborate(self):
        assert AIParticipant.should_elaborate("what is JWT?") is False


class TestCommandExtraction:
    def test_at_ai_question(self):
        cmd, args = AIParticipant.extract_ai_command("@ai what is JWT?")
        assert cmd == "ask"
        assert args == "what is JWT?"

    def test_research_command(self):
        cmd, args = AIParticipant.extract_ai_command("/research OAuth2 best practices")
        assert cmd == "research"
        assert args == "OAuth2 best practices"

    def test_summary_command(self):
        cmd, args = AIParticipant.extract_ai_command("/summary")
        assert cmd == "summary"
        assert args == ""

    def test_brief_command(self):
        cmd, args = AIParticipant.extract_ai_command("/brief")
        assert cmd == "brief"
        assert args == ""

    def test_unknown_command(self):
        cmd, args = AIParticipant.extract_ai_command("random text")
        assert cmd == "unknown"


class TestResponseLengthCap:
    def test_short_response_unchanged(self):
        text = "This is a short response."
        assert AIParticipant.cap_response_length(text) == text

    def test_long_response_capped(self):
        words = ["word"] * 200
        text = " ".join(words)
        capped = AIParticipant.cap_response_length(text)
        assert capped.endswith("...")
        assert len(capped.split()) <= 151  # 150 words + "..."

    def test_elaborate_allows_longer(self):
        words = ["word"] * 300
        text = " ".join(words)
        capped = AIParticipant.cap_response_length(text, elaborate=True)
        assert len(capped.split()) <= 501
