"""FastAPI app for the developer-workflow lane (A4, prefix /api/v1/developer-workflow).

Conventions: bearer identity via Section 1 `/api/v1/me` (fail closed), frozen
error envelope {"error": {"code", "message"}}, camelCase bodies, UUID-ish ids.
Operation endpoints (actions, verify) use the predeclared HAP/1 envelope;
resource endpoints use plain REST.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from harness_devworkflow.clients import (
    ContextClient,
    IdentityClient,
    OrchestratorClient,
    SandboxClient,
    UpstreamError,
    UpstreamUnreachable,
)
from harness_devworkflow.config import Config
from harness_devworkflow.service import DevWorkflowService, LaneError
from harness_devworkflow.store import Store
from harness_devworkflow.workspace import WorkspaceError, WorkspaceManager

PREFIX = "/api/v1/developer-workflow"
SCHEMA_VERSION = "hap/1"


_SNAKE = re.compile(r"_([a-z0-9])")
_RENAMES = {"cs_id": "changeSetId"}


def camelize(value: Any) -> Any:
    """Convert snake_case keys to the camelCase wire convention (values untouched)."""
    if isinstance(value, dict):
        return {
            _RENAMES.get(
                k, _SNAKE.sub(lambda m: m.group(1).upper(), k) if isinstance(k, str) else k
            ): camelize(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [camelize(v) for v in value]
    return value


class CamelJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return json.dumps(camelize(content), ensure_ascii=False, separators=(",", ":")).encode()


def _error_response(status: int, code: str, message: str, detail: Any = None) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if detail is not None:
        body["error"]["detail"] = detail
    return JSONResponse(status_code=status, content=body)


def _hap_response(
    request_id: str,
    status: str,
    result: Any | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "requestId": request_id,
        "status": status,
        "artifacts": [],
    }
    if result is not None:
        body["result"] = result
    if error is not None:
        body["error"] = error
    return body


def build_service(config: Config, http: httpx.AsyncClient | None = None) -> DevWorkflowService:
    """Wire clients from config. Unset bases mean the capability is absent."""
    http = http or httpx.AsyncClient(timeout=config.request_timeout_s)
    return DevWorkflowService(
        config=config,
        store=Store(config.data_dir / "devworkflow.sqlite3"),
        workspaces=WorkspaceManager(config.data_dir, config.repo_roots),
        identity=IdentityClient(config.identity_base, http) if config.identity_base else None,
        sandbox=SandboxClient(config.sandbox_base, http) if config.sandbox_base else None,
        orchestrator=(
            OrchestratorClient(config.orchestrator_base, http) if config.orchestrator_base else None
        ),
        context=ContextClient(config.context_base, http) if config.context_base else None,
    )


def create_app(config: Config, service: DevWorkflowService | None = None) -> FastAPI:
    service = service or build_service(config)
    app = FastAPI(
        title="harness developer-workflow (A4)",
        version="0.1.0",
        default_response_class=CamelJSONResponse,
    )

    if config.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.allowed_origins),
            allow_methods=["*"],
            allow_headers=["authorization", "content-type"],
        )

    async def principal_dep(request: Request) -> dict[str, Any]:
        authorization = request.headers.get("authorization", "")
        if not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": "UNAUTHORIZED", "message": "bearer token required"}},
            )
        token = authorization[7:].strip()
        if not token:
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": "UNAUTHORIZED", "message": "bearer token required"}},
            )
        principal = await service.resolve_principal(token)
        if principal is None:
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": "UNAUTHORIZED", "message": "invalid session token"}},
            )
        request.state.token = token
        return principal

    def _token(request: Request) -> str:
        return str(request.state.token)

    @app.exception_handler(LaneError)
    async def _lane_error(_: Request, exc: LaneError) -> JSONResponse:
        return _error_response(exc.status, exc.code, exc.message, exc.detail)

    @app.exception_handler(WorkspaceError)
    async def _workspace_error(_: Request, exc: WorkspaceError) -> JSONResponse:
        return _error_response(422, "WORKSPACE_ERROR", str(exc))

    @app.exception_handler(UpstreamError)
    async def _upstream_error(_: Request, exc: UpstreamError) -> JSONResponse:
        status = 502 if isinstance(exc, UpstreamUnreachable) else min(exc.status, 502)
        return _error_response(status, exc.code, f"{exc.service}: {exc.message}")

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=exc.status_code, content={"error": detail["error"]})
        return _error_response(exc.status_code, "HTTP_ERROR", str(detail))

    # -- health / capabilities ----------------------------------------------

    @app.get(f"{PREFIX}/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "schemaVersion": SCHEMA_VERSION}

    @app.get(f"{PREFIX}/capabilities")
    async def capabilities(
        request: Request,
        principal: dict = Depends(principal_dep),  # noqa: B008
        projectId: str | None = Query(default=None),
    ) -> dict[str, Any]:
        return await service.capabilities(_token(request), principal, projectId)

    # -- code sessions -------------------------------------------------------

    @app.post(f"{PREFIX}/code-sessions", status_code=201)
    async def create_code_session(
        request: Request,
        body: dict[str, Any],
        principal: dict = Depends(principal_dep),  # noqa: B008
    ) -> dict[str, Any]:
        return await service.create_session(_token(request), principal, body)

    @app.get(f"{PREFIX}/code-sessions")
    async def list_code_sessions(
        request: Request,
        principal: dict = Depends(principal_dep),  # noqa: B008
        projectId: str | None = Query(default=None),
    ) -> dict[str, Any]:
        return {"items": service.list_sessions(principal, projectId)}

    @app.get(f"{PREFIX}/code-sessions/{{session_id}}")
    async def get_code_session(
        session_id: str,
        request: Request,
        principal: dict = Depends(principal_dep),  # noqa: B008
    ) -> dict[str, Any]:
        return service._session_scoped(session_id, principal["teamId"])

    @app.post(f"{PREFIX}/code-sessions/{{session_id}}/close")
    async def close_code_session(
        session_id: str,
        request: Request,
        principal: dict = Depends(principal_dep),  # noqa: B008
    ) -> dict[str, Any]:
        service.close_session(session_id, principal)
        return {"sessionId": session_id, "status": "closed"}

    # -- files ---------------------------------------------------------------

    @app.get(f"{PREFIX}/code-sessions/{{session_id}}/tree")
    async def session_tree(
        session_id: str,
        request: Request,
        principal: dict = Depends(principal_dep),  # noqa: B008
        path: str = Query(default=""),
    ) -> dict[str, Any]:
        return service.list_tree(session_id, principal, path)

    @app.get(f"{PREFIX}/code-sessions/{{session_id}}/file")
    async def session_file(
        session_id: str,
        request: Request,
        principal: dict = Depends(principal_dep),  # noqa: B008
        path: str = Query(...),
    ) -> dict[str, Any]:
        return service.read_file(session_id, principal, path)

    @app.put(f"{PREFIX}/code-sessions/{{session_id}}/file")
    async def session_file_write(
        session_id: str,
        request: Request,
        body: dict[str, Any],
        principal: dict = Depends(principal_dep),  # noqa: B008
    ) -> dict[str, Any]:
        return service.write_file(
            session_id,
            principal,
            str(body.get("path") or ""),
            str(body.get("content") or ""),
            body.get("baseSha256"),
        )

    # -- change sets ---------------------------------------------------------

    @app.post(f"{PREFIX}/code-sessions/{{session_id}}/change-sets", status_code=201)
    async def create_change_set(
        session_id: str,
        request: Request,
        body: dict[str, Any],
        principal: dict = Depends(principal_dep),  # noqa: B008
    ) -> dict[str, Any]:
        return service.create_change_set(session_id, principal, body)

    @app.get(f"{PREFIX}/code-sessions/{{session_id}}/change-sets")
    async def list_change_sets(
        session_id: str,
        request: Request,
        principal: dict = Depends(principal_dep),  # noqa: B008
    ) -> dict[str, Any]:
        return {"items": service.list_change_sets(session_id, principal)}

    @app.get(f"{PREFIX}/change-sets/{{cs_id}}")
    async def get_change_set(
        cs_id: str,
        request: Request,
        principal: dict = Depends(principal_dep),  # noqa: B008
    ) -> dict[str, Any]:
        return service.get_change_set(cs_id, principal)

    @app.post(f"{PREFIX}/change-sets/{{cs_id}}/decisions")
    async def change_set_decisions(
        cs_id: str,
        request: Request,
        body: dict[str, Any],
        principal: dict = Depends(principal_dep),  # noqa: B008
    ) -> dict[str, Any]:
        decisions = body.get("decisions")
        if not isinstance(decisions, list):
            raise LaneError("INVALID_REQUEST", "decisions must be a list", 422)
        return service.decide_change_set(cs_id, principal, decisions)

    @app.post(f"{PREFIX}/change-sets/{{cs_id}}/apply")
    async def change_set_apply(
        cs_id: str,
        request: Request,
        body: dict[str, Any] | None = None,
        principal: dict = Depends(principal_dep),  # noqa: B008
    ) -> dict[str, Any]:
        idem = (body or {}).get("idempotencyKey")
        return service.apply_change_set(cs_id, principal, idem)

    @app.post(f"{PREFIX}/change-sets/{{cs_id}}/cancel")
    async def change_set_cancel(
        cs_id: str,
        request: Request,
        principal: dict = Depends(principal_dep),  # noqa: B008
    ) -> dict[str, Any]:
        return service.cancel_change_set(cs_id, principal)

    # -- sync / verify / evidence -------------------------------------------

    @app.post(f"{PREFIX}/code-sessions/{{session_id}}/sync")
    async def session_sync(
        session_id: str,
        request: Request,
        principal: dict = Depends(principal_dep),  # noqa: B008
    ) -> dict[str, Any]:
        return await service.sync_session(session_id, _token(request), principal)

    @app.post(f"{PREFIX}/code-sessions/{{session_id}}/verify")
    async def session_verify(
        session_id: str,
        request: Request,
        body: dict[str, Any],
        principal: dict = Depends(principal_dep),  # noqa: B008
    ) -> dict[str, Any]:
        if body.get("schemaVersion") != SCHEMA_VERSION:
            raise LaneError("INVALID_REQUEST", "schemaVersion must be 'hap/1'", 422)
        request_id = str(body.get("requestId") or uuid.uuid4())
        arguments = body.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise LaneError("INVALID_REQUEST", "arguments must be an object", 422)
        try:
            result = await service.verify(
                session_id,
                _token(request),
                principal,
                str(arguments.get("command") or ""),
                body.get("idempotencyKey"),
            )
            return _hap_response(request_id, "succeeded", result=result)
        except LaneError as exc:
            status = {"CAPABILITY_UNAVAILABLE": "unsupported"}.get(exc.code, "failed")
            if exc.code in ("FORBIDDEN_PATH", "ROLE_DENIED", "INVALID_REQUEST", "INVALID_PATH"):
                status = "denied" if exc.status in (401, 403) else "failed"
            return _hap_response(
                request_id, status, error={"code": exc.code, "message": exc.message}
            )
        except UpstreamError as exc:
            hap_status = "denied" if exc.status == 403 else "failed"
            return _hap_response(
                request_id, hap_status, error={"code": exc.code, "message": exc.message}
            )

    @app.get(f"{PREFIX}/code-sessions/{{session_id}}/evidence")
    async def session_evidence(
        session_id: str,
        request: Request,
        principal: dict = Depends(principal_dep),  # noqa: B008
    ) -> dict[str, Any]:
        return {"items": service.list_evidence(session_id, principal)}

    # -- comments ------------------------------------------------------------

    @app.post(f"{PREFIX}/code-sessions/{{session_id}}/comments", status_code=201)
    async def session_comment_add(
        session_id: str,
        request: Request,
        body: dict[str, Any],
        principal: dict = Depends(principal_dep),  # noqa: B008
    ) -> dict[str, Any]:
        return service.add_comment(session_id, principal, body)

    @app.get(f"{PREFIX}/code-sessions/{{session_id}}/comments")
    async def session_comments(
        session_id: str,
        request: Request,
        principal: dict = Depends(principal_dep),  # noqa: B008
        path: str | None = Query(default=None),
    ) -> dict[str, Any]:
        return {"items": service.list_comments(session_id, principal, path)}

    @app.delete(f"{PREFIX}/comments/{{comment_id}}")
    async def comment_delete(
        comment_id: str,
        request: Request,
        principal: dict = Depends(principal_dep),  # noqa: B008
    ) -> dict[str, Any]:
        service.delete_comment(comment_id, principal)
        return {"commentId": comment_id, "status": "deleted"}

    # -- code actions (HAP/1) ------------------------------------------------

    @app.post(f"{PREFIX}/code-sessions/{{session_id}}/actions")
    async def session_action(
        session_id: str,
        request: Request,
        body: dict[str, Any],
        principal: dict = Depends(principal_dep),  # noqa: B008
    ) -> dict[str, Any]:
        if body.get("schemaVersion") != SCHEMA_VERSION:
            raise LaneError("INVALID_REQUEST", "schemaVersion must be 'hap/1'", 422)
        request_id = str(body.get("requestId") or uuid.uuid4())
        operation = str(body.get("operation") or "")
        arguments = body.get("arguments") or {}
        if operation not in ("references", "blame", "add-tests", "explain", "refactor"):
            return _hap_response(
                request_id,
                "unsupported",
                error={"code": "UNSUPPORTED_OPERATION", "message": f"unknown action '{operation}'"},
            )
        if not isinstance(arguments, dict):
            return _hap_response(
                request_id,
                "failed",
                error={"code": "INVALID_REQUEST", "message": "arguments must be an object"},
            )
        try:
            outcome = await service.run_action(
                session_id, _token(request), principal, operation, arguments
            )
        except LaneError as exc:
            hap_status = "denied" if exc.status in (401, 403) else "failed"
            return _hap_response(
                request_id, hap_status, error={"code": exc.code, "message": exc.message}
            )
        except UpstreamError as exc:
            return _hap_response(
                request_id,
                "denied" if exc.status == 403 else "failed",
                error={"code": exc.code, "message": exc.message},
            )
        return _hap_response(
            request_id,
            outcome["outcome"],
            result=outcome.get("result"),
            error=outcome.get("error"),
        )

    # -- merge checks --------------------------------------------------------

    @app.get(f"{PREFIX}/code-sessions/{{session_id}}/merge-checks")
    async def session_merge_checks(
        session_id: str,
        request: Request,
        principal: dict = Depends(principal_dep),  # noqa: B008
    ) -> dict[str, Any]:
        return await service.merge_checks(session_id, _token(request), principal)

    return app


def app_from_env() -> FastAPI:
    """Factory for `uvicorn harness_devworkflow.app:app_from_env --factory` (no import-time I/O)."""
    return create_app(Config.from_env())
