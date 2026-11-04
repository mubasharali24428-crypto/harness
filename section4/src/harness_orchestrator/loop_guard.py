"""W2 — stop a run that is repeating itself or burning its tool budget.

`maxToolCalls` already exists, and so does `remaining_tool_calls`. What neither
of them sees is the run as it happens: the limit is computed once at run start
from `COUNT(*) FROM agent_tool_calls`, and that table is populated after the
engine call returns (`service.py:1406`). During the run it is empty. Native
`tool-fs` calls are also not bash calls, so the shell plugin's own counter never
counts them. The result, measured on 2026-10-26: two runs of 14 and 37 tool calls
against a one-sentence goal, stopped by nothing but the wall clock.

This guard sits on the W1 transcript forwarder, which is the one place every call
passes on both execution paths, and it is deliberately pure: it decides from the
event stream alone, so it can be tested without a database and cannot be wrong
because a table arrived late.

Two independent stops, either of which is enough:

* ``tool_budget_exhausted`` — more calls than the run was allowed, counting
  distinct calls rather than distinct shapes, because 100 different commands is
  still a burn.
* ``loop_detected`` — the same tool with the same arguments often enough to be a
  model going round, not working.

A run is stopped, not throttled: once either reason fires, every later call
returns the same reason. An unbounded guard that keeps saying "stopped" while the
worker continues would be the bug in a nicer coat.
"""

from __future__ import annotations

import os
import re

LOOP_DETECTED = "loop_detected"
TOOL_BUDGET_EXHAUSTED = "tool_budget_exhausted"

REPEAT_LIMIT_ENV = "HARNESS_LOOP_REPEAT_LIMIT"
#: Four identical calls is a model going round, not a model working. Low enough
#: to stop a burn early, high enough that a legitimate retry-after-a-transient-
#: failure (read, fail, read again) is not mistaken for a loop.
DEFAULT_REPEAT_LIMIT = 4

_WS = re.compile(r"\s+")

#: W20: calls that change the workspace. After one, the same screenshot or test
#: command is checking new state, not going round. Measured: a W14 design run
#: was stopped on its 4th identical `browser` screenshot although every one came
#: after an `edit`. Identical writes still count across resets.
MUTATING_TOOLS = frozenset({"write", "edit"})


def loop_repeat_limit() -> int | None:
    """Operator-tunable repeat limit; unset means the default, not "disabled".

    Disabled is the empty value, because "no config" falling through to no
    detection is how the budget ended up decorative in the first place.
    """
    raw = os.environ.get(REPEAT_LIMIT_ENV)
    if raw is None:
        return DEFAULT_REPEAT_LIMIT
    raw = raw.strip()
    if not raw or raw.lower() in {"off", "none", "0"}:
        return None
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_REPEAT_LIMIT
    return value if value > 0 else None



def _shape(name: str | None, arguments: str | None) -> str:
    """The identity of a call: which tool, doing what.

    Whitespace is collapsed so `git  status` and `git status` are one shape. That
    is not cosmetic — a looping model re-emits commands with formatting drift, and
    an exact-match detector would score 37 near-identical calls as 37 different
    ones and call it progress.
    """
    return f"{name or 'unknown'} {_WS.sub(' ', (arguments or '')).strip()}"


class LoopGuard:
    """Observes live tool records and names the reason a run should stop."""

    def __init__(self, max_calls: int | None, repeat_limit: int | None) -> None:
        for label, value in (("max_calls", max_calls), ("repeat_limit", repeat_limit)):
            if value is not None and value <= 0:
                raise ValueError(f"{label} must be positive or None, got {value!r}")
        self._max_calls = max_calls
        self._repeat_limit = repeat_limit
        self._calls = 0
        self._shapes: dict[str, int] = {}
        self._stop: str | None = None

    @property
    def calls_seen(self) -> int:
        return self._calls

    @property
    def stop_reason(self) -> str | None:
        return self._stop

    def repeat_of(self, name: str, arguments: str | None) -> int:
        return self._shapes.get(_shape(name, arguments), 0)

    def observe(self, record: dict) -> str | None:
        """Feed one transcript record. Results are acknowledged, not counted."""
        if self._stop is not None:
            return self._stop
        if not isinstance(record, dict) or record.get("phase") != "call":
            return None

        self._calls += 1
        if self._max_calls is not None and self._calls > self._max_calls:
            self._stop = TOOL_BUDGET_EXHAUSTED
            return self._stop

        name = record.get("name")
        if name in MUTATING_TOOLS:
            self._shapes = {
                k: v for k, v in self._shapes.items() if k.split(" ", 1)[0] in MUTATING_TOOLS
            }
        key = _shape(name, record.get("arguments"))
        seen = self._shapes.get(key, 0) + 1
        self._shapes[key] = seen
        if self._repeat_limit is not None and seen >= self._repeat_limit:
            self._stop = LOOP_DETECTED
            return self._stop
        return None
