#!/usr/bin/env python3
"""Offline S-02 / S-01 cache check: zero spend, no network, no Docker, no database.

Replays a recorded multi-attempt run (``fixtures/cache-replay-v1.json``) through the REAL
section4 executor seam (``OrchestratorService._execute``) with a capturing engine, then
feeds the engine results through the REAL ``scripts/eval/bench_stats.py``. It checks:

  C1  every attempt's engine result carries the same ``promptPrefix`` sha256, and that
      digest is the sha256 of the first ``bytes`` bytes of the goal the engine received;
  C2  every goal starts with the identical prefix bytes; text edited mid-run never shows up
      in a later attempt's goal;
  C3  simulated byte-prefix cache: the common prefix of consecutive goals covers at least
      the frozen prefix plus the task text (``prefix + plan.goal``), i.e. what a provider
      prefix cache could serve is never smaller than the stable part;
  C4  ``bench_stats.cell_stats`` reports the hand-computed cache-read split for the
      fixture usage (pooled ratio, per-attempt ratios, nWithUsage, one prefix digest);
  C5  control: with the freeze removed (the pre-S-02 behaviour: re-render every attempt)
      C1 and C4's digest count FAIL, so this check can detect the regression it guards.

What this does NOT show: real provider cache-hit ratios. Usage in the fixture is
synthetic. Real cache-read ratios and TTFT need a live paid batch on the owner's Mac
(``bench_trials.py`` against a real provider), which this script never runs.

Usage (section4 venv, from the repo root):
    section4/.venv/bin/python scripts/eval/cache_offline_check.py [--out report.json]
Exit 0 = all checks pass, 1 = a check failed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
FIXTURE = HERE / "fixtures" / "cache-replay-v1.json"
sys.path.insert(0, str(REPO / "section4" / "src"))
sys.path.insert(0, str(HERE))

import bench_stats
import harness_orchestrator.service as service_module
from harness_orchestrator import prompt_prefix
from harness_orchestrator.service import OrchestratorService

# The minimum schema the executor seam touches (same tables as section4's
# tests/unit/test_goal_image_execution.py). A throwaway SQLite file: no Postgres needed.
DDL = """
CREATE TABLE teams (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) NOT NULL, budget_usd REAL NULL);
CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL);
CREATE TABLE run_claims (
    run_id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL, state VARCHAR(20) NOT NULL,
    claimed_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, worker_id VARCHAR(80) NOT NULL);
CREATE TABLE cost_ledger (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR(36),
    agent_role VARCHAR(20) DEFAULT 'primary', model VARCHAR(120) DEFAULT 'stub',
    input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
    cost_usd NUMERIC(12,6) DEFAULT 0, latency_ms INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00');
CREATE TABLE agent_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id VARCHAR(36) NOT NULL, type VARCHAR(80) NOT NULL,
    payload TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00');
CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL);
CREATE TABLE agent_plans (
    id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL, goal TEXT NOT NULL,
    files_likely_to_change TEXT DEFAULT '[]', steps TEXT DEFAULT '[]', tests TEXT DEFAULT '[]',
    risks TEXT DEFAULT '[]', estimated_cost_usd REAL DEFAULT 0, forbidden_paths TEXT DEFAULT '[]',
    expected_result TEXT DEFAULT '', status VARCHAR(20) NOT NULL,
    created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00+00:00');
"""


def _sqlite_factory(path: Path):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    async def setup():
        async with engine.begin() as db:
            for stmt in DDL.split(";"):
                if stmt.strip():
                    await db.execute(text(stmt))
            await db.execute(text("INSERT INTO projects (id, team_id) VALUES ('p1', 't1')"))

    asyncio.run(setup())
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class ReplayEngine:
    """Records the goal of each attempt and answers with that attempt's recorded usage."""

    api_key = None

    def __init__(self, usages: list[dict[str, int]]):
        self.usages = list(usages)
        self.goals: list[str] = []

    def execute_goal(self, goal=None, session_id=None, model=None, on_progress=None, images=None):
        self.goals.append(goal or "")
        usage = self.usages[len(self.goals) - 1]
        return {"sessionId": session_id, "engine": "replay", "status": "completed",
                "finalResponse": "ok", "usage": dict(usage)}


def _common_prefix_len(a: bytes, b: bytes) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def replay(fixture: dict[str, Any], freeze: bool = True) -> dict[str, Any]:
    """Run the fixture's attempts through the service; return what the engine saw."""
    attempts = fixture["attempts"]
    engine = ReplayEngine([a["usage"] for a in attempts])
    tmp = tempfile.TemporaryDirectory()
    db_engine, factory = _sqlite_factory(Path(tmp.name) / "replay.db")
    # The guidance seam only runs with a session factory; it is patched below.
    svc = OrchestratorService(engine=engine, session_factory=factory)
    state = {"guidance": ""}

    async def guidance_since(_factory, _run_id, after):
        return state["guidance"], after + 1

    original_guidance = service_module.collab.guidance_since
    original_frozen = prompt_prefix.frozen_prefix
    service_module.collab.guidance_since = guidance_since
    if not freeze:  # C5 control: the pre-S-02 behaviour, re-rendered every attempt
        prompt_prefix.frozen_prefix = prompt_prefix.assemble_prefix
    try:
        async def start():
            await svc.propose_plan("replay-run", goal=fixture["goal"], project_id="p1")
            await svc.approve_plan("replay-run", svc.runs["replay-run"]["plan_id"], "u-replay")

        asyncio.run(start())
        run_data = svc.runs["replay-run"]
        run_data["teamId"] = "t1"
        plan = svc.planner.get_plan(run_data["plan_id"])
        run_data.update(json.loads(json.dumps(fixture["sources"])))
        results = []
        for a in attempts:
            run_data.update(a.get("edits") or {})
            state["guidance"] = a.get("guidance") or ""
            asyncio.run(svc._execute(run_data, plan))
            results.append(dict(run_data.get("engine_result") or {}))
    finally:
        service_module.collab.guidance_since = original_guidance
        prompt_prefix.frozen_prefix = original_frozen
        asyncio.run(db_engine.dispose())
        tmp.cleanup()
    return {"goals": engine.goals, "results": results, "taskText": plan.goal}


def evaluate(fixture: dict[str, Any], rep: dict[str, Any]) -> dict[str, Any]:
    goals = [g.encode("utf-8") for g in rep["goals"]]
    results = rep["results"]
    task = rep["taskText"].encode("utf-8")
    edited = [v for a in fixture["attempts"] for v in (a.get("edits") or {}).values()]
    checks: dict[str, dict[str, Any]] = {}

    records = [r.get("promptPrefix") or {} for r in results]
    digests = [rec.get("sha256") for rec in records]
    sizes = [rec.get("bytes") for rec in records]
    head_ok = all(
        isinstance(n, int) and hashlib.sha256(g[:n]).hexdigest() == d
        for g, d, n in zip(goals, digests, sizes)
    )
    checks["C1_same_digest_every_attempt"] = {
        "pass": bool(digests) and None not in digests and len(set(digests)) == 1 and head_ok,
        "distinctDigests": len({d for d in digests if d}),
        "digestIsHeadOfGoal": head_ok,
    }

    prefix_len = sizes[0] if sizes and isinstance(sizes[0], int) else 0
    first_prefix = goals[0][:prefix_len] if goals else b""
    leaked = [e for e in edited if any(e.encode("utf-8") in g for g in goals)]
    guidance = [(a.get("guidance") or "").encode("utf-8") for a in fixture["attempts"]]
    volatile_last = all(not v or g.rfind(v) >= prefix_len + len(task)
                        for g, v in zip(goals, guidance))
    checks["C2_identical_prefix_bytes_no_mid_run_edits"] = {
        "pass": (prefix_len > 0 and all(g.startswith(first_prefix) for g in goals)
                 and not leaked and volatile_last),
        "prefixBytes": prefix_len,
        "guidanceAfterTask": volatile_last,
        "midRunEditsLeaked": len(leaked),
    }

    stable = prefix_len + len(task)
    reuse = [None] + [_common_prefix_len(goals[i - 1], goals[i]) for i in range(1, len(goals))]
    checks["C3_simulated_prefix_cache_covers_stable_part"] = {
        "pass": all(r is not None and r >= stable for r in reuse[1:]),
        "stableBytes": stable,
        "reusableBytesPerAttempt": reuse,
        "goalBytesPerAttempt": [len(g) for g in goals],
        "note": "byte-level longest common prefix with the previous attempt; a simulation of a "
                "prefix cache, not a provider measurement",
    }

    attempts = [{"usage": r.get("usage"), "promptPrefix": r.get("promptPrefix")} for r in results]
    cell = bench_stats.cell_stats("cache-replay-v1", "replay", "offline", attempts)
    cache = cell.get("cache") or {}
    per_attempt = [bench_stats.cache_run(a)["cacheReadRatio"] for a in attempts]
    exp = fixture["expected"]
    checks["C4_bench_stats_cache_split"] = {
        "pass": (cache.get("pooledCacheReadRatio") == exp["pooledCacheReadRatio"]
                 and per_attempt == exp["perAttemptCacheReadRatio"]
                 and cache.get("nWithUsage") == exp["nWithUsage"]
                 and cache.get("distinctPrefixDigests") == exp["distinctPrefixDigests"]
                 and cache.get("claim") == exp["claim"]),
        "pooledCacheReadRatio": cache.get("pooledCacheReadRatio"),
        "perAttemptCacheReadRatio": per_attempt,
        "nWithUsage": cache.get("nWithUsage"),
        "distinctPrefixDigests": cache.get("distinctPrefixDigests"),
        "claim": cache.get("claim"),
        "expected": {k: v for k, v in exp.items() if k != "note"},
    }
    return {"checks": checks, "cache": cache}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fixture", type=Path, default=FIXTURE)
    ap.add_argument("--out", type=Path, default=None, help="write the JSON report here")
    args = ap.parse_args(argv)
    fixture = json.loads(args.fixture.read_text())
    # The executor's best-effort side paths (rules, checkpoints, metering) log warnings
    # against the minimal schema; they do not affect the goal bytes this check reads.
    logging.disable(logging.WARNING)

    frozen = evaluate(fixture, replay(fixture, freeze=True))
    control = evaluate(fixture, replay(fixture, freeze=False))
    control_detected = (not control["checks"]["C1_same_digest_every_attempt"]["pass"]
                        and not control["checks"]["C4_bench_stats_cache_split"]["pass"])
    frozen["checks"]["C5_control_without_freeze_is_detected"] = {
        "pass": control_detected,
        "controlDistinctDigests": control["checks"]["C1_same_digest_every_attempt"][
            "distinctDigests"],
        "controlReusableBytesPerAttempt": control["checks"][
            "C3_simulated_prefix_cache_covers_stable_part"]["reusableBytesPerAttempt"],
    }
    ok = all(c["pass"] for c in frozen["checks"].values())
    report = {
        "check": "cache-offline-check",
        "fixture": str(args.fixture.relative_to(REPO)) if args.fixture.is_relative_to(REPO)
        else str(args.fixture),
        "fixtureVersion": fixture.get("version"),
        "verdict": "PASS" if ok else "FAIL",
        "checks": frozen["checks"],
        "scope": "offline replay through the real executor seam + real bench_stats; usage is "
                 "synthetic. Real cache-hit ratios need a live paid batch on the owner's Mac.",
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n")
    print(text)
    print(f"\n{report['verdict']} cache-offline-check", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
