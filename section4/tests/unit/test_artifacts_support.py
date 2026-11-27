"""Shared fixture for the part 48 tests (lane AR): the REAL migration 0049 on sqlite, the run
ownership tables, minimal stand-ins for tables other lanes own (plan_versions, task_specs,
run_completion, pull_requests), the artifacts and orchestrator routers, a fake clock, a blob
store under tmp_path and a stub for the service's done-signal diff."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness_contracts.roles import Principal, Role
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_orchestrator import api as orch_api
from harness_orchestrator import artifacts, collab, deployments, shares
from harness_orchestrator.api import get_principal
from harness_orchestrator.api import router as orch_router
from harness_orchestrator.service import OrchestratorService

MIGRATIONS = Path(__file__).resolve().parents[3] / "section1" / "migrations"
FILES = ("0049_artifacts.sql", "0063_artifact_shares.sql", "0064_deployments.sql")
P = "/api/v1/orchestrator"
T0 = datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC)

DDL = (
    "CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
    " name VARCHAR(200) NOT NULL DEFAULT 'x', workspace_id VARCHAR(36), repo_full_name"
    " VARCHAR(400))",
    "CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36),"
    " state VARCHAR(40), branch VARCHAR(300))",
    "CREATE TABLE team_members (id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
    " user_id VARCHAR(36), display_name VARCHAR(200) NOT NULL DEFAULT 'x',"
    " member_type VARCHAR(16) NOT NULL DEFAULT 'human',"
    " role VARCHAR(16) NOT NULL DEFAULT 'engineer')",
    "CREATE TABLE audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " actor_member_id VARCHAR(36), actor_kind VARCHAR(16) NOT NULL,"
    " action VARCHAR(80) NOT NULL, target_type VARCHAR(60) NOT NULL,"
    " target_id VARCHAR(80) NOT NULL, detail TEXT NOT NULL DEFAULT '{}',"
    " ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE run_participants (run_id VARCHAR(36) NOT NULL, team_id VARCHAR(36) NOT NULL,"
    " user_id VARCHAR(36) NOT NULL, role VARCHAR(16) NOT NULL DEFAULT 'participant',"
    " joined_at TIMESTAMP, PRIMARY KEY (run_id, user_id))",
    "CREATE TABLE pull_requests (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36),"
    " run_id VARCHAR(36), github_pr_number INTEGER, pr_url VARCHAR(500), status VARCHAR(30),"
    " created_at TIMESTAMP)",
)
# Lane PL / EX tables, as little as lineage reads. Dropped by tests that want them absent.
OTHER_LANES = (
    "CREATE TABLE plan_versions (id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL,"
    " team_id VARCHAR(36) NOT NULL, version INTEGER NOT NULL, state VARCHAR(12) NOT NULL,"
    " content_sha256 VARCHAR(64) NOT NULL DEFAULT 'x')",
    "CREATE TABLE task_specs (run_id VARCHAR(36) PRIMARY KEY, team_id VARCHAR(36) NOT NULL,"
    " statement TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, updated_at TIMESTAMP)",
    "CREATE TABLE run_completion (run_id VARCHAR(36) PRIMARY KEY, agent_state VARCHAR(16),"
    " human_state VARCHAR(16) NOT NULL DEFAULT 'none')",
)

USERS = {  # user -> (team, role, member_type)
    "lead": ("team-a", Role.ENGINEER, "human"),
    "eng2": ("team-a", Role.ENGINEER, "human"),
    "view": ("team-a", Role.VIEWER, "human"),
    "guest": ("team-a", Role.GUEST, "human"),
    "boss": ("team-a", Role.ADMIN, "human"),
    "bot": ("team-a", Role.ENGINEER, "agent"),
    "evil": ("team-b", Role.ADMIN, "human"),
    "evil2": ("team-b", Role.ENGINEER, "human"),
}
SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PDF = b"%PDF-1.7\n" + b"x" * 32
ELF = b"\x7fELF" + b"\x02\x01\x01" + b"\x00" * 32


def migration_statements() -> list[str]:
    out = []
    for name in FILES:
        body = "\n".join(
            ln
            for ln in (MIGRATIONS / name).read_text().splitlines()
            if not ln.lstrip().startswith("--")
        )
        out += [s.strip() for s in body.split(";") if s.strip()]
    return out


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_diff(*files: tuple[str, list[str]]) -> str:
    """A small git-style diff: one hunk replacing line 1 of each (path, new lines)."""
    out = []
    for path, new in files:
        out += [
            f"diff --git a/{path} b/{path}",
            "index 111..222 100644",
            f"--- a/{path}",
            f"+++ b/{path}",
            f"@@ -1,1 +1,{len(new)} @@",
            "-old",
            *[f"+{ln}" for ln in new],
        ]
    return "\n".join(out) + "\n"


class Env:
    def __init__(self, client, who, factory, clock, service, diffs, blobs):
        self.client, self.who, self.factory, self.clock = client, who, factory, clock
        self.service, self.diffs, self.blobs = service, diffs, blobs

    def as_(self, user):
        self.who["u"] = user
        return self.client

    def tick(self, **kw):
        self.clock["t"] = self.clock["t"] + timedelta(**kw)

    def q(self, sql, **kw):
        async def go():
            async with self.factory() as db:
                return (await db.execute(text(sql), kw)).all()

        return asyncio.run(go())

    def x(self, sql, **kw):
        async def go():
            async with self.factory() as db:
                await db.execute(text(sql), kw)
                await db.commit()

        asyncio.run(go())

    def principal(self, user):
        team, role, _ = USERS[user]
        return Principal(userId=user, teamId=team, role=role)

    def audit(self, action=None):
        rows = self.q("SELECT action, target_type, target_id, detail FROM audit_log ORDER BY seq")
        return rows if action is None else [r for r in rows if r.action == action]

    def url(self, run, tail=""):
        return f"{P}/runs/{run}{tail}"

    def upload(self, user, run, name, data, ctype="application/octet-stream", **params):
        return self.as_(user).post(
            self.url(run, "/artifacts"),
            params={"name": name, **params},
            content=data,
            headers={"Content-Type": ctype},
        )

    def put(self, user, run, name, data, **params):
        r = self.upload(user, run, name, data, **params)
        assert r.status_code in (200, 201), r.text
        return r.json()["artifact"]

    def blob_files(self):
        return [p for p in self.blobs.rglob("*") if p.is_file()]

    def participant(self, run, user):
        self.x(
            "INSERT INTO run_participants (run_id, team_id, user_id, role, joined_at)"
            " VALUES (:r, :t, :u, 'participant', :ts)",
            r=run,
            t=USERS[user][0],
            u=user,
            ts=self.clock["t"],
        )

    def plan(self, run, version, state):
        self.x(
            "INSERT INTO plan_versions (id, run_id, team_id, version, state)"
            " VALUES (:i, :r, 'team-a', :v, :s)",
            i=f"pv-{run}-{version}",
            r=run,
            v=version,
            s=state,
        )

    # -- version-control helpers
    def set_diff(self, run, diff):
        self.diffs[run] = diff

    def changes(self, user, run):
        r = self.as_(user).get(self.url(run, "/changes"))
        assert r.status_code == 200, r.text
        return r.json()

    def review_all(self, user, run, decision="approved"):
        data = self.changes(user, run)
        for c in data["items"]:
            r = self.as_(user).post(
                self.url(run, f"/changes/{c['id']}/review"),
                json={"decision": decision, "diffSha": c["diffSha"]},
            )
            assert r.status_code == 200, r.text
        return self.changes(user, run)

    def git(self, user, run, **body):
        r = self.as_(user).put(self.url(run, "/git-state"), json=body)
        assert r.status_code == 200, r.text
        return r.json()

    def ready(self, run="r1", user="lead"):
        """Diff recorded, every change approved, base fresh, head recorded: merge-approvable."""
        self.set_diff(run, git_diff(("src/a.py", ["print(1)"]), ("src/b.py", ["print(2)"])))
        self.git(
            user,
            run,
            baseBranch="main",
            baseSha=SHA_A,
            observedHeadSha=SHA_A,
            headBranch=f"agent/{run}",
            runHeadSha=SHA_C,
        )
        return self.review_all(user, run)

    def merge(self, user, run, data, **extra):
        body = {"changesDigest": data["changesDigest"], "headSha": SHA_C, **extra}
        return self.as_(user).post(self.url(run, "/merge-approval"), json=body)


def err(resp, status, code):
    assert resp.status_code == status, resp.text
    assert resp.json()["detail"]["error"]["code"] == code, resp.text
    return resp.json()["detail"]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_AUTHZ_TEST_MODE", raising=False)
    monkeypatch.delenv("HARNESS_PILOT_TEAMS", raising=False)
    for k in (
        "HARNESS_ARTIFACT_MAX_BYTES",
        "HARNESS_ARTIFACT_RUN_MAX_COUNT",
        "HARNESS_ARTIFACT_RUN_MAX_BYTES",
        "HARNESS_ARTIFACT_RETENTION_DAYS",
    ):
        monkeypatch.delenv(k, raising=False)
    blobs = tmp_path / "blobs"
    monkeypatch.setenv("HARNESS_ARTIFACT_ROOT", str(blobs))
    clock = {"t": T0}
    monkeypatch.setattr(collab, "db_now", lambda: clock["t"])
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/a.db")

    async def setup():
        async with engine.begin() as conn:
            for d in (*DDL, *OTHER_LANES, *migration_statements()):
                await conn.execute(text(d))
            await conn.execute(
                text(
                    "INSERT INTO projects (id, team_id, repo_full_name) VALUES"
                    " ('p', 'team-a', 'acme/app'), ('q', 'team-b', 'acme/other')"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO agent_runs (id, project_id, branch) VALUES"
                    " ('r1', 'p', 'agent/r1'), ('r2', 'p', 'agent/r2'), ('r3', 'q', 'agent/r3')"
                )
            )
            for u, (t, role, kind) in USERS.items():
                await conn.execute(
                    text(
                        "INSERT INTO team_members (id, team_id, user_id, display_name, role,"
                        " member_type) VALUES (:i, :t, :u, :n, :r, :k)"
                    ),
                    {"i": f"m-{u}", "t": t, "u": u, "n": f"Name {u}", "r": role.value, "k": kind},
                )

    asyncio.run(setup())
    factory = async_sessionmaker(engine, expire_on_commit=False)

    class Bus:
        async def publish(self, *a, **k):
            return None

        async def ensure_run(self, *a, **k):
            return None

    service = OrchestratorService(session_factory=factory, bus=Bus())
    diffs: dict[str, str] = {}

    async def get_done_diff(run_id):
        if run_id not in diffs:
            raise KeyError(run_id)
        return diffs[run_id]

    service.get_done_diff = get_done_diff
    monkeypatch.setattr(orch_api, "_service", service)
    # configure_artifacts sets a module global; register it so teardown restores it
    # (left set, later tests silently used this test's DB: Q1 2026-11-21).
    monkeypatch.setattr(artifacts, "_service", None)
    artifacts.configure_artifacts(service)

    app = FastAPI()
    app.include_router(orch_router)
    app.include_router(artifacts.router)
    app.include_router(shares.router)
    app.include_router(deployments.router)
    who = {"u": "lead"}
    app.dependency_overrides[get_principal] = lambda: Principal(
        userId=who["u"], teamId=USERS[who["u"]][0], role=USERS[who["u"]][1]
    )
    yield Env(TestClient(app), who, factory, clock, service, diffs, blobs)
    asyncio.run(engine.dispose())
