"""Marathon 2.0 W16 slice 4 — push the audit trail into a customer's SIEM.

Pull-only export is the honest status quo a customer cannot automate: a collector
that appends lines is enough, and the delivery must survive a restart without
re-sending or skipping. So this module owns three things:

  /api/v1/teams/{id}/siem/sinks       admin CRUD over collector endpoints
  push_due(...)                        the delivery step a worker calls
  the sink's cursor                    the last seq handed to the collector

Laws, and why each one is not decoration:

* **The collector receives exactly what an auditor would export.** The rows come
  from `audit_rows.read_audit_page` — the same reader the export endpoint uses,
  chain fields included — because two copies of a team-scoping rule eventually
  disagree, and the disagreement is invisible from the SIEM.
* **One wire format for both directions.** A batch is the NDJSON body of the
  export (`format=ndjson`), so a customer's parser is written once.
* **At-least-once, deduplicated by id.** The cursor advances only on a 2xx, so a
  crash between send and commit replays the batch rather than skipping it; every
  batch carries `Idempotency-Key: <sinkId>:<first>-<last>` so a collector that
  cares can drop the replay. Claiming is compare-and-set on
  (state, attempts, next_attempt_at), so two workers never hand the same team's
  rows to the collector twice concurrently.
* **A destination is an egress decision, not a string.** HTTPS, port 443 only,
  no userinfo/query/fragment, host on the operator allow-list
  (`HARNESS_SIEM_ALLOWED_HOSTS`), and no literal-IP or `.local` destination —
  the same law webhooks already obey, reused from `egress.py` rather than
  re-typed, because a security check duplicated is a security check halved.
* **A dead collector is said out loud.** After MAX_ATTEMPTS consecutive failures
  the sink goes `blocked`, the error and status are stored on the row, an audit
  row is written, and nothing more is sent until an admin resumes it. Silent
  retry forever would hide an outage; silently dropping would lose evidence.
* **The secret is write-only.** The HMAC key is Fernet-encrypted at rest under
  the salted KDF and never read back — only a hint. Rotation replaces it; a
  signature is `t=<ts>,v1=hmac(secret, "<ts>." + body)`, so a collector can
  recompute it without knowing anything about Harness.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, error_body
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    and_,
    or_,
    select,
    update,
)
from sqlalchemy.orm import Mapped, mapped_column

from .audit_emit import (
    emit_siem_sink_blocked,
    emit_siem_sink_changed,
    emit_siem_sink_created,
    emit_siem_sink_deleted,
)
from .audit_rows import count_after, latest_seq, read_audit_page
from .auth import get_principal
from .crypto import KeyVault
from .egress import host_is_public, validate_public_https
from .models import Base, uid
from .sso import _require_team_tier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/teams/{team_id}/siem/sinks")

MAX_SINKS_PER_TEAM = 5
MIN_SECRET = 16
MAX_SECRET = 128
BATCH_ROWS = 200  # rows per POST — a collector wants small, frequent files
MAX_ATTEMPTS = 8
RETRY_BASE_SECONDS = 60
RETRY_MAX_SECONDS = 3600
LEASE_SECONDS = 120
ALLOWED_HOSTS_ENV = "HARNESS_SIEM_ALLOWED_HOSTS"
SIGNATURE_HEADER = "X-Harness-Signature"
STATE_ACTIVE = "active"
STATE_SENDING = "sending"
STATE_BLOCKED = "blocked"


class SiemSink(Base):
    """Mirror of 0071_siem_sinks.sql siem_sinks.

    `cursor_seq` is the last audit seq handed to the collector, and it is NOT a
    foreign key: retention prunes old rows, and a cursor pointing at a pruned
    seq is the normal state of a long-lived sink, not corruption.
    """

    __tablename__ = "siem_sinks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    collector_url: Mapped[str] = mapped_column(Text, nullable=False)
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    secret_hint: Mapped[str] = mapped_column(String(8), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    state: Mapped[str] = mapped_column(String(12), nullable=False, default=STATE_ACTIVE)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    cursor_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(40))
    delivered_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_batch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SinkCreateBody(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    collectorUrl: str = Field(min_length=12, max_length=2048)
    sharedSecret: str = Field(min_length=MIN_SECRET, max_length=MAX_SECRET)
    # "latest" starts the cursor at the newest row now; "earliest" replays the
    # whole retained trail. Default is latest because turning on a collector
    # should not silently push a year of other people's security events.
    startFrom: Literal["latest", "earliest"] = "latest"


class SinkPatchBody(BaseModel):
    active: bool | None = None
    label: str | None = Field(default=None, min_length=1, max_length=120)
    collectorUrl: str | None = Field(default=None, min_length=12, max_length=2048)
    sharedSecret: str | None = Field(default=None, min_length=MIN_SECRET, max_length=MAX_SECRET)
    resume: bool = False  # clear a `blocked` state after the cause is fixed
    expectedVersion: int = Field(ge=1)


def _now() -> datetime:
    return datetime.now(UTC)


def _team_admin(principal: Principal, team_id: str) -> None:
    if principal.teamId != team_id:
        raise HTTPException(status_code=404, detail=error_body("NOT_FOUND", "team not found"))
    role = getattr(principal.role, "value", principal.role)
    if role != "admin":
        raise HTTPException(status_code=403, detail=error_body("FORBIDDEN", "team admin required"))


def allowed_hosts() -> set[str]:
    return {
        part.strip().lower().rstrip(".")
        for part in os.getenv(ALLOWED_HOSTS_ENV, "").split(",")
        if part.strip()
    }


def _validate_url(url: str) -> str:
    return validate_public_https(
        url,
        allowed=allowed_hosts(),
        code="INVALID_SIEM_URL",
        label="SIEM collector",
    )


def _public(row: SiemSink, *, pending: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row.id,
        "label": row.label,
        "collectorUrl": row.collector_url,
        "secretHint": row.secret_hint,
        "active": bool(row.active),
        "state": row.state,
        "version": row.version,
        "cursor": row.cursor_seq,
        "attempts": row.attempts,
        "lastStatusCode": row.last_status_code,
        "lastError": row.last_error,
        "deliveredRows": row.delivered_rows,
        "nextAttemptAt": row.next_attempt_at.isoformat() if row.next_attempt_at else None,
        "lastBatchAt": row.last_batch_at.isoformat() if row.last_batch_at else None,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }
    if pending is not None:
        out["pendingRows"] = pending
    return out


async def _sink(db, team_id: str, sink_id: str) -> SiemSink:
    row = (
        await db.execute(
            select(SiemSink).where(SiemSink.id == sink_id, SiemSink.team_id == team_id)
        )
    ).scalar_one_or_none()
    if row is None:
        # An id from another team and an id that never existed answer the same.
        raise HTTPException(status_code=404, detail=error_body("NOT_FOUND", "sink not found"))
    return row


@router.post("", status_code=201)
async def create_sink(
    team_id: str,
    body: SinkCreateBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    """Register a collector endpoint (admin + Team tier). The shared secret is
    stored encrypted and never returned again."""
    _team_admin(principal, team_id)
    await _require_team_tier(request, team_id, "The SIEM sink")
    url = _validate_url(body.collectorUrl)
    async with request.app.state.session_factory() as db:
        live = (
            await db.execute(
                select(SiemSink).where(SiemSink.team_id == team_id, SiemSink.state == STATE_ACTIVE)
            )
        ).scalars().all()
        if len(live) >= MAX_SINKS_PER_TEAM:
            raise HTTPException(
                status_code=409,
                detail=error_body(
                    "LIMIT_EXCEEDED", f"a team may hold {MAX_SINKS_PER_TEAM} SIEM sinks"
                ),
            )
        if body.startFrom == "earliest":
            cursor = 0
        else:
            # A fresh sink starts at the newest row this team already has:
            # turning on a collector must not dump years of security events into
            # it. Replaying history is the explicit startFrom="earliest" choice.
            cursor = await latest_seq(db, team_id)
        vault = request.app.state.vault
        row = SiemSink(
            id=uid(),
            team_id=team_id,
            label=body.label.strip(),
            collector_url=url,
            secret_encrypted=vault.encrypt(body.sharedSecret),
            secret_hint=KeyVault.hint(body.sharedSecret),
            cursor_seq=cursor,
            next_attempt_at=_now(),
            created_by=principal.userId,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(row)
        await db.commit()
        await emit_siem_sink_created(
            db,
            actor_user_id=principal.userId,
            team_id=team_id,
            target_id=row.id,
            detail={"label": row.label, "startFrom": body.startFrom, "cursor": row.cursor_seq},
        )
        await db.commit()
        pending = await count_after(db, team_id, after_seq=row.cursor_seq)
        return _public(row, pending=pending)


@router.get("")
async def list_sinks(
    team_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    """List the team's collectors with their lag, so a stalled SIEM is visible."""
    _team_admin(principal, team_id)
    async with request.app.state.session_factory() as db:
        rows = (
            await db.execute(
                select(SiemSink)
                .where(SiemSink.team_id == team_id)
                .order_by(SiemSink.created_at, SiemSink.id)
            )
        ).scalars().all()
        return {
            "items": [
                _public(
                    row,
                    pending=await count_after(db, team_id, after_seq=row.cursor_seq),
                )
                for row in rows
            ],
            "limit": MAX_SINKS_PER_TEAM,
        }


@router.get("/{sink_id}")
async def get_sink(
    team_id: str,
    sink_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    _team_admin(principal, team_id)
    async with request.app.state.session_factory() as db:
        row = await _sink(db, team_id, sink_id)
        return _public(row, pending=await count_after(db, team_id, after_seq=row.cursor_seq))


@router.patch("/{sink_id}")
async def patch_sink(
    team_id: str,
    sink_id: str,
    body: SinkPatchBody,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> dict[str, Any]:
    """Change a collector's settings. `expectedVersion` is required: a cursor or
    endpoint changed by a stale UI click would silently re-point live evidence."""
    _team_admin(principal, team_id)
    async with request.app.state.session_factory() as db:
        row = await _sink(db, team_id, sink_id)
        if row.version != body.expectedVersion:
            raise HTTPException(
                status_code=409,
                detail=error_body("VERSION_CONFLICT", f"sink is at version {row.version}"),
            )
        changed: dict[str, Any] = {}
        if body.label is not None and body.label.strip() != row.label:
            changed["label"] = [row.label, body.label.strip()]
            row.label = body.label.strip()
        if body.collectorUrl is not None:
            url = _validate_url(body.collectorUrl)
            if url != row.collector_url:
                changed["collectorUrl"] = [row.collector_url, url]
                row.collector_url = url
        if body.sharedSecret is not None:
            vault = request.app.state.vault
            row.secret_encrypted = vault.encrypt(body.sharedSecret)
            row.secret_hint = KeyVault.hint(body.sharedSecret)
            # The value itself never enters the log, not even as a hint pair.
            changed["sharedSecret"] = ["rotated", "rotated"]
        if body.active is not None and bool(body.active) != bool(row.active):
            changed["active"] = [bool(row.active), bool(body.active)]
            row.active = body.active
        if body.resume:
            if row.state != STATE_BLOCKED:
                raise HTTPException(
                    status_code=422,
                    detail=error_body("VALIDATION_ERROR", "this sink is not blocked"),
                )
            changed["state"] = [STATE_BLOCKED, STATE_ACTIVE]
            row.state = STATE_ACTIVE
            row.attempts = 0
            row.next_attempt_at = _now()
            row.last_error = None
            row.last_status_code = None
        row.version += 1
        row.updated_at = _now()
        await db.commit()
        await emit_siem_sink_changed(
            db,
            actor_user_id=principal.userId,
            team_id=team_id,
            target_id=row.id,
            detail={"changed": changed, "version": row.version},
        )
        await db.commit()
        return _public(row, pending=await count_after(db, team_id, after_seq=row.cursor_seq))


@router.delete("/{sink_id}", status_code=204)
async def delete_sink(
    team_id: str,
    sink_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> None:
    """Remove a collector. Idempotent 204, like the other integration deletes."""
    _team_admin(principal, team_id)
    async with request.app.state.session_factory() as db:
        row = (
            await db.execute(
                select(SiemSink).where(SiemSink.id == sink_id, SiemSink.team_id == team_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        label = row.label
        await db.delete(row)
        await db.commit()
        await emit_siem_sink_deleted(
            db, actor_user_id=principal.userId, team_id=team_id, target_id=sink_id,
            detail={"label": label},
        )
        await db.commit()
    return None


# ---------------------------------------------------------------- delivery


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """`t=<ts>,v1=<hex>` over "<ts>." + body — recomputable by the collector."""
    digest = hmac.new(
        secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def ndjson(rows: list[dict[str, Any]]) -> bytes:
    return "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows).encode()


async def _http_sender(url: str, body: bytes, headers: dict[str, str]) -> int:
    host = url.split("//", 1)[1].split("/", 1)[0].split(":", 1)[0].lower()
    if host not in allowed_hosts() or not host_is_public(host):
        raise ValueError("destination_not_allowed")
    import httpx

    async with httpx.AsyncClient(
        timeout=15.0, follow_redirects=False, trust_env=False
    ) as client:
        response = await client.post(url, content=body, headers=headers)
        return response.status_code


async def push_due(
    session_factory,
    *,
    vault: KeyVault,
    now: datetime | None = None,
    sender=_http_sender,
    batch_rows: int = BATCH_ROWS,
) -> dict[str, int]:
    """Claim every due collector and hand it the next batch of its team's rows.

    Returns counts for the worker's log line. The cursor moves only after a 2xx,
    so a crash mid-batch replays that batch instead of skipping it.
    """
    at = now or _now()
    totals = {"pushed": 0, "retrying": 0, "blocked": 0, "empty": 0}
    async with session_factory() as db:
        due = (
            (
                await db.execute(
                    select(SiemSink)
                    .where(
                        SiemSink.active.is_(True),
                        # Two ways a collector is workable: idle and past its
                        # retry clock, or claimed by a worker that died before
                        # releasing it (the lease expired). A `sending` row that
                        # is not yet stale stays locked so two workers cannot
                        # hand the collector the same batch twice, and `blocked`
                        # is an admin's decision, not ours.
                        or_(
                            and_(
                                SiemSink.state == STATE_ACTIVE,
                                SiemSink.next_attempt_at <= at,
                            ),
                            and_(
                                SiemSink.state == STATE_SENDING,
                                SiemSink.claimed_at.is_not(None),
                                SiemSink.claimed_at <= at - timedelta(seconds=LEASE_SECONDS),
                            ),
                        ),
                    )
                    .order_by(SiemSink.next_attempt_at, SiemSink.id)
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        claimed: list[tuple[str, str, str, int]] = []
        for row in due:
            cas = [
                SiemSink.id == row.id,
                SiemSink.state == row.state,
                SiemSink.attempts == row.attempts,
                SiemSink.next_attempt_at == row.next_attempt_at,
            ]
            cas.append(
                SiemSink.claimed_at.is_(None)
                if row.claimed_at is None
                else SiemSink.claimed_at == row.claimed_at
            )
            result = await db.execute(
                update(SiemSink).where(*cas).values(state=STATE_SENDING, claimed_at=at)
            )
            if result.rowcount:
                claimed.append((row.id, row.team_id, row.collector_url, row.cursor_seq))
        await db.commit()

    for sink_id, team_id, url, cursor in claimed:
        async with session_factory() as db:
            rows, has_more = await read_audit_page(db, team_id, after_seq=cursor, limit=batch_rows)
            if not rows:
                await db.execute(
                    update(SiemSink)
                    .where(SiemSink.id == sink_id)
                    .values(
                        state=STATE_ACTIVE,
                        attempts=0,
                        claimed_at=None,
                        next_attempt_at=at + timedelta(seconds=_poll_seconds()),
                        updated_at=at,
                    )
                )
                await db.commit()
                totals["empty"] += 1
                continue

            body = ndjson(rows)
            last_seq = rows[-1]["seq"]
            ts = int(at.timestamp())
            secret = vault.decrypt(
                (
                    await db.execute(
                        select(SiemSink.secret_encrypted).where(SiemSink.id == sink_id)
                    )
                ).scalar_one()
            )
            headers = {
                "Content-Type": "application/x-ndjson",
                SIGNATURE_HEADER: sign(secret, ts, body),
                "X-Harness-Timestamp": str(ts),
                "Idempotency-Key": f"{sink_id}:{cursor + 1}-{last_seq}",
                "User-Agent": "harness-siem-sink/1",
            }
            try:
                status = await sender(url, body, headers)
            except Exception as exc:  # noqa: BLE001 - any transport error is a retry
                status, reason = 0, type(exc).__name__
            else:
                reason = "" if 200 <= status < 300 else f"http-{status}"

            if 200 <= status < 300:
                await db.execute(
                    update(SiemSink)
                    .where(SiemSink.id == sink_id)
                    .values(
                        state=STATE_ACTIVE,
                        cursor_seq=last_seq,
                        attempts=0,
                        claimed_at=None,
                        last_status_code=status,
                        last_error=None,
                        delivered_rows=SiemSink.delivered_rows + len(rows),
                        last_batch_at=at,
                        next_attempt_at=(
                            at  # more rows waiting: go again on the next tick
                            if has_more
                            else at + timedelta(seconds=_poll_seconds())
                        ),
                        updated_at=at,
                    )
                )
                await db.commit()
                totals["pushed"] += 1
                continue

            attempt_no = (
                await db.execute(select(SiemSink.attempts).where(SiemSink.id == sink_id))
            ).scalar_one() + 1
            delay = min(RETRY_BASE_SECONDS * 2 ** (attempt_no - 1), RETRY_MAX_SECONDS)
            blocked = attempt_no >= MAX_ATTEMPTS
            await db.execute(
                update(SiemSink)
                .where(SiemSink.id == sink_id)
                .values(
                    state=STATE_BLOCKED if blocked else STATE_ACTIVE,
                    attempts=attempt_no,
                    claimed_at=None,
                    last_status_code=status or None,
                    last_error=(reason or "send_failed")[:40],
                    next_attempt_at=at + timedelta(seconds=delay),
                    updated_at=at,
                )
            )
            await db.commit()
            if blocked:
                async with session_factory() as db:
                    row = await db.get(SiemSink, sink_id)
                    await emit_siem_sink_blocked(
                        db,
                        actor_user_id=None,
                        team_id=team_id,
                        target_id=sink_id,
                        actor_kind="system",
                        detail={
                            "attempts": attempt_no,
                            "lastError": row.last_error,
                            "label": row.label,
                        },
                    )
                    await db.commit()
                totals["blocked"] += 1
            else:
                totals["retrying"] += 1
    return totals


def _poll_seconds() -> int:
    raw = os.getenv("HARNESS_SIEM_POLL_SECONDS", "30")
    try:
        return max(5, min(int(raw), 300))
    except ValueError:
        return 30
