"""W1 — the context window the run page shows must be the model's, not a default.

`_context_window()` read ``DSH_CONTEXT_WINDOW`` with a fallback of 1,000,000 and
the run page presented that as "the window this run uses". The OpenRouter
transport patch declares ``contextWindow: 65536`` for the model actually in use,
and both live drills displayed 1M anyway (docs/backlog.md, "From W1 M07"). A
number nobody configured is a guess, and roadmap ground rule 1 forbids shipping
an affordance that does not do the thing.

The UI already renders "window unknown" for a null, so the honest value is
available; these tests pin that the backend stops inventing one.
"""

import pytest

from harness_orchestrator.context_window import configured_context_window


def test_no_window_when_the_operator_configured_none(monkeypatch):
    """The default is not evidence of a window; absence of a config is unknown."""
    monkeypatch.delenv("DSH_CONTEXT_WINDOW", raising=False)
    assert configured_context_window() is None


def test_configured_window_is_reported_verbatim(monkeypatch):
    monkeypatch.setenv("DSH_CONTEXT_WINDOW", "65536")
    assert configured_context_window() == 65536


def test_unparsable_window_is_unknown_rather_than_a_silent_default(monkeypatch):
    """Previously "huge" fell through to 1,000,000 — the worst case of the bug:
    an explicit operator value was replaced by the number it failed to parse."""
    monkeypatch.setenv("DSH_CONTEXT_WINDOW", "huge")
    assert configured_context_window() is None


def test_execution_started_says_the_window_is_unknown(monkeypatch):
    """The event that seeds the run page must carry the unknown, not a number."""
    from harness_contracts.events import EventType

    from harness_orchestrator.state_machine import AgentStateMachine

    monkeypatch.delenv("DSH_CONTEXT_WINDOW", raising=False)
    events = []
    sm = AgentStateMachine(run_id="run-cw-001", on_event=lambda t, p: events.append((t, p)))
    sm.start_planning()
    sm.plan_proposed("plan-001")
    sm.approve_plan("plan-001", "user-1")
    sm.start_execution()
    started = [p for t, p in events if t == EventType.EXECUTION_STARTED]
    assert started, "expected an execution_started event"
    assert started[-1]["contextWindow"] is None


def test_context_meta_keeps_compaction_but_drops_the_invented_window(monkeypatch):
    from harness_orchestrator.engine import _context_meta

    monkeypatch.delenv("DSH_CONTEXT_WINDOW", raising=False)
    events = [
        {
            "type": "compaction/summary",
            "data": {"shadowedTokenCount": 1200},
        }
    ]
    meta = _context_meta(events)
    assert meta["contextWindow"] is None
    assert meta["compactions"] == 1
    assert meta["compactedTokens"] == 1200


@pytest.mark.asyncio
async def test_context_view_reports_unknown_window_from_durable_events(monkeypatch):
    from harness_orchestrator.execution import _context_view

    monkeypatch.delenv("DSH_CONTEXT_WINDOW", raising=False)

    class _Rows:
        def all(self):
            return []

    class _Db:
        async def execute(self, *a, **kw):
            return _Rows()

    view = await _context_view(_Db(), "run-cw-002")
    assert view["contextWindow"] is None
    assert view["compactions"] == 0
