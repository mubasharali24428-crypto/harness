"""M14-W4 F1a — run-cost metering: engine completion → cost_ledger, live end-to-end.

Closes the H-011 RUN-cost gap. The S3 router already records per-call spend
(``harness_router.api.record_call_usage`` on every successful /call); the
engine path bypassed that REST seam entirely (S4 calls DeepSeek Harness
directly in-process), so a completed agent run never landed in the ledger.
The hook below records ONE team-attributed ``cost_ledger`` row when an engine
run settles, so GET /api/v1/models/spend reflects real run costs.

Writer choice: Section 1's :func:`record_usage` (M17-B3: promoted verbatim to
``harness_contracts.cost_ledger`` so sections stop importing each other's
packages). The writer is SQLAlchemy-session-shaped and takes exactly the
factory this service already holds, attributes through the frozen chain
(cost_ledger.run_id → agent_runs.project_id → projects.team_id), binds money
as exact NUMERIC(12,6)-scale TEXT (H-023), and is fire-and-forget — it never
raises into orchestration. It is imported lazily inside the hook.

Token accounting: live dsh runs surface usage as ``assistant/chunk`` events
with ``chunk.type == "usage"`` ({inputTokens, outputTokens, cacheReadTokens,
...}); the wrapper folds those into the result under ``usage``. When no real
usage exists (backend_runner stubs, dry mode), tokens are estimated from the
goal/response text at ~4 chars/token — the same convention the router's
``_estimate_tokens`` uses for contract results without token fields — and the
row's model stays the configured one, so attribution is honest either way.

Cost math lives ONLY in :class:`harness_contracts.pricing.PricingEngine`
(M17-B3: promoted from section3's pricing.py — same catalog values, exact
Decimal at NUMERIC(12,6)); this module never invents prices.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Mirrors harness_router.api._estimate_tokens (~4 chars per token): the frozen
# ModelResult contract carries no token fields and the router estimates the
# same way, so an estimated engine row uses the identical convention.
_CHARS_PER_TOKEN = 4


def estimate_tokens(goal: str, response_text: str) -> tuple[int, int]:
    """(input_tokens, output_tokens) estimate from goal + final response text."""
    return max(1, len(goal or "") // _CHARS_PER_TOKEN), max(
        1, len(response_text or "") // _CHARS_PER_TOKEN
    )


def fold_usage(result: dict[str, Any], goal: str) -> tuple[int, int]:
    """Real token usage from an engine result, else the text-based estimate.

    Accepts both result shapes the completion path sees: a raw SDK mapping
    with a ``usage`` key (folded by the wrapper from dsh usage chunks) or the
    plain {sessionId, engine, status, finalResponse} dict.
    """
    usage = result.get("usage")
    if isinstance(usage, dict):
        try:
            return int(usage.get("inputTokens") or 0), int(usage.get("outputTokens") or 0)
        except (TypeError, ValueError):
            pass
    return estimate_tokens(goal, str(result.get("finalResponse") or ""))


def resolve_engine_model(engine: Any, result: dict[str, Any]) -> str:
    """The model to attribute the row to.

    Prefers the model that ACTUALLY answered (raw-02 L1 honesty law — the
    ``deepseek-harness-sdk:<model>`` engine name embeds it); falls back to the
    configured primary; finally the bare "stub" name so injected/dry runs still
    record truthfully-priced (catalog default) rows instead of lying about a
    provider they never touched.
    """
    engine_name = str(result.get("engine") or "")
    if engine_name.startswith("deepseek-harness-sdk:"):
        return engine_name.split(":", 1)[1]
    configured = getattr(engine, "model", None)
    if isinstance(configured, str) and configured:
        return configured
    return "stub"


async def record_run_cost(service: Any, run_data: dict[str, Any], plan: Any) -> None:
    """Record one completed engine run's actual usage/cost into cost_ledger.

    Called from the completion path only (never on cancel/failure paths).
    Fire-and-forget by construction: any failure logs at warning level and
    returns — metering must NEVER break orchestration. Skips honestly (debug
    log, no row) when there is no DB factory or the team cannot be resolved;
    fabricating unattributable spend is worse than recording none.
    """
    factory = getattr(service, "run_queue_factory", None) or getattr(
        service, "session_factory", None
    )
    if factory is None:
        logger.debug("run-cost metering skipped: no DB wired")
        return
    try:
        # Lazy imports keep this module importable in unit-only contexts where
        # only harness_contracts is installed.
        from harness_contracts.cost_ledger import record_usage
        from harness_contracts.pricing import PricingEngine

        run_id = str(run_data.get("runId") or "") or None
        goal = str(run_data.get("goal") or "")
        result = run_data.get("engine_result") or {}
        input_tokens, output_tokens = fold_usage(result, goal)
        model = resolve_engine_model(getattr(service, "engine", None), result)
        latency_ms = int(result.get("latencyMs") or 0)
        cost_usd = PricingEngine.calculate_cost(model, input_tokens, output_tokens)

        team_id = await service._team_for_run(run_data)
        if not team_id:
            logger.warning(
                "run-cost metering skipped for run %s: team unresolvable", run_id
            )
            return

        row = await record_usage(
            factory,
            team_id=team_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            run_id=run_id,
            agent_role="primary",
            latency_ms=latency_ms,
        )
        if row is not None:
            logger.info(
                "recorded run cost for %s: %s tokens $%s",
                run_id,
                input_tokens + output_tokens,
                row.cost_usd,
            )
    except Exception:  # noqa: BLE001 — metering must never break the run path
        logger.warning(
            "run-cost metering failed for run %s; continuing",
            run_data.get("runId"),
            exc_info=True,
        )


async def record_engine_tool_calls(
    service: Any, run_data: dict[str, Any], calls: list[dict[str, Any]] | None
) -> None:
    """Write the engine run's own tool calls (``result["toolCalls"]``) to ``agent_tool_calls``.

    Part 44: the run's ``toolCalls`` usage counts these rows, so the engine's calls now count
    toward ``maxToolCalls`` alongside agent-reported ones. ``assignment_id`` is the run id (the
    engine is the run's root actor, not an agent assignment), ``reported_by`` is ``engine``, and
    the call id dedupes a replayed result. Fire-and-forget like :func:`record_run_cost`.
    """
    factory = getattr(service, "run_queue_factory", None) or getattr(
        service, "session_factory", None
    )
    if not calls or factory is None:
        return
    try:
        from uuid import uuid4

        from sqlalchemy import text

        from harness_orchestrator.agent_tree import _SECRETISH
        from harness_orchestrator.collab import db_now

        run_id = str(run_data["runId"])
        team_id = await service._team_for_run(run_data)
        if not team_id:
            logger.warning("engine tool calls not recorded for run %s: team unresolvable", run_id)
            return
        async with factory() as db:
            for call in calls:
                target = _SECRETISH.sub("[redacted]", str(call.get("target") or ""))[:200] or None
                await db.execute(
                    text(
                        "INSERT INTO agent_tool_calls (id, assignment_id, run_id, team_id, tool,"
                        " target, summary, outcome, cost_microusd, reported_by, request_id,"
                        " created_at) VALUES (:id, :r, :r, :t, :tool, :tg, :s, :o, 0, 'engine',"
                        " :q, :n) ON CONFLICT DO NOTHING"
                    ),
                    {
                        "id": str(uuid4()), "r": run_id, "t": team_id,
                        "tool": str(call.get("tool") or "unknown")[:80], "tg": target,
                        "s": f"engine {call.get('tool')} call", "o": call.get("outcome") or "error",
                        "q": str(call.get("callId"))[:64], "n": db_now(),
                    },
                )
            await db.commit()
    except Exception:  # noqa: BLE001 — recording must never break the run path
        logger.warning(
            "engine tool-call recording failed for run %s", run_data.get("runId"), exc_info=True
        )


__all__ = [
    "estimate_tokens",
    "fold_usage",
    "record_engine_tool_calls",
    "record_run_cost",
    "resolve_engine_model",
]
