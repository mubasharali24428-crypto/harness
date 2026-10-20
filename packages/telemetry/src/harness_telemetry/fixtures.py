"""Known event fixtures: failures, rework, abandonment, model-claims-vs-reality.

Fixed timestamps (UTC, mid-October 2026) so every metric has an exactly
computable expected value. Two teams, three projects. Scenario coverage:
accepted-after-rework, claimed-completion-rejected-by-review, never-approved,
blocked, in-flight, rejected-without-recovery.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

AS_OF = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)

PROJECT_TEAM = {"proj-a": "team-a", "proj-b": "team-b"}


def _ev(
    run: str, project: str, seq: int, type_: str, ts: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = {"runId": run}
    body.update(payload or {})
    return {
        "type": type_,
        "payload": body,
        "ts": ts,
        "seq": seq,
        "projectId": project,
        "runId": run,
    }


def events() -> list[dict[str, Any]]:
    e: list[dict[str, Any]] = []

    # run-a1: clean success -> ACCEPTED (35 min to first useful change)
    e += [
        _ev("run-a1", "proj-a", 1, "plan_proposed", "2026-10-13T09:00:00+00:00",
            {"goal": "Add median word length to textstats"}),
        _ev("run-a1", "proj-a", 2, "plan_approved", "2026-10-13T09:05:00+00:00"),
        _ev("run-a1", "proj-a", 3, "execution_started", "2026-10-13T09:06:00+00:00"),
        _ev("run-a1", "proj-a", 4, "engine_completed", "2026-10-13T09:30:00+00:00",
            {"summary": "Implemented median_word_length; pytest green, ruff clean."}),
        _ev("run-a1", "proj-a", 5, "done_signal_received", "2026-10-13T09:31:00+00:00"),
        _ev("run-a1", "proj-a", 6, "review_passed", "2026-10-13T09:33:00+00:00",
            {"findings": {"passed": True, "syntaxOk": True, "conflictsDetected": False,
                          "briefAligned": True, "issues": []}}),
        _ev("run-a1", "proj-a", 7, "pr_opened", "2026-10-13T09:35:00+00:00",
            {"prId": "pr-101", "url": "https://github.com/org/repo/pull/101"}),
    ]

    # run-a2: engine failed once, review rejected once, then accepted (85 min).
    # Two execution attempts = rework; cost is the highest of the batch.
    e += [
        _ev("run-a2", "proj-a", 1, "plan_proposed", "2026-10-13T10:00:00+00:00",
            {"goal": "Fix flaky checkout test"}),
        _ev("run-a2", "proj-a", 2, "plan_approved", "2026-10-13T10:02:00+00:00"),
        _ev("run-a2", "proj-a", 3, "execution_started", "2026-10-13T10:03:00+00:00"),
        _ev("run-a2", "proj-a", 4, "engine_failed", "2026-10-13T10:20:00+00:00",
            {"reason": "test timeout"}),
        _ev("run-a2", "proj-a", 5, "execution_started", "2026-10-13T10:25:00+00:00"),
        _ev("run-a2", "proj-a", 6, "done_signal_received", "2026-10-13T11:00:00+00:00"),
        _ev("run-a2", "proj-a", 7, "engine_completed", "2026-10-13T11:01:00+00:00",
            {"summary": "Second attempt green."}),
        _ev("run-a2", "proj-a", 8, "review_failed", "2026-10-13T11:05:00+00:00",
            {"findings": {"passed": False, "issues": ["test not hermetic"]}}),
        _ev("run-a2", "proj-a", 9, "review_passed", "2026-10-13T11:20:00+00:00",
            {"findings": {"passed": True, "issues": []}}),
        _ev("run-a2", "proj-a", 10, "pr_opened", "2026-10-13T11:25:00+00:00",
            {"prId": "pr-102", "url": "https://github.com/org/repo/pull/102"}),
    ]

    # run-a3: model claims completion, independent review rejects, no PR.
    # THE distinguishing fixture: engine_completed != accepted change.
    e += [
        _ev("run-a3", "proj-a", 1, "plan_proposed", "2026-10-14T09:00:00+00:00",
            {"goal": "Bump docs dependency"}),
        _ev("run-a3", "proj-a", 2, "plan_approved", "2026-10-14T09:01:00+00:00"),
        _ev("run-a3", "proj-a", 3, "execution_started", "2026-10-14T09:02:00+00:00"),
        _ev("run-a3", "proj-a", 4, "engine_completed", "2026-10-14T09:40:00+00:00",
            {"summary": "Done."}),
        _ev("run-a3", "proj-a", 5, "done_signal_received", "2026-10-14T09:41:00+00:00"),
        _ev("run-a3", "proj-a", 6, "review_failed", "2026-10-14T09:45:00+00:00",
            {"findings": {"passed": False, "briefAligned": False, "issues": ["wrong file"]}}),
    ]

    # run-a4: proposed, never approved -> abandoned (age > window).
    e += [
        _ev("run-a4", "proj-a", 1, "plan_proposed", "2026-10-14T11:00:00+00:00",
            {"goal": "Refactor metrics module"}),
    ]

    # run-a5: blocked awaiting clarification (terminal-ish, no acceptance).
    e += [
        _ev("run-a5", "proj-a", 1, "plan_proposed", "2026-10-15T08:00:00+00:00",
            {"goal": "Migrate auth flow"}),
        _ev("run-a5", "proj-a", 2, "plan_approved", "2026-10-15T08:02:00+00:00"),
        _ev("run-a5", "proj-a", 3, "execution_started", "2026-10-15T08:03:00+00:00"),
        _ev("run-a5", "proj-a", 4, "blocked_awaiting_clarification", "2026-10-15T08:30:00+00:00",
            {"summary": "Which IdP?"}),
    ]

    # run-a6: in-flight today -> active.
    e += [
        _ev("run-a6", "proj-a", 1, "plan_proposed", "2026-10-17T09:00:00+00:00",
            {"goal": "Add retry to uploader"}),
        _ev("run-a6", "proj-a", 2, "plan_approved", "2026-10-17T09:01:00+00:00"),
        _ev("run-a6", "proj-a", 3, "execution_started", "2026-10-17T09:02:00+00:00"),
    ]

    # run-b1: clean success on team-b (75 min; slow approval: 30 min).
    e += [
        _ev("run-b1", "proj-b", 1, "plan_proposed", "2026-10-15T10:00:00+00:00",
            {"goal": "Cache user lookups"}),
        _ev("run-b1", "proj-b", 2, "plan_approved", "2026-10-15T10:30:00+00:00"),
        _ev("run-b1", "proj-b", 3, "execution_started", "2026-10-15T10:31:00+00:00"),
        _ev("run-b1", "proj-b", 4, "done_signal_received", "2026-10-15T11:10:00+00:00"),
        _ev("run-b1", "proj-b", 5, "engine_completed", "2026-10-15T11:11:00+00:00",
            {"summary": "Cache added; tests green."}),
        _ev("run-b1", "proj-b", 6, "review_passed", "2026-10-15T11:12:00+00:00",
            {"findings": {"passed": True, "issues": []}}),
        _ev("run-b1", "proj-b", 7, "pr_opened", "2026-10-15T11:15:00+00:00",
            {"prId": "pr-201", "url": "https://github.com/org/repo/pull/201"}),
    ]

    # run-b2: rejected by review, never recovered; goal carries PII + a token
    # (scrubbing fixture: nothing raw may reach storage, spans or dashboards).
    e += [
        _ev("run-b2", "proj-b", 1, "plan_proposed", "2026-10-16T09:00:00+00:00",
            {"goal": "Fix login for alice@example.com; token = ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"}),
        _ev("run-b2", "proj-b", 2, "plan_approved", "2026-10-16T09:05:00+00:00"),
        _ev("run-b2", "proj-b", 3, "execution_started", "2026-10-16T09:06:00+00:00"),
        _ev("run-b2", "proj-b", 4, "review_failed", "2026-10-16T09:40:00+00:00",
            {"findings": {"passed": False, "issues": ["regression in session cookie"]}}),
    ]
    return e


def costs() -> list[dict[str, Any]]:
    return [
        {"runId": "run-a1", "costUsd": "3.20", "inputTokens": 12000, "outputTokens": 3000,
         "latencyMs": 1440000, "model": "deepseek-v4.1"},
        {"runId": "run-a2", "costUsd": "7.50", "inputTokens": 31000, "outputTokens": 8000,
         "latencyMs": 4920000, "model": "deepseek-v4.1"},
        {"runId": "run-a3", "costUsd": "4.00", "inputTokens": 18000, "outputTokens": 4000,
         "latencyMs": 2280000, "model": "deepseek-v4.1"},
        {"runId": "run-a4", "costUsd": "0.10", "inputTokens": 400, "outputTokens": 100,
         "latencyMs": 60000, "model": "deepseek-v4.1"},
        {"runId": "run-a5", "costUsd": "1.80", "inputTokens": 7000, "outputTokens": 1500,
         "latencyMs": 1620000, "model": "deepseek-v4.1"},
        {"runId": "run-a6", "costUsd": "0.60", "inputTokens": 2500, "outputTokens": 600,
         "latencyMs": 300000, "model": "deepseek-v4.1"},
        {"runId": "run-b1", "costUsd": "2.40", "inputTokens": 9000, "outputTokens": 2200,
         "latencyMs": 1500000, "model": "deepseek-v4.1"},
        {"runId": "run-b2", "costUsd": "5.10", "inputTokens": 22000, "outputTokens": 5200,
         "latencyMs": 2040000, "model": "deepseek-v4.1"},
    ]
