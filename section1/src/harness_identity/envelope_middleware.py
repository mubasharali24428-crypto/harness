"""M12-W2.1 lane F1 — error envelope unification + Idempotency-Key middleware.

Fixes H-019 + H-020 (docs/clientela/part1a-critical-high.md, S-015/S-016).

H-019 — error envelope unification
    Every error response returns the FROZEN contract shape
    ``{"error": {code, message}}`` (harness_contracts.api.error_body), with a
    mirrored ``detail`` field for backward compatibility with existing clients:

        {"error": {"code": "...", "message": "..."},
         "detail": {"error": {"code": ..., "message": ...}}}

    Covered paths:
      - ``RequestValidationError`` (FastAPI 422s previously escaped the
        envelope as bare ``{"detail": [...]}``).
      - Starlette ``HTTPException``: handlers already raising
        ``error_body()`` inside ``detail`` pass through byte-for-byte; plain
        string/other detail payloads are normalized into the envelope.
      - Unhandled exceptions (500): envelope + log, never a stack-trace body.

    This file is a section-local canonical copy; contracts/ is FROZEN and
    stays untouched.

H-020 — Idempotency-Key middleware on mutating POSTs
    A POST carrying an ``Idempotency-Key`` header executes once per
    (concrete path, principal, key); replays return the cached status+body
    without re-running the handler. Keys are scoped per CONCRETE path
    (never collapsed onto the route template: recycling one key across two
    resources must never false-replay) and per principal. Storage is the
    DB-backed ``idempotency_keys`` table
    (section1/migrations/0015_idempotency.sql); the reserve row carries a
    JSONB placeholder — ``'{}'`` bound as $2 and cast ``::jsonb``. The
    column is JSONB and asyncpg rejects a bare '' (the old literal made
    every reserve fail open); an inline ``'{}'::jsonb`` literal cannot be
    used either because statements go through ``str.format`` for the table
    name, and ``{}`` is a format field. TTL/cleanup is Wave 3's job (the
    table already carries ``expires_at``). Every storage problem fails OPEN
    so the middleware can never take the API down.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from harness_contracts import error_body
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
_IDEMPOTENT_METHODS = frozenset({"POST"})

logger = logging.getLogger("harness.envelope")


def _envelope(code: str, message: str) -> dict:
    """Contract envelope + mirrored legacy ``detail`` field.

    The mirror is byte-for-byte identical to the pre-unification body shape,
    so existing clients reading ``resp["detail"]["error"]["code"]`` keep
    working through and after the migration window.
    """
    body = error_body(code, message)
    return {"error": dict(body["error"]), "detail": {"error": dict(body["error"])}}


def install_error_envelope(app: FastAPI) -> None:
    """H-019: register exception handlers normalizing every error response to
    ``{"error": {code, message}}`` (+ legacy ``detail`` mirror)."""

    @app.exception_handler(RequestValidationError)
    async def _validation_envelope(  # noqa: RUF029 — FastAPI handler protocol
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        summary = "; ".join(
            "{}: {}".format(
                ".".join(str(p) for p in err.get("loc", [])[-2:]),
                err.get("msg", "invalid"),
            )
            for err in exc.errors()
        )
        return JSONResponse(
            status_code=422,
            content=_envelope("VALIDATION_ERROR", f"invalid request: {summary}"),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_envelope(  # noqa: RUF029 — FastAPI handler protocol
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        detail = exc.detail
        inner = detail.get("error") if isinstance(detail, dict) else None
        if isinstance(inner, dict) and "code" in inner and "message" in inner:
            # Already contract-shaped (routes raise HTTPException(detail=error_body(...))):
            # pass through unchanged, only adding the compat mirror. Keys a route adds beside
            # `error` (docs/realtime.md 5.4: currentVersion, currentOwnerUserId) reach the client.
            code = str(inner["code"])
            message = str(inner["message"])
            extra = {k: v for k, v in detail.items() if k != "error"}
            content = _envelope(code, message)
            content["detail"].update(extra)
            content.update(extra)
            return JSONResponse(
                status_code=exc.status_code,
                content=content,
                headers=exc.headers,
            )
        elif isinstance(detail, dict):
            code = "REQUEST_ERROR"
            message = json.dumps(detail)
        else:
            # Legacy string details never carried machine-readable codes.
            code = "REQUEST_ERROR"
            message = str(detail)
        # W15: headers a route attached (Retry-After on a 429,
        # WWW-Authenticate on a 401) are part of the contract — normalizing
        # the body must not drop them.
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(code, message),
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def _unhandled_envelope(  # noqa: RUF029 — FastAPI handler protocol
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=_envelope("INTERNAL_ERROR", "internal server error"),
        )


def route_template(path: str) -> str:
    """Collapse raw ids in a concrete path onto its route template. Kept for
    logging/metrics labeling; the idempotency fingerprint deliberately uses
    the CONCRETE path instead, so recycling one Idempotency-Key across two
    resources (/runs/a/start vs /runs/b/start) can never false-replay."""
    parts: list[str] = []
    for seg in path.split("/"):
        if not seg:
            continue
        is_numeric = seg.isdigit()
        is_prefixed_uuid = "-" in seg and seg[0].isalpha() and not seg.startswith("api")
        if is_numeric or is_prefixed_uuid:
            parts.append("{id}")
        else:
            parts.append(seg)
    return "/" + "/".join(parts)


def principal_scope_key(request: Request) -> str:
    """Stable per-principal scope token: bearer-token digest when present,
    else client IP. Digest only — session tokens never reach storage/logs."""
    auth = request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    if token:
        material = f"token:{token}"
    elif request.client is not None:
        material = f"ip:{request.client.host}"
    else:
        material = "anon"
    return hashlib.sha256(material.encode()).hexdigest()


class IdempotencyMiddleware:
    """H-020: DB-backed replay cache for mutating POSTs.

    Wiring (ATLAS merge): ``app.add_middleware(IdempotencyMiddleware)`` after
    the pool exists AND set ``app.state.idempotency_pool = <asyncpg pool>``.
    First request with an Idempotency-Key executes and stores (status, body);
    concurrent/replayed requests carrying the same key get the cached pair
    back verbatim. Scoped by (concrete path, principal, key); any storage
    failure fails OPEN.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        key = headers.get(IDEMPOTENCY_KEY_HEADER, "").strip()
        method = scope.get("method", "GET").upper()
        if method not in _IDEMPOTENT_METHODS or not key or len(key) > 255:
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        # Scope = concrete path (never a template collapse: two different
        # resources must not share a key) + principal digest + client key.
        fingerprint = f"{request.url.path}|{principal_scope_key(request)}|{key}"
        pool = getattr(request.app.state, "idempotency_pool", None)
        table = getattr(request.app.state, "idempotency_table", "idempotency_keys")
        if pool is None:
            logger.warning("Idempotency-Key ignored: no pool wired on app.state (fail-open)")
            await self.app(scope, receive, send)
            return

        cached = await self._lookup(pool, table, fingerprint)
        if cached is None:
            reserved = await self._reserve(pool, table, fingerprint)
            if not reserved:
                # Another worker holds this exact (route, principal, key) right
                # now; poll briefly for its stored result instead of double-
                # executing. Holder vanished => execute locally, never hang.
                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
                    cached = await self._lookup(pool, table, fingerprint)
                    if cached is not None:
                        break
        if cached is not None:
            await self._send_replay(send, cached)
            return

        status_holder: list[int] = []
        chunks: list[bytes] = []

        async def send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start":
                status_holder.append(int(message["status"]))
                mutable = MutableHeaders(scope=message)
                mutable.append("Idempotent-Replay", "false")
            elif (
                message["type"] == "http.response.body"
                and status_holder
                and status_holder[0] < 400
            ):
                chunk = message.get("body", b"")
                if chunk:
                    chunks.append(bytes(chunk))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            await self._release(pool, table, fingerprint)
            raise
        if status_holder and status_holder[0] < 400:
            raw = b"".join(chunks)
            try:
                parsed = json.loads(raw.decode()) if raw else {}
            except (ValueError, UnicodeDecodeError):
                parsed = {"raw": raw.decode(errors="replace")}
            await self._store(pool, table, fingerprint, status_holder[0], parsed)

    async def _send_replay(self, send: Send, cached: tuple[int, dict]) -> None:
        status_code, payload = cached
        body = json.dumps(payload).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"idempotent-replay", b"true"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    # -- storage helpers (asyncpg pool) --------------------------------------

    @staticmethod
    def _table_sql(table: str, statement: str) -> str:
        # Table name comes from app.state (operator-controlled); validate it as
        # a bare identifier before string-formatting into SQL.
        if not table or not table.replace("_", "").isalnum():
            table = "idempotency_keys"
        return statement.format(table=table)

    async def _lookup(self, pool: Any, table: str, fingerprint: str) -> tuple[int, dict] | None:
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    self._table_sql(
                        table,
                        "SELECT status_code, response_body FROM {table} "
                        "WHERE fingerprint = $1",
                    ),
                    fingerprint,
                )
        except Exception:
            logger.warning("idempotency lookup failed; executing normally", exc_info=True)
            return None
        if row is None:
            return None
        try:
            status = int(row["status_code"])
            if status == 0 or not row["response_body"]:
                # status_code = 0 is the reserve sentinel: the holder is still
                # executing. Treat as a miss so pollers keep waiting instead of
                # replaying an invalid status-0 placeholder response.
                return None
            return status, json.loads(row["response_body"])
        except (ValueError, TypeError):
            return None

    async def _reserve(self, pool: Any, table: str, fingerprint: str) -> bool:
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    self._table_sql(
                        table,
                        "INSERT INTO {table} (fingerprint, status_code, response_body) "
                        "VALUES ($1, 0, $2::jsonb) ON CONFLICT (fingerprint) DO NOTHING "
                        "RETURNING fingerprint",
                    ),
                    fingerprint,
                    "{}",
                )
            return row is not None
        except Exception:
            logger.warning("idempotency reserve failed; executing normally", exc_info=True)
            return True

    async def _release(self, pool: Any, table: str, fingerprint: str) -> None:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    self._table_sql(
                        table,
                        "DELETE FROM {table} WHERE fingerprint = $1 AND status_code = 0",
                    ),
                    fingerprint,
                )
        except Exception:
            logger.warning("idempotency release failed", exc_info=True)

    async def _store(
        self, pool: Any, table: str, fingerprint: str, status_code: int, payload: dict
    ) -> None:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    self._table_sql(
                        table,
                        "UPDATE {table} SET status_code = $2, "
                        "response_body = $3::jsonb "
                        "WHERE fingerprint = $1",
                    ),
                    fingerprint,
                    int(status_code),
                    json.dumps(payload),
                )
        except Exception:
            logger.warning("idempotency store failed; next replay re-executes", exc_info=True)
