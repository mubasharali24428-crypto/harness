"""Section 11 — FastAPI surface for the Context Pillar.

Endpoints (all under contracts.API_PREFIX = /api/v1, per api-conventions.md):

  POST /context/index             — start an indexing job {repoPath, projectId}
  GET  /context/index/{job_id}    — job status (filesIndexed/chunksIndexed/
                                    filesSkipped/filesPruned/error)
  POST /context/search            — W21-C1 frozen query API
                                    {query, projectId, topK} -> scored chunks

M22-M2: a plan-tier cap hit on the index answers HTTP 402 whose body carries
the frozen error envelope PLUS the structured ``upgrade`` CTA block
({tier, cap, capField, used, upgradeTo, cta}) — an upgrade action, never a
bare dead-end denial.

Auth: every route requires a resolved Principal via app.state.get_principal
(bearer dep over Section 1's frozen schema), mirroring harness_review's
fail-closed pattern. Gated access is proven in tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import harness_contracts as contracts
from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from harness_contracts import make_bearer_dep

from .config import Settings
from .embeddings import EmbeddingBackend, make_backend
from .models import IndexRequest, IndexStatus, SearchRequest, SearchResponse
from .service import ContextService, RepoIndexError, TierCapError

if TYPE_CHECKING:
    from harness_contracts.roles import Principal
    from sqlalchemy.ext.asyncio import async_sessionmaker

router = APIRouter(prefix=contracts.API_PREFIX)


async def get_principal(request: Request) -> Principal:
    """Auth resolution via app.state.get_principal; fail closed (D6/P0.6)."""
    dep = getattr(request.app.state, "get_principal", None)
    if dep is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"code": "AUTH_NOT_CONFIGURED", "message": "auth not wired"}},
        )
    return await dep(request)


def _svc(request: Request) -> ContextService:
    return request.app.state.context_service


@router.get("/health")
async def health():
    """Liveness only — no auth, no DB (compose healthcheck contract)."""
    return {"ok": True}


@router.get("/healthz")
async def healthz(request: Request) -> dict:
    await _require_auth(request)
    return {"ok": True}


@router.post("/context/index", response_model=None)
async def start_index(body: IndexRequest, request: Request):
    await _require_auth(request)
    try:
        return await _svc(request).start_index(body.repo_path, body.project_id)
    except RepoIndexError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TierCapError as exc:
        # M22-M2: a tier cap hit is NOT a dead end. Answer 402 with the
        # frozen error envelope PLUS the structured upgrade block
        # {tier, cap, capField, used, upgradeTo, cta} at the TOP level of
        # the body (JSONResponse keeps FastAPI from nesting it under
        # ``detail``) so the UI renders an upgrade action from the contract
        # shape alone.
        return JSONResponse(status_code=402, content=exc.wire)


@router.get("/context/index/{job_id}", response_model=IndexStatus)
async def index_status(job_id: str, request: Request) -> IndexStatus:
    await _require_auth(request)
    st = _svc(request).get_job(job_id)
    if st is None:
        raise HTTPException(status_code=404, detail=f"unknown index job: {job_id}")
    return st


@router.post("/context/search", response_model=SearchResponse)
async def search(body: SearchRequest, request: Request) -> SearchResponse:
    await _require_auth(request)
    svc = _svc(request)
    async with svc.session_factory() as session:
        results = await svc.search(
            await session.connection(), body.query, body.project_id, body.top_k
        )
    return SearchResponse(project_id=body.project_id, query=body.query, results=results)


async def _require_auth(request: Request) -> None:
    """Routes declare no principal param; enforce resolution explicitly so an
    unwired auth state fails closed instead of serving anonymous traffic.
    Resolution itself raises 401 on missing/invalid/expired bearer tokens."""
    dep = getattr(request.app.state, "get_principal", None)
    if dep is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "UNAUTHORIZED", "message": "auth not wired"}},
        )
    await dep(request)


def make_app(
    session_factory: async_sessionmaker,
    settings: Settings,
    backend: EmbeddingBackend | None = None,
):
    """Build the FastAPI app.

    session_factory: async_sessionmaker over any SQLAlchemy async engine
    (asyncpg on PG, aiosqlite in tests). backend: embedding implementation;
    defaults to Settings.embedding_backend ("ollama" locally).
    """


    app = FastAPI(title="harness-context", version="1.0.0rc1")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.session_factory = session_factory
    app.state.settings = settings

    get_bearer = make_bearer_dep(session_factory)

    async def resolve_principal(request: Request) -> Principal:
        return await get_bearer(request)

    app.state.get_principal = resolve_principal

    if backend is None:
        backend = make_backend(
            settings.embedding_backend,
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
        )
    app.state.context_service = ContextService(
        session_factory,
        backend,
        window=settings.chunk_window,
        overlap=settings.chunk_overlap,
    )

    @app.get("/health")
    async def root_health():
        # Unauthenticated liveness for deploy healthchecks (compose convention).
        return {"ok": True}

    app.include_router(router)
    return app
