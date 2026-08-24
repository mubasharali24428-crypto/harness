"""M15-G6b — per-team settings CRUD, starting with reviewer thresholds.

Own router module per house convention (rules_api.py / teams_api.py): ATLAS
registers it at merge with ``app.include_router(router)`` — app.py gets only
its standard merge-wiring lines.

Endpoints (both under /api/v1):
- GET  /teams/{team_id}/settings/reviewer-thresholds
      -> 200 {teamId, key, thresholdsSource, thresholds{...}, updatedAt,
              updatedBy}
      Missing row = "no tuning configured": 200 with thresholdsSource
      "defaults" and the documented default document (the reviewer already
      runs on defaults when nothing is stored). Unknown/foreign team answers
      the SAME 404 as a bogus id — no cross-tenant existence oracle.
- PUT  /teams/{team_id}/settings/reviewer-thresholds  -> 200 same projection.
      Upsert (one live document per team). ADMIN-GATED: engineers/viewers/
      guests get 403 FORBIDDEN — threshold tuning changes what the reviewer
      blocks org-wide, a strictly stronger action than rules editing.
      Malformed documents answer 422 VALIDATION_ERROR and persist NOTHING:
      unknown keys are rejected outright (typos must not silently no-op),
      values are validated against the shared model imported from Section 8's
      harness_review.heuristic (single source of truth for shape + ranges).
- DELETE /teams/{team_id}/settings/reviewer-thresholds -> 204. Idempotent;
      deleting restores defaults-by-absence.

Security posture mirrors rules_api.py: get_principal scoping, can()-style
role gate via explicit role check (admin-only), error bodies through
contracts.error_body, audit via audit_emit helpers that never raise into the
request path.
"""

import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, error_body
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from .audit_emit import emit_reviewer_thresholds_deleted, emit_reviewer_thresholds_saved
from .auth import get_principal
from .models import TeamSetting

router = APIRouter(prefix="/api/v1")

# Must match harness_review.heuristic.REVIEWER_THRESHOLD_KEY — the writer and
# reader agree on one storage key. (Imported lazily below; S1 imports S8's
# pure validation model, never its DB layer.)
SETTINGS_KEY_REVIEWER_THRESHOLDS = "reviewer_thresholds"


class ThresholdsBody(BaseModel):
    """Raw PUT payload. Shape/range validation is delegated to the shared
    Section 8 model so both sections can never drift apart."""

    thresholds: dict[str, Any]

    model_config = {"extra": "forbid"}


def _shared_thresholds_model():
    """Import the single validation definition from Section 8.

    Only the PURE pydantic model is imported (harness_review.heuristic) —
    this keeps one source of truth for the document contract while respecting
    the section-isolation law (no S1→S8 store/db/service imports).
    """
    from harness_review.heuristic import ReviewerThresholds

    return ReviewerThresholds


def _validate_thresholds_document(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate against the shared model; return the canonical camelCase doc.

    Raises ValueError (wrapped into 422 by callers) on any malformed input:
    unknown keys, wrong types, out-of-range numbers. Unknown keys are
    REJECTED here (a typo'd knob must never silently store as defaults) even
    though the Section 8 lenient READER ignores them for forward
    compatibility — write-side strict, read-side forgiving.
    """
    from harness_review.heuristic import REVIEWER_THRESHOLD_KEY

    if SETTINGS_KEY_REVIEWER_THRESHOLDS != REVIEWER_THRESHOLD_KEY:  # pragma: no cover
        # Guard against silent key drift between the two constants.
        raise RuntimeError("reviewer thresholds storage key drifted between S1 and S8")
    model = _shared_thresholds_model()
    allowed = set(model.model_fields) | {
        f.alias or name for name, f in model.model_fields.items()
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"unknown threshold key(s): {', '.join(unknown)}")
    try:
        parsed = model.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    # Store the canonical camelCase projection so S8's lenient reader and any
    # UI read back exactly what was tuned.
    return parsed.model_dump(by_alias=True)


def _public(row: TeamSetting | None, team_id: str) -> dict[str, Any]:
    """camelCase projection per api-conventions.md. Lenient exactly like the
    Section 8 reader: a stored-but-corrupt value projects the documented
    defaults with thresholdsSource "defaults" instead of erroring."""
    from harness_review.heuristic import DEFAULT_REVIEWER_THRESHOLDS

    if row is None or not row.value:
        return {
            "teamId": team_id,
            "key": SETTINGS_KEY_REVIEWER_THRESHOLDS,
            "thresholdsSource": "defaults",
            "thresholds": DEFAULT_REVIEWER_THRESHOLDS.model_dump(by_alias=True),
            "updatedAt": None,
            "updatedBy": None,
        }
    try:
        stored = json.loads(row.value)
    except Exception:  # noqa: BLE001 — a corrupt row still reads, as defaults
        stored = None
    if not isinstance(stored, dict):
        return {
            "teamId": team_id,
            "key": SETTINGS_KEY_REVIEWER_THRESHOLDS,
            "thresholdsSource": "defaults",
            "thresholds": DEFAULT_REVIEWER_THRESHOLDS.model_dump(by_alias=True),
            "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
            "updatedBy": row.updated_by,
        }
    return {
        "teamId": team_id,
        "key": SETTINGS_KEY_REVIEWER_THRESHOLDS,
        "thresholdsSource": "team",
        "thresholds": stored,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
        "updatedBy": row.updated_by,
    }


async def _owned_team_setting(
    db, team_id: str, principal: Principal
) -> HTTPException | None:
    """404 unless the caller's own team — unknown and foreign indistinguishable."""
    if team_id != principal.teamId:
        return HTTPException(
            status_code=404, detail=error_body("NOT_FOUND", "team not found")
        )
    return None


def _role_value(role: object) -> str:
    """Normalize a role to its lowercase value string (teams_api convention:
    ORM reads surface StrEnum members; never rely on cross-module enum
    identity, compare VALUES)."""
    value = getattr(role, "value", role)
    return str(value)


def _admin_gate(principal: Principal) -> None:
    """Only admins tune reviewer thresholds (M15-G6b contract)."""
    if _role_value(principal.role) != "admin":
        raise HTTPException(
            status_code=403,
            detail=error_body(
                "FORBIDDEN", "only team admins may change reviewer thresholds"
            ),
        )


@router.get("/teams/{team_id}/settings/reviewer-thresholds")
async def get_reviewer_thresholds(
    team_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    """Read the team's reviewer thresholds ("" config = documented defaults)."""
    async with request.app.state.session_factory() as db:
        denied = await _owned_team_setting(db, team_id, principal)
        if denied:
            raise denied
        row = (
            await db.execute(
                select(TeamSetting).where(
                    TeamSetting.team_id == team_id,
                    TeamSetting.key == SETTINGS_KEY_REVIEWER_THRESHOLDS,
                )
            )
        ).scalar_one_or_none()
        return _public(row, team_id)


@router.put("/teams/{team_id}/settings/reviewer-thresholds")
async def put_reviewer_thresholds(
    team_id: str,
    body: ThresholdsBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    """Upsert the team's reviewer thresholds (admin-only).

    Validation happens BEFORE any write: a malformed document answers 422 and
    leaves the stored row untouched.
    """
    _admin_gate(principal)
    try:
        canonical = _validate_thresholds_document(body.thresholds)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=error_body("VALIDATION_ERROR", f"invalid reviewer thresholds: {exc}"),
        ) from exc

    async with request.app.state.session_factory() as db:
        denied = await _owned_team_setting(db, team_id, principal)
        if denied:
            raise denied
        row = (
            await db.execute(
                select(TeamSetting).where(
                    TeamSetting.team_id == team_id,
                    TeamSetting.key == SETTINGS_KEY_REVIEWER_THRESHOLDS,
                )
            )
        ).scalar_one_or_none()
        created = row is None
        if row is None:
            row = TeamSetting(team_id=team_id, key=SETTINGS_KEY_REVIEWER_THRESHOLDS)
            db.add(row)
        row.value = json.dumps(canonical)
        row.updated_by = principal.userId
        row.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(row)
        await emit_reviewer_thresholds_saved(
            db,
            actor_user_id=principal.userId,
            team_id=principal.teamId,
            target_id=team_id,
            detail={"created": created},
        )
        return _public(row, team_id)


@router.delete("/teams/{team_id}/settings/reviewer-thresholds", status_code=204)
async def delete_reviewer_thresholds(
    team_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    """Reset to defaults by removing the stored document. Idempotent."""
    _admin_gate(principal)
    async with request.app.state.session_factory() as db:
        denied = await _owned_team_setting(db, team_id, principal)
        if denied:
            raise denied
        row = (
            await db.execute(
                select(TeamSetting).where(
                    TeamSetting.team_id == team_id,
                    TeamSetting.key == SETTINGS_KEY_REVIEWER_THRESHOLDS,
                )
            )
        ).scalar_one_or_none()
        had_row = row is not None
        if had_row:
            await db.delete(row)
            await db.commit()
            await emit_reviewer_thresholds_deleted(
                db,
                actor_user_id=principal.userId,
                team_id=principal.teamId,
                target_id=team_id,
            )
    # No row stored: fall through — DELETE of defaults stays 204 (idempotent).
