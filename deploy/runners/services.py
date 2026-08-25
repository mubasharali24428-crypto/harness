"""Assembly runners: one FastAPI app per backend service, wired at deploy time."""

import json
import os
import sys
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[2]
for section in sorted((HARNESS_ROOT).glob("section*")):
    src = section / "src"
    if src.exists():
        sys.path.insert(0, str(src))

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


def simple_app(router, title):
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

    @app.get("/health")
    async def health():
        return {"ok": True}

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

    return app


def app_github():
    from harness_github.app import make_app

    return make_app(get_factory(), base_settings())


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

    app = simple_app(router, "harness-orchestrator")
    app.add_middleware(RequestContextMiddleware)  # X-Request-ID + access log + metrics

    @app.on_event("startup")
    async def _start_run_queue_worker():
        worker.run_forever()

    @app.on_event("shutdown")
    async def _stop_run_queue_worker():
        worker.stop()
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
    return app


def app_sandbox():
    from harness_contracts.events import EventType
    from harness_contracts.fastapi_auth import make_bearer_dep
    from harness_identity.eventbus import EventBus
    from harness_sandbox.api import configure_sandbox, router
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

    configure_sandbox(SandboxService(on_stream=_publish_stream_event))
    app = simple_app(router, "harness-sandbox")
    # M12-G8: mount the read-only tree router on the same app + bearer auth.
    app.include_router(tree_router)
    app.state.get_principal = make_bearer_dep(factory)
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

    @app.get("/health")
    async def health():
        return {"ok": True}

    app.state.get_principal = make_bearer_dep(factory)
    # Wave9-E (append): security headers on the meeting app (S6).
    from harness_identity.security_headers import install_security_headers

    install_security_headers(app)
    # M17-B3 / C-2: the meeting app answers the frozen error envelope like
    # every other deployed service (canonical middleware in harness_identity).
    from harness_identity.envelope_middleware import install_error_envelope

    install_error_envelope(app)
    app.include_router(router)
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

    async def diff_provider(project_id: str, pr_number: int) -> str:
        # Diff arrives via Section 2's contract (getPRDiff); requires a
        # connected GitHub account for the acting user.
        async with factory() as db:
            svc = GitHubService(db, vault)
            data = await svc.getPRDiff("system", project_id, pr_number)
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

    @app.get("/health")
    async def health():
        return {"ok": True}

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
