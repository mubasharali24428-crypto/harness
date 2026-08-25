"""M19-N4 — the canned demo's versioned replay fixture (C10).

Curated RECORDED material only — nothing here claims a live provider call:

- Task goal/steps/tests/risks/forbiddenPaths/expectedResult: VERBATIM from
  ``section10/demo_repo/.harness/demo-task.json`` (the shipped demo task).
- Diff: the canonical completion of that task's own TODO in
  ``section10/demo_repo/src/textstats/stats.py`` (tests/test_stats.py pins it).
- PR number/url: section8 golden conformance values
  (``section8/tests/fixtures/golden_fixtures.py``).
- Review verdict strings: the exact deterministic output of Section 4's
  ``ReviewerSubAgent.evaluate_changes`` heuristic path (no model) for that
  diff — pinned by tests/unit/test_demo_seed.py so replay can never drift
  from what the reviewer would really say.
- Event vocabulary/payload shapes: frozen contracts EventType + the exact
  payloads AgentStateMachine emits.
- Cost: priced by harness_contracts PricingEngine at seed time for the
  recorded model + tokens — catalog truth, not an invented number.

Instance ids (project/run/plan/pr) are minted per team at seed time with the
stable ``demo-`` prefix; the ``__DEMO_*__`` tokens below are bound then.

Versioned: bump ``version`` on any content change; tests pin the current
shape so silent fixture edits fail loudly.
"""

from __future__ import annotations

from typing import Any

#: Bump on content changes — consumers (and the honesty tests) pin this.
FIXTURE_VERSION = 1

#: Machine-readable marker written into EVERY seeded event payload — the
#: honest-demo law's durable half. ``source`` names this module's provenance.
REPLAY_MARKER = {"replay": True, "source": "demo_replay.py (recorded)"}

_REPLAY: dict[str, Any] = {
    "project": {
        "name": "harness-demo-textstats",
        "repoFullName": "local/demo-repo",
    },
    "run": {
        "branchConvention": "agent/__DEMO_RUN_ID__",
        "goal": (
            "textstats.stats.median_word_length currently raises NotImplementedError. "
            "Implement it: median length of whitespace-separated words, 0 for empty "
            "input. The failing test tests/test_stats.py::test_median_word_length "
            "already exists and must pass."
        ),
        "engineMode": "replay",
        "finalResponse": (
            "Implemented median_word_length in src/textstats/stats.py; "
            "python -m pytest -q green, ruff clean."
        ),
        # Recorded usage of the replayed leg; cost is derived from these via
        # the real PricingEngine at seed time (never hardcoded money here).
        "usage": {
            "model": "stub",
            "inputTokens": 60,
            "outputTokens": 18,
            "latencyMs": 1400,
        },
        "diffText": (
            "diff --git a/src/textstats/stats.py b/src/textstats/stats.py\n"
            "index 7c4a3f2..a1b2c3d 100644\n"
            "--- a/src/textstats/stats.py\n"
            "+++ b/src/textstats/stats.py\n"
            "@@ -24,4 +24,9 @@ def median_word_length(text: str) -> float:\n"
            '-    raise NotImplementedError("demo task: implement median_word_length")\n'
            "+    lengths = [len(word) for word in text.split()]\n"
            "+    if not lengths:\n"
            "+        return 0.0\n"
            "+    lengths.sort()\n"
            "+    n = len(lengths)\n"
            "+    mid = n // 2\n"
            "+    return float(lengths[mid]) if n % 2 else (lengths[mid - 1] + lengths[mid]) / 2"
        ),
        "plan": {
            "filesLikelyToChange": ["src/textstats/stats.py"],
            "steps": [
                "Read src/textstats/stats.py and tests/test_stats.py",
                "Implement median_word_length without modifying the tests",
                "Run python -m pytest -q and python -m ruff check .",
            ],
            "tests": ["python -m pytest -q", "python -m ruff check ."],
            "risks": ["Modifying tests or pyproject.toml instead of the implementation"],
            "estimatedCostUsd": 0.01,
            "forbiddenPaths": ["tests/test_stats.py", "pyproject.toml"],
            "expectedResult": (
                "All tests pass, lint is clean, only src/textstats/stats.py changed."
            ),
        },
        # Exact deterministic verdict of ReviewerSubAgent.evaluate_changes for
        # the diff above against the goal above (heuristic path, no model).
        "review": {
            "passed": True,
            "modelUsed": None,
            "syntaxOk": True,
            "conflictsDetected": False,
            "briefAligned": True,
            "summary": "Review passed cleanly.",
            "issues": [],
        },
        "pullRequest": {
            "number": 42,
            "url": "https://github.com/org/demo-repo/pull/42",
            "status": "open",
            "summaryMd": "",
        },
        # Ordered lifecycle trail in the frozen EventType vocabulary, payload
        # shapes exactly as AgentStateMachine emits them. Timestamps are the
        # recorded wall-clock of the captured session (UTC, ISO-8601).
        "events": [
            {
                "type": "plan_proposed",
                "ts": "2026-08-20T09:00:00+00:00",
                "payload": {"planId": "__DEMO_PLAN_ID__", "runId": "__DEMO_RUN_ID__"},
            },
            {
                "type": "plan_approved",
                "ts": "2026-08-20T09:00:41+00:00",
                "payload": {
                    "planId": "__DEMO_PLAN_ID__",
                    "runId": "__DEMO_RUN_ID__",
                    "approvedBy": "demo@harness.dev",
                },
            },
            {
                "type": "execution_started",
                "ts": "2026-08-20T09:00:42+00:00",
                "payload": {"runId": "__DEMO_RUN_ID__"},
            },
            {
                "type": "terminal_output",
                "ts": "2026-08-20T09:00:58+00:00",
                "payload": {"runId": "__DEMO_RUN_ID__", "line": "$ python -m pytest -q"},
            },
            {
                "type": "terminal_output",
                "ts": "2026-08-20T09:01:07+00:00",
                "payload": {"runId": "__DEMO_RUN_ID__", "line": "3 passed in 0.02s"},
            },
            {
                "type": "diff_progress",
                "ts": "2026-08-20T09:01:20+00:00",
                "payload": {
                    "runId": "__DEMO_RUN_ID__",
                    "path": "src/textstats/stats.py",
                    "checkpoint": {"kind": "agent-edit", "source": "replay"},
                },
            },
            {
                "type": "engine_completed",
                "ts": "2026-08-20T09:01:21+00:00",
                "payload": {
                    "runId": "__DEMO_RUN_ID__",
                    "summary": (
                        "Implemented median_word_length in src/textstats/stats.py; "
                        "python -m pytest -q green, ruff clean."
                    ),
                },
            },
            {
                "type": "done_signal_received",
                "ts": "2026-08-20T09:03:12+00:00",
                "payload": {"runId": "__DEMO_RUN_ID__"},
            },
            {
                "type": "review_passed",
                "ts": "2026-08-20T09:03:14+00:00",
                "payload": {
                    "runId": "__DEMO_RUN_ID__",
                    "findings": {
                        "passed": True,
                        "modelUsed": None,
                        "syntaxOk": True,
                        "conflictsDetected": False,
                        "briefAligned": True,
                        "summary": "Review passed cleanly.",
                        "issues": [],
                    },
                },
            },
            {
                "type": "pr_opened",
                "ts": "2026-08-20T09:03:30+00:00",
                "payload": {
                    "runId": "__DEMO_RUN_ID__",
                    "prId": "__DEMO_PR_ID__",
                    "url": "https://github.com/org/demo-repo/pull/42",
                },
            },
        ],
    },
}


def load_fixture() -> dict[str, Any]:
    """Return the versioned replay fixture.

    Raises ValueError on shape corruption rather than seeding a half-truth:
    the honesty law extends to the seeder itself.
    """
    if FIXTURE_VERSION != 1 or not isinstance(_REPLAY.get("run"), dict):
        raise ValueError("demo replay fixture malformed (version/shape)")
    return {"version": FIXTURE_VERSION, **_REPLAY}
