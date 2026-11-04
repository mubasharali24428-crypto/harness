#!/usr/bin/env python3
"""W22 event replay under load: disconnect during publication, overflow, truncation.

Events are produced by INSERTs into agent_events, the same committed-row + NOTIFY
path the orchestrator uses to reach the identity service's project socket. The
drill uses a fresh project and makes no model call.

A: a socket reads live events while rows keep arriving, disconnects mid-stream,
   reconnects with afterSeq and must end with every project seq exactly once.
B: one burst far above the live queue (1000) and the replay cap (5000). The
   server must close the socket for overflow; the resume must mark truncation;
   REST paging from the client's cursor must then recover the skipped history.
"""
import asyncio
import json
import uuid
from pathlib import Path

import websockets
from w3_stop_spike import IDENTITY, http, login, sh

BURST = 12000


def psql(sql):
    return sh("docker", "exec", "harness-db-1", "psql", "-U", "harness", "-d", "harness",
              "-Atc", sql).stdout.strip()


def insert(pid, n, phase):
    psql("INSERT INTO agent_events (project_id, type, payload) SELECT "
         f"'{pid}', 'terminal_output', jsonb_build_object('phase', '{phase}', 'i', g) "
         f"FROM generate_series(1, {n}) g")


def project_seqs(pid):
    rows = psql(f"SELECT seq FROM agent_events WHERE project_id='{pid}' ORDER BY seq")
    return [int(s) for s in rows.split()]


async def read_until_closed(ws, seen, timeout=20):
    """Collect seqs until the server closes or goes quiet; return (close code, marker)."""
    marker = None
    try:
        while True:
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            if "seq" in frame:
                seen.append(frame["seq"])
            elif frame.get("replayTruncated"):
                marker = frame
    except websockets.ConnectionClosed as closed:
        return closed.rcvd.code if closed.rcvd else None, marker
    except TimeoutError:
        return "quiet", marker


async def main():
    tag = uuid.uuid4().hex[:12]
    record = {"tag": tag, "burst": BURST, "verdict": "FAIL", "checks": {}}
    out = Path(__file__).resolve().parents[2] / "docs/briefs/evidence/w22" / f"replay-load-{tag}.json"
    checks = record["checks"]
    try:
        token = login(f"w22-load-{tag}@example.com")
        status, project = http("POST", f"{IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"w22 load {tag}"})
        assert status == 201
        pid = project["id"]
        record["projectId"] = pid
        headers = {"Authorization": f"Bearer {token}"}
        url = f"ws://127.0.0.1:8001/api/v1/ws/{pid}"

        # A: disconnect while publication continues.
        async def produce():
            for _ in range(30):
                await asyncio.to_thread(insert, pid, 10, "A")
                await asyncio.sleep(0.05)

        first = []
        producer = asyncio.create_task(produce())
        async with websockets.connect(url, additional_headers=headers) as ws:
            while len(first) < 60:
                frame = json.loads(await asyncio.wait_for(ws.recv(), 30))
                first.append(frame["seq"])
        cursor = first[-1]
        await asyncio.sleep(0.3)  # publication continues while disconnected
        resumed = []
        async with websockets.connect(f"{url}?afterSeq={cursor}", additional_headers=headers) as ws:
            await producer
            expected_a = project_seqs(pid)
            while len(resumed) < len(expected_a) - len(first):
                frame = json.loads(await asyncio.wait_for(ws.recv(), 30))
                resumed.append(frame["seq"])
        record["A"] = {"published": len(expected_a), "beforeDisconnect": len(first),
                       "cursor": cursor, "afterResume": len(resumed)}
        checks["A_no_seq_at_or_below_cursor"] = all(s > cursor for s in resumed)
        checks["A_union_exact"] = first + resumed == expected_a
        if not checks["A_union_exact"]:
            got = first + resumed
            at = next((i for i, (g, e) in enumerate(zip(got, expected_a)) if g != e), None)
            record["A"]["mismatch"] = {
                "sameSet": sorted(got) == expected_a, "duplicates": len(got) - len(set(got)),
                "missing": sorted(set(expected_a) - set(got))[:20], "firstDiffIndex": at,
                "gotAround": got[max(0, (at or 0) - 3):(at or 0) + 4],
                "expectedAround": expected_a[max(0, (at or 0) - 3):(at or 0) + 4]}
        checks["A_rows_arrived_while_disconnected"] = producer.done() and resumed[0] > cursor

        # B: overflow and truncation.
        base = project_seqs(pid)[-1]
        live = []
        async with websockets.connect(url, additional_headers=headers) as ws:
            await asyncio.to_thread(insert, pid, BURST, "B")
            code, _ = await read_until_closed(ws, live)
        all_seqs = project_seqs(pid)
        burst = [s for s in all_seqs if s > base]
        last = live[-1] if live else base
        record["B"] = {"burstFirst": burst[0], "burstLast": burst[-1], "liveReceived": len(live),
                       "closeCode": code, "lastLiveSeq": last}
        checks["B_overflow_closed_socket"] = code in (1013, 4408)
        # The connect replays phase A first; only seqs past `base` are burst deliveries.
        live_burst = [s for s in live if s > base]
        record["B"]["liveBurstDelivered"] = len(live_burst)
        checks["B_live_prefix_in_order"] = live_burst == burst[: len(live_burst)]

        replayed = []
        async with websockets.connect(f"{url}?afterSeq={last}", additional_headers=headers) as ws:
            _, marker = await read_until_closed(ws, replayed, timeout=5)
        record["B"].update({"resumeReplayed": len(replayed), "marker": marker})
        gap = [s for s in burst if s > last]
        checks["B_truncation_marked"] = bool(marker) and marker["oldestSeqSent"] == replayed[0]
        checks["B_resume_is_newest_cap"] = replayed == gap[-len(replayed):]

        # Backfill (last, oldestSeqSent) over REST: bounded by before_seq, paging by nextCursor.
        backfill, cursor_b, pages = [], last, 0
        stop = marker["oldestSeqSent"] if marker else last
        while cursor_b < stop and pages < 20:
            status, page = http("GET", f"{IDENTITY}/api/v1/projects/{pid}/events/replay"
                                f"?after_seq={cursor_b}&before_seq={stop}&limit=2000",
                                token=token)
            assert status == 200
            pages += 1
            items = [e["seq"] for e in page["items"]]
            if not items or page["nextCursor"] <= cursor_b:
                break
            backfill += [s for s in items if s < stop]
            cursor_b = page["nextCursor"]
        missing = sorted(set(gap) - set(backfill) - set(replayed))
        record["B"].update({"restPages": pages, "restBackfilled": len(backfill),
                            "missingAfterRecovery": len(missing),
                            "missingRange": [missing[0], missing[-1]] if missing else None})
        checks["B_recovered_everything"] = not missing
        record["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    except Exception as exc:  # noqa: BLE001 — keep failed evidence; no credential text
        record["errorType"] = type(exc).__name__
        record["error"] = str(exc)[:300]
    finally:
        out.write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps({"verdict": record["verdict"], "checks": checks, "evidence": str(out)}))
    return 0 if record["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
