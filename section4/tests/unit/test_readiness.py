"""W23: the readiness report names what would stop a run, and which component it is.

The worker cases run the real daemon handler in-process, so a token mismatch is
the same ``401`` the W20 outage produced; the stale-daemon case is a stand-in
server that authenticates but has no ``/version`` route, which is exactly what a
daemon image built before this change answers.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator import readiness, worker_daemon
from harness_orchestrator.api import get_principal, router
from harness_orchestrator.service import OrchestratorService

TOKEN = "daemon-token-for-tests"
KEY = "sk-realistic-0123456789abcdef"
LIVE_ENV = {
    "ENGINE_LIVE_MODE": "live",
    "DEEPSEEK_API_KEY": KEY,
    "DEEPSEEK_BASE_URL": "https://openrouter.ai/api/v1",
    "DSH_MODEL": "anthropic/claude-haiku-5.5",
    # OpenRouter needs the reviewed patch (W23 F2); a real file, so `exists` holds.
    "DSH_PATCH_FILES": str(
        Path(__file__).resolve().parents[3] / "deploy/patches/openrouter.patch.yml"),
}


def _by_id(entries):
    return {e["check"]: e for e in entries}


def _serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture()
def daemon(monkeypatch):
    monkeypatch.setenv("HARNESS_WORKER_DAEMON_TOKEN", TOKEN)
    server, url = _serve(worker_daemon.Handler)
    yield url
    server.shutdown()


def _stub(status, payload):
    """A daemon that accepts any bearer and answers /version with ``status``."""

    class Stub(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Stub


# --------------------------------------------------------------- provider/model


def test_a_dry_engine_fails_the_provider_check():
    checks = _by_id(readiness.check_engine({"ENGINE_LIVE_MODE": "dry"}))
    assert checks["provider"]["status"] == "fail"
    assert checks["provider"]["reason"] == "ENGINE_DRY"
    assert checks["model"]["status"] == "unknown"


def test_a_live_engine_passes_and_never_echoes_the_key():
    entries = readiness.check_engine(LIVE_ENV)
    checks = _by_id(entries)
    assert checks["provider"]["status"] == "ok"
    assert checks["model"]["status"] == "ok"
    assert "anthropic/claude-haiku-5.5" in checks["model"]["detail"]
    assert KEY not in json.dumps(entries)


def test_a_placeholder_key_names_the_variable_not_the_value():
    entries = readiness.check_engine({**LIVE_ENV, "DEEPSEEK_API_KEY": "changeme"})
    provider = _by_id(entries)["provider"]
    assert provider["status"] == "fail"
    assert "DEEPSEEK_API_KEY" in provider["detail"]
    assert "changeme" not in json.dumps(entries)


# ---------------------------------------------------------------- worker/revision


def test_no_daemon_means_in_process_workers():
    checks = _by_id(readiness.check_worker("", "", readiness.code_digest()))
    assert checks["worker"]["status"] == "ok"
    assert checks["revision"]["status"] == "ok"


def test_a_matching_daemon_passes_worker_and_revision(daemon):
    checks = _by_id(readiness.check_worker(daemon, TOKEN, readiness.code_digest()))
    assert checks["worker"]["status"] == "ok"
    assert checks["revision"]["status"] == "ok"


def test_a_token_mismatch_is_caught_and_names_the_daemon(daemon):
    """The W20 outage: /ready green, every dispatch answered 401."""
    checks = _by_id(readiness.check_worker(daemon, "a-stale-token", readiness.code_digest()))
    assert checks["worker"]["status"] == "fail"
    assert checks["worker"]["reason"] == "WORKER_UNAUTHORIZED"
    assert checks["worker"]["component"] == "worker-daemon"
    assert checks["revision"]["status"] == "unknown"
    assert "a-stale-token" not in json.dumps(checks)


def test_a_missing_token_fails_without_calling(daemon):
    checks = _by_id(readiness.check_worker(daemon, "", readiness.code_digest()))
    assert checks["worker"]["reason"] == "WORKER_TOKEN_UNSET"


def test_a_daemon_built_before_this_check_is_named_stale():
    server, url = _serve(_stub(404, {"error": "not found"}))
    try:
        checks = _by_id(readiness.check_worker(url, TOKEN, readiness.code_digest()))
    finally:
        server.shutdown()
    assert checks["worker"]["status"] == "ok"  # reachable and authorised
    assert checks["revision"]["status"] == "fail"
    assert checks["revision"]["reason"] == "WORKER_PREDATES_READINESS"
    assert checks["revision"]["component"] == "worker-daemon"


def test_a_daemon_on_other_code_is_named_stale():
    server, url = _serve(_stub(200, {"codeDigest": "0" * 64}))
    try:
        checks = _by_id(readiness.check_worker(url, TOKEN, readiness.code_digest()))
    finally:
        server.shutdown()
    assert checks["revision"]["status"] == "fail"
    assert checks["revision"]["reason"] == "WORKER_CODE_DRIFT"
    assert checks["revision"]["component"] == "worker-daemon"


def test_an_unreachable_daemon_fails_the_worker_check():
    server, url = _serve(_stub(200, {}))
    server.shutdown()
    server.server_close()
    checks = _by_id(readiness.check_worker(url, TOKEN, readiness.code_digest(), timeout=2))
    assert checks["worker"]["reason"] == "WORKER_UNREACHABLE"
    assert checks["revision"]["status"] == "unknown"


def test_the_digest_is_stable_and_ignores_bytecode():
    assert readiness.code_digest() == readiness.code_digest()
    assert len(readiness.code_digest()) == 64


# ------------------------------------------------------- repository/permissions


def test_repository_states():
    assert readiness.check_repository("acme/app", known=True)["status"] == "ok"
    missing = readiness.check_repository(None, known=True)
    assert missing["status"] == "fail"
    assert missing["reason"] == "PROJECT_REPO_NOT_CONNECTED"
    assert readiness.check_repository(None, known=False)["status"] == "unknown"


@pytest.mark.parametrize(
    ("role", "status"),
    [(Role.ADMIN, "ok"), (Role.ENGINEER, "ok"), (Role.VIEWER, "fail"), (Role.GUEST, "fail")],
)
def test_permissions_follow_the_frozen_role_table(role, status):
    principal = Principal(userId="u", teamId="t", role=role)
    assert readiness.check_permissions(principal)["status"] == status


def test_the_report_is_ok_only_when_every_check_is_ok():
    ok = {"check": "a", "status": "ok"}
    assert readiness.report([ok, ok])["ok"] is True
    assert readiness.report([ok, {"check": "b", "status": "unknown"}])["ok"] is False


# ------------------------------------------------------------------------ route


@pytest.fixture
def db_service(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/ready.db")

    async def _create():
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, "
                "team_id VARCHAR(36) NOT NULL, repo_full_name VARCHAR(400))"
            ))
            await conn.execute(text(
                "INSERT INTO projects VALUES ('p-ours', 'team-a', 'acme/app'),"
                " ('p-theirs', 'team-b', NULL)"
            ))

    import asyncio

    asyncio.run(_create())
    yield OrchestratorService(session_factory=async_sessionmaker(engine, expire_on_commit=False))
    asyncio.run(engine.dispose())


def _client(monkeypatch, service, team="team-a", role=Role.ENGINEER):
    for name, value in LIVE_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("HARNESS_WORKER_DAEMON_URL", raising=False)
    monkeypatch.setattr(orch_api, "_service", service)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: Principal(userId="u", teamId=team, role=role)
    return TestClient(app)


def test_the_route_reports_every_check_for_an_owned_project(monkeypatch, db_service):
    res = _client(monkeypatch, db_service).get(
        "/api/v1/orchestrator/readiness", params={"projectId": "p-ours"}
    )
    assert res.status_code == 200
    body = res.json()
    assert [c["check"] for c in body["checks"]] == [
        "provider", "model", "worker", "revision", "repository", "permissions"
    ]
    assert KEY not in res.text


def test_the_route_does_not_confirm_a_foreign_project(monkeypatch, db_service):
    client = _client(monkeypatch, db_service)
    foreign = client.get("/api/v1/orchestrator/readiness", params={"projectId": "p-theirs"})
    unknown = client.get("/api/v1/orchestrator/readiness", params={"projectId": "p-nope"})
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json() == unknown.json()


# ------------------------------------------------------------- W23 F2: request format


def test_an_openai_gateway_without_its_patch_fails_before_the_first_run():
    env = {k: v for k, v in LIVE_ENV.items() if k != "DSH_PATCH_FILES"}
    checks = _by_id(readiness.check_engine(env))
    assert checks["provider"]["status"] == "fail"
    assert checks["provider"]["reason"] == "PROVIDER_FORMAT_MISMATCH"
    assert "DSH_PATCH_FILES" in checks["provider"]["detail"]
    assert checks["model"]["status"] == "unknown"
    assert KEY not in json.dumps(checks)


def test_a_missing_patch_file_fails_like_the_engine_would():
    checks = _by_id(readiness.check_engine({**LIVE_ENV, "DSH_PATCH_FILES": "/nope.yml"}))
    assert checks["provider"]["reason"] == "PATCH_FILE_MISSING"


@pytest.mark.parametrize(
    "url", ["https://api.anthropic.com", "https://api.deepseek.com/anthropic"])
def test_an_anthropic_format_host_needs_no_patch(url):
    env = {k: v for k, v in LIVE_ENV.items() if k != "DSH_PATCH_FILES"}
    env["DEEPSEEK_BASE_URL"] = url
    checks = _by_id(readiness.check_engine(env))
    assert checks["provider"]["status"] == "ok"
    assert "Anthropic Messages API" in checks["provider"]["detail"]


def test_a_patched_engine_says_the_patch_sets_the_format():
    provider = _by_id(readiness.check_engine(LIVE_ENV))["provider"]
    assert "set by DSH_PATCH_FILES" in provider["detail"]
