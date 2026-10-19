"""Part 50 (lane SE): search. Sqlite with the REAL migrations, a fake clock, explicit index syncs.
Behaviour: docs/search-and-export.md. The invariant under test throughout: the index is never the
authority, so a stale or even hostile index row cannot show a user anything they may not see."""

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import text

from harness_orchestrator import exports, search

from . import test_search_support as support
from .test_search_support import P, err

env = support.env  # the shared fixture


def texts(env_, user, **params):
    return [h["snippet"] for h in env_.find(user, **params)["results"]]


# ------------------------------------------------------------------- basic indexing


def test_comments_are_indexed_and_found_with_a_live_snippet(env):
    env.post("lead", "run", "r1", "the quarterly invoice export is slow")
    env.post("lead", "session", "s1", "invoice numbers must stay stable")
    res = env.sync()
    assert res["errors"] == []
    assert res["team-a"]["comments"]["indexed"] == 2
    out = env.find("eng2", q="invoice")
    assert len(out["results"]) == 2
    hit = out["results"][0]
    assert hit["kind"] == "comment" and "invoice" in hit["snippet"]
    assert out["snippetEncoding"] == "html"
    assert not any(k in out for k in ("total", "count", "totalCount"))


def test_second_sync_is_incremental_via_watermarks(env):
    env.post("lead", "run", "r1", "first note")
    first = env.sync("team-a")["team-a"]["comments"]
    assert first["indexed"] == 1
    env.tick(seconds=1)
    env.post("lead", "run", "r1", "second note")
    second = env.sync("team-a")["team-a"]["comments"]
    assert second["watermark"] > first["watermark"]
    wm = env.q("SELECT watermark FROM search_index_state WHERE source = 'comments'")
    assert wm[0].watermark == second["watermark"]
    assert len(env.docs("comment")) == 2
    # an unchanged source re-indexes nothing new (overlap rows are upserts, not duplicates)
    env.sync("team-a")
    assert len(env.docs("comment")) == 2


def test_event_stream_uses_a_seq_watermark_and_skips_noise(env):
    env.event("approval.requested", {"approvalId": "a1", "reason": "needs a review"})
    env.event("presence.joined", {"x": 1})
    env.event("typing.start", {})
    env.event("cursor.moved", {})
    env.event("guidance.delivered", {"upTo": 4})
    env.agent_event("engine_completed", {"summary": "all green after retry"})
    env.agent_event("terminal_output", {"summary": "SECRET-LOOKING build log line"})
    env.agent_event("diff_progress", {"summary": "diff chunk"})
    res = env.sync("team-a")["team-a"]
    assert res["collab_events"]["indexed"] == 1 and res["agent_events"]["indexed"] == 1
    kinds = sorted(d.title for d in env.docs("event"))
    assert kinds == ["approval.requested", "engine_completed"]
    assert res["collab_events"]["watermark"] == str(
        env.q("SELECT MAX(seq) AS m FROM run_collab_events")[0].m
    )
    assert env.hits("lead", q="green") == [("event", "a1")]
    env.event("redirect.guidance", {"content": "please add pagination"})
    env.sync("team-a")
    assert [h for h in env.hits("lead", q="pagination")] == [("event", "c6")]


def test_missing_source_tables_are_skipped_not_errors(env):
    env.x("DROP TABLE artifacts")
    res = env.sync("team-a")
    assert res["errors"] == []
    assert res["team-a"]["artifacts"] == {"skipped": True}
    assert res["team-a"]["comments"].get("skipped") is None


# ------------------------------------------------------ nothing hidden is ever indexed


def test_hidden_deleted_and_purged_comments_are_not_indexed(env):
    keep = env.comment_id("run", "r1", "visible orchard plan")
    hid = env.comment_id("run", "r1", "hidden orchard plan", user="eng2")
    dele = env.comment_id("run", "r1", "deleted orchard plan", user="eng2")
    env.as_("boss").post(
        f"{P}/runs/r1/comments/{hid}/moderate", json={"action": "hide", "reason": "off topic"}
    )
    assert env.as_("eng2").delete(f"{P}/runs/r1/comments/{dele}").status_code == 200
    env.sync()
    assert [d.source_id for d in env.docs("comment")] == [keep]
    assert env.hits("lead", q="orchard") == [("comment", keep)]


def test_a_stale_index_row_cannot_leak_a_comment_hidden_afterwards(env):
    cid = env.comment_id("run", "r1", "launch codes are in the vault")
    env.sync()
    assert env.hits("eng2", q="vault") == [("comment", cid)]
    # hide it WITHOUT re-syncing: the index still holds the row
    env.as_("boss").post(
        f"{P}/runs/r1/comments/{cid}/moderate", json={"action": "hide", "reason": "sensitive"}
    )
    assert env.docs("comment")[0].source_id == cid
    assert env.hits("eng2", q="vault") == []
    assert env.hits("boss", q="vault") == []  # the index never serves hidden text, even to admins
    env.sync()
    assert env.docs("comment") == []  # the next sync removes the document


def test_deleted_and_past_retention_comments_vanish_from_hits_before_any_job_runs(env):
    cid = env.comment_id("run", "r1", "retention example sentence")
    env.sync()
    assert env.hits("lead", q="retention") == [("comment", cid)]
    env.as_("boss").put(f"{P}/discussion-settings", json={"retentionDays": 30})
    env.tick(days=31)
    assert env.hits("lead", q="retention") == []


def test_secret_shaped_text_is_redacted_in_the_index_and_in_snippets(env):
    env.post(
        "lead",
        "run",
        "r1",
        "deploy with password=hunter2 and token ghp_abcdefghijklmnopqrstuvwxyz0123 ok",
    )
    env.event(
        "redirect.guidance",
        {
            "content": "use Bearer abcdefghijklmnopqrstuvwxyz now",
            "apiKey": "sk-should-never-appear-anywhere-0001",
        },
    )
    env.sync()
    blob = " ".join(d.body + " " + d.title for d in env.docs())
    for secret in ("hunter2", "ghp_abcdef", "abcdefghijklmnopqrstuvwxyz", "sk-should"):
        assert secret not in blob
    assert env.hits("lead", q="hunter2") == []
    out = env.find("lead", q="deploy")
    assert "[redacted]" in out["results"][0]["snippet"]
    assert "hunter2" not in json.dumps(out)


def test_hidden_reasoning_keys_are_not_indexed(env):
    env.event(
        "redirect.guidance",
        {"content": "visible guidance", "reasoning": "private plan xyzzy", "thoughts": "xyzzy"},
    )
    env.sync()
    assert env.hits("lead", q="xyzzy") == []
    assert env.hits("lead", q="visible") == [("event", "c1")]


def test_deleted_sessions_and_unavailable_artifacts_are_not_searchable(env):
    env.x(
        "INSERT INTO artifacts VALUES ('ar1', 'team-a', 'r1', 'report-final.txt',"
        " 'text/plain', 'output', 'available', 'lead', :c)",
        c=env.clock["t"],
    )
    env.x(
        "INSERT INTO artifacts VALUES ('ar2', 'team-a', 'r1', 'report-evil.exe',"
        " 'application/x-msdownload', 'upload', 'quarantined', 'lead', :c)",
        c=env.clock["t"],
    )
    env.x(
        "UPDATE collab_sessions SET state = 'deleted', title = 'doomed roadmap' WHERE id = 'spriv'"
    )
    env.sync()
    assert env.hits("lead", q="report") == [("artifact", "ar1")]
    assert env.hits("boss", q="doomed") == []
    # quarantined AFTER indexing, before the next sync: still never served
    env.x("UPDATE artifacts SET state = 'quarantined' WHERE id = 'ar1'")
    assert env.hits("lead", q="report") == []
    env.sync()
    assert env.docs("artifact") == []


# --------------------------------------------------- authorization re-checked per hit


def test_team_isolation_and_planted_foreign_rows(env):
    env.post("evil", "session", "sb", "foreign orchard secrets")
    env.sync()
    assert env.hits("lead", q="orchard") == []
    assert env.hits("evil", q="orchard") != []
    # a hostile/stale index row of team-b's run planted INSIDE team-a's partition serves nothing
    env.x(
        "INSERT INTO search_documents (id, team_id, kind, source_id, title, body, created_at,"
        " indexed_at) VALUES ('x1', 'team-a', 'run', 'r3', 'other', 'other executing', :c, :c)",
        c=env.clock["t"],
    )
    assert env.hits("lead", q="other") == []  # r3 is not team-a's run
    assert env.hits("evil2", q="other") == [("run", "r3")]


def test_private_session_comments_follow_membership_live(env):
    env.post("lead", "session", "spriv", "private budget numbers")
    env.sync()
    assert env.hits("lead", q="budget") != []
    assert env.hits("eng2", q="budget") == []  # same team, not a member
    assert env.hits("boss", q="budget") != []  # admins see every session of the team
    env.x(
        "UPDATE session_members SET status = 'removed' WHERE session_id = 'spriv'"
        " AND user_id = 'lead'"
    )
    assert env.hits("lead", q="budget") == []  # lost access: nothing, with the index untouched
    assert len(env.docs("comment")) == 1


def test_no_oracle_cursor_or_count_for_rows_the_caller_cannot_see(env):
    for i in range(4):
        env.tick(seconds=1)
        env.post("lead", "session", "spriv", f"zebra private {i}")
    env.tick(seconds=1)
    shown = env.comment_id("run", "r1", "zebra public one")
    env.sync()
    out = env.find("eng2", q="zebra", limit=1)
    assert [h["sourceId"] for h in out["results"]] == [shown]
    assert out["nextCursor"] is None  # four private matches exist; none may be hinted at
    assert out["scanLimited"] is False
    empty = env.find("eng2", q="zebra private")
    assert empty["results"] == [] and empty["nextCursor"] is None


def test_edited_text_is_judged_on_the_live_comment_not_the_indexed_copy(env):
    cid = env.comment_id("run", "r1", "alpha-sentinel before editing")
    env.sync()
    r = env.as_("lead").patch(
        f"{P}/runs/r1/comments/{cid}",
        json={"body": "gamma-sentinel after editing", "expectedVersion": 1},
    )
    assert r.status_code == 200
    assert env.hits("lead", q="alpha-sentinel") == []  # stale term: dropped, never shown
    assert env.hits("lead", q="gamma-sentinel") == []  # not indexed yet: eventual
    env.sync()
    out = env.find("lead", q="gamma-sentinel")
    assert [h["sourceId"] for h in out["results"]] == [cid]
    assert "alpha" not in json.dumps(out)


def test_pilot_allowlist_hides_run_data_but_not_session_comments(env, monkeypatch):
    env.post("lead", "run", "r1", "pilot gated sentence")
    env.post("lead", "session", "s1", "pilot gated sentence")
    env.sync()
    assert len(env.hits("lead", q="gated")) == 2
    monkeypatch.setenv("HARNESS_PILOT_TEAMS", "team-z")
    after = env.hits("lead", q="gated")
    assert len(after) == 1 and after[0][0] == "comment"
    assert all(h["sessionId"] == "s1" for h in env.find("lead", q="gated")["results"])


# ---------------------------------------------------------------------------- filters


def seed_filters(env):
    env.post("lead", "run", "r1", "tabletop topic")
    env.tick(hours=1)
    env.post("eng2", "run", "r1", "tabletop again")
    env.tick(hours=1)
    env.approval("ap1", diff="d")
    env.x("UPDATE high_impact_approvals SET summary = 'tabletop deletion', paths = '[\"a.py\"]'")
    env.sync()


def test_filters_kind_participant_status_date_project_and_approval_state(env):
    seed_filters(env)
    assert {k for k, _ in env.hits("lead", q="tabletop")} == {"comment", "approval"}
    assert [k for k, _ in env.hits("lead", q="tabletop", kind="approval")] == ["approval"]
    only_eng2 = env.find("lead", q="tabletop", participantId="eng2")["results"]
    assert [h["actorUserId"] for h in only_eng2] == ["eng2"]
    assert env.hits("lead", q="tabletop", approvalState="pending") == [("approval", "ap1")]
    assert env.hits("lead", q="tabletop", approvalState="approved") == []
    assert env.hits("lead", kind="run", status="executing") == [("run", "r1")]
    assert env.hits("lead", kind="run", approvalState="pending") == [("run", "r1")]
    assert env.hits("lead", q="tabletop", projectId="p") != []
    assert env.hits("lead", q="tabletop", projectId="q") == []
    first = "2030-01-01T12:30:00+00:00"
    last = "2030-01-01T13:30:00+00:00"
    window = env.find("lead", q="tabletop", kind="comment", **{"from": first, "to": last})
    assert [h["actorUserId"] for h in window["results"]] == ["eng2"]


def test_filters_are_rechecked_against_the_live_source(env):
    seed_filters(env)
    env.x("UPDATE agent_runs SET state = 'done' WHERE id = 'r1'")
    assert env.hits("lead", kind="run", status="executing") == []  # indexed value is stale
    env.x("UPDATE high_impact_approvals SET state = 'approved' WHERE id = 'ap1'")
    assert env.hits("lead", q="tabletop", approvalState="pending") == []


def test_query_validation(env):
    err(env.as_("lead").get(f"{P}/search"), 422, "BAD_QUERY")
    err(env.as_("lead").get(f"{P}/search", params={"q": "x"}), 422, "BAD_QUERY")
    err(
        env.as_("lead").get(f"{P}/search", params={"q": "ok", "cursor": "garbage"}),
        422,
        "BAD_CURSOR",
    )
    err(
        env.as_("lead").get(f"{P}/search", params={"q": "ok", "from": "yesterday"}),
        422,
        "BAD_FILTER",
    )
    err(
        env.as_("lead").get(
            f"{P}/search", params={"q": "ok", "from": "2031-01-01", "to": "2030-01-01"}
        ),
        422,
        "BAD_FILTER",
    )
    assert env.as_("lead").get(f"{P}/search", params={"q": "ok", "kind": "nope"}).status_code == 422


# -------------------------------------------------------------------- paging, snippets


def test_cursor_paging_never_skips_or_repeats(env):
    ids = []
    for i in range(7):
        env.tick(seconds=1)
        ids.append(env.comment_id("run", "r1", f"pagingword number {i}"))
    env.sync()
    seen, cursor = [], None
    for _ in range(10):
        params = {"q": "pagingword", "limit": 3, **({"cursor": cursor} if cursor else {})}
        out = env.find("eng2", **params)
        seen += [h["sourceId"] for h in out["results"]]
        cursor = out["nextCursor"]
        if cursor is None:
            break
    assert seen == list(reversed(ids))


def test_snippets_are_escaped_bounded_and_built_from_live_text(env):
    long = "x" * 300 + " <script>alert(1)</script> & marker " + "y" * 300
    env.post("lead", "run", "r1", long)
    env.sync()
    hit = env.find("lead", q="marker")["results"][0]
    assert "<script>" not in hit["snippet"] and "&lt;script&gt;" in hit["snippet"]
    assert len(hit["snippet"]) <= 3 * 200  # bounded (escaping can expand, never unbounded)
    assert "marker" in hit["snippet"]


def test_runs_sessions_and_approvals_are_searchable_by_title_and_body(env):
    env.approval("ap1")
    env.x("UPDATE high_impact_approvals SET summary = 'remove legacy tables'")
    env.sync()
    assert env.hits("lead", q="alpha", kind="run") == [("run", "r1")]
    assert env.hits("lead", q="billing") == [("run", "r1")]  # the plan goal
    assert env.hits("lead", q="Session title s1") == [("session", "s1")]
    assert env.hits("eng2", q="Session title spriv") == []  # not a member
    assert env.hits("lead", q="legacy") == [("approval", "ap1")]
    assert env.hits("evil", q="alpha") == []


# --------------------------------------------------------------- status, refresh, reindex


def test_status_documents_delay_consistency_and_hides_counts_from_non_admins(env):
    env.post("lead", "run", "r1", "status check")
    env.sync()
    env.tick(seconds=90)
    low = env.as_("eng2").get(f"{P}/search/status").json()
    assert low["engine"] == "like" and low["documentCounts"] == {}
    src = {s["source"]: s for s in low["sources"]}
    assert src["comments"]["lagSeconds"] == 90 and src["comments"]["watermark"]
    assert "re-authorised" in low["consistency"] and "eventual" in low["consistency"]
    assert low["indexing"]["overlapSeconds"] == 300
    assert "quarantined or deleted artifacts" in low["notIndexed"]
    admin = env.as_("boss").get(f"{P}/search/status").json()
    assert admin["documentCounts"]["comment"] == 1
    assert env.as_("evil2").get(f"{P}/search/status").json()["documentCounts"] == {}


def test_query_refreshes_a_stale_index_but_not_a_fresh_one(env, monkeypatch):
    monkeypatch.setenv("HARNESS_SEARCH_AUTO_SYNC_SECONDS", "30")
    env.post("lead", "run", "r1", "refresh alpha")
    assert env.hits("lead", q="refresh") != []  # first query builds the index
    env.post("lead", "run", "r1", "refresh beta")
    assert len(env.hits("lead", q="refresh")) == 1  # fresh enough: not re-synced
    env.tick(seconds=31)
    assert len(env.hits("lead", q="refresh")) == 2


def test_reindex_rebuilds_audits_and_cleans_hard_deleted_sources(env):
    cid = env.comment_id("run", "r1", "doomed comment text")
    env.comment_id("run", "r1", "survivor text")
    env.sync()
    env.x("DELETE FROM comments WHERE id = :i", i=cid)  # hard delete at the source
    env.sync()
    assert len(env.docs("comment")) == 2  # incremental sync cannot see a vanished row...
    assert env.hits("lead", q="doomed") == []  # ...but serving drops it (live source missing)
    out = asyncio.run(search.reindex(env.factory, "team-a"))
    assert len(env.docs("comment")) == 1 and out["errors"] == []
    audit = env.audit("search.reindexed")
    assert len(audit) == 1
    detail = json.loads(audit[0].detail)
    assert detail["teamId"] == "team-a" and "doomed" not in audit[0].detail
    assert detail["documents"]["comment"] == 1


def test_postgres_search_sql_uses_full_text_and_degrades_to_like_elsewhere():
    sql, args = search.match_sql("fts", ["billing", "export"])
    assert "to_tsvector('simple'" in sql and args == {"fts": "billing export"}
    sql, args = search.match_sql("like", ["100%", "a_b"])
    assert sql.count("LIKE") == 2 and args["m0"] == "%100!%%" and args["m1"] == "%a!_b%"
    assert search.match_sql("fts", []) == ("1 = 1", {})


def test_erased_authors_events_are_not_reindexed(env):
    env.event("redirect.guidance", {"content": "gone-person said something"}, actor="eng2")
    env.event("redirect.guidance", {"content": "kept-person said something"}, actor="lead")
    env.sync()
    assert len(env.docs("event")) == 2
    r = env.as_("eng2").post(f"{P}/deletion-requests", json={})
    assert r.status_code == 201, r.text
    env.tick(seconds=1)
    r = env.as_("boss").post(f"{P}/deletion-requests/{r.json()['id']}/approve", json={})
    assert r.status_code == 200 and r.json()["state"] == "executed", r.text
    assert [d.body for d in env.docs("event")] == ["kept-person said something"]
    env.event("redirect.guidance", {"content": "late-gone-event"}, actor="eng2")  # same instant
    env.tick(seconds=5)
    env.event("redirect.guidance", {"content": "new-after-deletion"}, actor="eng2")
    env.sync()
    assert env.hits("lead", q="new-after-deletion") != []
    assert env.hits("lead", q="gone-person") == []
    assert env.hits("lead", q="late-gone-event") == []


@pytest.mark.parametrize("user", ["lead", "view", "guest"])
def test_every_role_may_search_but_only_what_it_may_see(env, user):
    env.post("lead", "run", "r1", "role sentence")
    env.sync()
    assert env.hits(user, q="role sentence") == [("comment", env.docs("comment")[0].source_id)]


def test_ddl_is_portable_and_team_columns_exist(env):
    cols = {r[1] for r in env.q("SELECT * FROM pragma_table_info('search_documents')")}
    assert {"team_id", "kind", "source_id", "participant_ids", "approval_state"} <= cols
    with pytest.raises(Exception):  # noqa: B017 - the kind CHECK must hold
        env.x(
            "INSERT INTO search_documents (id, team_id, kind, source_id, created_at, indexed_at)"
            " VALUES ('z', 'team-a', 'bogus', 'x', :c, :c)",
            c=env.clock["t"],
        )
    assert text("SELECT 1") is not None


# ------------------------------------------------------- degradation, batching, budgets


def test_full_text_failure_degrades_to_like(env, monkeypatch):
    env.post("lead", "run", "r1", "fallback sentence here")
    env.sync()
    monkeypatch.setattr(search, "_engine", lambda db: "fts")  # sqlite has no to_tsvector
    assert [h[0] for h in env.hits("lead", q="fallback")] == ["comment"]


def test_indexing_pages_through_batches_and_reports_partial_progress(env, monkeypatch):
    monkeypatch.setattr(search, "BATCH", 2)
    for i in range(5):
        env.tick(seconds=1)
        env.post("lead", "run", "r1", f"batchword {i}")
    for i in range(6):
        env.event("redirect.guidance", {"content": f"seqword {i}"})
    first = env.sync("team-a", max_batches=1)["team-a"]
    assert first["comments"]["indexed"] == 5  # mutable sources always finish
    assert first["collab_events"]["more"] is True
    assert int(first["collab_events"]["watermark"]) < 11
    env.sync("team-a")
    env.sync("team-a")
    assert len(env.hits("lead", q="seqword", limit=50)) == 6
    assert len(env.hits("lead", q="batchword", limit=50)) == 5


def test_concurrent_syncs_do_not_duplicate_documents(env):
    for i in range(3):
        env.post("lead", "run", "r1", f"concurrent {i}")

    async def go():
        await asyncio.gather(*[search.sync_index(env.factory, "team-a") for _ in range(3)])

    asyncio.run(go())
    assert len(env.docs("comment")) == 3


def test_scan_budget_exhaustion_is_reported_and_the_cursor_continues(env, monkeypatch):
    monkeypatch.setattr(search, "CAND_BATCH", 2)
    monkeypatch.setenv("HARNESS_SEARCH_SCAN_BUDGET", "4")
    env.post("eng2", "run", "r1", "budgetword visible oldest")
    for i in range(6):
        env.tick(seconds=1)
        env.post("lead", "session", "spriv", f"budgetword private {i}")
    env.sync()
    first = env.find("eng2", q="budgetword")
    assert first["results"] == [] and first["scanLimited"] is True and first["nextCursor"]
    cursor, found = first["nextCursor"], []
    for _ in range(5):
        out = env.find("eng2", q="budgetword", cursor=cursor)
        found += [h["snippet"] for h in out["results"]]
        cursor = out["nextCursor"]
        if not cursor:
            break
    assert found == ["budgetword visible oldest"]


def test_session_export_files_are_deleted_with_the_authors_data(env):
    env.post("eng2", "session", "s1", "session words")
    job = env.export("lead", kind="session_history", scopeId="s1")
    assert Path(exports.store_dir(), f"{job['id']}.json").exists()
    rid = env.as_("eng2").post(f"{P}/deletion-requests", json={}).json()["id"]
    assert env.as_("boss").post(f"{P}/deletion-requests/{rid}/approve", json={}).status_code == 200
    assert not Path(exports.store_dir(), f"{job['id']}.json").exists()
