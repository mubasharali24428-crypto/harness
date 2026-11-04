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


def _usage_tuples(usage: dict[str, Any]) -> tuple[int, int] | None:
    """(input+cacheRead, output) from a usage dict; cache reads bill as input.

    The pricing catalog has no cache-read tier, so cached prompt tokens are
    priced at the input rate — an upper bound on the real bill, never a row
    that pretends those tokens were never sent.
    """
    try:
        input_tokens = int(usage.get("inputTokens") or 0)
        output_tokens = int(usage.get("outputTokens") or 0)
        cache_read = int(usage.get("cacheReadTokens") or 0)
    except (TypeError, ValueError):
        return None
    if input_tokens == 0 and output_tokens == 0 and cache_read == 0:
        return None
    return input_tokens + cache_read, output_tokens


def fold_usage(
    result: dict[str, Any],
    goal: str,
    *,
    engine: Any = None,
    run_id: str | None = None,
    allow_estimate: bool = True,
) -> tuple[int, int] | None:
    """Real token usage from the engine, else the session transcript, else an estimate.

    Order matters (measured on 2026-10-29): the live SDK in this deployment
    emits no usage chunks, so ``result["usage"]`` is usually absent while the
    session transcript records every billed step. The transcript is read next;
    only a run with neither falls back to the ~4 chars/token estimate (stub/dry
    mode), keeping every row honestly attributable.
    """
    usage = result.get("usage")
    if isinstance(usage, dict):
        found = _usage_tuples(usage)
        if found is not None:
            return found
    reader = getattr(engine, "session_usage", None)
    if callable(reader) and run_id:
        transcript = reader(run_id)
        if isinstance(transcript, dict):
            found = _usage_tuples(transcript)
            if found is not None:
                return found
    if not allow_estimate:
        return None  # a failed run with no record spent nothing we can prove
    return estimate_tokens(goal, str(result.get("finalResponse") or ""))


async def _recorded_tokens(factory: Any, run_id: str | None) -> tuple[int, int] | None:
    """Tokens already in cost_ledger for this run (None when unreadable)."""
    if not run_id:
        return (0, 0)
    from sqlalchemy import text

    try:
        async with factory() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT COALESCE(SUM(input_tokens), 0),"
                        " COALESCE(SUM(output_tokens), 0)"
                        " FROM cost_ledger WHERE run_id = :rid"
                    ),
                    {"rid": run_id},
                )
            ).first()
    except Exception:  # noqa: BLE001 — caller decides; never guess
        return None
    return (int(row[0] or 0), int(row[1] or 0)) if row else (0, 0)


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


async def record_run_cost(
    service: Any, run_data: dict[str, Any], plan: Any, *, allow_estimate: bool = True
) -> None:
    """Record one engine attempt's actual usage/cost into cost_ledger.

    Called on every terminal path — completion, timeout, exception, cancel,
    guard stop, staging failure and a non-completed status — so spend that
    really happened is never invisible. The failure paths pass
    ``allow_estimate=False``: they bill only usage the transcript proves, never
    a text estimate. Only the unbilled delta is recorded, so a run that runs
    twice (a blocked attempt later resumed) cannot double-count.
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
        engine = getattr(service, "engine", None)
        folded = fold_usage(
            result, goal, engine=engine, run_id=run_id, allow_estimate=allow_estimate
        )
        if folded is None:
            logger.info("run-cost metering: no measurable usage for %s", run_id)
            return
        input_tokens, output_tokens = folded
        model = resolve_engine_model(engine, result)
        latency_ms = int(result.get("latencyMs") or 0)

        # A run can execute more than once (a blocked attempt that is resumed).
        # session_usage() sums the WHOLE session, so billing it each time would
        # count the earlier attempt twice. Record only the delta; an unreadable
        # ledger means we cannot know the delta, so skip rather than double-bill.
        already = await _recorded_tokens(factory, run_id)
        if already is None:
            logger.warning(
                "run-cost metering skipped for run %s: prior rows unreadable", run_id
            )
            return
        input_delta = max(0, input_tokens - already[0])
        output_delta = max(0, output_tokens - already[1])
        if input_delta == 0 and output_delta == 0:
            logger.debug("run-cost metering: no new tokens for %s", run_id)
            return
        input_tokens, output_tokens = input_delta, output_delta
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
        from harness_orchestrator.review_summary import call_summary

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
                        "s": call_summary(call.get("tool"), call.get("exitCode")),
                        "o": call.get("outcome") or "error",
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
