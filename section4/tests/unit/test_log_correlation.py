"""Ops acceptance (part 28): ids reach ordinary log lines, not just the access log."""

from __future__ import annotations

import json
import logging

from harness_orchestrator.logging_utils import JsonFormatter, request_id_scope


def _line(**extra) -> dict:
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "run started", None, None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return json.loads(JsonFormatter().format(rec))


def test_request_id_inherited_from_contextvar_on_plain_log_line():
    with request_id_scope("rid-123"):
        assert _line()["requestId"] == "rid-123"
    assert _line()["requestId"] is None


def test_explicit_request_id_wins_and_run_action_ids_emitted():
    with request_id_scope("ctx"):
        out = _line(request_id="explicit", run_id="run-9", action_id="act-4")
    assert out["requestId"] == "explicit"
    assert out["runId"] == "run-9" and out["actionId"] == "act-4"
