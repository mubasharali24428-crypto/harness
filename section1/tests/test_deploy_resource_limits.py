"""Mission 20 T3 — resource limits + readiness probes (S-GAMMA OPS-03/06).

Pins the deploy-side contract so drift fails CI:

* every compose service carries deploy.resources.limits (mem/cpus/pids) and
  a json-file logging driver with max-size/max-file;
* sizing law: db generous-but-bounded, engine tiers (orchestrator/sandbox)
  larger than the plain app tier, one-shot jobs smallest;
* all seven split backends answer BOTH probes: /health stays cheap liveness,
  /ready gates on db reachable + migrations applied (migrate.check_drift);
* readiness behavior proven FUNCTIONALLY over ASGI: db up ⇒ 200; db
  unreachable ⇒ 503 naming "db-unreachable"; drifted schema_migrations
  ledger ⇒ 503 naming "schema-drift"; /health never reflects db state.

The live-behavior twin (docker inspect limits, stop db ⇒ unhealthy-not-
restarting) ran at execution time; operator contract: docs/runbooks/ops.md §3.1.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPO_ROOT / "deploy"
_COMPOSE = DEPLOY / "docker-compose.yml"
_SERVICES = DEPLOY / "runners" / "services.py"

# The seven split backends (SERVICES registry in services.py).
BACKEND_PORTS = {
    "identity": 8001,
    "github": 8002,
    "models": 8003,
    "orchestrator": 8004,
    "sandbox": 8005,
    "meeting": 8006,
    "review": 8008,
}

def _limits(prefix: str, mem: str, cpu: str, pids: str) -> tuple[str, str, str]:
    """(memory, cpus, pids) exactly as published in docker-compose.yml."""
    return (
        f"${{HARNESS_{prefix}_MEM_LIMIT:-{mem}}}",
        f"${{HARNESS_{prefix}_CPU_LIMIT:-{cpu}}}",
        f"${{HARNESS_{prefix}_PIDS_LIMIT:-{pids}}}",
    )


# Sizing law per service-class envelope (docs/runbooks/ops.md §3.1).
EXPECTED_LIMITS: dict[str, tuple[str, str, str]] = {}
for _svc in ("db", "walg-db", "db-loadtest"):  # Postgres: generous but bounded
    EXPECTED_LIMITS[_svc] = _limits("DB", "1g", "1.0", "512")
for _svc in ("orchestrator", "backend", "orchestrator-loadtest"):  # engine tier
    EXPECTED_LIMITS[_svc] = _limits("ORCH", "768m", "1.0", "256")
for _svc in ("identity", "github", "models", "meeting", "review",
             "identity-loadtest"):  # plain uvicorn tier
    EXPECTED_LIMITS[_svc] = _limits("APP", "384M", "0.50", "128")
for _svc in ("migrate", "migrate-loadtest", "events-retention", "cost-rollup",
             "db-maintenance"):  # one-shot / DB-only jobs
    EXPECTED_LIMITS[_svc] = _limits("JOB", "256m", "0.25", "64")
EXPECTED_LIMITS["sandbox"] = _limits("SANDBOX", "768m", "1.0", "256")  # agent code
EXPECTED_LIMITS["frontend"] = _limits("FRONTEND", "512m", "0.50", "192")
# Shared-pid-envelope specials:
EXPECTED_LIMITS["prometheus"] = (
    "${HARNESS_PROM_MEM_LIMIT:-512m}",
    "${HARNESS_PROM_CPU_LIMIT:-0.50}",
    "${HARNESS_JOB_PIDS_LIMIT:-64}",
)
EXPECTED_LIMITS["walg"] = (
    "${HARNESS_WALG_MEM_LIMIT:-512m}",
    "${HARNESS_WALG_CPU_LIMIT:-0.50}",
    "${HARNESS_JOB_PIDS_LIMIT:-64}",
)
EXPECTED_LIMITS["proxy"] = _limits("PROXY", "128m", "0.25", "64")


def _compose() -> dict:
    return yaml.safe_load(_COMPOSE.read_text())


# ------------------------------------------------------- compose law (OPS-03)


def test_every_service_has_resource_limits():
    """No unbounded container next to Postgres — ALL services, profiles too."""
    compose = _compose()
    unbounded = [
        name
        for name, svc in sorted(compose["services"].items())
        if not svc.get("deploy", {}).get("resources", {}).get("limits")
    ]
    assert unbounded == [], f"services without resource limits: {unbounded}"


@pytest.mark.parametrize(("service", "expected"), sorted(EXPECTED_LIMITS.items()))
def test_sizing_law_per_service(service, expected):
    """The published mem/cpu/pids envelope per class, verbatim."""
    limits = _compose()["services"][service]["deploy"]["resources"]["limits"]
    got = (limits["memory"], limits["cpus"], limits["pids"])
    assert got == expected, f"{service}: {got} != published {expected}"


def _default_of(raw: str) -> str:
    """`${VAR:-fallback}` → `fallback` (raw compose text, pre-interpolation)."""
    return raw.split(":-")[1].rstrip("}")


def _mem_mib(compose, svc) -> int:
    raw = _default_of(compose["services"][svc]["deploy"]["resources"]["limits"]["memory"])
    num = float(raw.rstrip("mMgG"))
    return int(num * 1024 if raw[-1] in "gG" else num)


def _pids(compose, svc) -> int:
    return int(_default_of(compose["services"][svc]["deploy"]["resources"]["limits"]["pids"]))


def test_engine_tiers_outsize_plain_app_tier():
    """Orchestrator/sandbox run dsh subprocesses by design: their ceilings must
    exceed the plain uvicorn tier's or a full engine fan-out dies to its own
    limits — while Postgres still out-ranks every single app container."""
    compose = _compose()
    assert _pids(compose, "identity") < min(
        _pids(compose, "orchestrator"), _pids(compose, "sandbox")
    )
    assert _mem_mib(compose, "identity") < min(
        _mem_mib(compose, "orchestrator"), _mem_mib(compose, "sandbox")
    )
    assert _mem_mib(compose, "db") > max(
        _mem_mib(compose, "orchestrator"),
        _mem_mib(compose, "sandbox"),
        _mem_mib(compose, "frontend"),
    )


# ------------------------------------------------------- log caps law (OPS-04)


def test_every_service_rotates_json_logs():
    """json-file + max-size/max-file on ALL services: no slow-motion disk
    exhaustion on the disk holding pgdata."""
    bad = []
    for name, svc in sorted(_compose()["services"].items()):
        log = svc.get("logging") or {}
        opts = log.get("options") or {}
        if log.get("driver") != "json-file" or "max-size" not in opts or "max-file" not in opts:
            bad.append(name)
    assert bad == [], f"services without rotation-capped json-file logging: {bad}"


def test_log_cap_defaults_are_10m_x3():
    opts = _compose()["services"]["identity"]["logging"]["options"]
    assert _default_of(opts["max-size"]) == "10m"
    assert _default_of(opts["max-file"]) == "3"


# -------------------------------------------------- readiness probe machinery


@pytest.fixture()
def probes_module(monkeypatch):
    """Import deploy/runners/services.py for real (bytecode identical to what
    uvicorn loads). The module-level master-key fail-fast is satisfied the way
    the loadtest stack does it — explicit ALLOW_DEV opt-in — and monkeypatch
    guarantees no env leaks into sibling tests."""
    monkeypatch.setenv("HARNESS_ALLOW_DEV_MASTER_KEY", "1")
    monkeypatch.setenv("HARNESS_MASTER_KEY", "")
    spec = importlib.util.spec_from_file_location("deploy_services_t3", _SERVICES)
    assert spec is not None and spec.loader is not None  # noqa: S101 — structural
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _probe_app(mod) -> FastAPI:
    """Bare app carrying exactly the shared probe installer."""
    app = FastAPI(title="t3-probe")
    mod._install_probes(app, 9999)
    return app


async def test_ready_ok_when_db_and_schema_match(probes_module, tmp_path):
    """/ready = 200 when db reachable AND ledger matches: sqlite with NO
    schema_migrations table is check_drift's fresh-install case (nothing
    applied, nothing to disagree)."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'ok.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = _probe_app(probes_module)
    app.state.session_factory = factory

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        health = await client.get("/health")
        ready = await client.get("/ready")

    assert health.status_code == 200
    assert health.json() == {"ok": True}
    assert ready.status_code == 200
    assert ready.json() == {"ok": True, "db": "ok", "migrations": "ok"}
    await engine.dispose()


async def test_ready_reports_db_unreachable_not_crash(probes_module, tmp_path):
    """OPS-06's core demand: dependency gone ⇒ 503 NAMING the reason — never a
    raised exception, never restart-worthy process death. /health stays 200
    throughout: liveness ≠ readiness.

    (aiosqlite would happily CREATE a plain missing file, so the engine points
    into a directory that does not exist — connect() fails on every probe,
    like a stopped Postgres.)"""
    engine = create_async_engine("sqlite+aiosqlite:///{nonexistent_dir}/gone.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = _probe_app(probes_module)
    app.state.session_factory = factory

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        health = await client.get("/health")
        ready = await client.get("/ready")

    assert health.status_code == 200  # liveness: process fine
    assert health.json() == {"ok": True}
    assert ready.status_code == 503  # readiness: dependency gone
    assert ready.json() == {"ok": False, "not_ready": ["db-unreachable"]}
    await engine.dispose()


async def test_ready_reports_schema_drift(probes_module, tmp_path):
    """A rolled-forward database (applied ⊄ declared) surfaces in the PROBE,
    not only at boot: bogus version in the ledger ⇒ 503 'schema-drift'."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'drifted.db'}")
    async with engine.begin() as conn:
        await conn.execute(
            text("CREATE TABLE schema_migrations (version VARCHAR PRIMARY KEY)")
        )
        await conn.execute(text("INSERT INTO schema_migrations (version) VALUES ('9999')"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = _probe_app(probes_module)
    app.state.session_factory = factory

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        res = await client.get("/ready")

    assert res.status_code == 503
    assert res.json() == {"ok": False, "not_ready": ["schema-drift"]}
    await engine.dispose()


def test_all_seven_backends_install_probes_with_their_port():
    """Static pin: each app builder wires _install_probes(app, ITS port) so a
    new backend cannot ship without both routes; the old per-app /health stubs
    are gone (liveness defined once, in the shared installer). The sandbox is
    the one backend that also reports egress in /ready — the proxy is its
    reason to exist, so that fact is pinned too."""
    src = _SERVICES.read_text()
    assert src.count('@app.get("/health")') == 1
    for name, port in BACKEND_PORTS.items():
        extra = ", reports_egress=True" if name == "sandbox" else ""
        assert f"_install_probes(app, {port}{extra})" in src, f"{name} ({port}) unwired"


def test_compose_healthchecks_probe_readiness():
    """/ready — not /health — is what Docker polls on every split backend, at
    the readiness cadence (10s/5s/6 retries)."""
    compose = _compose()
    wrong = []
    for name in BACKEND_PORTS:
        hc = compose["services"][name]["healthcheck"]
        joined = str(hc["test"])
        if "/ready" not in joined or f":{BACKEND_PORTS[name]}" not in joined:
            wrong.append(name)
        assert hc["interval"] == "10s", name
    assert wrong == [], f"backends not probed on /ready: {wrong}"
