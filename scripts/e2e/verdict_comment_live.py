#!/usr/bin/env python3
"""P-15 live check over real sockets, zero third-party contact.

Starts (1) a stand-in GitHub API and (2) a stand-in review service as local HTTP servers, and (3) the
REAL section2 GitHub service (uvicorn, scratch SQLite) whose outbound GitHub calls are rewritten to (1).
Then runs the REAL orchestrator CLI (`python -m harness_orchestrator.verdict_post`) against them:
dry run, create, update-in-place, bad token, unlinked run, writes-off. Nothing leaves this machine; no
real PR, token or model is involved.

    python3 scripts/e2e/verdict_comment_live.py [--out docs/briefs/evidence/90day/live/verdict-comment-live.json]

Exit 0 only if every check passes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "tok1"
GOAL = "fix it " + "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789" + " and password=hunter2 @everyone"
MARKER = "<!-- harness-verdict:v1 run=run-1 -->"

LAUNCHER = r'''
import asyncio, hashlib, sys
import httpx, uvicorn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from harness_contracts import KeyVault
from harness_github.app import make_app
from harness_github.config import Settings
from harness_github.models import Base

db_path, port, gh_port = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
EXTRA = [
 "CREATE TABLE agent_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, type TEXT, payload TEXT, ts TEXT)",
 "CREATE TABLE agent_tool_calls (id TEXT PRIMARY KEY, run_id TEXT, tool TEXT, target TEXT, outcome TEXT, summary TEXT, reported_by TEXT, created_at TEXT)",
 "CREATE TABLE cost_ledger (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, cost_usd REAL)",
 "CREATE TABLE agent_plans (id TEXT PRIMARY KEY, run_id TEXT, goal TEXT, estimated_cost_usd REAL, created_at TEXT)",
]

class Rewrite(httpx.AsyncBaseTransport):
    """Send every outbound GitHub call to the local stand-in instead of api.github.com."""
    def __init__(self):
        self.inner = httpx.AsyncHTTPTransport()
    async def handle_async_request(self, request):
        request.url = request.url.copy_with(scheme="http", host="127.0.0.1", port=gh_port)
        request.headers["Host"] = f"127.0.0.1:{gh_port}"
        return await self.inner.handle_async_request(request)

async def main():
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for ddl in EXTRA:
            await conn.execute(text(ddl))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(database_url=f"sqlite+aiosqlite:///{db_path}")
    enc = KeyVault(settings.master_key).encrypt("gho_fake_token_123")
    async with factory() as s:
        for stmt, p in [
            ("INSERT INTO users (id, email, name, is_active) VALUES ('u1','dev@x.com','D',1)", {}),
            ("INSERT INTO teams (id, name) VALUES ('t1','team')", {}),
            ("INSERT INTO team_members (id, team_id, user_id, display_name, member_type, role) VALUES ('m1','t1','u1','D','human','admin')", {}),
            ("INSERT INTO auth_sessions (token_hash, user_id, team_id, expires_at) VALUES (:h,'u1','t1','2100-01-01T00:00:00Z')", {"h": hashlib.sha256(b"tok1").hexdigest()}),
            ("INSERT INTO projects (id, team_id, name, repo_full_name, github_writes_enabled) VALUES ('p1','t1','demo','org/repo', 1)", {}),
            ("INSERT INTO github_connections (id, user_id, github_login, access_token_encrypted, scopes, created_at) VALUES ('gc1','u1','founder-dev',:t,'[]','2026-01-01T00:00:00Z')", {"t": enc}),
        ]:
            await s.execute(text(stmt), p)
        await s.commit()
    app = make_app(factory, settings)
    app.state.transport = Rewrite()
    await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")).serve()

asyncio.run(main())
'''


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeGitHub(BaseHTTPRequestHandler):
    comments: dict[int, dict] = {}
    requests: list[tuple[str, str, dict | None]] = []

    def log_message(self, *a):  # silence
        pass

    def _send(self, code: int, payload) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict | None:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else None

    def _handle(self, method: str) -> None:
        body = self._body()
        path = self.path.split("?")[0]
        FakeGitHub.requests.append((method, path, body))
        m = re.fullmatch(r"/repos/org/repo/issues/(\d+)/comments", path)
        if m and method == "POST":
            cid = len(FakeGitHub.comments) + 1
            FakeGitHub.comments[cid] = {"id": cid, "html_url": f"http://gh.local/c/{cid}",
                                        "body": (body or {}).get("body", ""), "pr": int(m.group(1))}
            return self._send(201, FakeGitHub.comments[cid])
        if m and method == "GET":
            return self._send(200, [c for c in FakeGitHub.comments.values() if c["pr"] == int(m.group(1))])
        m = re.fullmatch(r"/repos/org/repo/issues/comments/(\d+)", path)
        if m and method == "PATCH" and int(m.group(1)) in FakeGitHub.comments:
            FakeGitHub.comments[int(m.group(1))]["body"] = (body or {}).get("body", "")
            return self._send(200, FakeGitHub.comments[int(m.group(1))])
        self._send(404, {"message": "not found"})

    do_GET = lambda self: self._handle("GET")  # noqa: E731
    do_POST = lambda self: self._handle("POST")  # noqa: E731
    do_PATCH = lambda self: self._handle("PATCH")  # noqa: E731
    do_PUT = lambda self: self._handle("PUT")  # noqa: E731
    do_DELETE = lambda self: self._handle("DELETE")  # noqa: E731


class FakeReview(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        ok = self.headers.get("Authorization") == f"Bearer {TOKEN}" and self.path.endswith("/pr-1/risks")
        raw = json.dumps([{"path": "src/auth.py", "severity": "high", "rule": "auth",
                           "reason": "auth path"}] if ok else {"detail": "no"}).encode()
        self.send_response(200 if ok else 401)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def serve(handler) -> tuple[ThreadingHTTPServer, int]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def wait_http(url: str, seconds: float = 40) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:  # noqa: BLE001 - polling
            time.sleep(0.4)
    return False


def seed_orchestrator_rows(db: str) -> None:
    con = sqlite3.connect(db, timeout=15)
    con.execute("INSERT INTO agent_runs (id, project_id, branch, state, pr_id) VALUES ('run-1','p1','agent/run-1','done','pr-1')")
    con.execute("INSERT INTO pull_requests (id, project_id, run_id, github_pr_number, pr_url, summary_md, status) VALUES ('pr-1','p1','run-1',7,'http://gh.local/pr/7','','open')")
    con.execute("INSERT INTO agent_events (run_id, type, payload, ts) VALUES ('run-1','plan_approved', ?, '2026-11-13T00:00:00Z')",
                (json.dumps({"approvedBy": "user-9", "approvedAt": "2026-11-13T00:00:01Z"}),))
    con.execute("INSERT INTO agent_tool_calls VALUES ('c1','run-1','bash','pytest -q','ok','pytest -q (exit 0)','engine','2026-11-13')")
    con.execute("INSERT INTO cost_ledger (run_id, cost_usd) VALUES ('run-1', 0.0123)")
    con.execute("INSERT INTO agent_plans VALUES ('pl1','run-1', ?, 0.05, '2026-11-13')", (GOAL,))
    con.commit()
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs/briefs/evidence/90day/live/verdict-comment-live.json"))
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="verdict-live-"))
    db = str(tmp / "live.db")
    gh_srv, gh_port = serve(FakeGitHub)
    rv_srv, rv_port = serve(FakeReview)
    s2_port = free_port()
    (tmp / "launcher.py").write_text(LAUNCHER, encoding="utf-8")
    s2 = subprocess.Popen(
        [str(ROOT / "section2/.venv/bin/python"), str(tmp / "launcher.py"), db, str(s2_port), str(gh_port)],
        cwd=tmp, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    checks: dict[str, bool] = {}
    notes: dict[str, object] = {}
    try:
        up = wait_http(f"http://127.0.0.1:{s2_port}/openapi.json")
        checks["section2_up"] = up
        if not up:
            notes["section2_log"] = (s2.stdout.read() if s2.poll() is not None else "no response")[-2000:]
            raise SystemExit("section2 did not start: " + str(notes["section2_log"]))
        seed_orchestrator_rows(db)

        def cli(*extra: str, token: str = TOKEN, run: str = "run-1"):
            env = {**os.environ, "HARNESS_DATABASE_URL": f"sqlite+aiosqlite:///{db}",
                   "HARNESS_GITHUB_URL": f"http://127.0.0.1:{s2_port}",
                   "HARNESS_REVIEW_URL": f"http://127.0.0.1:{rv_port}", "HARNESS_API_TOKEN": token}
            return subprocess.run([str(ROOT / "section4/.venv/bin/python"), "-m",
                                   "harness_orchestrator.verdict_post", run, *extra],
                                  cwd=ROOT / "section4", env=env, capture_output=True, text=True, timeout=120)

        posts = lambda: [r for r in FakeGitHub.requests if r[0] in ("POST", "PATCH")]  # noqa: E731

        dry = cli("--dry-run")
        checks["dry_run_ok_and_posts_nothing"] = dry.returncode == 0 and not posts()
        body = json.loads(dry.stdout).get("body", "") if dry.returncode == 0 else ""
        checks["dry_run_has_facts"] = all(x in body for x in ("user-9", "0.012300", "src/auth.py", "pytest -q"))

        first = cli()
        r1 = json.loads(first.stdout) if first.returncode == 0 else {}
        checks["create_ok"] = first.returncode == 0 and r1.get("action") == "created" and len(FakeGitHub.comments) == 1
        posted = next(iter(FakeGitHub.comments.values()), {}).get("body", "")
        checks["posted_carries_marker_and_footer"] = MARKER in posted and "A human merges." in posted
        leaks = ["ghp_abcdef", "hunter2", "@everyone", TOKEN]
        checks["posted_has_no_secret_or_mention"] = not any(x in posted for x in leaks)
        notes["posted_leak_probe"] = {x: (x in posted) for x in leaks}

        second = cli()
        r2 = json.loads(second.stdout) if second.returncode == 0 else {}
        checks["update_in_place_same_comment"] = (second.returncode == 0 and r2.get("action") == "updated"
                                                  and r2.get("commentId") == r1.get("commentId")
                                                  and len(FakeGitHub.comments) == 1)

        bad = cli(token="wrong-token")
        checks["bad_token_refused"] = bad.returncode != 0 and len(FakeGitHub.comments) == 1
        unlinked = cli(run="ghost")
        checks["unknown_run_refused"] = unlinked.returncode != 0 and "ghost" in (unlinked.stderr + unlinked.stdout)

        con = sqlite3.connect(db, timeout=15)
        con.execute("UPDATE projects SET github_writes_enabled = 0 WHERE id = 'p1'")
        con.commit()
        before = len(posts())
        off = cli()
        checks["writes_off_refused_no_github_call"] = off.returncode != 0 and len(posts()) == before
        con.execute("UPDATE projects SET github_writes_enabled = 1 WHERE id = 'p1'")
        con.commit()
        con.close()

        methods = {m for m, _, _ in FakeGitHub.requests}
        checks["github_only_got_get_post_patch_no_merge"] = (
            methods <= {"GET", "POST", "PATCH"} and not any("/merge" in p for _, p, _ in FakeGitHub.requests))
        notes["github_requests"] = [(m, p) for m, p, _ in FakeGitHub.requests]
        notes["stderr_samples"] = {"bad_token": bad.stderr[-300:], "unknown_run": unlinked.stderr[-300:],
                                   "writes_off": off.stderr[-300:]}
    finally:
        s2.terminate()
        try:
            s2.wait(timeout=10)
        except subprocess.TimeoutExpired:
            s2.kill()
        gh_srv.shutdown()
        rv_srv.shutdown()

    ok = bool(checks) and all(checks.values())
    out = {"schema": 1, "kind": "p15-verdict-comment-live", "ok": ok, "checks": checks, "notes": notes,
           "scope": ("real section2 service + real orchestrator CLI over localhost sockets; stand-in GitHub "
                     "and review servers; no real PR, token, model or network"),
           "ranAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "sourceSha256": {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest()[:12] for f in (
               "section4/src/harness_orchestrator/verdict_post.py",
               "section4/src/harness_orchestrator/verdict_comment.py",
               "section2/src/harness_github/service.py")}}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": ok, "checks": checks}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
