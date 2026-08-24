"""VT-5 idempotency x concurrency probe v2 — REAL asyncpg + real Postgres.

Single event loop shared by pool and app (httpx ASGITransport): two concurrent
POSTs with the SAME Idempotency-Key must collapse to ONE handler execution.
"""
import asyncio
import sys

import asyncpg
import httpx
from fastapi import FastAPI

sys.path.insert(0, "/Users/user/untitled folder 2/harness/section1/src")

from harness_identity.envelope_middleware import IdempotencyMiddleware, install_error_envelope

DSN = "postgresql://harness:harness@localhost:5499/vt5_walk"

executions = {"n": 0}


def build_app(pool):
    app = FastAPI()
    install_error_envelope(app)

    @app.post("/api/v1/vt5/side-effect")
    async def _handler():
        executions["n"] += 1
        await asyncio.sleep(0.15)
        return {"ok": True}

    app.add_middleware(IdempotencyMiddleware)
    app.state.idempotency_pool = pool
    app.state.idempotency_table = "idempotency_keys"
    return app


async def main():
    conn = await asyncpg.connect(DSN)
    await conn.execute("DELETE FROM idempotency_keys WHERE fingerprint LIKE '%vt5-%'")
    await conn.close()

    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=8)
    app = build_app(pool)

    async def fire(client, key):
        return await client.post(
            "/api/v1/vt5/side-effect", headers={"Idempotency-Key": key}
        )

    # ---- A: concurrent duplicates, same key ------------------------------
    executions["n"] = 0
    transport = httpx.ASGITransport(app=app, client=("10.9.9.9", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://vt5") as client:
        r1, r2 = await asyncio.gather(fire(client, "vt5-dup"), fire(client, "vt5-dup"))
    print(f"C1 same-key pair : {r1.status_code}/{r2.status_code} "
          f"replay={[r1.headers.get('idempotent-replay'), r2.headers.get('idempotent-replay')]} "
          f"execs={executions['n']}  <-- MUST be 1")
    row = await pool.fetchrow(
        "SELECT fingerprint, status_code, response_body FROM idempotency_keys "
        "WHERE fingerprint LIKE '%|vt5-dup'")
    print(f"C1 stored row    : {dict(row) if row else None}")

    # ---- B: distinct keys execute independently --------------------------
    executions["n"] = 0
    async with httpx.AsyncClient(transport=transport, base_url="http://vt5") as client:
        d1, d2 = await asyncio.gather(fire(client, "vt5-k1"), fire(client, "vt5-k2"))
    print(f"C2 distinct keys : {d1.status_code}/{d2.status_code}, execs={executions['n']} (must be 2)")

    # ---- C: sequential replay of first key -------------------------------
    executions["n"] = 0
    async with httpx.AsyncClient(transport=transport, base_url="http://vt5") as client:
        s1 = await fire(client, "vt5-dup")
    print(f"C3 seq replay    : {s1.status_code} replay={s1.headers.get('idempotent-replay')} "
          f"execs={executions['n']} (must be 0)")

    rows = await pool.fetch(
        "SELECT fingerprint, status_code FROM idempotency_keys WHERE fingerprint LIKE '%|vt5-%' ORDER BY 1")
    print(f"C4 table rows    : {[ (r['fingerprint'].split('|')[-1], r['status_code']) for r in rows ]}")

    await pool.close()


asyncio.run(main())
print("IDEM-PROBE v2 DONE")
