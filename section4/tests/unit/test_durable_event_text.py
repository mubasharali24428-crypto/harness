"""W4 (leak drill finding): a tool result must never be dropped for its bytes.

Found live: `cat /proc/self/environ` produced a NUL-separated dump, PostgreSQL
rejected the JSONB payload (`\\u0000 cannot be converted to text`), and the
whole `tool_result` row disappeared — the transcript showed a tool call with no
result, which reads as "the tool never answered". A search over stored rows
would then also miss anything that result contained. The event text is made
durable at the emission point instead.
"""

from __future__ import annotations

from harness_orchestrator.service import durable_event_text


def test_nul_bytes_are_replaced_not_dropped():
    assert durable_event_text("HOME=/tmp\x00PATH=/bin\x00") == "HOME=/tmp\ufffdPATH=/bin\ufffd"


def test_the_rest_of_the_text_is_preserved_verbatim():
    original = "line one\nline two\ttab \u00e9 \U0001f525 end"
    assert durable_event_text(original) == original


def test_lone_surrogates_are_normalised_so_the_payload_encodes():
    text = durable_event_text("broken \udcff pair")
    assert "broken" in text and "pair" in text
    text.encode("utf-8")  # would raise on a lone surrogate


def test_none_becomes_empty_text_like_the_other_fields():
    assert durable_event_text(None) == ""
    assert durable_event_text(0) == "0"
