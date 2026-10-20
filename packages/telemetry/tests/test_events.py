"""Parsing, scrubbing and cost rows."""

from __future__ import annotations

import pytest

from harness_telemetry import parse_cost, parse_event, parse_stream, scrub_text


def test_parse_event_requires_run_attribution():
    with pytest.raises(ValueError):
        parse_event({"type": "plan_proposed", "ts": "2026-10-17T00:00:00+00:00", "seq": 1,
                     "projectId": "p"})


def test_parse_stream_sorts_by_run_then_seq():
    raws = [
        {"type": "b", "ts": "2026-10-17T00:00:02+00:00", "seq": 2, "projectId": "p", "runId": "r1"},
        {"type": "a", "ts": "2026-10-17T00:00:01+00:00", "seq": 1, "projectId": "p", "runId": "r1"},
        {"type": "c", "ts": "2026-10-17T00:00:00+00:00", "seq": 1, "projectId": "p", "runId": "r0"},
    ]
    events = parse_stream(raws)
    assert [(e.runId, e.seq) for e in events] == [("r0", 1), ("r1", 1), ("r1", 2)]


def test_scrub_text_redacts_secrets_then_emails_and_is_idempotent():
    raw = (
        "token = ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789 "
        "and xoxb-1234567890-abcdefghij for alice@example.com "
        "password: hunter2hunter2 bearer AbCdEfGhIjKlMnOpQrStUv"
    )
    once = scrub_text(raw)
    assert "ghp_" not in once and "xoxb-" not in once and "alice@" not in once
    assert "hunter2hunter2" not in once
    assert "[EMAIL]" in once and "[REDACTED]" in once
    assert scrub_text(once) == once


def test_parse_cost_decimal_and_defaults():
    entry = parse_cost({"runId": "r1", "costUsd": "1.25"})
    assert str(entry.costUsd) == "1.25"
    assert entry.inputTokens == 0 and entry.model == "unknown"
