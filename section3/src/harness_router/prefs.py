"""Per-team model preferences: durable role models + reasoning effort.

Storage is the frozen ``team_model_prefs`` table (migration 0062) reached
through the same asyncpg pool the cost ledger already uses. The pool is
optional: without it the router answers with the deployment defaults and says
so (``persisted: false``), and writes are refused rather than silently lost.

Reasoning effort is stored everywhere but only put on the wire for providers
that accept a ``reasoning`` object (OpenRouter-style endpoints). The API
exposes ``reasoningWireSupported`` so the UI can say that honestly instead of
pretending every endpoint honours it.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from harness_router.config import MODEL_PRICING_CATALOG, config

logger = logging.getLogger(__name__)

REASONING_EFFORTS: tuple[str, ...] = ("off", "low", "medium", "high")
DEFAULT_REASONING_EFFORT = "off"

_SELECT_SQL = (
    "SELECT primary_model, reviewer_model, reasoning_effort, updated_at "
    "FROM team_model_prefs WHERE team_id = $1"
)
_UPSERT_SQL = (
    "INSERT INTO team_model_prefs "
    "(team_id, primary_model, reviewer_model, reasoning_effort, updated_by, updated_at) "
    "VALUES ($1, $2, $3, $4, $5, now()) "
    "ON CONFLICT (team_id) DO UPDATE SET "
    "primary_model = EXCLUDED.primary_model, "
    "reviewer_model = EXCLUDED.reviewer_model, "
    "reasoning_effort = EXCLUDED.reasoning_effort, "
    "updated_by = EXCLUDED.updated_by, updated_at = now()"
)


def reasoning_wire_supported() -> bool:
    """True when the configured provider endpoint accepts a ``reasoning`` object.

    OpenRouter does; DeepSeek's first-party API uses its own thinking switch
    and may reject unknown fields, so the preference is stored but not sent.
    """
    base = (os.environ.get("DEEPSEEK_BASE_URL") or "").lower()
    return "openrouter" in base


def defaults(team_id: str) -> dict[str, Any]:
    return {
        "teamId": team_id,
        "primaryModel": config.default_primary_model,
        "reviewerModel": config.default_reviewer_model,
        "reasoningEffort": DEFAULT_REASONING_EFFORT,
        "persisted": False,
        "reasoningWireSupported": reasoning_wire_supported(),
        "updatedAt": None,
    }


def model_known(model: str) -> bool:
    return model in MODEL_PRICING_CATALOG


async def load_prefs(pool: Any, team_id: str) -> dict[str, Any]:
    """Team preferences or the deployment defaults. Never raises."""
    if pool is None or not hasattr(pool, "fetchrow"):
        return defaults(team_id)
    try:
        row = await pool.fetchrow(_SELECT_SQL, team_id)
    except Exception:  # noqa: BLE001 — a read failure must not break model calls
        logger.warning("team_model_prefs read failed for team %s", team_id, exc_info=True)
        return defaults(team_id)
    if row is None:
        return defaults(team_id)
    updated = row["updated_at"] if "updated_at" in row else None
    return {
        "teamId": team_id,
        "primaryModel": row["primary_model"],
        "reviewerModel": row["reviewer_model"],
        "reasoningEffort": row["reasoning_effort"],
        "persisted": True,
        "reasoningWireSupported": reasoning_wire_supported(),
        "updatedAt": updated.isoformat() if hasattr(updated, "isoformat") else updated,
    }


async def save_prefs(
    pool: Any,
    team_id: str,
    *,
    primary_model: str,
    reviewer_model: str,
    reasoning_effort: str,
    updated_by: str,
) -> None:
    """Upsert the team's row. Raises on storage failure so the route can 503."""
    if pool is None or not hasattr(pool, "execute"):
        raise RuntimeError("model-preference storage is not wired")
    await pool.execute(
        _UPSERT_SQL,
        team_id,
        primary_model,
        reviewer_model,
        reasoning_effort,
        updated_by,
    )
