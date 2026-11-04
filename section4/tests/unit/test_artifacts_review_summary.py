"""W24: GET runs/{r}/review-summary reads only what the run recorded and says so when it recorded
nothing. Sqlite, the shared part 48 fixture, minimal stand-ins for the tables other lanes own."""

import uuid
from datetime import timedelta

from harness_orchestrator.review_summary import call_summary

from . import test_artifacts_support as support
from .test_artifacts_support import T0, err, git_diff

env = support.env  # the shared fixture

TABLES = (
    "CREATE TABLE agent_tool_calls (id VARCHAR(36) PRIMARY KEY, assignment_id VARCHAR(36),"
    " run_id VARCHAR(36), team_id VARCHAR(36), tool VARCHAR(80), target VARCHAR(200),"
    " summary VARCHAR(500), outcome VARCHAR(16), reported_by VARCHAR(36), created_at TIMESTAMP)",
    "CREATE TABLE cost_ledger (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR(36),"
    " model VARCHAR(120), input_tokens INTEGER, output_tokens INTEGER, cost_usd NUMERIC)",
    "CREATE TABLE agent_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id VARCHAR(36),"
    " run_id VARCHAR(36), type VARCHAR(60), payload TEXT DEFAULT '{}', ts TIMESTAMP)",
)


def url(env, run="r1"):
    return env.url(run, "/review-summary")


def tables(env):
    for ddl in TABLES:
        env.x(ddl)


def call(env, n, tool, target=None, *, exit=None, outcome="ok", run="r1", team="team-a",
         reported_by="engine"):
    env.x(
        "INSERT INTO agent_tool_calls (id, assignment_id, run_id, team_id, tool, target, summary,"
        " outcome, reported_by, created_at) VALUES (:i, :r, :r, :t, :tool, :tg, :s, :o, :by, :at)",
        i=str(uuid.uuid4()), r=run, t=team, tool=tool, tg=target, s=call_summary(tool, exit),
        o=outcome, by=reported_by, at=T0 + timedelta(seconds=n),
    )


def spend(env, model, tin, tout, usd, run="r1"):
    env.x(
        "INSERT INTO cost_ledger (run_id, model, input_tokens, output_tokens, cost_usd)"
        " VALUES (:r, :m, :i, :o, :c)",
        r=run, m=model, i=tin, o=tout, c=usd,
    )


def mark(env, kind, seconds, run="r1"):
    env.x(
        "INSERT INTO agent_events (project_id, run_id, type, ts) VALUES ('p', :r, :k, :ts)",
        r=run, k=kind, ts=T0 + timedelta(seconds=seconds),
    )


def summary(env, user="lead", run="r1"):
    r = env.as_(user).get(url(env, run))
    assert r.status_code == 200, r.text
    assert "no-store" in r.headers["cache-control"]
    return r.json()["summary"]


def codes(s):
    return [u["code"] for u in s["uncertainty"]]


def test_there_is_no_summary_until_the_run_has_a_diff(env):
    err(env.as_("lead").get(url(env)), 404, "NOT_FOUND")
    env.set_diff("r1", git_diff(("src/app.py", ["x = 1"])))
    assert env.as_("evil").get(url(env)).status_code == 404  # another team's run
    assert env.as_("lead").get(url(env)).status_code == 200


def test_a_run_with_only_a_diff_lists_its_files_and_reports_the_rest_unknown(env):
    env.set_diff("r1", git_diff(("src/app.py", ["x = 1", "y = 2"]), ("README.md", ["hi"])))
    s = summary(env)
    assert [(f["path"], f["change"], f["added"], f["removed"]) for f in s["files"]] == [
        ("src/app.py", "modified", 2, 1),
        ("README.md", "modified", 1, 1),
    ]
    assert s["tests"] == {"known": False, "runs": []}
    assert s["cost"]["usd"] is None and s["cost"]["inputTokens"] is None
    assert s["elapsedSeconds"] is None
    assert {"TOOL_CALLS_NOT_SURFACED", "USAGE_UNAVAILABLE", "ELAPSED_UNKNOWN"} <= set(codes(s))
    assert "re-ran nothing" in s["basis"]


def test_recorded_calls_ledger_and_events_become_tests_cost_and_time(env):
    tables(env)
    env.set_diff("r1", git_diff(("calc.py", ["return a + b"])))
    call(env, 1, "read", "calc.py")
    call(env, 2, "bash", "pytest -q", exit=1)
    call(env, 3, "edit", "calc.py")
    call(env, 4, "bash", "pytest -q", exit=0)
    spend(env, "anthropic/claude-haiku-5.5", 1000, 100, 0.001)
    spend(env, "anthropic/claude-haiku-5.5", 500, 50, 0.0005)
    mark(env, "execution_started", 0)
    mark(env, "engine_completed", 42.5)

    s = summary(env)
    assert s["tests"]["runs"] == [
        {"command": "pytest -q", "result": "failed"},
        {"command": "pytest -q", "result": "passed"},
    ]
    assert s["failures"] == []  # the latest run of a command decides: fixed, then green
    assert s["cost"] == {
        "usd": 0.0015, "source": "cost ledger", "model": "anthropic/claude-haiku-5.5",
        "inputTokens": 1500, "outputTokens": 150,
    }
    assert s["elapsedSeconds"] == 42.5
    assert s["uncertainty"] == []


def test_a_test_with_no_recorded_exit_status_is_unknown_never_passed(env):
    tables(env)
    env.set_diff("r1", git_diff(("calc.py", ["return a + b"])))
    call(env, 1, "bash", "pytest -q")  # a run recorded before exit statuses were kept
    s = summary(env)
    assert s["tests"]["runs"] == [{"command": "pytest -q", "result": "unknown"}]
    assert "TEST_RESULT_UNKNOWN" in codes(s)
    assert s["failures"] == []


def test_a_failing_test_and_an_edit_after_it_are_both_said_out_loud(env):
    tables(env)
    env.set_diff("r1", git_diff(("calc.py", ["return a + b"])))
    call(env, 1, "bash", "python -m unittest", exit=1)
    call(env, 2, "bash", "whoami", outcome="denied")
    call(env, 3, "write", "calc.py")
    s = summary(env)
    assert s["tests"]["runs"] == [{"command": "python -m unittest", "result": "failed"}]
    assert {f["what"] for f in s["failures"]} == {
        "python -m unittest failed",
        "1 tool call(s) refused or errored: bash whoami",
    }
    assert "EDITED_AFTER_LAST_TEST" in codes(s)


def test_a_failed_run_says_so_and_a_retry_restarts_the_clock(env):
    tables(env)
    env.set_diff("r1", git_diff(("calc.py", ["return a + b"])))
    mark(env, "execution_started", 0)
    mark(env, "engine_failed", 10)
    mark(env, "execution_started", 100)
    mark(env, "engine_failed", 130)
    s = summary(env)
    assert s["elapsedSeconds"] == 30  # the last attempt, not the whole wait
    assert {"kind": "run", "what": "the run ended failed"} in s["failures"]
    assert "RUN_NOT_COMPLETED" in codes(s)

    mark(env, "execution_started", 200)  # still running: no end, so no elapsed time is invented
    assert summary(env)["elapsedSeconds"] is None


def test_another_teams_rows_and_agent_reported_calls_are_never_mixed_in(env):
    tables(env)
    env.set_diff("r1", git_diff(("calc.py", ["return a + b"])))
    env.set_diff("r3", git_diff(("other.py", ["x = 1"])))
    call(env, 1, "bash", "pytest -q", exit=0)
    call(env, 2, "bash", "pytest -q", exit=1, team="team-b")  # same run id, wrong team
    call(env, 3, "bash", "pytest -q", exit=1, reported_by="agent-7")  # an agent's own report
    call(env, 4, "bash", "pytest -q", exit=1, run="r3", team="team-b")
    spend(env, "m", 10, 1, 9.0, run="r3")
    mark(env, "execution_started", 0, run="r3")
    mark(env, "engine_completed", 500, run="r3")

    s = summary(env)
    assert s["tests"]["runs"] == [{"command": "pytest -q", "result": "passed"}]
    assert s["cost"]["usd"] is None and s["elapsedSeconds"] is None
    assert summary(env, user="evil", run="r3")["cost"]["usd"] == 9.0
    assert env.as_("lead").get(url(env, "r3")).status_code == 404


def test_a_viewer_can_read_the_summary(env):
    env.set_diff("r1", git_diff(("calc.py", ["return a + b"])))
    assert env.as_("view").get(url(env)).status_code == 200
    assert env.as_("guest").get(url(env)).status_code in (200, 403)  # whatever read allows
