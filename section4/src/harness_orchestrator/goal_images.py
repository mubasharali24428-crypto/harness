"""Goal images: screenshots that travel with the goal into the model call.

Why this module is small
------------------------
Uploads, team scoping, size caps, content-addressed storage, magic-byte MIME
verification and retention already exist (``artifacts.py`` +
``artifact_store.py``). A goal image is an artifact whose ``kind`` is
``upload`` and whose verified media type is raster. This module does one thing:
decide which of a run's image artifacts may travel into the prompt, and in
which shape.

What the runtime actually accepts
---------------------------------
The bundled SDK runtime validates prompt content blocks against the ACP union
``text | image | resource | resource_link | audio`` and, for images, requires
``{"type": "image", "data": <canonical base64>, "mimeType": ...}`` with
``mimeType`` in the four raster types it admits (png, jpeg, webp, gif). It then
stores the decoded bytes in its own attachment store and rewrites the block to
an attachment reference — so base64 here is a transport detail, never a
persisted transcript payload.

Enforcement order (each step can only shrink what reaches the model):

1. SQL: rows for THIS run with ``state = 'available'``, image media types,
   oldest first. Every artifact row was written through a route that
   authorizes the run's own team, so run scope is already the tenant boundary;
   when the team id is known it is applied as a second filter, and when it is
   not (a replica that never saw the bind, a scratch store without the
   projects table) the run scope still holds and the summary records
   ``teamScoped: false`` instead of silently attaching nothing.
2. Byte limits — ours (env-tunable) AND the runtime's, so a rejected request is
   not the first we hear of it.
3. Retention, evaluated at read time like the artifacts API does: an
   expired-but-unpurged image is not sent, because the API would refuse it.
4. Canonical base64 round-trip, because the runtime rejects non-canonical input
   and a silently dropped image is worse than a reported one.
5. Model capability. The runtime refuses with ``model "x" does not declare
   image input``; if ``HARNESS_VISION_MODELS`` is configured we refuse BEFORE
   the call instead of after, which is the difference between a fast honest
   error and a billed failure.

Nothing here raises: the caller gets an outcome (blocks + what was skipped and
why) so a run degrades to text-only with a visible reason rather than dying in
plumbing.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from harness_contracts.timeutil import as_utc, now_utc
from sqlalchemy import text

logger = logging.getLogger(__name__)

# The runtime's admission set (AcpContent IMAGE_MEDIA_TYPES). Anything else is
# rejected at prompt time, so selecting it here would only defer the error.
IMAGE_MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")

DEFAULT_MAX_IMAGE_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_IMAGES = 3
DEFAULT_MAX_TOTAL_BYTES = 8 * 1024 * 1024

_SELECT_BASE = (
    "SELECT id, name, media_type, size_bytes, storage_ref, retention_until FROM artifacts "
    "WHERE run_id = :r AND state = 'available' "
    "AND kind = 'upload' AND media_type IN (:m0, :m1, :m2, :m3)"
)
# Applied only when the team id is actually known (see the module docstring):
# run scope is the tenant boundary either way, and a missing scratch mirror of
# the bind must not turn into "the agent never saw your screenshot".
_TEAM_FILTER = " AND team_id = :t"
_ORDER = " ORDER BY created_at ASC, id ASC"


def _int_env(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("ignoring non-integer %s=%r", name, raw)
        return default
    return value if value > 0 else default


def limits() -> dict[str, int]:
    """Byte/count ceilings, env-overridable (positive ints only)."""
    return {
        "maxImageBytes": _int_env("HARNESS_GOAL_IMAGE_MAX_BYTES", DEFAULT_MAX_IMAGE_BYTES),
        "maxImages": _int_env("HARNESS_GOAL_IMAGE_MAX_COUNT", DEFAULT_MAX_IMAGES),
        "maxTotalBytes": _int_env("HARNESS_GOAL_IMAGE_TOTAL_BYTES", DEFAULT_MAX_TOTAL_BYTES),
    }


def enabled() -> bool:
    """Kill switch, on by default: unset ``HARNESS_GOAL_IMAGES`` keeps the path live."""
    return str(os.environ.get("HARNESS_GOAL_IMAGES", "1")).strip().lower() not in (
        "0",
        "off",
        "false",
        "no",
    )


def vision_allowlist() -> frozenset[str] | None:
    """Configured vision-capable model ids, or None when the deployment does not
    declare the set. None means 'do not refuse locally' — the runtime is still
    the authority, and its rejection is surfaced as the refusal reason."""
    raw = str(os.environ.get("HARNESS_VISION_MODELS", "")).strip()
    if not raw:
        return None
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


@dataclass
class GoalImages:
    """What may travel into the prompt, and what did not and why."""

    blocks: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    refused: str | None = None  # set => send no images at all, with this reason
    team_scoped: bool = True  # False => the team id was unavailable to filter on

    @property
    def selected(self) -> int:
        return len(self.blocks)

    def summary(self) -> dict[str, Any]:
        return {
            "requested": self.selected + len(self.skipped),
            "attached": self.selected,
            "skipped": self.skipped,
            "refused": self.refused,
            "teamScoped": self.team_scoped,
        }


def canonical_b64(data: bytes) -> str | None:
    """Base64 the runtime's canonical check accepts, or None.

    Round-trip rather than regex alone: decode(validate=True) plus re-encode
    proves the encoding is canonical, which is the admission test the runtime
    applies to ``data``.
    """
    encoded = base64.b64encode(data).decode("ascii")
    try:
        if base64.b64decode(encoded, validate=True) != data:
            return None
    except (binascii.Error, ValueError):
        return None
    return encoded


async def resolve_goal_images(
    factory: Any,
    run_id: str,
    team_id: str | None,
    model: str | None = None,
    *,
    read: Callable[[str], bytes | None] | None = None,
) -> GoalImages:
    """This run's goal images as SDK content blocks; never raises.

    ``factory`` may be None (bare/scratch mode: no durable artifacts to read).
    ``read`` is injectable for tests; production uses ``artifact_store.get``,
    run off the event loop because it re-hashes and reads up to several MiB.
    """
    out = GoalImages()
    if factory is None or not enabled():
        return out
    allow = vision_allowlist()
    if allow is not None and (model or "") not in allow:
        # Refuse before the call: the runtime would reject this model anyway,
        # and that rejection costs a request.
        out.refused = (
            f"model {model or 'unknown'} is not in HARNESS_VISION_MODELS; goal images were not sent"
        )
        return out
    if read is None:
        from harness_orchestrator import artifact_store

        read = artifact_store.get

    caps = limits()
    sql = _SELECT_BASE + (_TEAM_FILTER if team_id else "") + _ORDER
    params: dict[str, Any] = {
        "r": run_id,
        **{f"m{i}": media for i, media in enumerate(IMAGE_MEDIA_TYPES)},
    }
    if team_id:
        params["t"] = team_id
    out.team_scoped = bool(team_id)
    try:
        async with factory() as db:
            rows = (await db.execute(text(sql), params)).fetchall()
    except Exception:  # noqa: BLE001 — no artifacts table/old schema: text-only run
        logger.warning("goal image lookup failed for run %s", run_id, exc_info=True)
        return out

    total = 0
    now = now_utc()
    for row in rows:
        if out.selected >= caps["maxImages"]:
            _skip(out, row, "image count limit")
            continue
        if row.size_bytes > caps["maxImageBytes"]:
            _skip(out, row, f"larger than {caps['maxImageBytes']} bytes")
            continue
        if _expired(row.retention_until, now):
            # The artifacts API already 410s this row; sending its bytes would
            # keep a run alive past a retention decision somebody made.
            _skip(out, row, "retention expired")
            continue
        try:
            data = await asyncio.to_thread(read, row.storage_ref)
        except Exception:  # noqa: BLE001 — an unreadable blob is a skip, not a crash
            data = None
        if not data:
            _skip(out, row, "stored bytes missing")
            continue
        if total + len(data) > caps["maxTotalBytes"]:
            _skip(out, row, f"total image budget ({caps['maxTotalBytes']} bytes) exceeded")
            continue
        encoded = canonical_b64(data)
        if encoded is None:
            _skip(out, row, "image bytes are not canonically base64-encodable")
            continue
        out.blocks.append({"type": "image", "data": encoded, "mimeType": row.media_type})
        total += len(data)
    return out


def _skip(out: GoalImages, row: Any, reason: str) -> None:
    """Record a decline by name and reason — the artifact id is enough for the
    run page to link back to the upload, and the bytes never appear here."""
    out.skipped.append({"artifactId": row.id, "name": row.name, "reason": reason})


def _expired(retention_until: Any, now: Any) -> bool:
    """Read-time expiry, the same rule ``artifacts._eff_state`` applies."""
    if retention_until is None:
        return False
    try:
        return as_utc(retention_until) <= now
    except Exception:  # noqa: BLE001 — an unparseable stamp is not a licence to serve
        return True


__all__ = [
    "GoalImages",
    "IMAGE_MEDIA_TYPES",
    "canonical_b64",
    "enabled",
    "limits",
    "resolve_goal_images",
    "vision_allowlist",
]
