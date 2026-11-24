"""Assembly runners: one FastAPI app per backend service, wired at deploy time."""

import asyncio
import json
import os
import sys
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[2]
for section in sorted((HARNESS_ROOT).glob("section*")):
    src = section / "src"
    if src.exists():
        sys.path.insert(0, str(src))

# M20-T3: the readiness gate reuses migrate.check_drift (the SAME drift law
# entrypoint.sh enforces at boot) — put deploy/runners on sys.path for it.
sys.path.insert(0, str(HARNESS_ROOT / "deploy" / "runners"))

DATABASE_URL = os.environ.get(
    "HARNESS_DATABASE_URL", "postgresql+asyncpg://harness:harness@db:5432/harness"
)

_DEV_MASTER_KEY = b"dev-master-key-change-me-in-prod!"
MASTER_KEY = os.environ.get("HARNESS_MASTER_KEY", "").encode()
if not MASTER_KEY or MASTER_KEY == _DEV_MASTER_KEY:
    # F9 fail-fast: never silently run production secrets under a public constant.
    if os.environ.get("HARNESS_ALLOW_DEV_MASTER_KEY") != "1":
        raise SystemExit(
            "Refusing to start: set HARNESS_MASTER_KEY to a real 32+ byte secret "
            "(or HARNESS_ALLOW_DEV_MASTER_KEY=1 for local development)."
        )
    MASTER_KEY = _DEV_MASTER_KEY

_CORS_DEFAULT = "http://localhost:3000"


def _parse_cors_origins(raw: str) -> list[str]:
    """Accept BOTH env shapes: JSON array (pydantic convention, what compose
    sets) and comma-separated. The known pitfall — a JSON string fed to
    comma-split yields '["http://localhost:3000"]' as a single bogus origin,
    so every browser request fails CORS with Disallowed origin."""
    raw = raw.strip()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(o).strip() for o in parsed if str(o).strip()]
        except json.JSONDecodeError:
            pass
    return [o.strip() for o in raw.split(",") if o.strip()]


CORS_ORIGINS = _parse_cors_origins(os.environ.get("HARNESS_CORS_ORIGINS", _CORS_DEFAULT))


def get_factory():
    # M13-W3-H1a: pool math is owned by deploy/runners/db.py — ONE shared
    # factory so every service boots pool_size=8 / max_overflow=4
    # (8+4=12 ×7 services = 84 ≤ PG max_connections=120; docs/pool-math.md).
    from db import build_engine, make_session_factory

    return make_session_factory(build_engine(DATABASE_URL))


def base_settings(**kw):
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class S(BaseSettings):
        model_config = SettingsConfigDict(env_prefix="HARNESS_", extra="ignore")
        database_url: str = DATABASE_URL
        master_key: bytes = MASTER_KEY
        cors_origins: list[str] = CORS_ORIGINS
        # H2r ownership-proof knobs must exist here too — this local Settings
        # class replaces section1's for every deployed service.
        # C-001 (M11-W1): default True — unverified email cannot mint teams.
        require_ownership: bool = True
        otp_ttl_minutes: int = 10
        otp_max_attempts: int = 5
        # S2 GitHub OAuth knobs (deploy replaces section Settings wholesale).
        github_client_id: str = ""
        github_client_secret: str = ""
        github_redirect_uri: str = "http://localhost:8002/api/v1/github/oauth/callback"
        frontend_base: str = "http://localhost:3000"
        # M17-B4 (MAC2-F8) mirror — M18-C4 fix (found live: the loadtest probe
        # built at HEAD 500'd every login here): the login handler reads
        # settings.trusted_proxy_cidrs, so the deploy Settings class MUST carry
        # it too. Defaults match harness_identity.config.Settings exactly.
        trusted_proxy_cidrs: list[str] = [
            "127.0.0.0/8",
            "::1/128",
            "::/128",
            "fc00::/7",
            "fe80::/10",
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
        ]

    return S(**kw)


def _wire_observability(app, extra=None) -> None:
    """M20-T2 (OPS-21/22): request ids + JSON logs + /metrics on EVERY service.

    The orchestrator keeps its own Wave 8 wiring in app_orchestrator (its
    tests pin those exact objects); this is the shared path for the other
    six. HARNESS_LOG_FORMAT is applied once per service process at assembly
    time — uvicorn workers import services.py before serving.
    """
    from observability import (
        configure_logging,
        install_metrics_endpoint,
        RequestContextMiddleware,
    )

    configure_logging(os.environ.get("HARNESS_LOG_FORMAT", "json"))
    app.add_middleware(RequestContextMiddleware)  # X-Request-ID + access log
    install_metrics_endpoint(app, extra)  # public GET /metrics (Prometheus text)


def _install_probes(app, port, *, reports_egress: bool = False):
    """``reports_egress`` is True for the sandbox app ONLY: every backend
    container receives SANDBOX_EGRESS_PROXY_URL, so presence of the env
    cannot identify who actually uses the proxy — the app that owns the
    runner has to say so."""
    """M20-T3 (S-GAMMA OPS-03/OPS-06): TWO probes with distinct jobs.

    /health — LIVENESS. Cheap and dependency-free: answers 200 as long as
    the process can serve. Never touches the DB; Docker restarts on its
    failure only when the process itself is wedged/dead (via the
    entrypoint supervisor exit + restart policy pair).

    /ready — READINESS. Gates on every hard dependency before declaring
    this instance fit to serve traffic:
      1. db reachable: SELECT 1 over this service's own pooled factory;
      2. migrations applied: schema_migrations ⊆ THIS image's chain
         (deploy/runners/migrate.check_drift — same gate entrypoint.sh
         runs at boot, re-evaluated per probe).
      3. egress (informational only, sandbox app only): whether the allow-list
         proxy accepts a connection, reported as `egress`. It is never a
         failure reason — a run that needs no network must still be able to
         execute — but readiness must not imply reach either.

    Any failed check → 503 {"ok": false, ...reasons}. The compose
    healthcheck hits /ready, so a DB-down backend reports unhealthy for
    humans/proxies while the container keeps RUNNING — it is not-ready,
    NOT crash-looping. That is the fix for OPS-06's "db dies ⇒ seven
    backends answer 200 with a wall of 500s" restart-storm class.
    """
    from fastapi import Request
    from fastapi.responses import JSONResponse

    # deploy/runners is on sys.path (header); same drift law entrypoint uses.
    from migrate import check_drift  # noqa: I001 — keep fastapi block intact

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/ready")
    async def ready(request: Request):
        reasons: list[str] = []
        # Bind the factory HERE if the app builder did not (identity/github/
        # review set app.state.session_factory themselves; models/orchestrator/
        # sandbox/meeting build it via get_factory() but never stored it).
        factory = getattr(app.state, "session_factory", None)
        if factory is None:
            factory = get_factory()
            app.state.session_factory = factory
        # 1) Dependency reachable — a single round-trip through the pool.
        try:
            from sqlalchemy import text

            async with factory() as db:
                await db.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 — readiness must answer, not stack-trace
            reasons.append("db-unreachable")
        else:
            # 2) Schema matches this image's migration chain (read-only).
            engine = factory.kw["bind"]
            try:
                await check_drift(engine)
            except Exception:  # noqa: BLE001 — drift is a reason, not a traceback
                reasons.append("schema-drift")
        if reasons:
            return JSONResponse(
                status_code=503, content={"ok": False, "not_ready": reasons}
            )
        body: dict[str, object] = {"ok": True, "db": "ok", "migrations": "ok"}
        egress = await _egress_state() if reports_egress else None
        if egress is not None:
            body["egress"] = egress
        return body

    app.state.ready_port = port


async def _egress_state() -> str | None:
    """Informational only: is the allow-list proxy reachable from here?

    Deliberately NOT a readiness reason. A run that never touches the network
    is still executable while the proxy is down, so failing /ready would take
    the sandbox out of service for a dependency most runs do not use — and
    compose would restart a healthy container. But readiness must not imply
    reach either, so the answer is reported instead of assumed. ``None`` means
    this service has no egress pairing (only the sandbox container sets the
    env).
    """
    url = os.environ.get("SANDBOX_EGRESS_PROXY_URL", "").strip()
    if not url:
        return None
    hostport = url.split("://", 1)[-1].rstrip("/")
    host, _, port_text = hostport.partition(":")
    try:
        port = int(port_text or "3128")
    except ValueError:
        return "down"
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), 0.75
        )
    except (OSError, TimeoutError, ValueError):
        return "down"
    writer.close()
    try:
        await writer.wait_closed()
    except (OSError, TimeoutError):
        pass
    return "up"


def simple_app(router, title, extra_metrics=None):
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(title=title)
    # H1: no wildcard origins; browsers send credentials, so origins must be exact.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    # Wave9-E (append): security headers for simple_app consumers (S4/S5).
    from harness_identity.security_headers import install_security_headers

    install_security_headers(app)
    # M17-B3 / C-2: EVERY deployed service answers the frozen error envelope
    # {"error": {code, message}} (+ legacy detail mirror). Previously only S1
    # installed it, so bare HTTPException details from S3/S4/S5/S8 leaked as
    # raw strings and section7's api.ts had to normalize three wire shapes
    # client-side. The canonical middleware lives in harness_identity.
    from harness_identity.envelope_middleware import install_error_envelope

    install_error_envelope(app)
    # M20-T2: request ids + JSON logs + /metrics (shared with simple_app
    # consumers; identity/github get the identical stack below).
    _wire_observability(app, extra_metrics)
    return app


def app_identity():
    from harness_identity.app import make_app
    from harness_identity.ratelimit import install_rate_limit

    app = make_app(get_factory(), base_settings())

    # Mission10-B5 merge wiring: fixed-window rate limiting on the auth edge
    # (login + OTP request). Installed at assembly level so tests can build
    # un-limited apps via make_app and install their own tuned limiter.
    # DB failure fails OPEN (availability > strictness).
    install_rate_limit(app)

    # M20-T2: request ids + JSON logs + /metrics (identity, port 8001).
    # The event-delivery counters are appended so Prometheus scrapes them.
    from harness_identity import domain_metrics as identity_domain_metrics
    from harness_identity import realtime_metrics

    _wire_observability(
        app, lambda: identity_domain_metrics.render() + realtime_metrics.render())
    # M2-W13 fix: the runner control plane publishes run-terminal events
    # (engine_completed / engine_failed) on THIS app's bus, so a BYO run that
    # settles must reach the same notification tap the orchestrator's own
    # publishes get. Enqueue outbox rows here; the orchestrator's single
    # NotificationWorker drains the shared notification_outbox table. No second
    # worker on identity, so an event is never double-sent.
    from harness_identity.notifier import make_event_notifier

    app.state.event_notifier = make_event_notifier(get_factory(), base_settings())
    app.state.event_bus.add_event_listener(app.state.event_notifier)
    # M20-T3: /ready gates on db + migrations; compose healthcheck hits it.
    _install_probes(app, 8001)

    # M2-W13: expired runner nonces can never be replayed. The auth path prunes
    # inline; this sweep is the backstop for quiet runners (0 turns it off).
    nonce_every = float(os.environ.get("HARNESS_RUNNER_NONCE_PURGE_SECONDS", "600"))
    if nonce_every > 0:
        from harness_identity.runner_api import run_nonce_sweeper

        @app.on_event("startup")
        async def _start_runner_nonce_sweeper() -> None:
            app.state.runner_nonce_sweeper = asyncio.create_task(
                run_nonce_sweeper(get_factory(), nonce_every)
            )

        @app.on_event("shutdown")
        async def _stop_runner_nonce_sweeper() -> None:
            app.state.runner_nonce_sweeper.cancel()

    return app


def app_github():
    from harness_github.app import make_app

    app = make_app(get_factory(), base_settings())
    # M20-T2: request ids + JSON logs + /metrics (github, port 8002).
    _wire_observability(app)
    _install_probes(app, 8002)
    return app


def app_models():
    import asyncio
    import os

    from harness_router.api import (
        get_ledger_pool,
        pending_usage_tasks,
        router,
        set_ledger_pool,
    )

    app = simple_app(router, "harness-router")
    # W15 slice 2: the model-call spend surface was never limited in the
    # deployed stack — the hook existed but no factory installed it. The
    # counter store lives in rate_buckets, so the app needs a SQLAlchemy
    # session factory (the ledger pool below is asyncpg and not a substitute).
    from harness_router.ratelimit_hooks import install_router_rate_limit

    app.state.session_factory = get_factory()
    install_router_rate_limit(app, session_factory=app.state.session_factory)
    # M20-T3: /ready gates on db + migrations (models, port 8003).
    _install_probes(app, 8003)

    # S1-R01a: bearer auth on every router route. Section 3 routes fail
    # closed (503 AUTH_NOT_CONFIGURED) without this wiring.
    from harness_contracts.fastapi_auth import make_bearer_dep

    app.state.get_principal = make_bearer_dep(get_factory())

    # Wave9-A merge wiring: cost metering activates when this service boots
    # with a database; until then calls succeed unmetered and /spend
    # answers 503 honestly.
    dsn = os.environ.get(
        "HARNESS_DATABASE_URL",
        "postgresql+asyncpg://harness:harness@db:5432/harness",
    )
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)

    @app.on_event("startup")
    async def _start_cost_ledger_pool() -> None:
        import asyncpg

        set_ledger_pool(await asyncpg.create_pool(dsn=dsn, min_size=0, max_size=4))

    @app.on_event("shutdown")
    async def _stop_cost_ledger_pool() -> None:
        for task in pending_usage_tasks():  # drain in-flight metering writes
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except Exception:  # noqa: BLE001 — ledger must never block shutdown
                pass
        pool = get_ledger_pool()
        if pool is not None:
            await pool.close()

    return app


def app_orchestrator():
    import os

    from harness_contracts.fastapi_auth import make_bearer_dep
    from harness_identity.eventbus import EventBus

    from harness_orchestrator.api import (
        RequestContextMiddleware,
        configure_orchestrator,
        router,
    )
    from harness_orchestrator.engine_runtime import EngineRuntime
    from harness_orchestrator.logging_utils import configure_logging
    from harness_orchestrator.service import OrchestratorService
    from harness_orchestrator.worker import RunQueueWorker

    # Wave 8A observability: single-line JSON logs by default (env knob
    # HARNESS_LOG_FORMAT=json|console), request-id middleware + /metrics.
    configure_logging(os.environ.get("HARNESS_LOG_FORMAT", "json"))

    factory = get_factory()
    bus = EventBus(factory)
    # M18-C4 (throughput truth probe): an EXPLICIT, default-OFF injected engine
    # backend for load testing. ENGINE_BACKEND_ECHO=1 makes every run complete
    # deterministically WITHOUT a provider — the only way to measure the
    # platform ceiling (queue + executor + DB + events), because dry mode
    # answers status="not_configured" and blocks runs before the executor is
    # ever touched. Never set in production; the live path is untouched when
    # unset. Mirrors the backend_runner= injection section4's stress tests use.
    engine = None
    if os.environ.get("ENGINE_BACKEND_ECHO") == "1":
        from harness_orchestrator.engine import DeepSeekHarnessWrapper

        def _echo_backend(goal: str) -> str:
            import time as _time

            _time.sleep(float(os.environ.get("ENGINE_ECHO_WORK_MS", "50")) / 1000.0)
            return f"echo-backend completed: {goal[:120]}"

        engine = DeepSeekHarnessWrapper(backend_runner=_echo_backend)

    # M17-B5: engine runs execute on a DEDICATED bounded executor
    # (ENGINE_MAX_CONCURRENCY, default 8 = shipped per-team cap × 2 seeded
    # teams — see docs/engine-concurrency.md) with a hard per-run deadline
    # (ENGINE_RUN_TIMEOUT_SECONDS, default 600) and a hung-provider circuit
    # breaker. The advertised per-team caps are real only up to THIS bound.
    service = OrchestratorService(
        session_factory=factory,
        bus=bus,
        max_per_team=int(os.environ.get("HARNESS_RUN_CONCURRENCY", "4")),
        engine_runtime=EngineRuntime(),
        engine=engine,
    )  # F8/H3: persisted + rehydrated; Phase 1 durable run queue
    configure_orchestrator(service)

    if service.run_queue_factory is not None:
        worker = RunQueueWorker(
            service,
            poll_interval=float(os.environ.get("HARNESS_WORKER_POLL_SECONDS", "0.5")),
        )

    from harness_orchestrator import domain_metrics as orchestrator_domain_metrics

    # Approval/cancel/isolation/event-delivery counters ride the scraped /metrics
    # (simple_app registers it first; a later install_metrics_endpoint is shadowed).
    app = simple_app(router, "harness-orchestrator", orchestrator_domain_metrics.render)
    # Abuse guard: run start plus the collaboration mutation routes get per-IP budgets
    # (delivery.MUTATION_ROUTE_LIMITS). Added before RequestContextMiddleware so a 429 still
    # carries a request id. A store failure fails OPEN.
    from harness_contracts.ratelimit import DEFAULT_PROTECTED_ROUTES

    from harness_orchestrator.delivery import MUTATION_ROUTE_LIMITS
    from harness_orchestrator.ratelimit_hooks import (
        ORCHESTRATOR_ROUTE_LIMITS,
        install_orchestrator_rate_limit,
    )

    install_orchestrator_rate_limit(
        app,
        session_factory=factory,
        routes=(*DEFAULT_PROTECTED_ROUTES, *MUTATION_ROUTE_LIMITS),
        route_limits={**ORCHESTRATOR_ROUTE_LIMITS, **MUTATION_ROUTE_LIMITS},
    )
    app.add_middleware(RequestContextMiddleware)  # X-Request-ID + access log + metrics
    # M20-T3: /ready gates on db + migrations (orchestrator, port 8004).
    _install_probes(app, 8004)

    # M20-T2 (OPS-21): domain series + uniform root-level /metrics so ONE
    # prometheus scrape target covers every service. The legacy
    # /api/v1/orchestrator/metrics route (orchestrator's own collector)
    # stays for compatibility with existing dashboards/tests.
    from observability import install_domain_metrics, install_metrics_endpoint

    install_metrics_endpoint(app)
    install_domain_metrics(
        app, factory=factory, engine=factory.kw["bind"], bus=bus
    )  # queue depth, event-bus lag, DB pool saturation, runs started/completed

    import asyncio

    from harness_orchestrator.execution import run_limit_sweeper
    from harness_orchestrator.schedule import maintenance_jobs, run_periodically
    from harness_orchestrator.session_gate import session_run_sweeper
    from harness_orchestrator.workspace_gate import workspace_run_sweeper

    background: list = []

    @app.on_event("startup")
    async def _start_run_queue_worker():
        worker.run_forever()
        # Part 44.005: a RUNNING run that reaches its time/model/tool-call limit is paused by this
        # worker's sweep (0 turns it off). Start and claim checks already refuse new work.
        every = float(os.environ.get("HARNESS_LIMIT_SWEEP_SECONDS", "30"))
        if every > 0:
            background.append(asyncio.create_task(run_limit_sweeper(service, every)))
        # Part 39.008: a run still executing when its workspace is archived/deleted is stopped.
        ws_every = float(os.environ.get("HARNESS_WORKSPACE_SWEEP_SECONDS", "30"))
        if ws_every > 0:
            background.append(asyncio.create_task(workspace_run_sweeper(service, ws_every)))
        # Part 40: a run still executing when its session is paused is paused, and when it is
        # completed, archived or deleted is stopped.
        ss_every = float(os.environ.get("HARNESS_SESSION_SWEEP_SECONDS", "30"))
        if ss_every > 0:
            background.append(asyncio.create_task(session_run_sweeper(service, ss_every)))
        # Search-index sync and the export, discussion and artifact retention purges
        # (HARNESS_*_SECONDS, 0 turns one off; see harness_orchestrator.schedule).
        for label, job, interval in maintenance_jobs(factory):
            background.append(asyncio.create_task(run_periodically(label, job, interval)))
        # Parts 11/29: append the audit chain head to an external sink the database role cannot
        # write (harness_identity.audit_anchor); HARNESS_AUDIT_ANCHOR_PATH unset turns it off.
        anchor_path = os.environ.get("HARNESS_AUDIT_ANCHOR_PATH", "")
        anchor_every = float(os.environ.get("HARNESS_AUDIT_ANCHOR_SECONDS", "300"))
        if anchor_path and anchor_every > 0:
            from harness_identity.audit_anchor import anchor_head

            background.append(asyncio.create_task(run_periodically(
                "audit-anchor", lambda: anchor_head(factory, anchor_path), anchor_every)))

    @app.on_event("shutdown")
    async def _stop_run_queue_worker():
        worker.stop()
        for task in background:
            task.cancel()
        # M17-B5: release the dedicated engine executor's queued work.
        service.shutdown()

    # ------------------------------------------------------------------
    # Wave 9 node D — email notifications via the durable outbox.
    #
    # Every committed bus envelope is tapped (EventBus.add_event_listener);
    # run-completion / review-failure / PR-opened events enqueue an
    # notification_outbox row through a fire-and-forget task — this NEVER
    # blocks or breaks the event flow. A background NotificationWorker drains
    # queued rows with backoff: real SMTP when HARNESS_SMTP_HOST is set,
    # visible dev-log + status=sent otherwise (CI stays offline-green).
    # Recipient = payload.notifyEmail override, else HARNESS_NOTIFY_EMAIL
    # (unset => notifications off, rows are simply never enqueued).
    # ------------------------------------------------------------------
    from harness_identity.notifier import (
        NotificationWorker,
        make_event_notifier,
    )

    _notify_settings = base_settings()
    app.state.event_notifier = make_event_notifier(factory, _notify_settings)
    bus.add_event_listener(app.state.event_notifier)
    _notification_worker = NotificationWorker(
        factory,
        settings=_notify_settings,
        poll_interval=float(os.environ.get("HARNESS_NOTIFY_POLL_SECONDS", "30")),
    )

    @app.on_event("startup")
    async def _start_notification_worker():
        _notification_worker.start()

    @app.on_event("shutdown")
    async def _stop_notification_worker():
        _notification_worker.stop()

    app.state.get_principal = make_bearer_dep(factory)  # F2: bearer auth on every route
    # M10-C2 (A4): team-scoped runs index for S7's runs page.
    from harness_orchestrator.runs_index import router as runs_index_router

    app.include_router(runs_index_router)

    # ------------------------------------------------------------------
    # M11-W1-C1: plan-gate API router. Sole include block for W1-C1 —
    # shares the orchestrator's service so plans minted by /plans are the
    # same objects approve/reject/startRun drive.
    # ------------------------------------------------------------------
    from harness_orchestrator.plan_gate import configure_plan_gate
    from harness_orchestrator.plan_gate import router as plan_gate_router

    configure_plan_gate(service)
    app.include_router(plan_gate_router)

    # ------------------------------------------------------------------
    # M19-N4 (C10): canned-demo seeder — POST /demo/seed replays the
    # recorded demo run for the caller's team, zero provider keys.
    # Shares the same service so seeded runs rehydrate like organic ones.
    # ------------------------------------------------------------------
    from harness_orchestrator.demo import router as demo_router

    app.include_router(demo_router)

    # Session 4 (parts 41, 42, 45): presence, participation, control leases, agent tree.
    # Session 5 (part 43): discussion (comments, mentions, promote, search, retention).
    from harness_orchestrator import (
        agent_tree,
        adapters,
        artifacts,
        assignment_plan,
        control,
        delegation,
        deployments,
        discussion,
        execution,
        exports,
        participation,
        plans,
        presence,
        retention_holds,
        search,
        shares,
        visuals,
    )

    presence.configure_presence(service)
    participation.configure_participation(service)
    control.configure_control(service)
    agent_tree.configure_agent_tree(service)
    discussion.configure_discussion(service)
    plans.configure_plans(service)
    execution.configure_execution(service)
    artifacts.configure_artifacts(service)
    search.configure_search(service)
    exports.configure_exports(service)
    for _r in (presence.router, participation.router, control.router, agent_tree.router,
               delegation.router, discussion.router, plans.router, execution.router, artifacts.router, search.router,
               exports.router, retention_holds.router, shares.router,
               deployments.router, visuals.router, assignment_plan.router, adapters.router):
        app.include_router(_r)

    # Part 38.005: org policy applies to every classified run/plan route of org-linked teams
    # (additive to authz.require_role; teams without an org are untouched).
    from harness_contracts.policy_enforce import ORCHESTRATOR_RULES, install_policy_gate

    async def _policy_principal(request):
        try:
            return await request.app.state.get_principal(request)
        except Exception:  # noqa: BLE001 - the route's own dependency answers 401
            return None

    install_policy_gate(app, ORCHESTRATOR_RULES, _policy_principal, lambda _r: factory)
    return app


def app_sandbox():
    from harness_contracts.events import EventType
    from harness_contracts.fastapi_auth import make_bearer_dep
    from harness_identity.eventbus import EventBus
    from harness_sandbox.api import configure_sandbox, router
    from harness_sandbox.docker_runner import DockerSandboxRunner
    from harness_sandbox.local_runner import LocalSandboxRunner
    from harness_sandbox.service import SandboxService
    from harness_sandbox.tree_api import router as tree_router  # M12-G8 read-only tree

    factory = get_factory()
    bus = EventBus(factory)

    def _publish_stream_event(etype, payload):
        """M2: terminal_output/diff_progress → S1 bus (projectId-scoped)."""
        project_id = payload.get("projectId")
        if not project_id:
            return
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # sync route without a loop: live push skipped, log still truth

        async def _pub():
            try:
                await bus.publish(EventType(etype), project_id, dict(payload), None)
            except Exception:  # noqa: BLE001 — telemetry never breaks execution
                pass

        loop.create_task(_pub())

    _conf = os.environ.get("HARNESS_SANDBOX_CONFINEMENT", "auto")
    # Fail closed for the deployed sandbox path. Network access remains an
    # explicit opt-in; the Docker runner confines it to the allow-list proxy.
    _net = os.environ.get("HARNESS_SANDBOX_ALLOW_NETWORK", "0") == "1"
    _use_docker = os.environ.get("SANDBOX_USE_DOCKER", "false").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    _docker_image = os.environ.get("SANDBOX_DEFAULT_DOCKER_IMAGE", "").strip() or None
    _runner = (
        DockerSandboxRunner(image=_docker_image or "", allow_network=_net)
        if _use_docker
        else LocalSandboxRunner(confinement=_conf, allow_network=_net)
    )
    configure_sandbox(
        SandboxService(
            on_stream=_publish_stream_event,
            runner=_runner,
        )
    )
    app = simple_app(router, "harness-sandbox")
    # M20-T2: request ids + JSON logs + /metrics (sandbox, port 8005).
    _wire_observability(app)
    # M20-T3: /ready gates on db + migrations (sandbox, port 8005).
    _install_probes(app, 8005, reports_egress=True)
    # M12-G8: mount the read-only tree router on the same app + bearer auth.
    app.include_router(tree_router)
    app.state.get_principal = make_bearer_dep(factory)

    # S1-R01d: project → owning team, for sandbox task/tree/checkpoint scoping.
    async def _project_team_lookup(project_id: str) -> str | None:
        from sqlalchemy import text

        async with factory() as db:
            row = (
                await db.execute(
                    text("SELECT team_id FROM projects WHERE id = :pid"),
                    {"pid": project_id},
                )
            ).first()
        return row[0] if row else None

    app.state.project_team_lookup = _project_team_lookup
    return app


def app_meeting():
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from harness_contracts.fastapi_auth import make_bearer_dep
    from harness_meeting.api import router

    factory = get_factory()
    app = FastAPI(title="harness-meeting")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # M20-T3: shared liveness (/health) + readiness (/ready) probes.
    _install_probes(app, 8006)

    app.state.get_principal = make_bearer_dep(factory)

    # S1-R01d: project → owning team, gating meeting creation per team.
    async def _project_team_lookup(project_id: str) -> str | None:
        from sqlalchemy import text

        async with factory() as db:
            row = (
                await db.execute(
                    text("SELECT team_id FROM projects WHERE id = :pid"),
                    {"pid": project_id},
                )
            ).first()
        return row[0] if row else None

    app.state.project_team_lookup = _project_team_lookup

    # S1-R02b: AI-participant key resolution gets the real vault + frozen
    # Section 1 api_keys store (dual-read KDF lives in S1's crypto module).
    from harness_identity.crypto import KeyVault as IdentityKeyVault
    from harness_meeting.api import configure_meeting_service
    from harness_meeting.service import MeetingService

    configure_meeting_service(
        MeetingService(
            team_key_store=factory,
            vault=IdentityKeyVault(MASTER_KEY),
            models_base_url=os.environ.get("HARNESS_MODELS_URL", "http://models:8003"),
            db_factory=factory,
        )
    )
    # Wave9-E (append): security headers on the meeting app (S6).
    from harness_identity.security_headers import install_security_headers

    install_security_headers(app)
    # M17-B3 / C-2: the meeting app answers the frozen error envelope like
    # every other deployed service (canonical middleware in harness_identity).
    from harness_identity.envelope_middleware import install_error_envelope

    install_error_envelope(app)
    # W15 slice 2: brief generation is LLM work and was unthrottled in the
    # deployed stack; the hook existed but app_meeting never installed it.
    from harness_meeting.ratelimit_hooks import install_meeting_rate_limit

    install_meeting_rate_limit(app, session_factory=factory)
    app.include_router(router)
    # M20-T2: request ids + JSON logs + /metrics (meeting, port 8006).
    _wire_observability(app)
    return app


def app_review():
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from harness_contracts import KeyVault, make_bearer_dep
    from harness_github.service import GitHubService
    from harness_identity.audit import write_audit as s1_write_audit
    from harness_review.api import router as review_router
    from harness_review.config import Settings as ReviewSettings
    from harness_review.service import ReviewService

    factory = get_factory()
    settings = ReviewSettings(
        database_url=DATABASE_URL, master_key=MASTER_KEY, cors_origins=CORS_ORIGINS
    )
    app = FastAPI(title="harness-review")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    vault = KeyVault(MASTER_KEY)

    async def diff_provider(
        project_id: str,
        pr_number: int,
        team_id: str | None = None,
        *,
        actor_user_id: str | None = None,
    ) -> str:
        # Diff arrives via Section 2's contract (getPRDiff); requires a
        # connected GitHub account for the ACTING reviewer — never a shared
        # "system" identity. team_id carries the review principal's team so
        # cross-team reads fail closed (S1-R01d).
        if not actor_user_id:
            raise PermissionError("review diff requires an authenticated user")
        async with factory() as db:
            svc = GitHubService(db, vault)
            data = await svc.getPRDiff(
                actor_user_id, project_id, pr_number, team_id=team_id
            )
            await db.close()
            return data["diff"]

    app.state.session_factory = factory
    app.state.settings = settings
    app.state.vault = vault
    app.state.get_principal = make_bearer_dep(factory)
    app.state.review_service = ReviewService(
        session_factory=factory,
        diff_provider=diff_provider,
        write_audit=s1_write_audit,
    )

    # M20-T3: shared liveness (/health) + readiness (/ready) probes.
    # app.state.session_factory was set above, so /ready gates for real.
    _install_probes(app, 8008)

    # Wave9-E (append): security headers on the review app (S8).
    from harness_identity.security_headers import install_security_headers

    install_security_headers(app)
    # M17-B3 / C-2: the review app answers the frozen error envelope like
    # every other deployed service. This also normalizes S8's blanket-except
    # routes that raise HTTPException(500, str(exc)) (P-BETA M-3): the raw
    # string becomes {"error": {"code": "REQUEST_ERROR", ...}} + mirror.
    from harness_identity.envelope_middleware import install_error_envelope

    install_error_envelope(app)
    app.include_router(review_router)
    # M20-T2: request ids + JSON logs + /metrics (review, port 8008).
    _wire_observability(app)
    return app


SERVICES = {
    "identity": ("app_identity", 8001),
    "github": ("app_github", 8002),
    "models": ("app_models", 8003),
    "orchestrator": ("app_orchestrator", 8004),
    "sandbox": ("app_sandbox", 8005),
    "meeting": ("app_meeting", 8006),
    "review": ("app_review", 8008),
}


# uvicorn --factory aliases (entrypoint references these names)
identity_app = app_identity
github_app = app_github
models_app = app_models
orchestrator_app = app_orchestrator
sandbox_app = app_sandbox
meeting_app = app_meeting
review_app = app_review
