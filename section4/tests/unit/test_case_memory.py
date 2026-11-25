"""Task 9: case-base memory — proposals only, deterministic, similarity + decay."""

from datetime import UTC, datetime, timedelta

import pytest

from harness_orchestrator.case_memory import (
    decayed,
    fingerprint,
    record_case,
    retrieve_similar_cases,
)
from harness_orchestrator.denial_learning import SUGGESTION_MAX_CHARS

NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)


def test_case_is_a_bounded_proposal_never_applied():
    paths = ["docs/a.md"] * 3 + ["x" * 900]
    c = record_case("r1", "denials_repeated", "Write", paths, "denied", NOW)
    assert c["status"] == "proposed"
    assert len(c["suggestedRule"]) <= SUGGESTION_MAX_CHARS
    assert c["paths"] == sorted({"docs/a.md", "x" * 900})


def test_fingerprint_is_order_and_duplicate_insensitive():
    assert fingerprint("d", "Write", ["b", "a", "a"]) == fingerprint("d", "Write", ["a", "b"])
    assert fingerprint("d", "Write", ["a"]) != fingerprint("d", "Bash", ["a"])


def test_half_life_decay():
    assert decayed(1.0, NOW - timedelta(hours=168), NOW) == pytest.approx(0.5)
    assert decayed(1.0, NOW, NOW) == 1.0


def test_retrieval_ranks_similar_recovered_recent_first_and_skips_unrelated():
    fixed = record_case("r1", "denials_repeated", "Write", ["src/auth.py"], "recovered", NOW)
    stale = record_case("r2", "denials_repeated", "Write", ["src/auth.py"], "recovered",
                        NOW - timedelta(days=30))
    other = record_case("r3", "denials_repeated", "Bash", ["infra/main.tf"], "denied", NOW)
    hits = retrieve_similar_cases([stale, other, fixed], ["src/auth.py"], "Write", NOW)
    assert [h["runId"] for h in hits] == ["r1", "r2"]  # unrelated r3 absent; recent beats stale
    again = retrieve_similar_cases([fixed, stale, other], ["src/auth.py"], "Write", NOW)
    assert [h["caseId"] for h in again] == [h["caseId"] for h in hits]  # input order irrelevant


def test_unknown_outcome_rejected():
    with pytest.raises(ValueError):
        record_case("r1", "x", "Write", ["a"], "auto_fixed", NOW)


# --- Track 2 hardening: window scan + exact-fingerprint lookup (2026-11-25) ---------------

import asyncio  # noqa: E402
import json  # noqa: E402
import random  # noqa: E402

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from harness_orchestrator import case_memory  # noqa: E402

_DDL = (
    "CREATE TABLE case_memory (case_id VARCHAR(36) PRIMARY KEY, team_id TEXT NOT NULL,"
    " project_id TEXT, run_id TEXT NOT NULL, failure_class TEXT, tool TEXT, paths TEXT,"
    " fingerprint TEXT, outcome TEXT, weight REAL, suggested_rule TEXT,"
    " status TEXT CHECK (status = 'proposed'), recorded_at TIMESTAMP)"
)


async def _insert(db, team, c):
    await db.execute(text(
        "INSERT INTO case_memory (case_id, team_id, project_id, run_id, failure_class, tool,"
        " paths, fingerprint, outcome, weight, suggested_rule, status, recorded_at) VALUES"
        " (:c, :t, 'p', :r, :f, :tool, :paths, :fp, :o, :w, :s, 'proposed', :at)"),
        {"c": c["caseId"], "t": team, "r": c["runId"], "f": c["failureClass"],
         "tool": c["tool"], "paths": json.dumps(c["paths"]), "fp": c["fingerprint"],
         "o": c["outcome"], "w": c["weight"], "s": c["suggestedRule"], "at": c["recordedAt"]})


def _factory(tmp_path, cases_by_team):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/cases.db")

    async def setup():
        async with engine.begin() as conn:
            await conn.execute(text(_DDL))
            for team, cases in cases_by_team.items():
                for c in cases:
                    await _insert(conn, team, c)

    asyncio.run(setup())
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_merge_candidates_dedupes_by_case_id_and_keeps_first_order():
    a = record_case("r1", "d", "Write", ["a"], "denied", NOW)
    b = record_case("r2", "d", "Write", ["b"], "denied", NOW)
    assert [c["runId"] for c in case_memory.merge_candidates([a, b], [b, a], [])] == ["r1", "r2"]
    assert case_memory.merge_candidates([], []) == []


def test_exact_repeat_older_than_the_window_is_recalled_and_stays_team_scoped(
        tmp_path, monkeypatch):
    monkeypatch.setattr(case_memory, "MAX_SCAN", 5)
    old = record_case("r-old-repeat", "denials_repeated", "bash", ["/etc/nginx/nginx.conf"],
                      "recovered", NOW - timedelta(days=3))
    # Newer, unrelated cases fill the whole window (different tool and paths: score 0).
    noise = [record_case(f"r-noise-{i}", "denials_repeated", "Write", [f"n/{i}.md"], "denied",
                         NOW - timedelta(minutes=i)) for i in range(8)]
    foreign = record_case("r-foreign", "denials_repeated", "bash", ["/etc/nginx/nginx.conf"],
                          "recovered", NOW - timedelta(days=4))
    engine, factory = _factory(tmp_path, {"t1": [old, *noise], "t2": [foreign]})
    query = record_case("q", "denials_repeated", "bash", ["/etc/nginx/nginx.conf"], "denied",
                        NOW)
    try:
        async def go():
            async with factory() as db:
                window = await case_memory.load_cases(db, "t1")
                cands = await case_memory.load_candidates(db, "t1", query["fingerprint"])
            return window, cands

        window, cands = asyncio.run(go())
        assert len(window) == 5 and "r-old-repeat" not in [c["runId"] for c in window]
        # Old behaviour (window only) recalls nothing; the fingerprint lookup finds the repeat.
        assert retrieve_similar_cases(window, query["paths"], "bash", NOW) == []
        hits = retrieve_similar_cases(cands, query["paths"], "bash", NOW,
                                      limit=case_memory.MAX_RECALLED)
        assert [h["runId"] for h in hits] == ["r-old-repeat"]
        assert "r-foreign" not in json.dumps([c["runId"] for c in cands])
    finally:
        asyncio.run(engine.dispose())


def test_union_is_identical_to_the_window_scan_while_a_team_fits_in_the_window(tmp_path):
    rng = random.Random(20261125)
    tools, pool = ["bash", "Write"], [f"src/{n}.py" for n in "abcdef"]
    cases = [record_case(f"r{i}", "denials_repeated", rng.choice(tools),
                         rng.sample(pool, rng.randint(1, 3)),
                         rng.choice(["recovered", "denied", "repeat"]),
                         NOW - timedelta(hours=rng.randint(0, 500))) for i in range(60)]
    engine, factory = _factory(tmp_path, {"t1": cases})
    try:
        async def go():
            out = []
            async with factory() as db:
                for _ in range(25):
                    tool, paths = rng.choice(tools), rng.sample(pool, rng.randint(1, 3))
                    fp = fingerprint("denials_repeated", tool, paths)
                    window = await case_memory.load_cases(db, "t1")
                    union = await case_memory.load_candidates(db, "t1", fp)
                    out.append((retrieve_similar_cases(window, paths, tool, NOW, limit=3),
                                retrieve_similar_cases(union, paths, tool, NOW, limit=3)))
            return out

        for before, after in asyncio.run(go()):
            assert [h["caseId"] for h in before] == [h["caseId"] for h in after]
    finally:
        asyncio.run(engine.dispose())


def test_recall_and_record_finds_an_old_repeat_then_stores_a_proposal(tmp_path, monkeypatch):
    monkeypatch.setattr(case_memory, "MAX_SCAN", 2)
    old = record_case("r-old", "denials_repeated", "bash", ["infra/x.tf"], "recovered",
                      NOW - timedelta(days=2))
    noise = [record_case(f"n{i}", "denials_repeated", "Write", [f"d/{i}"], "denied",
                         NOW - timedelta(hours=i)) for i in range(4)]
    engine, factory = _factory(tmp_path, {"t1": [old, *noise]})
    try:
        similar = asyncio.run(case_memory.recall_and_record(
            factory, "t1", "p", "r-new", "denials_repeated", "bash", ["infra/x.tf"], NOW))
        assert [s["runId"] for s in similar] == ["r-old"]
        assert len(similar) <= case_memory.MAX_RECALLED

        async def rows():
            async with factory() as db:
                return (await db.execute(text(
                    "SELECT status FROM case_memory WHERE run_id = 'r-new'"))).all()

        assert [r.status for r in asyncio.run(rows())] == ["proposed"]  # CD-02
    finally:
        asyncio.run(engine.dispose())
