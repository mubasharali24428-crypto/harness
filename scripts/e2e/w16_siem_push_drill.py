#!/usr/bin/env python3
"""W16 slice 4 — prove a customer's SIEM actually receives the trail, and only it.

The unit lanes pin the sink API, the delivery loop and the window verdict. This
drill answers the two questions a security buyer asks the deployed system:

    "Will my collector keep getting the evidence after a restart or an outage,
     and can I check that what arrived is what you sealed?"

Part A runs against the live stack: registration and its refusals over the wire,
the NDJSON export's shape, `POST /api/v1/audit/verify` returning a REAL verdict
from the deployment's own Postgres (the SQLite lane can only prove it refuses),
and the deployed `siem-push` worker claiming a sink whose destination is dead —
attempts climbing while the cursor refuses to move.

Part B runs on a throwaway database on the same Postgres with a REAL collector
sitting behind an HTTP socket: the batch is signed, the collector recomputes the
HMAC, checks the chain linkage against the previous batch, dedupes a replay by
`Idempotency-Key`, and the drill then walks backoff -> blocked -> resume and the
tamper / pruned-prefix verdicts on rows the database actually sealed.

    python3 scripts/e2e/w16_siem_push_drill.py     (or `make drill-siem`)

The egress law itself (HTTPS + operator allow-list + no private answers) is
pinned by unit tests and enforced live at registration; Part B injects only the
transport seam (`push_due(sender=...)`) so it can talk to a local collector.

Costs: no engine spend. Writes audit rows on the deployed database (append-only,
by design) and drops its own throwaway DB.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "briefs" / "evidence" / "pilot-prep"
IDENTITY = os.environ.get("E2E_IDENTITY_URL", "http://127.0.0.1:8001").rstrip("/")
SIEM_HOST = os.environ.get("E2E_SIEM_HOST", "siem.drill.invalid")
SIEM_URL = f"https://{SIEM_HOST}/ingest/harness"
SECRET = "drill-collector-shared-secret-0123456789"
PG_DSN = os.environ.get(
    "HARNESS_TEST_PG", "postgresql://harness:harness@127.0.0.1:5499/harness"
)
SYS_PATH = [str(ROOT / "section1" / "src"), str(ROOT / "contracts" / "src")]
for entry in reversed(SYS_PATH):
    if entry not in sys.path:
        sys.path.insert(0, entry)

spec = importlib.util.spec_from_file_location(
    "compose_journey", ROOT / "scripts" / "e2e" / "compose_journey.py"
)
cj = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(cj)

mig_spec = importlib.util.spec_from_file_location(
    "harness_migrate", ROOT / "deploy" / "runners" / "migrate.py"
)
migrate_mod = importlib.util.module_from_spec(mig_spec)
assert mig_spec and mig_spec.loader
mig_spec.loader.exec_module(migrate_mod)

checks: list[dict] = []


def record(name: str, got: object, want: object) -> None:
    ok = got == want
    checks.append({"check": name, "got": got, "want": want, "ok": ok})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {got}" + ("" if ok else f" (want {want})"),
          flush=True)
    if not ok:
        raise SystemExit(json.dumps({"verdict": "FAIL", "failed": name, "checks": checks}))


def call(method, url, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw, status, headers = resp.read(), resp.status, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw, status, headers = exc.read(), exc.code, dict(exc.headers)
    try:
        return status, json.loads(raw or b"{}"), headers
    except ValueError:
        # Not JSON (the NDJSON export): keep the whole body — a truncated line is
        # exactly the defect this drill is looking for.
        return status, {"raw": raw.decode(errors="replace")}, headers


def psql(sql: str) -> str:
    proc = subprocess.run(
        ["docker", "exec", "harness-db-1", "psql", "-U", "harness", "-d", "harness",
         "-tAc", sql],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


# --------------------------------------------------------------------- Part A


def part_a() -> None:
    stamp = int(time.time())
    admin_token, admin = cj.authenticate("siem admin", f"w16-siem-admin-{stamp}@example.com")
    team_id = admin["teamId"]
    st, _, _ = call("PUT", f"{IDENTITY}/api/v1/billing/plan", admin_token, {"plan": "team"})
    record("team tier raised (an egress path is not a starter feature)", st, 200)

    member_email = f"w16-siem-eng-{stamp}@example.com"
    st, invite, _ = call(
        "POST", f"{IDENTITY}/api/v1/teams/{team_id}/invites", admin_token,
        {"email": member_email, "role": "engineer"},
    )
    record("engineer invited", st, 201)
    eng_token, eng = cj.authenticate("siem engineer", member_email)
    st, _, _ = call(
        "POST", f"{IDENTITY}/api/v1/invites/redeem", eng_token,
        {"token": invite["token"], "email": member_email},
    )
    record("engineer joins the team", st, 200)
    # The invite adds the seat; the session the engineer already holds is bound
    # to the team their own first login created. A team-bound login is what an
    # engineer of THIS team actually carries.
    st, session, _ = call(
        "POST", f"{IDENTITY}/api/v1/auth/login",
        body={"email": member_email, "teamId": team_id},
    )
    record("a session bound to this team is minted", st, 200)
    eng_token = session.get("token", "")

    other_token, other = cj.authenticate("siem stranger", f"w16-siem-stranger-{stamp}@example.com")
    sink = {"label": "drill splunk", "collectorUrl": SIEM_URL, "sharedSecret": SECRET}
    base = f"{IDENTITY}/api/v1/teams/{team_id}/siem/sinks"

    st, _, _ = call("POST", base, eng_token, sink)
    record("an engineer cannot register a collector", st, 403)
    st, _, _ = call("GET", base, eng_token)
    record("nor list them", st, 403)

    for name, url in (
        ("plaintext refused", "http://siem.drill.invalid/ingest"),
        ("not allow-listed refused", "https://evil.example.com/ingest"),
        ("loopback refused", "https://127.0.0.1/ingest"),
        ("query string refused", f"{SIEM_URL}?token=1"),
    ):
        st, body, _ = call("POST", base, admin_token, {**sink, "collectorUrl": url})
        record(name, (st, body.get("error", {}).get("code")), (422, "INVALID_SIEM_URL"))

    st, created, _ = call("POST", base, admin_token, sink)
    record("admin registers the collector", st, 201)
    record("the shared secret never comes back",
           "sharedSecret" in json.dumps(created) or "secretEncrypted" in json.dumps(created), False)
    record("only a hint does", created.get("secretHint", "").endswith(SECRET[-4:]), True)
    record("stored as ciphertext, not the value",
           psql(f"SELECT secret_encrypted = '{SECRET}' FROM siem_sinks WHERE id = '{created['id']}';"),
           "f")
    sink_id = created["id"]

    st, body, _ = call("GET", f"{base}/{sink_id}", other_token)
    unknown = uuid.uuid4()
    st_unknown, body_unknown, _ = call("GET", f"{base}/{unknown}", other_token)
    record("a foreign team's sink answers like an unknown one",
           (st, body), (st_unknown, body_unknown))

    # ------------------------------------------------------------- export shape
    st, raw, headers = call("GET", f"{IDENTITY}/api/v1/audit/export?format=ndjson&limit=25",
                            admin_token)
    record("ndjson export answers", st, 200)
    record("with the machine media type",
           headers.get("content-type", "").split(";")[0], "application/x-ndjson")
    lines = [line for line in raw.get("raw", "").splitlines() if line.strip()]
    record("one JSON object per line", len(lines) > 0, True)
    if lines:
        first = json.loads(lines[0])
        record("each line is self-contained with its chain fields",
               sorted(k for k in ("seq", "action", "chainPos", "rowHash") if k in first),
               ["action", "chainPos", "rowHash", "seq"])
    record("a cursor header comes with it", "x-harness-next-cursor" in
           {k.lower() for k in headers}, True)

    # ------------------------------------------------------------------- verify
    st, verdict, _ = call("POST", f"{IDENTITY}/api/v1/audit/verify", admin_token, {})
    record("the window verdict answers on the deployment's Postgres", st, 200)
    record("and the chain verifies", verdict.get("ok"), True)
    record("row by row, with hashes", bool(verdict.get("rows")) and
           all(row.get("rowHash") and row.get("chainPos") for row in verdict.get("rows", [])), True)
    record("the head is reported", (verdict.get("head") or {}).get("chainPos", 0) > 0, True)
    st, _, _ = call("POST", f"{IDENTITY}/api/v1/audit/verify", eng_token, {})
    record("an engineer cannot ask for the verdict", st, 403)
    record("and asking is itself in the trail",
           int(psql("SELECT count(*) FROM audit_log WHERE action = 'audit.verified';")) >= 1, True)

    # --------------------------------------------------- the deployed worker
    # The collector host is deliberately unreachable. The worker must claim the
    # sink and fail, and the cursor must stay put: a failed delivery that moved
    # the cursor would lose evidence silently.
    deadline = time.time() + 75
    attempts = 0
    while time.time() < deadline:
        st, listed, _ = call("GET", f"{base}/{sink_id}", admin_token)
        attempts = listed.get("attempts", 0)
        if attempts >= 1:
            break
        time.sleep(3)
    record("the deployed siem-push worker attempted delivery", attempts >= 1, True)
    record("a dead collector does not advance the cursor",
           listed.get("cursor"), created.get("cursor"))
    record("the lag stays visible", listed.get("pendingRows", 0) > 0, True)
    record("the failure is named", listed.get("lastError") not in (None, ""), True)
    record("nothing has been delivered", listed.get("deliveredRows"), 0)

    st, _, _ = call("DELETE", f"{base}/{sink_id}", admin_token)
    record("the collector is removed", st, 204)
    st, listed, _ = call("GET", base, admin_token)
    record("and the team is left with none", listed.get("items"), [])


# --------------------------------------------------------------------- Part B


class Collector:
    """A real HTTP endpoint: verifies the HMAC, keeps the batches, answers what
    the drill tells it to, and dedupes replays by Idempotency-Key like a SIEM."""

    def __init__(self):
        self.batches: list[dict] = []
        self.seen_keys: set[str] = set()
        self.status = 204
        self.serve = _server(self)

    def batches_for(self, sink_id: str) -> list[dict]:
        return [b for b in self.batches if b["sink"] == sink_id]

    def stop(self):
        self.serve.shutdown()


def _server(collector: "Collector"):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server contract
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            sig = self.headers.get("X-Harness-Signature", "")
            ts = self.headers.get("X-Harness-Timestamp", "")
            expected = hmac.new(
                SECRET.encode(), ts.encode() + b"." + body, hashlib.sha256
            ).hexdigest()
            signature_ok = sig == f"t={ts},v1={expected}"
            rows = [json.loads(line) for line in body.decode().splitlines() if line.strip()]
            key = self.headers.get("Idempotency-Key", "")
            deduped = key in collector.seen_keys
            collector.seen_keys.add(key)
            if not deduped:
                collector.batches.append(
                    {"sink": key.split(":", 1)[0], "rows": rows, "key": key,
                     "signature_ok": signature_ok, "status": collector.status}
                )
            self.send_response(collector.status)
            self.end_headers()

        def log_message(self, *args):  # silence the drill's own server
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def part_b(collector: Collector) -> None:
    import asyncpg
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from harness_identity.audit import write_audit
    from harness_identity.audit_chain import verify_chain, verify_window
    from harness_identity.crypto import KeyVault
    from harness_identity.models import Team, TeamMember, User
    from harness_identity.siem_sink import (
        MAX_ATTEMPTS,
        RETRY_BASE_SECONDS,
        STATE_BLOCKED,
        SiemSink,
        push_due,
    )
    from sqlalchemy import select, text

    base, dbname = PG_DSN.rsplit("/", 1)
    name = f"harness_siem_drill_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(f"{base}/harness")
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    url = f"{base}/{name}"
    engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    vault = KeyVault(b"drill-master-key-0123456789abcdef")
    try:
        async with engine.begin() as conn:
            for path in sorted((ROOT / "section1" / "migrations").glob("*.sql")):
                for stmt in migrate_mod.split_statements(path.read_text(encoding="utf-8")):
                    await conn.execute(text(stmt))

        async with factory() as s:
            s.add(User(id="u1", email="a@drill.invalid", name="a"))
            await s.commit()
            s.add(Team(id="t1", name="Drill"))
            await s.commit()
            s.add(TeamMember(id="m1", team_id="t1", user_id="u1", display_name="a", role="admin"))
            await s.commit()
            for i in range(6):
                await write_audit(
                    s, "member.removed", actor_member_id="m1", target_type="agent_run",
                    target_id=f"r{i}", detail={"teamId": "t1", "n": i},
                )
            await s.commit()
            s.add(
                SiemSink(
                    id="sink-b", team_id="t1", label="real collector",
                    collector_url="https://collector.drill.invalid/ingest",
                    secret_encrypted=vault.encrypt(SECRET),
                    secret_hint="…" + SECRET[-4:], cursor_seq=0,
                    next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
                    created_by="u1", created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            await s.commit()

        async def send(sink_url, body, headers):
            port = collector.serve.server_address[1]
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/ingest", data=body, method="POST"
            )
            for key, value in headers.items():
                req.add_header(key, value)
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status
            except urllib.error.HTTPError as exc:
                return exc.code
            except Exception:  # noqa: BLE001 - transport failure is a retry
                return 0

        at = datetime.now(UTC)
        totals = await push_due(factory, vault=vault, now=at, sender=send, batch_rows=4)
        record("a signed batch reaches the collector", totals.get("pushed"), 1)
        batch = collector.batches_for("sink-b")[0]
        record("the collector recomputes the HMAC", batch["signature_ok"], True)
        record("the batch carries the team's sealed rows",
               [row["seq"] for row in batch["rows"]], [1, 2, 3, 4])
        record("rows carry chain hashes the collector can keep",
               all(row.get("rowHash") and row.get("prevHash") is not None
                   or row.get("chainPos") == 1 for row in batch["rows"]), True)

        async def sink_row():
            async with factory() as s:
                return (await s.execute(select(SiemSink).where(SiemSink.id == "sink-b"))).scalar_one()

        row = await sink_row()
        record("the cursor moved to the last row handed over", row.cursor_seq, 4)
        record("and more rows are waiting, so the next tick is immediate",
               row.next_attempt_at.replace(tzinfo=UTC) <= at.replace(tzinfo=UTC) + timedelta(seconds=1),
               True)

        await push_due(factory, vault=vault, now=at, sender=send, batch_rows=4)
        second = collector.batches_for("sink-b")[1]
        record("the second batch continues where the first ended",
               [r["seq"] for r in second["rows"]], [5, 6])
        record("and links to it by prevHash",
               second["rows"][0]["prevHash"] == batch["rows"][-1]["rowHash"], True)

        # Restart with the cursor intact: nothing is re-sent.
        before = len(collector.batches_for("sink-b"))
        await push_due(factory, vault=vault, now=datetime.now(UTC) + timedelta(minutes=5), sender=send)
        record("a restart with nothing new posts nothing", len(collector.batches_for("sink-b")), before)

        # Crash between send and commit: the same batch replays under the same key.
        async with factory() as s:
            r = (await s.execute(select(SiemSink).where(SiemSink.id == "sink-b"))).scalar_one()
            r.cursor_seq = 4
            r.next_attempt_at = datetime.now(UTC)
            await s.commit()
        replay_at = datetime.now(UTC) + timedelta(minutes=6)
        await push_due(factory, vault=vault, now=replay_at, sender=send)
        record("a lost cursor update replays the same batch, not a new one",
               len(collector.batches_for("sink-b")), before)

        # A refused batch: cursor still, attempts one, backoff scheduled.
        collector.status = 500
        async with factory() as s:
            r = (await s.execute(select(SiemSink).where(SiemSink.id == "sink-b"))).scalar_one()
            for i in range(6):
                await write_audit(
                    s, "member.removed", actor_member_id="m1", target_type="agent_run",
                    target_id=f"late{i}", detail={"teamId": "t1", "late": i},
                )
            await s.commit()
        fail_at = datetime.now(UTC) + timedelta(minutes=10)
        totals = await push_due(factory, vault=vault, now=fail_at, sender=send)
        row = await sink_row()
        record("a refused batch is a retry", totals.get("retrying"), 1)
        record("the cursor did not move", row.cursor_seq, 6)
        record("attempts counted", row.attempts, 1)
        record("and the retry is scheduled one backoff step out",
               abs((row.next_attempt_at.replace(tzinfo=UTC) - fail_at).total_seconds()
                   - RETRY_BASE_SECONDS) < 1, True)

        # Walk it to blocked.
        for n in range(2, MAX_ATTEMPTS + 1):
            await push_due(
                factory, vault=vault,
                now=fail_at + timedelta(seconds=RETRY_BASE_SECONDS * (2 ** (n - 1))),
                sender=send,
            )
        row = await sink_row()
        record("a dead collector ends up blocked", row.state, STATE_BLOCKED)
        async with factory() as s:
            blocked_rows = (await s.execute(
                text("SELECT count(*) FROM audit_log WHERE action = 'siem.blocked'")
            )).scalar_one()
        record("and the trail says so", int(blocked_rows), 1)
        sent = len(collector.batches_for("sink-b"))
        await push_due(factory, vault=vault, now=datetime.now(UTC) + timedelta(days=1), sender=send)
        record("a blocked sink stays quiet", len(collector.batches_for("sink-b")), sent)

        # Resume: the backlog goes out.
        collector.status = 204
        async with factory() as s:
            r = (await s.execute(select(SiemSink).where(SiemSink.id == "sink-b"))).scalar_one()
            r.state = "active"
            r.attempts = 0
            r.next_attempt_at = datetime.now(UTC)
            await s.commit()
        await push_due(factory, vault=vault, now=datetime.now(UTC) + timedelta(days=1, seconds=5),
                       sender=send)
        resumed = collector.batches_for("sink-b")[-1]
        record("resuming delivers the backlog, in one batch",
               len(resumed["rows"]) > 0 and resumed["signature_ok"], True)
        row = await sink_row()
        async with factory() as s:
            last_seq = (await s.execute(text("SELECT max(seq) FROM audit_log"))).scalar_one()
        record("the last sealed row was handed over", row.cursor_seq, int(last_seq))

        # ------------------------------------------------------- the verdicts
        async with engine.connect() as conn:
            verdict = await verify_window(conn)
        record("the window verdict is clean", verdict["ok"], True)
        record("and covers the sealed rows", verdict["rowsChecked"], int(last_seq))
        record("with the head reached", verdict["windowComplete"], True)

        async with factory() as s:
            original = (await s.execute(
                text("SELECT detail FROM audit_log WHERE seq = 3")
            )).scalar_one()
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE audit_log DISABLE TRIGGER ALL"))
            await conn.execute(text(
                "UPDATE audit_log SET detail = '{\"teamId\": \"t1\", \"n\": 4242}'::jsonb"
                " WHERE seq = 3"
            ))
        async with engine.connect() as conn:
            tampered = await verify_window(conn)
        record("a tampered row makes the verdict false", tampered["ok"], False)
        record("and names exactly the row",
               [r["seq"] for r in tampered["rows"] if not r["ok"]], [3])

        # Put the row back exactly as the trigger sealed it, so the next verdict
        # is about retention alone and not about the tamper above.
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE audit_log SET detail = CAST(:d AS jsonb) WHERE seq = 3"),
                {"d": json.dumps(original) if isinstance(original, dict) else original},
            )
            await conn.execute(text("DELETE FROM audit_log WHERE seq = 1"))
            await conn.execute(text("ALTER TABLE audit_log ENABLE TRIGGER ALL"))
        async with engine.connect() as conn:
            pruned = await verify_window(conn)
        record("a pruned head is declared, not hidden", pruned["prunedPrefix"], True)
        record("the surviving window still links", pruned["ok"] and pruned["anchored"], True)
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(f"{base}/harness")
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()


def main() -> int:
    t0 = time.time()
    collector = Collector()
    try:
        print(f"W16 slice 4 SIEM drill against {IDENTITY} and {PG_DSN}", flush=True)
        print("\n-- Part A: the live stack ------------------------------------", flush=True)
        part_a()
        print("\n-- Part B: real collector, real chain (throwaway DB) ---------", flush=True)
        asyncio.run(part_b(collector))
    finally:
        collector.stop()
    failed = [c for c in checks if not c["ok"]]
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE / f"w16_siem_push_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(
        {"verdict": "PASS" if not failed else "FAIL", "checks": checks,
         "passed": len(checks) - len(failed), "failed": len(failed),
         "seconds": round(time.time() - t0, 1)}, indent=2) + "\n")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed in "
          f"{time.time() - t0:.1f}s -> {out.relative_to(ROOT)}", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
