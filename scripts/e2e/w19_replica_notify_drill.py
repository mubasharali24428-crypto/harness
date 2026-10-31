#!/usr/bin/env python3
"""W17/W19 leg: event-bus delivery ACROSS Postgres NOTIFY replicas (two processes).

The single-instance replay drills prove overflow/backfill but every event was
seen by the one process that also held the LISTEN. This drill closes the
cross-replica claim: a SECOND identity replica (harness-identity-b, port 8101,
same DB, its own LISTEN) is started, a project socket is opened on EACH replica,
and events are written through the committed agent_events + pg_notify trigger
path (a direct INSERT — the same row + NOTIFY the orchestrator's own publishes
produce, and which NO application process here publishes). Both replicas must
push every seq to their connected socket. A socket that receives an event its
own process never inserted can only have learned it from Postgres NOTIFY, so
delivery is proven to cross a process boundary — the fan-out a real multi-replica
deployment depends on. No model call. The second replica is torn down at the end.
"""
import asyncio
import json
import uuid
from pathlib import Path

import websockets
from w3_stop_spike import IDENTITY, http, login, sh

REPLICA_B = "ws://127.0.0.1:8101/api/v1/ws"
REPLICA_A = "ws://127.0.0.1:8001/api/v1/ws"
N = 40


def psql(sql):
    return sh("docker", "exec", "harness-db-1", "psql", "-U", "harness", "-d", "harness",
              "-Atc", sql).stdout.strip()


def insert(pid, n, phase):
    # The committed path: one INSERT fires the migration-0003 pg_notify trigger,
    # which every LISTENing process receives independently.
    psql("INSERT INTO agent_events (project_id, type, payload) SELECT "
         f"'{pid}', 'terminal_output', jsonb_build_object('phase', '{phase}', 'i', g) "
         f"FROM generate_series(1, {n}) g")


def project_seqs(pid):
    return [int(s) for s in psql(
        f"SELECT seq FROM agent_events WHERE project_id='{pid}' ORDER BY seq").split()]


async def collect(ws, want, seen, timeout=25):
    """Read until we have `want` seqs or go quiet; returns the ordered list."""
    try:
        while len(seen) < want:
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            if "seq" in frame:
                seen.append(frame["seq"])
    except (websockets.ConnectionClosed, TimeoutError):
        pass
    return seen


async def main():
    tag = uuid.uuid4().hex[:12]
    record = {"tag": tag, "n": N, "verdict": "FAIL", "checks": {}}
    out = (Path(__file__).resolve().parents[2] / "docs/briefs/evidence/w19"
           / f"replica-notify-{tag}.json")
    checks = record["checks"]
    try:
        token = login(f"w19-replica-{tag}@example.com")
        status, project = http("POST", f"{IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"w19 replica {tag}"})
        assert status == 201, f"project create -> {status}: {str(project)[:120]}"
        pid = project["id"]
        record["projectId"] = pid
        headers = {"Authorization": f"Bearer {token}"}

        # Baseline seqs already on the project (none, fresh); we track the delta.
        before = set(project_seqs(pid))

        async with websockets.connect(f"{REPLICA_A}/{pid}", additional_headers=headers) as a, \
                websockets.connect(f"{REPLICA_B}/{pid}", additional_headers=headers) as b:
            # Small settle so the initial replay of (empty) history is drained.
            await asyncio.sleep(0.3)
            seen_a, seen_b = [], []
            insert(pid, N, "cross-replica")
            want = len([s for s in project_seqs(pid) if s not in before])
            record["inserted"] = want
            ra, rb = await asyncio.gather(
                collect(a, want, seen_a), collect(b, want, seen_b))

        expected = sorted(s for s in project_seqs(pid) if s not in before)
        checks["durableCountMatchesInsert"] = want == N
        checks["replicaA_deliveredAll"] = sorted(ra) == expected
        checks["replicaB_deliveredAll"] = sorted(rb) == expected
        # A replica that only saw its OWN inserts could not list B's; here NEITHER
        # process inserted, so any delivery is by definition cross-process NOTIFY.
        checks["replicaB_sawEventsItDidNotInsert"] = len(rb) > 0
        checks["bothReplicasAgree"] = sorted(ra) == sorted(rb)
        record["replicaA_seqs"] = sorted(ra)[:5] + ["..."] + sorted(ra)[-2:]
        record["replicaB_count"] = len(rb)
        record["expectedCount"] = len(expected)
        checks_all = all(checks.values())
        record["verdict"] = "PASS" if checks_all else "FAIL"
    except Exception as exc:  # noqa: BLE001 — keep failed evidence; no credential text
        record["error"] = str(exc)[:300]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"verdict": record["verdict"], "checks": checks, "evidence": str(out)}))
    raise SystemExit(0 if record["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    asyncio.run(main())
