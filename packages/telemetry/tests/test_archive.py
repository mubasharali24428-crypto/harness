"""Archive: scrubbed ingest, search, ownership scoping, retention."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from harness_telemetry import Archive, fixtures, parse_cost, parse_stream

AS_OF = fixtures.AS_OF


def _ingest(archive: Archive, **kwargs):
    events = parse_stream(fixtures.events())
    costs = [parse_cost(c) for c in fixtures.costs()]
    return archive.ingest(events, costs, fixtures.PROJECT_TEAM, as_of=AS_OF, **kwargs)


def test_ingest_and_search_org_wide(tmp_path):
    archive = Archive(str(tmp_path / "a.sqlite"))
    report = _ingest(archive)
    assert report.runs == 8 and report.events == 42

    everything = archive.search(visible_teams=None)
    assert everything["total"] == 8
    assert [r["runId"] for r in everything["runs"]][:2] == ["run-a6", "run-b2"]

    team_a = archive.search(visible_teams=None, team="team-a")
    assert team_a["total"] == 6
    accepted = archive.search(visible_teams=None, outcome="accepted")
    assert {r["runId"] for r in accepted["runs"]} == {"run-a1", "run-a2", "run-b1"}


def test_search_is_scrubbed_and_finds_by_goal(tmp_path):
    archive = Archive(str(tmp_path / "a.sqlite"))
    _ingest(archive)
    hit = archive.search(visible_teams=None, q="login")
    assert [r["runId"] for r in hit["runs"]] == ["run-b2"]
    goal = hit["runs"][0]["goal"]
    assert "alice@example.com" not in goal and "[EMAIL]" in goal
    assert "ghp_" not in goal

    # the raw DB never holds the original text
    raw = sqlite3.connect(str(tmp_path / "a.sqlite"))
    stored = raw.execute("SELECT group_concat(goal) FROM runs").fetchone()[0]
    payloads = raw.execute("SELECT group_concat(payload_json) FROM events").fetchone()[0]
    assert "alice@example.com" not in stored and "ghp_AbCd" not in stored
    assert "alice@example.com" not in payloads and "ghp_AbCd" not in payloads
    raw.close()


def test_reingest_is_idempotent(tmp_path):
    archive = Archive(str(tmp_path / "a.sqlite"))
    _ingest(archive)
    _ingest(archive)
    assert archive.search(visible_teams=None)["total"] == 8
    raw = sqlite3.connect(str(tmp_path / "a.sqlite"))
    assert raw.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 42
    raw.close()


def test_team_scoping_is_fail_closed(tmp_path):
    archive = Archive(str(tmp_path / "a.sqlite"))
    _ingest(archive)
    assert archive.search(visible_teams=set())["total"] == 0
    only_a = archive.search(visible_teams={"team-a"})
    assert only_a["total"] == 6
    assert archive.get_run("run-b1", visible_teams={"team-a"}) is None
    assert archive.get_run("run-a1", visible_teams={"team-a"}) is not None


def test_get_run_includes_scrubbed_events(tmp_path):
    archive = Archive(str(tmp_path / "a.sqlite"))
    _ingest(archive)
    run = archive.get_run("run-b2", visible_teams=None)
    assert run["outcome"] == "rejected"
    assert [e["type"] for e in run["events"]][:2] == ["plan_proposed", "plan_approved"]
    assert "ghp_" not in run["goal"]


def test_retention_purge_removes_runs_and_orphaned_events(tmp_path):
    archive = Archive(str(tmp_path / "a.sqlite"))
    old = AS_OF - timedelta(days=120)
    _ingest(archive, retention_days=30, ingested_at=old)
    assert archive.purge_expired(now=AS_OF) == 8
    assert archive.search(visible_teams=None)["total"] == 0
    raw = sqlite3.connect(str(tmp_path / "a.sqlite"))
    assert raw.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    raw.close()


def test_pagination_and_unknown_project(tmp_path):
    archive = Archive(str(tmp_path / "a.sqlite"))
    events = parse_stream(fixtures.events())
    events.append(
        parse_stream(
            [{"type": "plan_proposed", "ts": "2026-10-17T10:00:00+00:00", "seq": 1,
              "projectId": "proj-unknown", "runId": "run-x", "payload": {"goal": "orphan"}}]
        )[0]
    )
    costs = [parse_cost(c) for c in fixtures.costs()]
    archive.ingest(events, costs, fixtures.PROJECT_TEAM, as_of=AS_OF)

    page = archive.search(visible_teams=None, limit=3, offset=0)
    assert page["total"] == 9 and len(page["runs"]) == 3
    page2 = archive.search(visible_teams=None, limit=3, offset=3)
    assert {r["runId"] for r in page["runs"]}.isdisjoint({r["runId"] for r in page2["runs"]})

    orphan = archive.search(visible_teams=None, q="orphan")
    assert orphan["total"] == 1 and orphan["runs"][0]["teamId"] is None
    assert archive.search(visible_teams={"team-a"})["total"] == 6
