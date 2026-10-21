"""Part 54 (session 12): WebSocket capacity probe for the project event stream.

Opens USERS x PER_USER sockets on the identity service (each user its own team and project,
each socket its own single-use, fingerprint-bound ticket), holds them all open, then proposes one
plan per project at the same moment and measures how long the `plan_proposed` envelope takes to
reach every socket of that project. Reports sockets opened/refused, delivered/expected and the
delivery latency percentiles. Point it at the ISOLATED loadtest stack (run_probe.sh), never the
live one:

    section1/.venv/bin/python scripts/loadtest/ws_capacity.py \
        --identity http://localhost:18001 --orchestrator http://localhost:18004 --users 50 --per-user 10

`--identity a,b` spreads sockets over identity replicas: socket k mints its ticket on replica k mod n
and connects to replica k+1 mod n, so every socket also redeems a ticket another replica minted.

Needs httpx and websockets (both in section1's venv).
"""

import argparse
import asyncio
import hashlib
import json
import statistics
import sys
import time
import uuid
from collections import Counter

import httpx
from websockets.asyncio.client import connect

UA = "harness-ws-capacity-probe/1"
FP = hashlib.sha256(UA.encode()).hexdigest()[:16]  # UA-only form; ephemeral.compute_client_fingerprint


def mint_run_id():  # same as harness_flow.mint_run_id (that module needs `requests`)
    return f"run-{uuid.uuid4().hex[:12]}"


def new_probe_xff():  # harness_flow.new_probe_xff: a TEST-NET source per user for the login limiter
    b = uuid.uuid4().bytes
    base = ("192.0.2", "198.51.100", "203.0.113")[b[0] % 3]
    return f"{base}.{1 + b[1] % 254}"


async def _user(http, args, i):
    xff = new_probe_xff()
    r = await http.post(f"{args.identity}/api/v1/auth/login", json={"email": f"ws{i}-{uuid.uuid4().hex[:6]}@probe.test"},
                        headers={"x-forwarded-for": xff} if xff else {})
    r.raise_for_status()
    token = r.json()["token"]
    # One synthetic source per user on every call (like harness_flow): the ticket mint is limited
    # per client IP, and the ticket is bound to the IP it was minted from.
    auth = {"authorization": f"Bearer {token}", "user-agent": UA, "x-forwarded-for": xff}
    r = await http.post(f"{args.identity}/api/v1/projects", json={"name": f"ws-{i}"}, headers=auth)
    r.raise_for_status()
    return token, r.json()["id"], auth


async def _socket(http, args, k, auth, project, inbox, opened, refused, stop, phases):
    mint, sock = args.ids[k % len(args.ids)], args.ids[(k + 1) % len(args.ids)]
    t0 = time.perf_counter()  # reset once the gate admits the POST
    try:  # the ticket POST too: a client-side timeout there must count as refused, not vanish
        # httpcore rescans its whole request queue per connection event (O(queued x pool)): 1000 queued
        # POSTs made this client CPU-bound and the ramp twice as long on two replicas. Queue here instead.
        async with args.mint_gate:
            t0 = time.perf_counter()
            r = await http.post(f"{mint}/api/v1/ws-ticket", json={"projectId": project}, headers=auth)
        if r.status_code != 200:
            refused.append(f"ticket {r.status_code}")
            return
        t1 = time.perf_counter()
        url = (sock.replace("http", "ws", 1)
               + f"/api/v1/ws/{project}?ticket={r.json()['ticket']}&fingerprint={FP}")
        async with connect(url, user_agent_header=UA, open_timeout=30, max_size=None,
                           additional_headers={"x-forwarded-for": auth["x-forwarded-for"]}) as ws:
            opened.append(sock)
            phases.append((t1 - t0, time.perf_counter() - t1))  # ticket mint, handshake
            while not stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), 1.0)
                except TimeoutError:
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "plan_proposed":
                    inbox.append((project, time.perf_counter(), sock))
    except Exception as exc:  # noqa: BLE001 - a refused or dropped socket is a measurement, not a crash
        # 10 s bucket of time since the ticket request: past 30 s a ticket has expired (WS_TICKET_TTL_SECONDS)
        refused.append(f"{type(exc).__name__}:{str(exc)[:80]} at {int((time.perf_counter() - t0) // 10) * 10}s+")


async def main(args):
    args.ids = args.identity.split(",")
    args.identity = args.ids[0]  # logins and projects go to the first replica
    limits = httpx.Limits(max_connections=200)
    args.mint_gate = asyncio.Semaphore(limits.max_connections)
    async with httpx.AsyncClient(timeout=60, limits=limits) as http:
        users = await asyncio.gather(*(_user(http, args, i) for i in range(args.users)))
        inbox, opened, refused, phases, stop = [], [], [], [], asyncio.Event()
        t_open = time.perf_counter()
        pairs = [(project, auth) for _, project, auth in users for _ in range(args.per_user)]
        tasks = [asyncio.create_task(_socket(http, args, k, auth, project, inbox, opened, refused, stop, phases))
                 for k, (project, auth) in enumerate(pairs)]
        expected = len(tasks)
        while len(opened) + len(refused) < expected and time.perf_counter() - t_open < 120:
            await asyncio.sleep(0.2)
        open_seconds = time.perf_counter() - t_open
        await asyncio.sleep(args.settle)
        sent = {}

        async def propose(token, project):
            run = mint_run_id()
            sent[project] = time.perf_counter()
            r = await http.post(f"{args.orchestrator}/api/v1/orchestrator/runs/{run}/plan",
                                json={"goal": "ws capacity probe", "projectId": project},
                                headers={"authorization": f"Bearer {token}"})
            r.raise_for_status()

        await asyncio.gather(*(propose(token, project) for token, project, _ in users))
        deadline = time.perf_counter() + args.wait
        while len(inbox) < len(opened) and time.perf_counter() < deadline:
            await asyncio.sleep(0.1)
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    lat = sorted((t - sent[p]) * 1000 for p, t, _ in inbox)
    pct = (lambda q: round(lat[min(len(lat) - 1, int(q * len(lat)))], 1)) if lat else (lambda q: None)
    out = {
        "sockets_requested": expected, "sockets_opened": len(opened), "sockets_refused": len(refused),
        "refusals": dict(Counter(refused).most_common(10)), "open_seconds": round(open_seconds, 2),
        "ramp_s": {name: {"p50": round(v[len(v) // 2], 2), "p95": round(v[int(0.95 * len(v))], 2), "max": round(v[-1], 2)}
                   for name, v in (("ticket", sorted(m for m, _ in phases)), ("handshake", sorted(h for _, h in phases))) if v},
        "delivered": len(inbox), "delivery_expected": len(opened),
        "latency_ms": {"p50": pct(0.5), "p95": pct(0.95), "p99": pct(0.99), "max": lat[-1] if lat else None,
                       "mean": round(statistics.mean(lat), 1) if lat else None},
        "per_replica": {u: {"opened": opened.count(u), "delivered": sum(1 for *_, s in inbox if s == u)}
                        for u in args.ids},
    }
    print(json.dumps(out, indent=2))
    return 0 if out["sockets_opened"] == expected and out["delivered"] == expected else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--identity", default="http://localhost:18001")
    ap.add_argument("--orchestrator", default="http://localhost:18004")
    ap.add_argument("--users", type=int, default=20)
    ap.add_argument("--per-user", type=int, default=10)
    ap.add_argument("--settle", type=float, default=2.0)
    ap.add_argument("--wait", type=float, default=30.0)
    sys.exit(asyncio.run(main(ap.parse_args())))
