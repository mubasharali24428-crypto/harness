"""W9 visual evidence on real SQL: migration 0065, provenance, pairing and isolation.

The validators are covered by ``test_visuals.py``; this exercises the routes
against the artifact store's own tables (sqlite, real migrations), because the
rules that matter here are about rows: a revision snapshot that must match, one
after per before (a partial unique index), and a foreign team seeing nothing.
"""

import asyncio
import struct
import zlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator import artifacts, collab, shares, visuals
from harness_orchestrator.api import get_principal
from harness_orchestrator.api import router as orch_router
from harness_orchestrator.service import OrchestratorService

from . import test_artifacts_support as support

MIGRATIONS = Path(__file__).resolve().parents[3] / "section1" / "migrations"
FILES = (*support.FILES, "0065_visual_artifacts.sql")
SHA_D = "d" * 40
BASE = "/api/v1/orchestrator/runs"


def statements() -> list[str]:
    out: list[str] = []
    for name in FILES:
        body = "\n".join(
            ln for ln in (MIGRATIONS / name).read_text().splitlines()
            if not ln.lstrip().startswith("--")
        )
        out += [s.strip() for s in body.split(";") if s.strip()]
    return out


def png(width: int, height: int) -> bytes:
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(
            ">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


@pytest.fixture()
def venv(tmp_path, monkeypatch):
    for k in ("HARNESS_ARTIFACT_MAX_BYTES", "HARNESS_ARTIFACT_RUN_MAX_COUNT",
              "HARNESS_ARTIFACT_RUN_MAX_BYTES", "HARNESS_ARTIFACT_RETENTION_DAYS",
              "HARNESS_VISUAL_MAX_BYTES"):
        monkeypatch.delenv(k, raising=False)
    blobs = tmp_path / "blobs"
    monkeypatch.setenv("HARNESS_ARTIFACT_ROOT", str(blobs))
    clock = {"t": support.T0}
    monkeypatch.setattr(collab, "db_now", lambda: clock["t"])
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/v.db")

    async def setup():
        async with engine.begin() as conn:
            for d in (*support.DDL, *support.OTHER_LANES, *statements()):
                await conn.execute(text(d))
            await conn.execute(text(
                "INSERT INTO projects (id, team_id, repo_full_name) VALUES"
                " ('p', 'team-a', 'acme/app'), ('q', 'team-b', 'acme/other')"))
            await conn.execute(text(
                "INSERT INTO agent_runs (id, project_id, branch) VALUES"
                " ('r1', 'p', 'agent/r1'), ('r3', 'q', 'agent/r3')"))
            for u, (t, role, kind) in support.USERS.items():
                await conn.execute(
                    text("INSERT INTO team_members (id, team_id, user_id, display_name, role,"
                         " member_type) VALUES (:i, :t, :u, :n, :r, :k)"),
                    {"i": f"m-{u}", "t": t, "u": u, "n": f"Name {u}", "r": role.value, "k": kind})

    asyncio.run(setup())
    factory = async_sessionmaker(engine, expire_on_commit=False)

    class Bus:
        async def publish(self, *a, **k):
            return None

        async def ensure_run(self, *a, **k):
            return None

    service = OrchestratorService(session_factory=factory, bus=Bus())
    monkeypatch.setattr(orch_api, "_service", service)
    artifacts.configure_artifacts(service)
    app = FastAPI()
    app.include_router(orch_router)
    app.include_router(artifacts.router)
    app.include_router(shares.router)
    app.include_router(visuals.router)
    who = {"u": "lead"}
    app.dependency_overrides[get_principal] = lambda: Principal(
        userId=who["u"], teamId=support.USERS[who["u"]][0], role=support.USERS[who["u"]][1])
    yield TestClient(app), who, factory, blobs
    asyncio.run(engine.dispose())


def record_head(client, who="lead", run="r1", head=support.SHA_C):
    r = client.put(f"{BASE}/{run}/git-state", json={
        "baseBranch": "main", "baseSha": support.SHA_A, "observedHeadSha": support.SHA_A,
        "headBranch": f"agent/{run}", "runHeadSha": head})
    assert r.status_code == 200, r.text


def attach(client, who, *, name="shot.png", role="before", pair=None, data=None):
    params = {"name": name, "role": role}
    if pair:
        params["pairOf"] = pair
    return client.post(f"{BASE}/r1/visuals", params=params, content=data or png(64, 48),
                       headers={"Content-Type": "application/octet-stream"})


def code(resp) -> str:
    return resp.json()["detail"]["error"]["code"]


def test_attach_refuses_a_run_without_a_recorded_head(venv):
    client, who, _, _ = venv
    r = attach(client, who)
    assert r.status_code == 409 and code(r) == "VISUAL_REVISION_UNBOUND", r.text


def test_before_and_after_pair_carry_the_reviewed_revision(venv):
    client, who, factory, _ = venv
    record_head(client)
    before = attach(client, who)
    assert before.status_code == 201, before.text
    body = before.json()["visual"]
    assert (body["role"], body["width"], body["height"]) == ("before", 64, 48)
    assert body["provenance"]["runHeadSha"] == support.SHA_C
    assert body["contentUrl"].endswith(f"artifacts/{body['id']}/content")

    after = attach(client, who, name="after.png", role="after", pair=body["id"])
    assert after.status_code == 201, after.text
    assert after.json()["visual"]["pairId"] == body["id"]

    listed = client.get(f"{BASE}/r1/visuals").json()
    assert len(listed["items"]) == 2
    assert len(listed["pairs"]) == 1
    pair = listed["pairs"][0]
    assert pair["before"]["id"] == body["id"]
    assert pair["after"]["id"] == after.json()["visual"]["id"]
    assert listed["items"][0]["runId"] == "r1"


def test_second_after_is_refused_and_leaves_no_available_row(venv):
    client, who, factory, _ = venv
    record_head(client)
    bid = attach(client, who).json()["visual"]["id"]
    assert attach(client, who, name="a1.png", role="after", pair=bid).status_code == 201
    second = attach(client, who, name="a2.png", role="after", pair=bid)
    assert second.status_code == 409 and code(second) == "VISUAL_PAIR_TAKEN", second.text
    items = client.get(f"{BASE}/r1/visuals").json()["items"]
    assert [i["name"] for i in items if i["role"] == "after"] == ["a1.png"]


def test_after_requires_a_before_of_the_same_run(venv):
    client, who, _, _ = venv
    record_head(client)
    missing = attach(client, who, role="after", pair="nope")
    assert missing.status_code == 422 and code(missing) == "VISUAL_PAIR_INVALID"
    without = attach(client, who, role="after")
    assert without.status_code == 422 and code(without) == "VISUAL_PAIR_INVALID"


def test_pair_across_revisions_is_refused(venv):
    client, who, _, _ = venv
    record_head(client, head=support.SHA_C)
    bid = attach(client, who).json()["visual"]["id"]
    record_head(client, head=SHA_D)
    moved = attach(client, who, name="after.png", role="after", pair=bid)
    assert moved.status_code == 409 and code(moved) == "VISUAL_PAIR_REVISION_MISMATCH", moved.text
    assert moved.json()["detail"]["after"]["runHeadSha"] == SHA_D


def test_non_raster_is_refused_rather_than_stored(venv):
    client, who, _, blobs = venv
    record_head(client)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="48"></svg>'
    r = attach(client, who, name="shot.svg", data=svg)
    assert r.status_code == 415 and code(r) == "VISUAL_TYPE_NOT_ALLOWED", r.text
    gif = b"GIF89a" + b"\x00" * 32
    assert code(attach(client, who, name="shot.gif", data=gif)) == "VISUAL_TYPE_NOT_ALLOWED"
    assert not [p for p in blobs.rglob("*") if p.is_file()]


def test_dimension_bounds_are_enforced_before_storage(venv):
    client, who, _, blobs = venv
    record_head(client)
    r = attach(client, who, data=png(9000, 20))
    assert r.status_code == 422 and code(r) == "VISUAL_DIMENSIONS", r.text
    assert r.json()["detail"]["width"] == 9000
    assert not [p for p in blobs.rglob("*") if p.is_file()]


def test_byte_cap_is_a_stable_refusal(venv, monkeypatch):
    client, who, _, _ = venv
    monkeypatch.setenv("HARNESS_VISUAL_MAX_BYTES", "64")
    record_head(client)
    r = attach(client, who, data=png(64, 48) + b"\x00" * 64)
    assert r.status_code == 413 and code(r) == "ARTIFACT_TOO_LARGE", r.text


def test_request_id_replay_returns_the_same_visual(venv):
    client, who, _, _ = venv
    record_head(client)
    body = png(64, 48)
    params = {"name": "shot.png", "role": "before", "requestId": "rq-1"}
    first = client.post(f"{BASE}/r1/visuals", params=params, content=body,
                        headers={"Content-Type": "application/octet-stream"})
    assert first.status_code == 201, first.text
    again = client.post(f"{BASE}/r1/visuals", params=params, content=body,
                        headers={"Content-Type": "application/octet-stream"})
    assert again.status_code == 200, again.text
    assert again.json()["replayed"] is True
    assert again.json()["visual"]["id"] == first.json()["visual"]["id"]
    items = client.get(f"{BASE}/r1/visuals").json()["items"]
    assert len(items) == 1 and items[0]["state"] == "available"


def test_foreign_team_sees_neither_the_visual_nor_its_run(venv):
    client, who, _, _ = venv
    record_head(client)
    bid = attach(client, who).json()["visual"]["id"]
    who["u"] = "evil"
    assert client.get(f"{BASE}/r1/visuals").status_code in (403, 404)
    assert client.get(f"{BASE}/r1/visuals/{bid}").status_code in (403, 404)
    assert attach(client, who).status_code in (403, 404)
    who["u"] = "lead"
    assert client.get(f"{BASE}/r1/visuals/{bid}").status_code == 200


def test_expired_visual_is_not_available_as_a_pair(venv):
    client, who, factory, _ = venv
    record_head(client)
    bid = attach(client, who).json()["visual"]["id"]

    async def expire():
        async with factory() as db:
            await db.execute(text(
                "UPDATE artifacts SET retention_until = '2000-01-01T00:00:00+00:00'"
                " WHERE id = :i"), {"i": bid})
            await db.commit()

    asyncio.run(expire())
    listed = client.get(f"{BASE}/r1/visuals").json()["items"]
    assert next(i for i in listed if i["id"] == bid)["state"] == "expired"
    r = attach(client, who, name="after.png", role="after", pair=bid)
    assert r.status_code == 409 and code(r) == "VISUAL_PAIR_UNAVAILABLE", r.text
