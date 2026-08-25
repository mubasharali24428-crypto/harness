"""M12-W2.1 lane F1 tests (Squad ALPHA) — H-019 + H-020, section6.

H-019: RequestValidationError (FastAPI 422) on the meetings router answers
the FROZEN error_body() envelope (+ legacy ``detail`` mirror); contract-
shaped HTTPException details (e.g. the NOT_FOUND envelope) pass through
byte-for-byte.

H-020: Idempotency-Key replay on POST /api/v1/meetings — same key twice =>
identical status+body and ONE created meeting; different key => separate
executions. Storage runs through a fake asyncpg pool driving the
middleware's real SQL paths; migration 0015 defines the table.

Scratch-app pattern per tests/conformance/test_meeting_conformance.py
(no repo conftest exists here either).
"""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness_meeting.api import get_principal, router
from harness_meeting.service import MeetingService

# M17-B3: the byte-identical S6 middleware fork is deleted; the canonical
# copy lives in harness_identity (tests reach it via the section1/src seam).
import sys as _sys
from pathlib import Path as _Path

_S1_SRC = str(_Path(__file__).resolve().parents[2] / "section1" / "src")
if _S1_SRC not in _sys.path:
    _sys.path.append(_S1_SRC)
from harness_identity.envelope_middleware import (  # noqa: E402
    IdempotencyMiddleware,
    install_error_envelope,
)


class FakeAsyncPgPool:
    """Minimal asyncpg-pool stand-in validating the middleware's SQL paths."""

    def __init__(self):
        self.rows = {}

    class _Conn:
        def __init__(self, pool):
            self._pool = pool

        async def fetchrow(self, sql, *args):
            stmt = " ".join(sql.split())
            if stmt.startswith("SELECT"):
                rec = self._pool.rows.get(args[0])
                return None if rec is None else dict(rec)
            if stmt.startswith("INSERT"):
                fp = args[0]
                if fp in self._pool.rows:
                    return None  # ON CONFLICT DO NOTHING
                self._pool.rows[fp] = {"fingerprint": fp, "status_code": 0, "response_body": ""}
                return {"fingerprint": fp}
            if stmt.startswith("UPDATE"):
                rec = self._pool.rows.get(args[0])
                if rec is not None:
                    rec["status_code"], rec["response_body"] = args[1], str(args[2])
                return None
            if stmt.startswith("DELETE"):
                self._pool.rows.pop(args[0], None)
                return None
            raise AssertionError(f"unexpected SQL: {stmt[:80]}")

        async def execute(self, sql, *args):
            await self.fetchrow(sql, *args)

    def acquire(self):
        return _AcquireCtx(self._Conn(self))


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


def _client(monkeypatch) -> TestClient:
    svc = MeetingService()
    monkeypatch.setattr("harness_meeting.api._service", svc)
    app = FastAPI()
    app.include_router(router)
    install_error_envelope(app)
    app.add_middleware(IdempotencyMiddleware)
    pool = FakeAsyncPgPool()
    app.state.idempotency_pool = pool
    app.state.idempotency_table = "idempotency_keys"
    app.dependency_overrides[get_principal] = lambda: SimpleNamespace(
        userId="u-f1", teamId="team-f1"
    )
    client = TestClient(app)
    client._f1_svc = svc  # noqa: SLF001 — test handle only
    client._f1_pool = pool  # noqa: SLF001
    return client


# --------------------------------------------------------------- H-019


def test_validation_error_envelope_on_meetings(monkeypatch):
    """Missing title used to escape as bare {"detail": [...]}."""
    client = _client(monkeypatch)
    res = client.post("/api/v1/meetings", json={"projectId": "proj-f1"})
    assert res.status_code == 422
    body = res.json()
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["detail"]["error"]["code"] == "VALIDATION_ERROR"
    assert "title" in body["error"]["message"]


def test_not_found_keeps_contract_code(monkeypatch):
    """detail=error_body(...) passthrough stays byte-for-byte (plus mirror)."""
    client = _client(monkeypatch)
    res = client.get("/api/v1/meetings/mtg-missing")
    assert res.status_code == 404
    body = res.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["detail"]["error"]["code"] == "NOT_FOUND"


# --------------------------------------------------------------- H-020


def test_same_key_replays_identically_single_side_effect(monkeypatch):
    client = _client(monkeypatch)
    headers = {"Idempotency-Key": "mtg-once"}
    payload = {"projectId": "proj-idem", "title": "F1 idempotent meeting"}

    r1 = client.post("/api/v1/meetings", json=payload, headers=headers)
    assert r1.status_code == 201, r1.text
    first_id = r1.json()["id"]

    r2 = client.post("/api/v1/meetings", json=payload, headers=headers)
    assert r2.status_code == 201
    assert r2.json() == r1.json()  # identical cached response...
    assert r2.json()["id"] == first_id  # ...same meeting id...
    assert r2.headers.get("idempotent-replay") == "true"  # ...served from cache

    assert len(client._f1_svc.meetings) == 1  # single side effect


def test_different_key_executes_separately(monkeypatch):
    client = _client(monkeypatch)
    payload = {"projectId": "proj-two", "title": "F1 separate executions"}

    ra = client.post(
        "/api/v1/meetings", json=payload, headers={"Idempotency-Key": "m-A"}
    )
    rb = client.post(
        "/api/v1/meetings", json=payload, headers={"Idempotency-Key": "m-B"}
    )
    assert ra.status_code == rb.status_code == 201
    assert ra.json()["id"] != rb.json()["id"]  # two real meetings...
    assert rb.headers.get("idempotent-replay") != "true"  # ...freshly executed
    assert len(client._f1_svc.meetings) == 2


def test_no_header_bypasses_cache(monkeypatch):
    client = _client(monkeypatch)
    res = client.post(
        "/api/v1/meetings",
        json={"projectId": "proj-nh", "title": "No header"},
    )
    assert res.status_code == 201
    assert res.headers.get("idempotent-replay") is None
