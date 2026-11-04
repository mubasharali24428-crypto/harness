"""W24: the review summary says only what the run recorded, and says "unknown" otherwise."""

import pytest

from harness_orchestrator import engine
from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.review_summary import (
    build_review_summary,
    diff_files,
    is_test_command,
)

# --------------------------------------------------------------- test-command detection


@pytest.mark.parametrize("command", [
    "pytest", "pytest -q tests/", "python -m pytest", "python3.13 -m unittest discover",
    ".venv/bin/pytest -x", "cd app && pytest -q", "uv run pytest", "poetry run pytest",
    "FOO=1 pytest", "timeout 60 pytest", "npm test", "npm run test:unit", "yarn test",
    "pnpm exec vitest", "npx vitest run", "go test ./...", "cargo test", "make test",
    "ls && pytest | tee out.txt", "dotnet test",
])
def test_these_commands_run_tests(command):
    assert is_test_command(command)


@pytest.mark.parametrize("command", [
    "", "ls -la", "grep pytest requirements.txt", "cat tests/test_a.py", "pytest --version",
    "pytest --collect-only", "python script.py", "npm install", "npm run build", "go build ./...",
    "echo pytest", "git commit -m 'run pytest'",
])
def test_these_commands_do_not(command):
    assert not is_test_command(command)


# ------------------------------------------------------------------------------ the diff

DIFF = """\
diff --git a/src/a.py b/src/a.py
index 111..222 100644
--- a/src/a.py
+++ b/src/a.py
@@ -1,3 +1,4 @@
 keep
-old
+new
+extra
-- a removed line that starts with dashes
diff --git a/src/b.py b/src/b.py
new file mode 100644
--- /dev/null
+++ b/src/b.py
@@ -0,0 +1,2 @@
+one
+two
diff --git a/src/c.py b/src/c.py
deleted file mode 100644
--- a/src/c.py
+++ /dev/null
@@ -1,2 +0,0 @@
-gone
-gone too
"""


def test_diff_files_counts_lines_and_kinds_per_file():
    files = diff_files(DIFF, ["src/a.py", "src/b.py"], ["src/c.py"])
    assert files == [
        {"path": "src/a.py", "change": "modified", "added": 2, "removed": 2},
        {"path": "src/b.py", "change": "added", "added": 2, "removed": 0},
        {"path": "src/c.py", "change": "deleted", "added": 0, "removed": 2},
    ]
    # src/a.py: "-old" plus a removed line whose own text starts with "-- " (not a header).


def test_a_file_the_diff_does_not_cover_stays_unknown_rather_than_guessed():
    files = diff_files("", ["big.bin"], [])
    assert files == [{"path": "big.bin", "change": "changed", "added": None, "removed": None}]


# ------------------------------------------------------------------------------ builder

CALLS_GREEN = [
    {"tool": "read", "target": "", "outcome": "ok"},
    {"tool": "edit", "target": "", "outcome": "ok"},
    {"tool": "bash", "target": "pytest -q", "outcome": "ok", "exitCode": 0},
]
USAGE = {"inputTokens": 7045, "outputTokens": 64}


def summary(**over):
    base = dict(files=[{"path": "a.py", "change": "modified", "added": 1, "removed": 1}],
                tool_calls=CALLS_GREEN, usage=USAGE, model="anthropic/claude-haiku-5.5",
                cost_usd=0.000736, cost_source="rate-card", elapsed_seconds=12.34,
                status="completed")
    base.update(over)
    return build_review_summary(**base)


def codes(s):
    return [u["code"] for u in s["uncertainty"]]


def test_a_green_run_lists_everything_and_has_nothing_left_unknown():
    s = summary()
    assert s["tests"]["runs"] == [{"command": "pytest -q", "result": "passed"}]
    assert s["failures"] == [] and s["uncertainty"] == []
    assert s["cost"] == {"usd": 0.000736, "source": "rate-card",
                         "model": "anthropic/claude-haiku-5.5",
                         "inputTokens": 7045, "outputTokens": 64}
    assert s["elapsedSeconds"] == 12.3
    assert "re-ran nothing" in s["basis"]


def test_a_failing_test_the_tool_reported_as_ok_is_still_a_failure():
    """The shell tool does not fail on a non-zero exit: `outcome` is `ok`, only exitCode tells."""
    calls = [{"tool": "bash", "target": "pytest -q", "outcome": "ok", "exitCode": 1}]
    s = summary(tool_calls=calls)
    assert s["tests"]["runs"][0]["result"] == "failed"
    assert s["failures"] == [{"kind": "test", "what": "pytest -q failed"}]


def test_a_fix_followed_by_a_green_rerun_is_not_a_failure():
    calls = [
        {"tool": "bash", "target": "pytest -q", "outcome": "ok", "exitCode": 1},
        {"tool": "edit", "target": "", "outcome": "ok"},
        {"tool": "bash", "target": "pytest -q", "outcome": "ok", "exitCode": 0},
    ]
    s = summary(tool_calls=calls)
    assert s["failures"] == [] and "EDITED_AFTER_LAST_TEST" not in codes(s)
    assert [r["result"] for r in s["tests"]["runs"]] == ["failed", "passed"]


def test_a_file_written_after_the_last_test_is_flagged():
    calls = [*CALLS_GREEN, {"tool": "write", "target": "", "outcome": "ok"}]
    assert "EDITED_AFTER_LAST_TEST" in codes(summary(tool_calls=calls))


def test_surfacing_no_tool_calls_means_unknown_not_none():
    s = summary(tool_calls=None)
    assert s["tests"] == {"known": False, "runs": []}
    assert codes(s) == ["TOOL_CALLS_NOT_SURFACED"]


def test_no_test_command_is_said_plainly():
    s = summary(tool_calls=[{"tool": "read", "target": "", "outcome": "ok"}])
    assert codes(s) == ["NO_TESTS_RUN"] and s["tests"]["runs"] == []


def test_a_test_command_without_an_exit_status_is_unknown_never_passed():
    s = summary(tool_calls=[{"tool": "bash", "target": "pytest", "outcome": "ok"}])
    assert s["tests"]["runs"][0]["result"] == "unknown"
    assert "TEST_RESULT_UNKNOWN" in codes(s)


def test_a_refused_test_command_is_a_failure_that_did_not_run():
    s = summary(tool_calls=[{"tool": "bash", "target": "pytest", "outcome": "error"}])
    assert s["tests"]["runs"][0]["result"] == "refused"
    assert s["failures"] == [{"kind": "test", "what": "pytest refused"}]


def test_other_errored_tool_calls_are_counted_once():
    refused = [{"tool": "bash", "target": f"curl x{i}", "outcome": "error"} for i in range(4)]
    calls = [*CALLS_GREEN, *refused]
    failures = summary(tool_calls=calls)["failures"]
    assert len(failures) == 1 and failures[0]["kind"] == "tool"
    assert failures[0]["what"].startswith("4 tool call(s) refused or errored: bash curl x0")
    assert failures[0]["what"].endswith("; ...")


def test_cost_is_never_defaulted_to_zero():
    no_usage = summary(usage=None, cost_usd=None, cost_source=None)
    assert no_usage["cost"]["usd"] is None and "USAGE_UNAVAILABLE" in codes(no_usage)
    unpriced = summary(cost_usd=None, cost_source=None)
    assert unpriced["cost"]["usd"] is None and unpriced["cost"]["inputTokens"] == 7045
    assert "COST_UNPRICED" in codes(unpriced)
    ledger_only = summary(usage=None, cost_usd=0.5, cost_source="cost_ledger")
    assert ledger_only["cost"]["usd"] == 0.5 and "USAGE_UNAVAILABLE" not in codes(ledger_only)


def test_time_and_diff_unknowns_are_named():
    assert "ELAPSED_UNKNOWN" in codes(summary(elapsed_seconds=None))
    assert "DIFF_OMITTED" in codes(summary(diff_omitted="too-large"))
    assert "NO_CHANGES" in codes(summary(files=[]))
    assert "NO_CHANGES" not in codes(summary(files=[], diff_omitted="too-large"))


def test_a_run_that_did_not_complete_says_so():
    s = summary(status="error")
    assert {"kind": "run", "what": "the run ended error"} in s["failures"]
    assert "RUN_NOT_COMPLETED" in codes(s)


# ------------------------------------------------- the engine records the exit status

LONG_OUTPUT = "x" * 6000


def test_exit_code_is_read_from_the_tail_the_preview_drops():
    content = [{"type": "text", "text": f"{LONG_OUTPUT}\n[exit code: 1]"}]
    assert "[exit code" not in engine._content_preview(content)  # the head-only preview loses it
    assert engine._exit_code(content) == 1
    assert engine._exit_code("ok\n[exit code: 0]") == 0
    assert engine._exit_code("killed\n[exit code: timeout]") == "timeout"
    assert engine._exit_code("no marker here") is None
    assert engine._exit_code("[exit code: 3] but not at the end\nmore") is None


def test_fold_tool_calls_keeps_the_exit_status_of_a_failing_command():
    events = [
        {"type": "tool/call", "data": {"callId": "a", "name": "bash",
                                       "arguments": '{"command": "pytest -q"}'}},
        {"type": "tool/result", "data": {"message": {
            "toolCallId": "a", "content": f"{LONG_OUTPUT}\n[exit code: 1]"}}},
        {"type": "tool/call", "data": {"callId": "b", "name": "read", "arguments": "{}"}},
        {"type": "tool/result", "data": {"message": {"toolCallId": "b", "content": "fine"}}},
    ]
    assert DeepSeekHarnessWrapper._fold_tool_calls(events) == [
        {"callId": "a", "tool": "bash", "target": "pytest -q", "outcome": "ok", "exitCode": 1},
        {"callId": "b", "tool": "read", "target": "", "outcome": "ok"},
    ]
    record = DeepSeekHarnessWrapper._tool_record(events[1])
    assert record["ok"] is True and record["exitCode"] == 1


def test_exit_status_rides_in_the_stored_call_summary():
    from harness_orchestrator.review_summary import call_summary, exit_code_from_summary

    for code in (0, 1, -9, "timeout"):
        assert exit_code_from_summary(call_summary("bash", code)) == code
    assert call_summary("read", None) == "engine read call"
    assert exit_code_from_summary("engine read call") is None
    assert exit_code_from_summary(None) is None
    assert exit_code_from_summary("engine bash call (exit 3) and more") is None  # only a suffix
