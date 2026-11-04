"""Section 10 HTTP API — /api/v1/onboarding/...

Follows contracts/api-conventions.md: camelCase JSON bodies, error bodies shaped
{"error": {"code", "message"}}, actions as POST /resource/{action}.
"""

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from harness_contracts.http_client import env_client
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from .checklist import checklist_payload
from .demo_repo import manifest_payload
from .guided_demo import demo_payload
from .steps import STEPS, OnboardingStep, steps_payload
from .store import StateStore
from .telemetry import (
    ExplicitOptInRequiredError,
    FullCollectionNotEnabledError,
    TelemetryEvent,
    TelemetrySettings,
    TelemetrySettingsUpdate,
    apply_update,
    validate_event,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ONBOARDING_", env_file=".env", extra="ignore")

    state_path: str = "./onboarding_state.json"
    cors_origins: list[str] = ["http://localhost:3000"]
    probe_timeout_seconds: float = 1.5
    identity_url: str = "http://localhost:8001"
    github_url: str = "http://localhost:8100"
    meeting_url: str = ""  # empty = section 6 not deployed yet
    orchestrator_url: str = "http://localhost:8004"


SERVICE_PROBES = {
    "identity": ("Identity & event bus (Section 1)", "identity_url", "/health"),
    "github": ("GitHub integration (Section 2)", "github_url", "/health"),
    "meeting": ("Meeting Area (Section 6)", "meeting_url", "/health"),
    "orchestrator": ("Orchestration engine (Section 4)", "orchestrator_url", "/health"),
    "onboarding": ("Onboarding (Section 10)", None, None),  # self — always up
}

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])


async def probe_available(client: httpx.AsyncClient, url: str, health_path: str) -> bool:
    """True when the service answers its health endpoint with < 500."""
    try:
        resp = await client.get(url.rstrip("/") + health_path)
        return resp.status_code < 500
    except httpx.HTTPError:
        return False


@router.get("/steps")
async def get_steps() -> dict:
    return {"items": steps_payload()}


# ---------------------------------------------------------------------------
# M10-B3 — GET /checklist: the interactive first-run checklist.
#
# Derived from steps.py's frozen order plus whatever real completion signal
# Section 10 actually persists today (StateStore). Honest done semantics — we
# never pretend a step completed when nothing recorded it:
#   * "other_integrations"  → done (required=False, purely informational;
#                             brief §11 says "None required beyond GitHub").
#   * "guided_demo"         → done once the security checklist was
#                             acknowledged (POST /security-checklist/
#                             acknowledge) — the one completion action this
#                             section owns end-to-end right now.
#   * everything else       → done=False until a section-owned signal exists
#                             (GitHub OAuth lives in S2, teams/members in S1,
#                             meetings in S6). Flipping these on later means
#                             extending derive_done, not faking state here.
# ---------------------------------------------------------------------------


class ChecklistItem(BaseModel):
    """Wire shape of one checklist row: {id, title, description?, done}."""

    id: str
    title: str
    description: str | None = None
    done: bool


def derive_done(step_id: str, *, security_checklist_acknowledged: bool) -> bool:
    """Whether an onboarding step counts as complete. Pure — see block above."""
    if step_id == "other_integrations":
        return True  # informational step, explicitly not required
    if step_id == "guided_demo":
        return security_checklist_acknowledged
    return False


def build_checklist_items(
    *,
    security_checklist_acknowledged: bool,
    steps: list[OnboardingStep] | None = None,
) -> list[dict]:
    """Ordered checklist payload derived from steps.py state. Pure."""
    ordered = sorted(steps if steps is not None else STEPS, key=lambda s: s.order)
    items: list[dict] = []
    for step in ordered:
        done = derive_done(step.id, security_checklist_acknowledged=security_checklist_acknowledged)
        items.append(
            ChecklistItem(
                id=step.id,
                title=step.title,
                description=step.description,
                done=done,
            ).model_dump()
        )
    return items


@router.get("/checklist")
async def get_checklist(request: Request) -> dict:
    state = request.app.state.store.load()
    return {
        "items": build_checklist_items(
            security_checklist_acknowledged=state["securityChecklistAcknowledged"],
        )
    }


@router.get("/state")
async def get_state(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    services = []
    async with env_client(timeout=settings.probe_timeout_seconds) as client:
        for key, (name, url_field, health_path) in SERVICE_PROBES.items():
            if url_field is None:
                services.append({"key": key, "name": name, "url": "self", "available": True})
                continue
            url = getattr(settings, url_field)
            if not url:
                services.append({"key": key, "name": name, "url": "", "available": False})
                continue
            available = await probe_available(client, url, health_path)
            services.append({"key": key, "name": name, "url": url, "available": available})

    by_key = {s["key"]: s["available"] for s in services}
    steps = []
    for step in STEPS:
        item = step.model_dump()
        item["available"] = by_key.get(step.serviceKey, True) if step.serviceKey else True
        steps.append(item)

    state = request.app.state.store.load()
    return {
        "services": services,
        "steps": steps,
        "securityChecklistAcknowledged": state["securityChecklistAcknowledged"],
        "telemetry": state["telemetry"],
    }


@router.get("/security-checklist")
async def get_security_checklist() -> dict:
    return checklist_payload()


@router.post("/security-checklist/acknowledge")
async def acknowledge_checklist(request: Request) -> dict:
    state = request.app.state.store.update(
        lambda s: s.__setitem__("securityChecklistAcknowledged", True)
    )
    return {"securityChecklistAcknowledged": state["securityChecklistAcknowledged"]}


@router.get("/telemetry-settings")
async def get_telemetry_settings(request: Request) -> dict:
    return request.app.state.store.load()["telemetry"]


@router.put("/telemetry-settings")
async def put_telemetry_settings(request: Request, body: TelemetrySettingsUpdate) -> dict:
    def mutate(state: dict) -> None:
        current = TelemetrySettings(**state["telemetry"])
        try:
            updated = apply_update(current, body)
        except ExplicitOptInRequiredError as e:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": "EXPLICIT_OPT_IN_REQUIRED", "message": str(e)}},
            ) from e
        state["telemetry"] = updated.model_dump()

    return request.app.state.store.update(mutate)["telemetry"]


@router.post("/telemetry/events", status_code=201)
async def post_telemetry_event(request: Request, body: TelemetryEvent) -> dict:
    store: StateStore = request.app.state.store
    record_holder: dict = {}

    def mutate(state: dict) -> None:
        current = TelemetrySettings(**state["telemetry"])
        try:
            event = validate_event(current, body)
        except FullCollectionNotEnabledError as e:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": "FULL_COLLECTION_NOT_ENABLED", "message": str(e)}},
            ) from e
        except ValueError as e:
            raise HTTPException(
                status_code=422,
                detail={"error": {"code": "UNKNOWN_TELEMETRY_KIND", "message": str(e)}},
            ) from e
        record = event.model_dump()
        state["telemetryEvents"].append(record)
        record_holder["record"] = record

    store.update(mutate)
    return {"accepted": True, "stored": record_holder["record"]}


@router.get("/guided-demo")
async def get_guided_demo() -> dict:
    return demo_payload()


@router.get("/demo-repo/manifest")
async def get_demo_repo_manifest() -> dict:
    return manifest_payload()


def make_app(settings: Settings | None = None, store: StateStore | None = None) -> FastAPI:
    app = FastAPI(title="harness-onboarding", version="0.1.0")
    resolved = settings or Settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = resolved
    app.state.store = store or StateStore(resolved.state_path)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "service": "harness-onboarding"}

    app.include_router(router)
    return app
