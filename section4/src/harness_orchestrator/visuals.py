"""Visual evidence for a run (W9).

A screenshot is not just another file: it is a claim about **which reviewed
revision** the picture shows and **what it changed**. Two runs on the same team
can produce pictures of the same page; a before/after pair that straddles two
revisions is not a comparison, it is two unrelated images.

This module adds the visual semantics on top of the artifact store:

* **Raster only, by magic bytes.** PNG, JPEG and WebP are accepted; GIF (animated),
  SVG and PDF are refused rather than quarantined — a screenshot pipeline should
  fail loudly, not store something the reviewer cannot see. The declared
  content type is never trusted.
* **Bounded.** ≤ 4 MiB, 16..8192 px per side, parsed from the header *before*
  anything is stored.
* **Bound to the reviewed revision.** The provenance snapshot (run head sha,
  base sha, approved plan version, changes digest) is taken at attach time and
  refuses when the run has no recorded head: evidence with no revision is not
  evidence.
* **Paired explicitly.** `role=before` has no pair; `role=after` names the
  before artifact and must carry the same provenance snapshot. One after per
  before (a unique partial index enforces it).
* **Same access as artifacts.** Reads are team- and run-scoped through the
  artifact store; a foreign team gets the same 404 as an unknown run. Sharing a
  visual uses the existing share route, which always serves
  ``application/octet-stream`` with a sandbox CSP — a screenshot link downloads,
  it never renders.
"""

from __future__ import annotations

import json
import os
import struct
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from harness_orchestrator import artifacts as _art

router = APIRouter(prefix="/api/v1/orchestrator", tags=["visuals"])

MAX_BYTES_ENV = "HARNESS_VISUAL_MAX_BYTES"
DEFAULT_MAX_BYTES = 4 * 1024 * 1024
MAX_PX = 8192
MIN_PX = 16
ALLOWED = ("png", "jpeg", "webp")
_SUFFIX_MEDIA = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "webp": "webp"}
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
RPO = Literal["before", "after"]


class VisualError(Exception):
    """A refusal with a stable code (same envelope shape as the artifact errors)."""

    def __init__(self, status: int, code: str, message: str,
                 detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail or {}


def _handled(coro):
    async def go():
        try:
            return await coro
        except VisualError as exc:
            return JSONResponse(
                {"error": {"code": exc.code, "message": exc.message},
                 "detail": {"error": {"code": exc.code, "message": exc.message}, **exc.detail}},
                status_code=exc.status,
                headers=_art._no_store(),
            )
        except _art.collab.CollabError as exc:  # the store's own refusals (cap, limits)
            extra = getattr(exc, "extra", {}) or {}
            return JSONResponse(
                {"error": {"code": exc.code, "message": str(exc)},
                 "detail": {"error": {"code": exc.code, "message": str(exc)}, **extra}},
                status_code=exc.status,
                headers=_art._no_store(),
            )

    return go()


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """Walk JPEG segments to the first SOFn frame header."""
    index = 2
    while index + 4 <= len(data):
        if data[index] != 0xFF:
            return None
        marker = data[index + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        if marker == 0xD9:  # EOI before any frame header
            return None
        length = int.from_bytes(data[index + 2:index + 4], "big")
        if length < 2:
            return None
        if marker in _SOF:
            if index + 9 > len(data):
                return None
            height = int.from_bytes(data[index + 5:index + 7], "big")
            width = int.from_bytes(data[index + 7:index + 9], "big")
            return width, height
        index += 2 + length
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]
    if chunk == b"VP8X":  # extended: 24-bit canvas size minus one
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if chunk == b"VP8L":  # lossless: 14-bit dimensions, packed
        if len(data) < 25 or data[20] != 0x2F:
            return None
        bits = int.from_bytes(data[21:25], "little")
        width = 1 + (bits & 0x3FFF)
        height = 1 + ((bits >> 14) & 0x3FFF)
        return width, height
    if chunk == b"VP8 ":  # lossy: 14-bit dimensions after the start code
        if len(data) < 30 or data[23:26] != b"\x9d\x01\x2a":
            return None
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return width, height
    return None


def image_info(data: bytes) -> tuple[str, int, int] | None:
    """(media, width, height) from the bytes alone; ``None`` for anything else."""
    if len(data) >= 24 and data[:8] == _PNG_MAGIC and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return ("png", width, height) if width and height else None
    if data[:3] == b"\xff\xd8\xff":
        size = _jpeg_size(data)
        return ("jpeg", *size) if size and size[0] and size[1] else None
    size = _webp_size(data)
    if size and size[0] and size[1]:
        return ("webp", *size)
    return None


def _suffix(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def validate_visual(name: str, data: bytes, max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, Any]:
    """The stored properties of one screenshot, or ``VisualError`` — never a guess."""
    if not data:
        raise VisualError(422, "EMPTY_ARTIFACT", "a visual artifact needs at least one byte")
    if len(data) > max_bytes:
        raise VisualError(413, "VISUAL_TOO_LARGE",
                          f"visual artifacts are limited to {max_bytes} bytes",
                          {"sizeBytes": len(data), "limitBytes": max_bytes})
    info = image_info(data)
    if info is None:
        raise VisualError(415, "VISUAL_TYPE_NOT_ALLOWED",
                          "only raster PNG, JPEG and WebP screenshots are accepted")
    media, width, height = info
    if media not in ALLOWED:  # defensive: image_info only returns these
        raise VisualError(415, "VISUAL_TYPE_NOT_ALLOWED", f"{media} is not allowed")
    suffix = _suffix(name)
    if suffix not in _SUFFIX_MEDIA or _SUFFIX_MEDIA[suffix] != media:
        raise VisualError(422, "VISUAL_TYPE_MISMATCH",
                          f"name suffix {suffix or '(none)'!r} does not match {media} bytes")
    if width < MIN_PX or height < MIN_PX:
        raise VisualError(422, "VISUAL_DIMENSIONS",
                          f"screenshots must be at least {MIN_PX}x{MIN_PX} px",
                          {"width": width, "height": height})
    if width > MAX_PX or height > MAX_PX:
        raise VisualError(422, "VISUAL_DIMENSIONS",
                          f"screenshots must be at most {MAX_PX}x{MAX_PX} px",
                          {"width": width, "height": height})
    return {"media": media, "width": width, "height": height, "sizeBytes": len(data)}


def _now() -> datetime:
    return datetime.now(UTC)


def _factory() -> Any:
    return _art._need_factory()


async def _provenance(db: Any, p: Any, run_id: str) -> dict[str, Any]:
    """The reviewed revision this image is evidence about — or a refusal."""
    git = (
        await db.execute(
            text(
                "SELECT repo_full_name, base_sha, run_head_sha, stale FROM run_git_state"
                " WHERE run_id = :r AND team_id = :t"
            ),
            {"r": run_id, "t": p.teamId},
        )
    ).first()
    if git is None or not git.run_head_sha:
        raise VisualError(
            409, "VISUAL_REVISION_UNBOUND",
            "record the run head (PUT git-state) before attaching visual evidence")
    plan_version = (
        await db.execute(
            text(
                "SELECT version FROM plan_versions WHERE run_id = :r AND team_id = :t"
                " AND state = 'approved' ORDER BY version DESC LIMIT 1"
            ),
            {"r": run_id, "t": p.teamId},
        )
    ).scalar()
    changes = await _art._current_changes(db, p, run_id)
    return {
        "runHeadSha": git.run_head_sha,
        "baseSha": git.base_sha,
        "repo": git.repo_full_name,
        "planVersion": int(plan_version) if plan_version is not None else None,
        "changesDigest": _art.changes_digest(changes) if changes else None,
        "stale": bool(git.stale),
        "recordedAt": _now().isoformat(),
    }


def _same_revision(left: dict[str, Any], right: dict[str, Any]) -> bool:
    fields = ("runHeadSha", "baseSha", "planVersion", "changesDigest")
    return all(left.get(k) == right.get(k) for k in fields)


def _view(row: Any, art: Any, now: datetime) -> dict[str, Any]:
    state = _art._eff_state(art, now)
    return {
        "id": row.artifact_id,
        "runId": row.run_id,
        "role": row.role,
        "pairId": row.pair_id,
        "name": art.name,
        "mediaType": art.media_type,
        "sha256": art.sha256,
        "sizeBytes": int(art.size_bytes),
        "width": int(row.width),
        "height": int(row.height),
        "state": state,
        "createdBy": row.created_by,
        "createdAt": _art._iso(row.created_at),
        "retentionUntil": _art._iso(art.retention_until),
        "provenance": _art._json(row.provenance, {}),
        "contentUrl": f"/api/v1/orchestrator/runs/{row.run_id}/artifacts/{row.artifact_id}/content",
    }


async def _visual_row(db: Any, p: Any, run_id: str, artifact_id: str) -> Any:
    return (
        await db.execute(
            text(
                "SELECT artifact_id, team_id, run_id, role, pair_id, width, height,"
                " provenance, created_by, created_at FROM visual_artifacts"
                " WHERE artifact_id = :i AND run_id = :r AND team_id = :t"
            ),
            {"i": artifact_id[:64], "r": run_id, "t": p.teamId},
        )
    ).first()


async def _artifact_row(db: Any, p: Any, run_id: str, artifact_id: str) -> Any:
    return (
        await db.execute(
            text(
                f"SELECT {_art._ACOLS} FROM artifacts WHERE id = :i AND run_id = :r"
                " AND team_id = :t"
            ),
            {"i": artifact_id[:64], "r": run_id, "t": p.teamId},
        )
    ).first()


@router.post("/runs/{runId}/visuals", status_code=201)
async def attach_visual(
    runId: str,
    request: Request,
    p: _art.Writer,
    role: Annotated[RPO, Query()],
    name: str = Query(..., max_length=120),
    pairOf: str | None = Query(None, max_length=64),
    capturedFrom: str | None = Query(None, max_length=300),
    retentionDays: int | None = Query(None, ge=1, le=3650),
    requestId: str | None = Query(None, max_length=64),
) -> Any:
    """Store one screenshot as visual evidence of this run's reviewed revision."""

    async def go() -> Any:
        clean = _art.artifact_store.clean_name(name)
        if clean is None:
            raise VisualError(422, "BAD_NAME",
                              "name must be 1..120 chars without path separators")
        if requestId is not None and not _art.REQ_RE.match(requestId):
            raise VisualError(422, "BAD_REQUEST_ID", "requestId must be 1..64 of A-Za-z0-9_.:-")
        cap = int(os.environ.get(MAX_BYTES_ENV, DEFAULT_MAX_BYTES))
        data = await _art._read_body(request, cap)
        validated = validate_visual(clean, data, cap)
        if role == "before" and pairOf:
            raise VisualError(422, "VISUAL_PAIR_INVALID", "a before image has no pair")
        if role == "after" and not pairOf:
            raise VisualError(422, "VISUAL_PAIR_INVALID", "an after image needs pairOf")

        factory = _factory()
        async with factory() as db:
            provenance = await _provenance(db, p, runId)
            if role == "after":
                before = await _visual_row(db, p, runId, pairOf or "")
                if before is None or before.role != "before":
                    raise VisualError(422, "VISUAL_PAIR_INVALID",
                                      "pairOf is not a 'before' visual of this run")
                before_art = await _artifact_row(db, p, runId, before.artifact_id)
                if before_art is None or _art._eff_state(before_art, _now()) != "available":
                    raise VisualError(409, "VISUAL_PAIR_UNAVAILABLE",
                                      "the before image is not available")
                if not _same_revision(_art._json(before.provenance, {}), provenance):
                    raise VisualError(
                        409, "VISUAL_PAIR_REVISION_MISMATCH",
                        "the before image evidences a different revision; capture both again",
                        {"before": _art._json(before.provenance, {}),
                         "after": provenance})
                taken = (
                    await db.execute(
                        text("SELECT 1 FROM visual_artifacts"
                             " WHERE pair_id = :i AND role = 'after'"),
                        {"i": before.artifact_id},
                    )
                ).first()
                if taken is not None:
                    raise VisualError(409, "VISUAL_PAIR_TAKEN",
                                      "this before image already has an after image")

        art, replayed = await _art.create_artifact(
            factory, p, runId, name=clean, kind="upload", data=data,
            declared=f"image/{validated['media']}", request_id=requestId,
            retention_days=retentionDays,
        )
        now = _now()
        if replayed:
            # The store hands back the artifact the first request created. A retry must
            # answer with the visual row it already recorded — never re-insert (which
            # would collide) and never tombstone evidence that exists.
            async with factory() as db:
                existing = await _visual_row(db, p, runId, art["id"])
                existing_art = await _artifact_row(db, p, runId, art["id"])
            if existing is None or existing_art is None:
                raise VisualError(409, "REQUEST_ID_REUSED",
                                  "requestId already names an artifact that is not a visual")
            return JSONResponse({"visual": _view(existing, existing_art, now),
                                 "replayed": True}, status_code=200,
                                headers=_art._no_store())
        try:
            async with factory() as db:
                await db.execute(
                    text(
                        "INSERT INTO visual_artifacts (artifact_id, team_id, run_id, role,"
                        " pair_id, width, height, provenance, created_by, created_at)"
                        " VALUES (:a, :t, :r, :role, :pair, :w, :h, :p, :u, :now)"
                    ),
                    {"a": art["id"], "t": p.teamId, "r": runId, "role": role,
                     "pair": pairOf, "w": validated["width"], "h": validated["height"],
                     "p": json.dumps(provenance), "u": p.userId, "now": now},
                )
                await _art._audit(
                    db, p, "visual.attached", "artifact", art["id"],
                    {"runId": runId, "artifactId": art["id"], "role": role,
                     "pairId": pairOf, "width": validated["width"],
                     "height": validated["height"], "media": validated["media"],
                     "runHeadSha": provenance["runHeadSha"],
                     "planVersion": provenance["planVersion"],
                     "capturedFrom": (capturedFrom or "")[:300]})
                await db.commit()
        except IntegrityError as exc:
            # A concurrent attach took the pair between the check and the insert.
            # The artifact is tombstoned: bytes are content-addressed and shared,
            # but the row must not claim to be evidence that was never attached.
            async with factory() as db:
                await db.execute(
                    text("UPDATE artifacts SET state = 'deleted', deleted_at = :n,"
                         " deleted_by = :u WHERE id = :i"),
                    {"n": _now(), "u": p.userId, "i": art["id"]})
                await db.commit()
            raise VisualError(409, "VISUAL_PAIR_TAKEN",
                              "this before image already has an after image") from exc

        row = {
            "artifact_id": art["id"], "run_id": runId, "role": role, "pair_id": pairOf,
            "width": validated["width"], "height": validated["height"],
            "provenance": json.dumps(provenance), "created_by": p.userId, "created_at": now,
        }
        art_row = SimpleNamespace(**_art_row_fields(art))
        return JSONResponse({"visual": _view(SimpleNamespace(**row), art_row, now),
                             "replayed": False}, status_code=201,
                            headers=_art._no_store())

    return await _handled(go())


def _art_row_fields(art: dict[str, Any]) -> dict[str, Any]:
    """The artifact view fields the visual view needs, from ``create_artifact`` output."""
    return {
        "name": art.get("name"), "media_type": art.get("mediaType"),
        "sha256": art.get("sha256"), "size_bytes": art.get("sizeBytes"),
        "retention_until": art.get("retentionUntil"), "state": art.get("state"),
    }


@router.get("/runs/{runId}/visuals")
async def list_visuals(runId: str, p: _art.Reader) -> Any:
    """Every visual of the run, with before/after pairs resolved."""

    async def go() -> Any:
        now = _now()
        async with _factory()() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT v.artifact_id, v.run_id, v.role, v.pair_id, v.width, v.height,"
                        " v.provenance, v.created_by, v.created_at, a.name AS art_name,"
                        " a.media_type AS art_media_type, a.sha256 AS art_sha256,"
                        " a.size_bytes AS art_size_bytes,"
                        " a.retention_until AS art_retention_until, a.state AS art_state"
                        " FROM visual_artifacts v JOIN artifacts a"
                        " ON a.id = v.artifact_id AND a.run_id = v.run_id"
                        " AND a.team_id = v.team_id"
                        " WHERE v.run_id = :r AND v.team_id = :t"
                        " ORDER BY v.created_at, v.artifact_id"
                    ),
                    {"r": runId, "t": p.teamId},
                )
            ).all()
        items = []
        for row in rows:
            art = SimpleNamespace(name=row.art_name, media_type=row.art_media_type,
                                  sha256=row.art_sha256, size_bytes=row.art_size_bytes,
                                  retention_until=row.art_retention_until, state=row.art_state)
            items.append(_view(row, art, now))
        pairs = {}
        for item in items:
            if item["role"] == "after" and item["pairId"]:
                pairs.setdefault(item["pairId"], {"before": None, "after": None})["after"] = item
            if item["role"] == "before":
                pairs.setdefault(item["id"], {"before": None, "after": None})["before"] = item
        return JSONResponse(
            {"items": items,
             "pairs": [v for v in pairs.values() if v["before"] or v["after"]]},
            headers=_art._no_store())

    return await _handled(go())


@router.get("/runs/{runId}/visuals/{artifactId}")
async def get_visual(runId: str, artifactId: str, p: _art.Reader) -> Any:
    async def go() -> Any:
        now = _now()
        async with _factory()() as db:
            row = await _visual_row(db, p, runId, artifactId)
            art = await _artifact_row(db, p, runId, artifactId)
        if row is None or art is None:
            raise VisualError(404, "NOT_FOUND", "visual not found")
        return JSONResponse({"visual": _view(row, art, now)}, headers=_art._no_store())

    return await _handled(go())
