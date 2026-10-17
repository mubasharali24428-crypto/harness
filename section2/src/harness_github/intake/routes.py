"""HTTP surface for intake: unauthenticated-by-bearer webhooks (signature-authenticated) and
principal-scoped management under /api/v1/developer-workflow/intake."""

import harness_contracts as contracts
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .service import IntakeError, IntakeService
from .store import public

PREFIX = f"{contracts.API_PREFIX}/developer-workflow/intake"


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": {"code": code, "message": message}})


def _role(principal) -> str:
    return str(getattr(principal.role, "value", principal.role)).lower()


def build_router(service_dep):
    from typing import Annotated

    from fastapi import Depends

    ServiceDep = Annotated[tuple, Depends(service_dep)]
    router = APIRouter(prefix=PREFIX)

    def svc(request: Request) -> IntakeService:
        return request.app.state.intake_service

    @router.post("/webhooks/{provider}/{registration_id}")
    async def webhook(provider: str, registration_id: str, request: Request):
        body = await request.body()
        out = await svc(request).handle_webhook(
            provider, registration_id, dict(request.headers), body
        )
        return JSONResponse(status_code=out.status, content=out.body)

    @router.post("/registrations", status_code=201)
    async def register(body: dict, request: Request, service: ServiceDep) -> dict:
        _, principal = service
        if _role(principal) != "admin":
            raise _err(403, "ROLE_DENIED", "only a team admin can register an intake webhook")
        try:
            return svc(request).register(
                team_id=principal.teamId,
                project_id=str(body.get("projectId") or ""),
                user_id=principal.userId,
                provider=str(body.get("provider") or ""),
                external_project=str(body.get("externalProject") or ""),
                label=str(body.get("label") or "harness"),
                api_token=body.get("apiToken") or None,
                api_base=body.get("apiBase") or None,
                signing_mode=bool(body.get("signing")),
            )
        except IntakeError as exc:
            raise _err(exc.status, exc.code, exc.message) from exc

    @router.get("/registrations")
    async def registrations(request: Request, service: ServiceDep) -> dict:
        _, principal = service
        return {"items": [public(r) for r in svc(request).store.registrations(principal.teamId)]}

    @router.get("/intakes")
    async def intakes(request: Request, service: ServiceDep, status: str | None = None) -> dict:
        _, principal = service
        return {"items": [public(i) for i in svc(request).store.intakes(principal.teamId, status)]}

    async def _decide(intake_id: str, request: Request, service, approve: bool) -> dict:
        _, principal = service
        if _role(principal) not in {"admin", "engineer"}:
            raise _err(403, "ROLE_DENIED", "your role cannot decide intakes")
        try:
            return svc(request).decide(intake_id, principal.teamId, principal.userId, approve)
        except IntakeError as exc:
            raise _err(exc.status, exc.code, exc.message) from exc

    @router.post("/intakes/{intake_id}/approve")
    async def approve(intake_id: str, request: Request, service: ServiceDep) -> dict:
        return await _decide(intake_id, request, service, True)

    @router.post("/intakes/{intake_id}/reject")
    async def reject(intake_id: str, request: Request, service: ServiceDep) -> dict:
        return await _decide(intake_id, request, service, False)

    return router
