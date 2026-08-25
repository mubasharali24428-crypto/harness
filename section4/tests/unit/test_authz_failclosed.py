"""M18-C1 (P-BETA H-3 / MAC2-F1) — authz fail-closed sweep tests.

Phase-A findings: authorize_run failed OPEN three separate ways and
plan_gate's team-scoping inherited the same posture. Post-fix every branch
that cannot PROVE ownership denies:

  - unwired session factory → 403 AUTHZ_UNAVAILABLE in production wiring;
    the legacy allow survives ONLY behind explicit HARNESS_AUTHZ_TEST_MODE=1
  - missing projectId on the run → deny + authz.denied audit row (no_project)
  - dangling projects row (project_id set, no row) → deny + authz.denied
    audit row (dangling_project)
  - plan-gate decision routes: bare-unit mode denies; DB-backed unattributed
    runs (no binding / dangling row / store probe failure) deny

No repo conftest existed for section4 before M18-C1; tests/conftest.py now
sets HARNESS_AUTHZ_TEST_MODE=1 for legacy suites. THIS module is the
production-posture suite: it clears the flag explicitly so every assertion
here runs under the real deployment posture.
"""

import asyncio
import json
import sys
from pathlib import Path

# Shared-time seam mirrors plan_gate.py: section1's src tree appended exactly
# like api.py's wave-1 audit seam (imports below carry noqa: E402).
_ROOT = Path(__file__).resolve().parents[2]
if (_ROOT / "section1" / "src") not in sys.path:
    sys.path.append(str(_ROOT / "section1" / "src"))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from harness_contracts.roles import Principal, Role  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from harness_orchestrator import api as orch_api  # noqa: E402
from harness_orchestrator.api import get_principal  # noqa: E402
from harness_orchestrator.api import router as orch_router  # noqa: E402
from harness_orchestrator.authz import (  # noqa: E402
    AUDIT_ACTION_AUTHZ_DENIED,
    AuthzUnavailableError,
    authorize_run,
)
from harness_orchestrator.plan_gate import (  # noqa: E402
    _plan_gate_principal,
    configure_plan_gate,
)
from harness_orchestrator.plan_gate import router as gate_router  # noqa: E402
from harness_orchestrator.service import OrchestratorService  # noqa: E402


def _principal(
    user_id: str = "u-fc", team_id: str = "team-a", role: Role = Role.ADMIN
) -> Principal:
    return Principal(userId=user_id, teamId=team_id, role=role)


@pytest.fixture(autouse=True)
def _production_posture(monkeypatch):
    """Run EVERY test in this module with the flag off: production posture.

    conftest.py sets HARNESS_AUTHZ_TEST_MODE=1 for the legacy suites; the
    fail-closed guarantees under test here must hold WITHOUT it.
    """
    monkeypatch.delenv("HARNESS_AUTHZ_TEST_MODE", raising=False)


class RecordingBus:
    """Duck-typed EventBus recording publishes (mirrors sibling fixtures)."""

    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(self, event_type, project_id, payload=None, run_id=None) -> None:
        name = event_type.value if hasattr(event_type, "value") else str(event_type)
        self.published.append(f"{run_id}:{name}:{json.dumps(payload or {}, default=str)}")

    async def ensure_run(self, project_id: str, run_id: str) -> None:
        return None


_DDL = (
    (
        "CREATE TABLE projects ("
        "id VARCHAR(36) PRIMARY KEY,"
        " team_id VARCHAR(36) NOT NULL,"
        " name VARCHAR(200) NOT NULL DEFAULT 'x')"
    ),
    "CREATE TABLE agent_runs (id VARCHAR(36) PRIMARY KEY,"
    " project_id VARCHAR(36), state VARCHAR(40))",
    (
        "CREATE TABLE audit_log ("
        "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
        "actor_member_id VARCHAR(36),"
        "actor_kind VARCHAR(16) NOT NULL,"
        "action VARCHAR(80) NOT NULL,"
        "target_type VARCHAR(60) NOT NULL,"
        "target_id VARCHAR(80) NOT NULL,"
        "detail TEXT NOT NULL DEFAULT '{}',"
        "ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    ),
)


@pytest.fixture()
def db_parts(tmp_path):
    """SQLite-backed service with projects/agent_runs/audit_log.

    Seeded rows: ``proj-a`` owned by ``team-a`` (the caller's team);
    ``proj-b`` owned by ``team-b`` (a foreign team). ``proj-dangle`` has NO
    row on purpose — that IS the gap under test.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/authz_fc.db")

    async def _create():
        async with engine.begin() as conn:
            for ddl in _DDL:
                await conn.execute(text(ddl))
            await conn.execute(
                text("INSERT INTO projects (id, team_id) VALUES ('proj-a', 'team-a')")
            )
            await conn.execute(
                text("INSERT INTO projects (id, team_id) VALUES ('proj-b', 'team-b')")
            )

    asyncio.run(_create())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = OrchestratorService(session_factory=factory, bus=RecordingBus())
    yield {"service": service, "factory": factory}
    asyncio.run(engine.dispose())


async def _audit_denials(factory, run_id: str | None = None):
    """All authz.denied rows for a run, parsed detail dicts attached."""

    async def _rows():
        async with factory() as db:
            sql = "SELECT action, target_id, detail FROM audit_log WHERE action = :a"
            params: dict = {"a": AUDIT_ACTION_AUTHZ_DENIED}
            if run_id is not None:
                sql += " AND target_id = :r"
                params["r"] = run_id
            return (await db.execute(text(sql), params)).all()

    rows = await _rows()
    out = []
    for action, target_id, detail in rows:
        payload = json.loads(detail) if isinstance(detail, str) else (detail or {})
        out.append({"action": action, "target_id": target_id, **payload})
    return out


# ===========================================================================
# Gap 0 — production wiring without a session factory DENIES
# ===========================================================================


async def test_unwired_factory_denies_in_production_posture():
    """MAC2-F1 branch 1: no session factory wired ⇒ DENY (AuthzUnavailable).

    The old guard returned the run unchecked ("bare unit mode"); nothing but
    an explicit HARNESS_AUTHZ_TEST_MODE=1 may restore that behavior.
    """
    service = OrchestratorService()  # exactly the forgotten-wiring shape
    with pytest.raises(AuthzUnavailableError) as excinfo:
        await authorize_run(service, "run-nofactory", _principal())
    assert excinfo.value.reason == "unwired_session_factory"


async def test_test_mode_flag_restores_legacy_bare_allow(monkeypatch):
    """The ONLY sanctioned escape hatch: explicit HARNESS_AUTHZ_TEST_MODE=1."""
    monkeypatch.setenv("HARNESS_AUTHZ_TEST_MODE", "1")
    service = OrchestratorService()
    run_data = await authorize_run(service, "run-flagged", _principal())
    assert run_data["runId"] == "run-flagged"


async def test_test_mode_flag_requires_exact_value(monkeypatch):
    """"true"/"yes"/anything-but-"1" keeps the fail-closed posture."""
    service = OrchestratorService()
    for value in ("true", "TRUE", "yes", "0", ""):
        monkeypatch.setenv("HARNESS_AUTHZ_TEST_MODE", value)
        with pytest.raises(AuthzUnavailableError):
            await authorize_run(service, "run-loose", _principal())


# ===========================================================================
# Gap 1 — missing projectId ⇒ DENY + audit row (reason=no_project)
# ===========================================================================


async def test_missing_project_denies_with_audit_row(db_parts):
    """A run bound to NOTHING attributable is owned by nobody ⇒ deny + audit.

    Shape: memory lost the binding AND agent_runs carries a NULL project_id
    (the frozen schema's nullable-binding gap). The guard consults
    agent_runs, finds the row but no usable attribution, and denies.
    """
    service: OrchestratorService = db_parts["service"]
    factory = db_parts["factory"]

    async def _seed_row():
        async with factory() as db:
            await db.execute(
                text(
                    "INSERT INTO agent_runs (id, project_id, state)"
                    " VALUES ('run-unbound', NULL, 'idle')"
                )
            )
            await db.commit()

    await _seed_row()
    with pytest.raises(AuthzUnavailableError) as excinfo:
        await authorize_run(service, "run-unbound", _principal(team_id="team-b"))
    assert excinfo.value.reason == "no_project"

    denials = await _audit_denials(factory, "run-unbound")
    assert len(denials) == 1
    denial = denials[0]
    assert denial["reason"] == "no_project"
    assert denial["projectId"] is None
    assert denial["teamId"] == "team-b"  # the denied principal
    assert denial["userId"] == "u-fc"


# ===========================================================================
# Gap 2 — dangling projects row ⇒ DENY + audit row (reason=dangling_project)
# ===========================================================================


async def test_dangling_project_row_denies_with_audit_row(db_parts):
    """project_id set, row gone (deleted/orphaned binding) ⇒ deny + audited.

    The old guard's ``if row is not None and ...`` allowed exactly this.
    """
    service: OrchestratorService = db_parts["service"]
    factory = db_parts["factory"]
    await service.propose_plan("run-dangle", project_id="proj-dangle")
    with pytest.raises(AuthzUnavailableError) as excinfo:
        await authorize_run(service, "run-dangle", _principal(team_id="team-a"))
    assert excinfo.value.reason == "dangling_project"

    denials = await _audit_denials(factory, "run-dangle")
    assert len(denials) == 1
    denial = denials[0]
    assert denial["reason"] == "dangling_project"
    assert denial["projectId"] == "proj-dangle"


async def test_unknown_run_id_stays_no_oracle_404(db_parts):
    """A genuinely unknown run id (no agent_runs row) denies via
    OwnershipDeniedError so the API seam keeps answering its historical
    no-oracle 404 — the sweep must not create a new oracle shape."""
    service: OrchestratorService = db_parts["service"]

    from harness_orchestrator.authz import OwnershipDeniedError

    with pytest.raises(OwnershipDeniedError):
        await authorize_run(service, "run-never-existed", _principal(team_id="team-a"))
    assert await _audit_denials(db_parts["factory"]) == []


# ===========================================================================
# Happy paths stay happy (the sweep must not break legit ownership)
# ===========================================================================


async def test_own_team_project_still_allowed_without_audit_noise(db_parts):
    service: OrchestratorService = db_parts["service"]
    factory = db_parts["factory"]
    await service.propose_plan("run-mine", project_id="proj-a")
    run_data = await authorize_run(service, "run-mine", _principal(team_id="team-a"))
    assert run_data["projectId"] == "proj-a"
    assert await _audit_denials(factory) == []


async def test_foreign_team_project_still_ownership_denied(db_parts):
    """The pre-existing cross-team deny keeps its dedicated exception."""
    service: OrchestratorService = db_parts["service"]

    from harness_orchestrator.authz import OwnershipDeniedError

    await service.propose_plan("run-theirs", project_id="proj-b")
    with pytest.raises(OwnershipDeniedError):
        await authorize_run(service, "run-theirs", _principal(team_id="team-a"))


# ===========================================================================
# HTTP seam — AUTHZ_UNAVAILABLE envelope on the api.py dependency chain
# ===========================================================================


def _orch_client(monkeypatch, service: OrchestratorService, principal: Principal):
    monkeypatch.setattr(orch_api, "_service", service)
    app = FastAPI()
    app.include_router(orch_router)
    app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app)


def test_route_unwired_service_answers_authz_unavailable(monkeypatch):
    """Production wiring forgot configure_orchestrator's factory ⇒ 403 with
    the AUTHZ_UNAVAILABLE envelope — never a silent allow."""
    service = OrchestratorService()  # module-level default: no factory
    client = _orch_client(monkeypatch, service, _principal(role=Role.ADMIN))
    res = client.get("/api/v1/orchestrator/runs/run-any/status")
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["error"]["code"] == "AUTHZ_UNAVAILABLE"


def test_route_missing_project_answers_authz_unavailable(db_parts, monkeypatch):
    """DB-backed run with an unattributable (NULL) binding ⇒ 403
    AUTHZ_UNAVAILABLE at the seam."""
    service: OrchestratorService = db_parts["service"]
    factory = db_parts["factory"]

    async def _seed_row():
        async with factory() as db:
            await db.execute(
                text(
                    "INSERT INTO agent_runs (id, project_id, state)"
                    " VALUES ('run-route-unbound', NULL, 'idle')"
                )
            )
            await db.commit()

    asyncio.run(_seed_row())
    client = _orch_client(monkeypatch, service, _principal(team_id="team-a"))
    res = client.post("/api/v1/orchestrator/runs/run-route-unbound/pause")
    assert res.status_code == 403, res.text
    body = res.json()["detail"]["error"]
    assert body["code"] == "AUTHZ_UNAVAILABLE"
    assert "no_project" in body["message"]


# ===========================================================================
# plan_gate posture pass — same explicit-deny pattern
# ===========================================================================


def _gate_client(service: OrchestratorService, principal: Principal) -> TestClient:
    configure_plan_gate(service)
    app = FastAPI()
    app.include_router(gate_router)

    async def _override() -> Principal:
        return principal

    app.dependency_overrides[_plan_gate_principal] = _override
    return TestClient(app)


def test_gate_bare_mode_decision_denies_without_flag():
    """Bare-unit propose→approve used to pass silently; now 403 unless the
    explicit test flag is set. (conftest set it for OTHER modules; the
    autouse fixture cleared it HERE.)"""
    service = OrchestratorService()
    owner = _gate_client(service, _principal(user_id="u-o", role=Role.ADMIN))
    proposed = owner.post(
        "/api/v1/orchestrator/plans", json={"goal": "g"}
    ).json()["planId"]
    res = owner.post(f"/api/v1/orchestrator/plans/{proposed}/approve")
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["error"]["code"] == "AUTHZ_UNAVAILABLE"


def test_gate_bare_mode_allows_only_behind_explicit_flag(monkeypatch):
    """With HARNESS_AUTHZ_TEST_MODE=1 explicitly set, the legacy bare walk
    (propose → approve → reject) still works end to end."""
    monkeypatch.setenv("HARNESS_AUTHZ_TEST_MODE", "1")
    service = OrchestratorService()
    owner = _gate_client(service, _principal(user_id="u-o", role=Role.ADMIN))
    first = owner.post("/api/v1/orchestrator/plans", json={"goal": "g1"}).json()[
        "planId"
    ]
    second = owner.post("/api/v1/orchestrator/plans", json={"goal": "g2"}).json()[
        "planId"
    ]
    assert owner.post(f"/api/v1/orchestrator/plans/{first}/approve").status_code == 200
    assert owner.post(f"/api/v1/orchestrator/plans/{second}/reject").status_code == 202


def test_gate_db_backed_unbound_and_unstamped_denies(db_parts):
    """DB-backed run with NEITHER a project binding NOR a mint-time stamp:
    nothing attributes it to any team ⇒ 403 AUTHZ_UNAVAILABLE (``no_project``)
    — never a silent pass."""
    service: OrchestratorService = db_parts["service"]
    owner = _gate_client(service, _principal(user_id="u-o", role=Role.ADMIN))
    proposed = owner.post("/api/v1/orchestrator/plans", json={"goal": "g"}).json()[
        "planId"
    ]
    run_data = next(iter(service.runs.values()))
    run_data.pop("projectId")  # simulate a run with no project attribution
    run_data.pop("teamId")  # …and no mint-time stamp either
    res = owner.post(f"/api/v1/orchestrator/plans/{proposed}/approve")
    assert res.status_code == 403, res.text
    body = res.json()["detail"]["error"]
    assert body["code"] == "AUTHZ_UNAVAILABLE"
    assert "no_project" in body["message"]


def test_gate_db_backed_unbound_foreign_stamp_is_no_oracle_404(db_parts):
    """Unbound run whose mint-time stamp names ANOTHER team: that is a
    CONFIRMED cross-team attribution ⇒ the M13-G5a no-oracle 404 (not the
    wiring-fault 403)."""
    service: OrchestratorService = db_parts["service"]
    owner = _gate_client(service, _principal(user_id="u-o", role=Role.ADMIN))
    proposed = owner.post("/api/v1/orchestrator/plans", json={"goal": "g"}).json()[
        "planId"
    ]
    run_data = next(iter(service.runs.values()))
    run_data.pop("projectId")
    run_data["teamId"] = "some-other-team"
    intruder = _gate_client(
        service, _principal(user_id="u-x", role=Role.ENGINEER, team_id="team-zzz")
    )
    res = intruder.post(f"/api/v1/orchestrator/plans/{proposed}/approve")
    unknown = intruder.post("/api/v1/orchestrator/plans/plan-nope/approve")
    assert res.status_code == 404
    assert res.json() == unknown.json()


def test_gate_dangling_project_denies_even_for_same_team_stamp(db_parts):
    """Dangling projects row: mint-time stamp matching the caller still does
    not resurrect ownership of an unresolvable project binding ⇒ 403
    dangling_project."""
    service: OrchestratorService = db_parts["service"]
    owner = _gate_client(service, _principal(user_id="u-a", role=Role.ADMIN, team_id="team-a"))
    proposed = owner.post("/api/v1/orchestrator/plans", json={"goal": "g"}).json()[
        "planId"
    ]
    run_data = next(iter(service.runs.values()))
    run_data["projectId"] = "proj-dangle"  # no projects row exists for it
    run_data["teamId"] = "team-a"  # stamp agrees with the caller — still deny
    res = owner.post(f"/api/v1/orchestrator/plans/{proposed}/approve")
    assert res.status_code == 403, res.text
    body = res.json()["detail"]["error"]
    assert body["code"] == "AUTHZ_UNAVAILABLE"
    assert "dangling_project" in body["message"]


def test_gate_store_probe_failure_fails_closed(db_parts, monkeypatch):
    """A degraded store used to degrade to 'no ownership information' + pass;
    it now denies loudly (store_probe_failed)."""
    service: OrchestratorService = db_parts["service"]

    class _BoomFactory:
        def __call__(self):
            raise RuntimeError("store down")

    service.session_factory = _BoomFactory()
    owner = _gate_client(service, _principal(user_id="u-a", role=Role.ADMIN, team_id="team-a"))
    proposed = owner.post("/api/v1/orchestrator/plans", json={"goal": "g"}).json()[
        "planId"
    ]
    run_data = next(iter(service.runs.values()))
    run_data["projectId"] = "proj-a"
    res = owner.post(f"/api/v1/orchestrator/plans/{proposed}/approve")
    assert res.status_code == 403, res.text
    body = res.json()["detail"]["error"]
    assert body["code"] == "AUTHZ_UNAVAILABLE"
    assert "store_probe_failed" in body["message"]


def test_gate_confirmed_foreign_still_no_oracle_404(db_parts):
    """Unchanged from M17-B4: a CONFIRMED mismatch stays indistinguishable
    from an unknown id (fail-closed work must not break the oracle law)."""
    service: OrchestratorService = db_parts["service"]
    owner = _gate_client(service, _principal(user_id="u-a", role=Role.ENGINEER, team_id="team-a"))
    proposed = owner.post("/api/v1/orchestrator/plans", json={"goal": "g"}).json()[
        "planId"
    ]
    run_data = next(iter(service.runs.values()))
    run_data["projectId"] = "proj-b"  # team-b's project, confirmed by the row
    intruder = _gate_client(
        service, _principal(user_id="u-z", role=Role.ENGINEER, team_id="team-zzz")
    )
    res = intruder.post(f"/api/v1/orchestrator/plans/{proposed}/approve")
    unknown = intruder.post("/api/v1/orchestrator/plans/plan-nope/approve")
    assert res.status_code == 404
    assert res.json()["detail"]["error"]["code"] == "NOT_FOUND"
    assert unknown.status_code == 404
    assert res.json() == unknown.json()


def test_gate_same_team_stamped_walk_intact_under_flag(monkeypatch, db_parts):
    """Owner-team decide flow on a DB-backed service keeps working when the
    mint-time stamp carries the day (flag ON — the legacy suites' world)."""
    monkeypatch.setenv("HARNESS_AUTHZ_TEST_MODE", "1")
    service: OrchestratorService = db_parts["service"]
    owner = _gate_client(service, _principal(user_id="u-b", role=Role.ENGINEER, team_id="team-b"))
    proposed = owner.post("/api/v1/orchestrator/plans", json={"goal": "mine"}).json()[
        "planId"
    ]
    run_data = next(iter(service.runs.values()))
    run_data.pop("projectId")  # no project binding at all
    run_data["teamId"] = "team-b"  # mint-time stamp: same team
    approved = owner.post(f"/api/v1/orchestrator/plans/{proposed}/approve")
    assert approved.status_code == 200, approved.text


def test_gate_role_gate_stays_first_under_fail_closed():
    """Viewer/guest keep their uniform 403 ROLE_DENIED before any scoping —
    including in production posture where scoping would deny anyway."""
    service = OrchestratorService()  # bare: scoping would 403 UNAVAILABLE…
    viewer = _gate_client(
        service, _principal(user_id="u-v", role=Role.VIEWER, team_id="team-zzz")
    )
    res = viewer.post("/api/v1/orchestrator/plans/plan-whatever/approve")
    # …but the ROLE check fires first and answers the historical code.
    assert res.status_code == 403
    assert res.json()["detail"]["error"]["code"] == "ROLE_DENIED"
