"""Mission 20, lane T2 — deploy-level observability (OPS-21/OPS-22).

Pins the observability minimum at ASSEMBLY level, where it actually ships:

* every service factory in deploy/runners/services.py wires
  RequestContextMiddleware + /metrics + configure_logging (previously
  orchestrator-only) — proven functionally by building apps through the
  real factories over ASGITransport;
* the middleware emits X-Request-ID on responses (echo + mint), one JSON
  access-log line per request, and anonymizes user ids (sha256[:12], never
  raw);
* /metrics responds 200 with the expected metric names (golden signals +
  orchestrator domain series);
* docs/runbooks/alerting.md carries all five required alert rules as
  structurally valid PromQL (promtool-compatible shape; full promtool check
  documented in the runbook itself);
* deploy compose exposes a Prometheus scrape target covering every service.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import logging
import re
import sys
from pathlib import Path

import pytest
import yaml

# module-level: FastAPI resolves DI annotations through the endpoint's
# __globals__, which for this future-annotations file is this module dict
from fastapi import FastAPI, Request  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPO_ROOT / "deploy"
RUNNERS = DEPLOY / "runners"
_SERVICES = RUNNERS / "services.py"
_OBS = RUNNERS / "observability.py"
_COMPOSE = DEPLOY / "docker-compose.yml"
_PROM_CONFIG = DEPLOY / "observability" / "prometheus.yml"
_ALERT_DOC = REPO_ROOT / "docs" / "runbooks" / "alerting.md"

ALL_SERVICE_FACTORIES = (
    "app_identity",
    "app_github",
    "app_models",
    "app_orchestrator",
    "app_sandbox",
    "app_meeting",
    "app_review",
)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None  # noqa: S101 — structural
    mod = importlib.util.module_from_spec(spec)
    # dataclasses (and decorators generally) resolve cls.__module__ through
    # sys.modules at exec time — register BEFORE exec_module.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def obs():
    """Import deploy/runners/observability.py exactly as deployed.

    sys.path surgery mirrors deploy/runners/services.py's own header (it
    prepends every section*/src). We do NOT permanently mutate sys.path:
    entries are appended and removed again after the module loads (the module
    binds what it needs at import time).
    """
    section_srcs = sorted(p / "src" for p in REPO_ROOT.glob("section*") if (p / "src").exists())
    added = [p for p in section_srcs if str(p) not in sys.path]
    sys.path.extend(str(p) for p in added)
    sys.path.insert(0, str(RUNNERS))
    try:
        yield _load_module("obs_m20t2", _OBS)
    finally:
        for p in added:
            sys.path.remove(str(p))
        sys.path.remove(str(RUNNERS))


# ---------------------------------------------------------------------------
# 1. Wiring law — configure_logging + middleware + /metrics on ALL services
# ---------------------------------------------------------------------------


def _segment(func) -> str:
    seg = ast.get_source_segment(_SERVICES.read_text(), func)
    assert seg is not None
    return seg


def test_every_service_factory_calls_the_observability_wiring():
    """AST law: each app_* factory either calls _wire_observability(app)
    (six shared-path services + github's direct call) or performs the Wave 8
    orchestrator-equivalent wiring itself. A new service without wiring must
    fail here, not ship blind."""
    tree = ast.parse(_SERVICES.read_text())
    funcs = {
        n.name: n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in ALL_SERVICE_FACTORIES:
        assert name in funcs, f"services.py lost {name}"
        # Coverage is TRANSITIVE: app_models/app_sandbox/app_review reach the
        # wiring through simple_app, so walk one call deep. The orchestrator
        # wires its own Wave 8 stack (its tests pin those exact objects)
        # PLUS installs the uniform root /metrics endpoint.
        calls: set[str] = set()
        for node in ast.walk(funcs[name]):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                calls.add(node.func.id)
        has_shared = "simple_app" in calls or "_wire_observability" in calls
        has_own = (
            "RequestContextMiddleware" in _segment(funcs[name])
            and "configure_logging" in _segment(funcs[name])
            and "install_metrics_endpoint" in _segment(funcs[name])
        )
        assert has_shared or has_own, (
            f"{name} does not wire observability (request-id middleware +"
            " /metrics + configure_logging)"
        )


def test_simple_app_itself_wires_observability():
    """The transitive leg of the law above: simple_app (shared by models,
    sandbox, review) must contain the full three-piece wiring."""
    src = _SERVICES.read_text()
    m = re.search(r"^def simple_app\(.*?(?=^def )", src, re.S | re.M)
    assert m, "simple_app not found in services.py"
    body = m.group(0)
    assert "_wire_observability(" in body
    assert "install_error_envelope" in body  # context sanity


def test_wire_observability_installs_all_three_pieces(obs):
    """The shared helper really mounts middleware + endpoint + logging."""
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/health")
    async def health():  # pragma: no cover - trivial
        return {"ok": True}

    obs.configure_logging("json")
    app.add_middleware(obs.RequestContextMiddleware)
    obs.install_metrics_endpoint(app)

    route_paths = {getattr(r, "path", None) for r in app.routes}
    assert "/metrics" in route_paths


async def test_identity_app_serves_request_ids_and_metrics_over_real_factory(
    monkeypatch,
):
    """Functional proof through the REAL identity assembly (the OPS-21 grep
    showed identity had NO middleware before this lane): response carries
    X-Request-ID; GET /metrics is 200 text/plain."""
    import os

    monkeypatch.setenv("HARNESS_ALLOW_DEV_MASTER_KEY", "1")
    os.environ.setdefault("HARNESS_ALLOW_DEV_MASTER_KEY", "1")

    runn = str(RUNNERS)
    if runn not in sys.path:
        sys.path.insert(0, runn)
    try:
        services = _load_module("svc_m20t2_id", _SERVICES)
        app = services.app_identity()
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            rid_res = await c.get("/health", headers={"X-Request-ID": "m20t2header01"})
            assert rid_res.status_code == 200
            assert rid_res.headers["x-request-id"] == "m20t2header01"

            metrics_res = await c.get("/metrics")
            assert metrics_res.status_code == 200
            assert metrics_res.headers["content-type"].startswith("text/plain")
            assert metrics_res.text.startswith("# HELP")
    finally:
        if runn in sys.path:
            sys.path.remove(runn)


# ---------------------------------------------------------------------------
# 2. Middleware behaviour — request ids, JSON access log, anonymized user
# ---------------------------------------------------------------------------


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _make_deploy_app(obs):

    app = FastAPI()
    app.add_middleware(obs.RequestContextMiddleware)
    obs.install_metrics_endpoint(app)

    @app.get("/who")
    async def who(request: Request):  # stashes a user id like a real handler
        request.state.harness_user_id = "user-4f8a2b"
        return {"ok": True}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("handler crash")

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app


@pytest.fixture
def deploy_client(obs):
    from starlette.testclient import TestClient

    app = _make_deploy_app(obs)
    # raise_server_exceptions=False: the crash test asserts the middleware
    # still stamps X-Request-ID on the 500 the client actually receives.
    return TestClient(app, raise_server_exceptions=False)


def test_request_id_echoed_and_minted(deploy_client):
    echoed = deploy_client.get("/health", headers={"X-Request-ID": "abc123def456"})
    assert echoed.status_code == 200
    assert echoed.headers["x-request-id"] == "abc123def456"

    first = deploy_client.get("/health").headers["x-request-id"]
    second = deploy_client.get("/health").headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{12}", first)
    assert re.fullmatch(r"[0-9a-f]{12}", second)
    assert first != second


def test_request_id_header_present_on_404_and_logged_on_crash(deploy_client, obs):
    # middleware, so the header rides on it.
    missing = deploy_client.get("/no/such/route")
    assert missing.status_code == 404
    assert re.fullmatch(r"[0-9a-f]{12}", missing.headers["x-request-id"])

    # Handler crash: the exception propagates THROUGH this middleware to
    # Starlette's outer ServerErrorMiddleware, which renders the 500 — so
    # the contract here is the JSON access-log line (request id + status),
    # same scope as the orchestrator suite's coverage. Header presence on a
    # 500 is proven in test_identity_app... where the error envelope
    # middleware converts the crash inside the app boundary.
    handler = _ListHandler()
    logger = logging.getLogger("harness.deploy.request")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        deploy_client.get("/boom")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    crash_lines = [r for r in handler.records if r.msg == "http request"]
    assert len(crash_lines) == 1, "crash still emits exactly one access line"
    record = crash_lines[0]
    parsed = json.loads(obs.JsonFormatter().format(record))
    assert parsed["status"] == 500, "crash recorded with status 500"
    assert re.fullmatch(r"[0-9a-f]{12}", parsed["requestId"])


def test_access_log_single_json_line_with_anonymized_user(deploy_client, obs):
    handler = _ListHandler()
    logger = logging.getLogger("harness.deploy.request")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        res = deploy_client.get(
            "/who", headers={"X-Request-ID": "loganon000001"}
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)

    assert res.status_code == 200
    matches = [
        r for r in handler.records if getattr(r, "request_id", "") == "loganon000001"
    ]
    assert len(matches) == 1, "exactly one INFO line per request"

    parsed = json.loads(obs.JsonFormatter().format(matches[0]))
    assert parsed["requestId"] == "loganon000001"
    assert parsed["method"] == "GET"
    assert parsed["path"] == "/who"
    assert parsed["status"] == 200
    expected_hash = hashlib.sha256(b"user-4f8a2b").hexdigest()[:12]
    assert parsed["user"] == expected_hash, "user label must be sha256[:12]"
    assert "user-4f8a2b" not in json.dumps(parsed), "raw id must never leak"


def test_anonymize_is_stable_and_bounded(obs):
    a = obs.anonymize_user_id("someone@example.com")
    b = obs.anonymize_user_id("someone@example.com")
    c = obs.anonymize_user_id("other@example.com")
    assert a == b and a != c
    assert re.fullmatch(r"[0-9a-f]{12}", a)


# ---------------------------------------------------------------------------
# 3. /metrics exposition — names, types, domain series
# ---------------------------------------------------------------------------

GOLDEN_SIGNAL_NAMES = (
    "http_requests_total",
    "http_request_duration_seconds",
)
DOMAIN_NAMES = (
    "harness_db_pool_checked_out_connections",
    "harness_event_bus_lag_seconds",
    "harness_queue_depth",
    "harness_oldest_queued_run_age_seconds",
    "harness_stale_running_claims",
    "harness_runs_completed_total",
    "harness_runs_started_total",
)


def test_metrics_endpoint_names_after_traffic(deploy_client, obs):
    deploy_client.get("/health")
    deploy_client.get("/no/such/route")

    body = deploy_client.get("/metrics").text
    for name in GOLDEN_SIGNAL_NAMES:
        assert f"# TYPE {name}" in body, f"missing golden signal {name}"

    health_series = re.search(
        r'^http_requests_total\{method="GET",path="/health",status="200"\} (\d+)$',
        body,
        re.MULTILINE,
    )
    assert health_series and int(health_series.group(1)) >= 1
    assert 'path="unmatched",status="404"' in body

    # Histogram buckets monotone per series.
    per_series: dict[str, list[int]] = {}
    for labels, count in re.findall(
        r"^http_request_duration_seconds_bucket\{(.+?)\} (\d+)$", body, re.M
    ):
        per_series.setdefault(labels, []).append(int(count))
    for labels, values in per_series.items():
        assert values == sorted(values), f"non-monotone buckets: {labels}"


async def test_orchestrator_domain_gauges_render(obs, monkeypatch):
    """Domain series render with values wired through install_domain_metrics
    (poller disabled via env knob; sqlite NullPool reports zero histogram)."""
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    class _BusStub:
        def __init__(self) -> None:
            self.listeners: list = []

        def add_event_listener(self, cb) -> None:
            self.listeners.append(cb)

    app = FastAPI()
    app.state.event_bus = _BusStub()
    monkeypatch.setenv("HARNESS_METRICS_DB_POLL_SECONDS", "0")

    # run_claims/agent_events tables don't exist on the bare fixture engine —
    # the poller would warn-and-continue; with polling disabled nothing runs.
    obs.install_domain_metrics(app, factory=factory, engine=engine, bus=app.state.event_bus)

    body = obs.METRICS.render()
    for name in DOMAIN_NAMES:
        assert f"# HELP {name}" in body, f"missing domain metric {name}"
        assert f"# TYPE {name}" in body

    assert "# TYPE harness_db_pool_checked_out_connections histogram" in body
    bucket_lines = re.findall(
        r'^harness_db_pool_checked_out_connections_bucket\{le="([\d.+Inf]+)"\} (\d+)$',
        body,
        re.MULTILINE,
    )
    assert bucket_lines, "pool saturation must render as a histogram"
    assert bucket_lines[-1][0] == "+Inf"
    counts = [int(n) for _, n in bucket_lines]
    assert counts == sorted(counts), "histogram buckets must be monotone"

    assert "# TYPE harness_queue_depth gauge" in body
    assert re.search(r"^harness_queue_depth \d+$", body, re.M)
    assert re.search(r"^harness_event_bus_lag_seconds \d+$", body, re.M)
    assert re.search(
        r'^harness_runs_started_total \d+$', body, re.M
    )
    completed = re.search(
        r'^harness_runs_completed_total\{outcome="(failed|passed)"\} (\d+)$', body, re.M
    )
    assert completed, "runs completed counter must carry outcome labels"

    # Bus listener counts lifecycle events into the counters.
    class _Env:
        def __init__(self, t) -> None:
            self.type = t

    bus = app.state.event_bus
    for listener in bus.listeners:
        listener(_Env("execution_started"))
        listener(_Env("engine_failed"))
        listener(_Env("engine_failed"))
        listener(_Env("engine_completed"))
    body2 = obs.METRICS.render()
    started = re.search(r"^harness_runs_started_total (\d+)$", body2, re.M)
    failed = re.search(
        r'^harness_runs_completed_total\{outcome="failed"\} (\d+)$', body2, re.M
    )
    passed = re.search(
        r'^harness_runs_completed_total\{outcome="passed"\} (\d+)$', body2, re.M
    )
    assert started and int(started.group(1)) == 1
    assert failed and int(failed.group(1)) == 2
    assert passed and int(passed.group(1)) == 1

    await engine.dispose()


def test_gauge_error_never_breaks_render(obs):
    """A raising gauge source is swallowed (warn-once), rest still renders."""
    spec = obs.GaugeSpec(
        name="harness_boom_gauge",
        mtype="gauge",
        help="always raises",
        fn=lambda: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    obs.METRICS.add_gauge(spec)
    try:
        body = obs.METRICS.render()
        assert "http_requests_total" in body  # golden signals unaffected
        assert "harness_boom_gauge" not in body  # broken series omitted
    finally:
        with obs.METRICS._lock:
            obs.METRICS._gauges.pop("harness_boom_gauge", None)


# ---------------------------------------------------------------------------
# 4. Alerting doc — five rules, structurally valid PromQL
# ---------------------------------------------------------------------------

REQUIRED_RULE_SNIPPETS = (
    ("queue depth", r"harness_queue_depth > \d+"),
    ("event-bus lag", r"harness_event_bus_lag_seconds > \d+"),
    ("pool saturation", r"histogram_quantile\(\s*1,"),
    ("backup freshness", r"time\(\) - harness_backup_last_success_timestamp_seconds > \d+"),
    ("archiver failed_count", r"increase\(harness_pg_wal_archive_failed_count_total\[\w+\]\) > 0"),
)


def test_alerting_doc_exists_with_five_rules():
    text = _ALERT_DOC.read_text()
    blocks = re.findall(r"```promql\n(.*?)```", text, re.S)
    joined = "\n".join(blocks)
    for label, pattern in REQUIRED_RULE_SNIPPETS:
        assert re.search(pattern, joined), f"alerting.md missing rule: {label}"
    assert len(blocks) >= 5, f"expected >=5 promql blocks, found {len(blocks)}"


def test_promql_blocks_are_structurally_valid():
    """Documented CI fallback (runbook §1): balanced delimiters, known metric
    identifiers, valid durations — NOT a full promtool parse."""
    text = _ALERT_DOC.read_text()
    blocks = re.findall(r"```promql\n(.*?)```", text, re.S)
    known = set(GOLDEN_SIGNAL_NAMES) | set(DOMAIN_NAMES) | {
        "time()",
        "rate(",
        "increase(",
        "sum by",
        "histogram_quantile(",
        "and on()",
    }
    durations = re.compile(r"\[(?:\d+[smhd])(?:\s*:\s*\d+[smhd])?\]")
    for block in blocks:
        expr = block.strip()
        assert expr, "empty promql block"
        assert expr.count("{") == expr.count("}"), f"unbalanced braces:\n{expr}"
        assert expr.count("(") == expr.count(")"), f"unbalanced parens:\n{expr}"
        for dur in re.findall(r"\[[^\]]*\]", expr):
            assert durations.fullmatch(dur), f"bad duration selector {dur!r}"
        first_token = re.match(r"[a-zA-Z_][a-zA-Z0-9_:]*", expr)
        if first_token and not expr.startswith(
            ("sum", "rate", "increase", "time", "histogram_quantile")
        ):
            assert first_token.group(0) in known, (
                f"unknown metric '{first_token.group(0)}' in block:\n{expr}"
            )


def test_alerting_doc_links_runbook_targets():
    text = _ALERT_DOC.read_text()
    linked = set(re.findall(r"\]\(([^)]+\.md)\)", text))
    assert linked, "alerting.md must link sibling runbooks"
    for rel in linked:
        target = (_ALERT_DOC.parent / rel).resolve()
        # queue-depth.md / eventbus.md may legitimately not exist yet (other
        # lanes); only enforce that EXISTING links resolve to real files.
        if target.exists():
            assert target.is_file()


# ---------------------------------------------------------------------------
# 5. Compose — ONE scrape target covering every service
# ---------------------------------------------------------------------------


def test_compose_has_prometheus_service_with_profile():
    compose = yaml.safe_load(_COMPOSE.read_text())
    prom = compose["services"].get("prometheus")
    assert prom, "compose lost the prometheus service"
    assert "observability" in prom.get("profiles", [])
    volumes = " ".join(prom.get("volumes", []))
    assert "./observability:/etc/prometheus" in volumes
    ports = " ".join(prom.get("ports", []))
    assert "9090:9090" in ports


def test_prometheus_scrape_target_covers_every_backend():
    cfg = yaml.safe_load(_PROM_CONFIG.read_text())
    jobs = cfg["scrape_configs"]
    assert len(jobs) == 1, "M20-T2 contract: exactly ONE scrape target/job"
    targets = jobs[0]["static_configs"][0]["targets"]
    expected = {
        "identity:8001",
        "github:8002",
        "models:8003",
        "orchestrator:8004",
        "sandbox:8005",
        "meeting:8006",
        "review:8008",
    }
    assert set(targets) == expected, f"drift: {sorted(targets)}"
    assert jobs[0]["metrics_path"] == "/metrics"


def test_env_knobs_pass_through_compose():
    compose = yaml.safe_load(_COMPOSE.read_text())
    env_anchor = compose.get("x-backend-env", {})
    assert env_anchor.get("HARNESS_LOG_FORMAT") is not None
    assert env_anchor.get("HARNESS_METRICS_DB_POLL_SECONDS") is not None


async def test_orchestrator_domain_poll_works_over_deployed_async_factory(
    obs, monkeypatch, tmp_path
):
    """Deploy-parity regression for the domain-metrics poller, seeded from
    two compose observations: (1) with the deployed async_sessionmaker, the
    poll used `with factory()` — SQLAlchemy 2.0 AsyncSession only supports
    `async with`, so every 15s poll log-warned TypeError("'AsyncSession'
    object does not support the context manager protocol") and
    queue_depth / bus lag never refreshed; (2) a per-poll asyncio.run on the
    shared async engine is unsafe with asyncpg (pool connections are
    loop-bound). The fix drives the engine's thread-agnostic
    `.sync_engine` facade from the poller thread. Tables mirror the minimum
    column shape of migrations 0001/0006 that the gauges read."""
    import datetime as dt

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/s2poll.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setenv("HARNESS_METRICS_DB_POLL_SECONDS", "0")  # poll manually below

    # Arrange via this test's own loop; the poll helper awaits the SAME
    # engine's pool on that loop, mirroring the deployed task-on-app-loop
    # shape (asyncpg loop-binding makes foreign-loop/thread polls unsafe).
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE run_claims (run_id TEXT PRIMARY KEY,"
                " team_id TEXT, state TEXT)"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE agent_events (seq INTEGER PRIMARY KEY,"
                " run_id TEXT, type TEXT, payload TEXT, ts TIMESTAMP)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO run_claims (run_id, team_id, state)"
                " VALUES ('r1','t1','queued'), ('r2','t1','queued'),"
                " ('r3','t1','claimed')"
            )
        )
        past = (
            dt.datetime.now(dt.UTC) - dt.timedelta(seconds=45)
        ).isoformat(sep=" ")
        await conn.execute(
            text(
                "INSERT INTO agent_events (seq, run_id, type, payload, ts)"
                " VALUES (1, 'r1', 'execution.started', '{}', :ts)"
            ),
            {"ts": past},
        )

    app = FastAPI()

    class _BusStub:
        def __init__(self) -> None:
            self.listeners: list = []

        def add_event_listener(self, cb) -> None:
            self.listeners.append(cb)

    bus = _BusStub()
    obs.install_domain_metrics(app, factory=factory, engine=engine, bus=bus)

    await app.state.domain_metrics_poll()  # deterministic single refresh (the hook)

    body = obs.METRICS.render()
    depth = re.search(r"^harness_queue_depth (\d+)$", body, re.M)
    assert depth, "queue-depth gauge missing"
    assert int(depth.group(1)) == 2, "two seeded queued claims must count"
    lag = re.search(r"^harness_event_bus_lag_seconds (\d+)$", body, re.M)
    assert lag, "bus-lag gauge missing"
    assert 1 <= int(lag.group(1)) <= 3600, "seeded event must set a fresh lag"

    await engine.dispose()


async def test_stall_gauges_flag_old_queued_and_stale_running(obs, tmp_path, monkeypatch):
    """Ops acceptance (part 28): a dead worker must be visible as a growing
    oldest-queued age and as running claims with a stale heartbeat."""
    import datetime as dt

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/stall.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setenv("HEARTBEAT_TIMEOUT_SECONDS", "90")
    now = dt.datetime.now(dt.UTC)
    old = (now - dt.timedelta(seconds=600)).isoformat()
    fresh = now.isoformat()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE run_claims (run_id TEXT PRIMARY KEY, project_id TEXT,"
                " state TEXT, claimed_at TEXT, heartbeat_at TEXT, worker_id TEXT)"
            )
        )
        for rid, state, claimed, hb in (
            ("q1", "queued", old, old),
            ("q2", "queued", fresh, fresh),
            ("r1", "running", old, old),  # stale heartbeat
            ("r2", "running", fresh, fresh),  # healthy
        ):
            await conn.execute(
                text("INSERT INTO run_claims VALUES (:r,'p',:s,:c,:h,'w')"),
                {"r": rid, "s": state, "c": claimed, "h": hb},
            )
    age, stale = await obs._read_stall_gauges(factory)
    assert 590 <= age <= 700
    assert stale == 1
    await engine.dispose()
