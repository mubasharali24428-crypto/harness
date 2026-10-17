"""A4 fix — execution prompt assembly tests.

The anchor assertions for review finding A4: the assembled engine input must
carry the goal, the project rules block and the retrieved context, with
untrusted framing + provenance, BOUNDED, and byte-identical to plan.goal on
bare runs (no rules, no context) so pre-A4 behavior is preserved exactly.
"""

from harness_orchestrator.execution_prompt import (
    FRAMING_VERSION,
    RETRIEVED_CLOSE,
    RETRIEVED_OPEN,
    SYSTEM_CONTEXT_HEADER,
    TRUNCATION_MARKER,
    UNTRUSTED_CONTEXT_HEADER,
    UNTRUSTED_CONTEXT_NOTE,
    build_execution_prompt,
    resolve_execution_prompt_max_tokens,
)
from harness_orchestrator.rules_loader import build_rules_block

GOAL = "Add retry with backoff to the upload client."
RULES = build_rules_block("Always use pnpm.\nNo default exports.")
CONTEXT = (
    "@@ src/net/upload.py:40-58 (python)\n"
    "def upload(path):\n    return _post(path)"
)


class TestLegacyByteIdentity:
    def test_bare_goal_passes_through_exactly(self):
        result = build_execution_prompt(goal=GOAL)
        assert result.text == GOAL
        assert result.frames is False
        assert result.context_truncated is False

    def test_whitespace_only_blocks_do_not_frame(self):
        result = build_execution_prompt(goal=GOAL, rules_block="  \n", context_block=" ")
        assert result.text == GOAL
        assert result.frames is False

    def test_goal_is_never_altered_when_framing(self):
        result = build_execution_prompt(goal=GOAL, rules_block=RULES, context_block=CONTEXT)
        assert result.text.startswith(GOAL)


class TestFraming:
    def test_rules_ride_under_system_context_header(self):
        result = build_execution_prompt(goal=GOAL, rules_block=RULES)
        assert result.frames is True
        assert SYSTEM_CONTEXT_HEADER in result.text
        assert "Always use pnpm." in result.text
        assert GOAL in result.text

    def test_context_is_explicitly_untrusted_data(self):
        result = build_execution_prompt(goal=GOAL, context_block=CONTEXT)
        assert UNTRUSTED_CONTEXT_HEADER in result.text
        assert UNTRUSTED_CONTEXT_NOTE in result.text
        assert RETRIEVED_OPEN in result.text and RETRIEVED_CLOSE in result.text
        assert "upload.py" in result.text and "_post(path)" in result.text
        # the data never gets command framing
        assert result.text.index(RETRIEVED_OPEN) < result.text.index(RETRIEVED_CLOSE)

    def test_both_blocks_present_in_order(self):
        result = build_execution_prompt(
            goal=GOAL, rules_block=RULES, context_block=CONTEXT
        )
        text = result.text
        assert text.index(GOAL) < text.index(SYSTEM_CONTEXT_HEADER)
        assert text.index(SYSTEM_CONTEXT_HEADER) < text.index(UNTRUSTED_CONTEXT_HEADER)


class TestProvenance:
    def test_footer_cites_project_paths_and_drops(self):
        result = build_execution_prompt(
            goal=GOAL,
            context_block=CONTEXT,
            provenance={
                "projectId": "p-x1",
                "paths": ["src/net/upload.py", "src/net/http.py"],
                "droppedChunks": 3,
                "indexedAt": "2026-10-17T00:00:00Z",
            },
        )
        text = result.text
        assert "project: p-x1" in text
        assert "src/net/upload.py" in text and "src/net/http.py" in text
        assert "dropped for budget at resolve time: 3" in text
        assert "2026-10-17T00:00:00Z" in text
        assert FRAMING_VERSION in text

    def test_footer_absent_when_no_provenance(self):
        result = build_execution_prompt(goal=GOAL, rules_block=RULES)
        assert "PROVENANCE:" not in result.text


class TestBounds:
    def test_overflow_truncates_context_but_never_goal_or_rules(self):
        fat_context = "x" * 40_000
        result = build_execution_prompt(
            goal=GOAL,
            rules_block=RULES,
            context_block=fat_context,
            max_tokens=600,
        )
        assert result.context_truncated is True
        assert TRUNCATION_MARKER in result.text
        assert result.text.startswith(GOAL)  # goal intact
        assert "Always use pnpm." in result.text  # rules intact
        assert fat_context not in result.text  # fat data is what got cut
        assert result.tokens_estimated <= 600

    def test_fitting_context_not_marked_truncated(self):
        result = build_execution_prompt(
            goal=GOAL, context_block=CONTEXT, max_tokens=4000
        )
        assert result.context_truncated is False
        assert TRUNCATION_MARKER not in result.text

    def test_env_override_respected(self):
        assert (
            resolve_execution_prompt_max_tokens(
                {"CONTEXT_MAX_TOKENS": "1000", "EXECUTION_PROMPT_MAX_TOKENS": "1234"}
            )
            == 1234
        )
        assert (
            resolve_execution_prompt_max_tokens({"CONTEXT_MAX_TOKENS": "1000"}) == 2000
        )
        assert resolve_execution_prompt_max_tokens({"EXECUTION_PROMPT_MAX_TOKENS": "junk"}) > 0


class TestMeta:
    def test_meta_is_json_safe_and_honest(self):
        import json

        meta = build_execution_prompt(
            goal=GOAL, rules_block=RULES, context_block=CONTEXT
        ).meta()
        json.dumps(meta)
        assert meta["framed"] is True
        assert meta["rulesBytes"] > 0
        assert meta["contextBytes"] > 0
        assert meta["contextTruncated"] is False

    def test_bare_meta_reports_zero_frames(self):
        meta = build_execution_prompt(goal=GOAL).meta()
        assert meta["framed"] is False
        assert meta["rulesBytes"] == 0 and meta["contextBytes"] == 0
