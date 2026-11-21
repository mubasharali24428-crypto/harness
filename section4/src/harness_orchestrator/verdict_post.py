"""P-15 wiring: read one run's RECORDED facts, build the advisory verdict comment, and POST it
to the GitHub service. Operator/CLI path for the supervised pilot; the comment is advisory only
and a human merges. Unknown facts stay unknown (see verdict_comment).

    HARNESS_DATABASE_URL=... HARNESS_GITHUB_URL=... HARNESS_REVIEW_URL=... HARNESS_API_TOKEN=... \
        python -m harness_orchestrator.verdict_post RUN_ID [--dry-run]

``--dry-run`` prints the comment and posts nothing. Needs the project's GitHub write opt-in on the
GitHub service side (the service refuses otherwise). Section isolation: this module talks to
section2 and section8 over HTTP only; it imports neither.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import httpx
from harness_contracts.http_client import env_client
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .review_summary import build_review_summary, exit_code_from_summary
from .verdict_comment import build_verdict_comment, verdict_marker

_RUN_SQL = text(
    "SELECT r.project_id AS project_id, r.pr_id AS pr_id, pr.github_pr_number AS pr_number"
    " FROM agent_runs r LEFT JOIN pull_requests pr ON pr.id = r.pr_id WHERE r.id = :r"
)


def _json(value: Any) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


async def collect_facts(db: AsyncSession, run_id: str) -> dict:
    """Recorded facts for a run: ``{facts, project_id, pr_id, pr_number}``. Raises LookupError
    when the run is unknown or has no linked PR. Sandbox boundary is not recorded per run today,
    so it is left out and renders as "not recorded"."""
    run = (await db.execute(_RUN_SQL, {"r": run_id})).mappings().first()
    if run is None:
        raise LookupError(f"unknown run {run_id}")
    if run["pr_number"] is None:
        raise LookupError(f"run {run_id} has no linked PR")
    approval = (
        await db.execute(
            text("SELECT payload, ts FROM agent_events WHERE run_id = :r AND type = 'plan_approved'"
                 " ORDER BY seq DESC LIMIT 1"),
            {"r": run_id},
        )
    ).first()
    plan: dict = {}
    if approval is not None:
        payload = _json(approval[0])
        approved_at = payload.get("approvedAt") or (str(approval[1]) if approval[1] else None)
        plan = {"approved_by": payload.get("approvedBy"), "approved_at": approved_at}
    calls = (
        await db.execute(
            text("SELECT tool, target, outcome, summary FROM agent_tool_calls"
                 " WHERE run_id = :r AND reported_by = 'engine' ORDER BY created_at, id"),
            {"r": run_id},
        )
    ).all()
    summary = build_review_summary(
        files=[],
        tool_calls=[{"tool": c.tool, "target": c.target,
                     "outcome": "ok" if c.outcome == "ok" else "error",
                     "exitCode": exit_code_from_summary(c.summary)} for c in calls] or None,
        usage=None, model=None, cost_usd=None, cost_source=None, elapsed_seconds=None, status=None,
    )
    actual = (await db.execute(text("SELECT SUM(cost_usd) FROM cost_ledger WHERE run_id = :r"),
                               {"r": run_id})).scalar()
    plan_row = (
        await db.execute(
            text("SELECT goal, estimated_cost_usd FROM agent_plans WHERE run_id = :r"
                 " ORDER BY created_at DESC, id DESC LIMIT 1"),
            {"r": run_id},
        )
    ).first()
    facts = {
        "run_id": run_id,
        "goal": plan_row[0] if plan_row else None,
        "plan": plan,
        "tests": summary.get("tests"),
        "uncertainty": summary.get("uncertainty"),
        "cost": {"actual_usd": actual, "estimated_usd": plan_row[1] if plan_row else None},
    }
    return {"facts": facts, "project_id": run["project_id"], "pr_id": run["pr_id"],
            "pr_number": int(run["pr_number"])}


async def fetch_risk_flags(review_url: str, token: str, pr_id: str | None,
                           transport: httpx.AsyncBaseTransport | None = None) -> list[dict] | None:
    """Path-risk flags from the review service; None when unavailable (never "no risks")."""
    if not pr_id:
        return None
    try:
        async with env_client(transport=transport, timeout=15) as client:
            resp = await client.get(f"{review_url.rstrip('/')}/api/v1/reviews/{pr_id}/risks",
                                    headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        return [{"path": f.get("path"), "severity": f.get("severity"), "reason": f.get("reason")}
                for f in resp.json()]
    except (httpx.HTTPError, ValueError):
        return None


async def post_comment(github_url: str, token: str, project_id: str, pr_number: int, run_id: str,
                       body: str, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    async with env_client(transport=transport, timeout=30) as client:
        resp = await client.post(
            f"{github_url.rstrip('/')}/api/v1/projects/{project_id}/pulls/{pr_number}/verdict-comment",
            headers={"Authorization": f"Bearer {token}"},
            json={"runId": run_id, "bodyMd": body, "marker": verdict_marker(run_id)},
        )
    resp.raise_for_status()
    return resp.json()


async def run(db_factory: async_sessionmaker, run_id: str, *, github_url: str, review_url: str,
              token: str, dry_run: bool = False, transport: httpx.AsyncBaseTransport | None = None
              ) -> dict:
    async with db_factory() as db:
        got = await collect_facts(db, run_id)
    facts = got["facts"]
    facts["risk_flags"] = await fetch_risk_flags(review_url, token, got["pr_id"], transport)
    body = build_verdict_comment(facts, pr_number=got["pr_number"], secrets=[token])
    if dry_run:
        return {"dryRun": True, "prNumber": got["pr_number"], "body": body}
    result = await post_comment(github_url, token, got["project_id"], got["pr_number"], run_id,
                                body, transport)
    return {"dryRun": False, **result}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_id")
    parser.add_argument("--dry-run", action="store_true", help="print the comment, post nothing")
    args = parser.parse_args()

    async def go() -> dict:
        engine = create_async_engine(os.environ["HARNESS_DATABASE_URL"])
        try:
            return await run(
                async_sessionmaker(engine, expire_on_commit=False), args.run_id,
                github_url=os.environ.get("HARNESS_GITHUB_URL", "http://localhost:8002"),
                review_url=os.environ.get("HARNESS_REVIEW_URL", "http://localhost:8008"),
                token=os.environ["HARNESS_API_TOKEN"], dry_run=args.dry_run,
            )
        finally:
            await engine.dispose()

    try:
        result = asyncio.run(go())
    except (LookupError, httpx.HTTPError) as exc:
        # concise operator error; these messages never contain the token
        print(f"verdict_post: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
