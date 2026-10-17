"""Section 11 — FastAPI surface for the Context Pillar.

Endpoints (all under contracts.API_PREFIX = /api/v1, per api-conventions.md):

  POST /context/index             — start an indexing job {repoPath, projectId}
  GET  /context/index/{job_id}    — job status (filesIndexed/chunksIndexed/
                                    filesSkipped/filesPruned/error)
  POST /context/search            — W21-C1 frozen query API
                                    {query, projectId, topK} -> scored chunks
  POST /context/search-multi      — one query across up to 10 projects (F013)
  POST /context/rank              — goal -> token-budgeted ranked bundle (F016)
  POST /context/git-search        — bounded git-history retrieval (F012)
  GET  /context/freshness/{project_id}   — content-addressed freshness (F011)
  GET  /context/graph/{project_id}       — dependency graph (F014)
  GET  /context/report/graph/{project_id}— standalone HTML graph report (F014)
  GET  /context/symbols/{project_id}     — symbol map / find-references (F015)

M22-M2: a plan-tier cap hit on the index answers HTTP 402 whose body carries
the frozen error envelope PLUS the structured ``upgrade`` CTA block
({tier, cap, capField, used, upgradeTo, cta}) — an upgrade action, never a
bare dead-end denial.

Auth: every route requires a resolved Principal via app.state.get_principal
(bearer dep over Section 1's frozen schema), mirroring harness_review's
fail-closed pattern. Gated access is proven in tests.

Authz (this session): every route ALSO proves project ownership via
``authz.authorize_project`` (projects.team_id == principal.teamId) BEFORE any
work. Unknown project and foreign-team project both answer one no-oracle
404; an unverifiable check answers 403 AUTHZ_UNAVAILABLE — fail closed.
"""

from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING

import harness_contracts as contracts
from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from harness_contracts import make_bearer_dep

from .authz import AuthzUnavailableError, ProjectAccessDenied, authorize_project
from .config import Settings
from .embeddings import EmbeddingBackend, make_backend
from .git_history import InvalidRepoPathError, search_history
from .graph import build_dependency_graph
from .models import (
    CommitFileModel,
    DocIndexRequest,
    GitSearchRequest,
    GitSearchResponse,
    HistoryCommitModel,
    IndexRequest,
    IndexStatus,
    MultiSearchRequest,
    MultiSearchResponse,
    ProjectResults,
    RankRequest,
    SearchRequest,
    SearchResponse,
    SymbolReferenceModel,
    SymbolsResponse,
)
from .report import render_dependency_graph_html
from .service import ContextService, RepoIndexError, TierCapError
from .symbols import find_symbol, list_symbols

if TYPE_CHECKING:
    from harness_contracts.roles import Principal
    from sqlalchemy.ext.asyncio import async_sessionmaker

router = APIRouter(prefix=contracts.API_PREFIX)

#: One indistinguishable text for both "no such project" and "not your
#: project" — probing project ids across team boundaries must stay blind.
_NO_ORACLE_404 = {"error": {"code": "NOT_FOUND", "message": "project not found"}}


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


async def _principal_or_401(request: Request) -> Principal:
    """Resolve the principal or fail closed with 401 (no app wiring)."""
    dep = getattr(request.app.state, "get_principal", None)
    if dep is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "UNAUTHORIZED", "message": "auth not wired"}},
        )
    return await dep(request)


async def _authorize_principal(request: Request, principal: Principal, project_id: str) -> None:
    """Prove project ownership for an ALREADY-resolved principal.

    Unknown and foreign-team projects share one no-oracle 404; an
    unevaluable check denies with 403 AUTHZ_UNAVAILABLE.
    """
    factory = getattr(request.app.state, "session_factory", None)
    try:
        await authorize_project(factory, principal, project_id)
    except ProjectAccessDenied as exc:
        raise HTTPException(status_code=404, detail=_NO_ORACLE_404) from exc
    except AuthzUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {"code": "AUTHZ_UNAVAILABLE", "message": str(exc.reason)}
            },
        ) from exc


async def _authorize(request: Request, project_id: str) -> Principal:
    """Resolve the principal AND prove project ownership (fail closed)."""
    principal = await _principal_or_401(request)
    await _authorize_principal(request, principal, project_id)
    return principal


def _no_indexed_repo(project_id: str) -> HTTPException:
    """404 for filesystem-dependent reads with no known indexed root."""
    return HTTPException(
        status_code=404,
        detail={
            "error": {
                "code": "NO_INDEXED_REPO",
                "message": f"no indexed repo root recorded for project '{project_id}'",
            }
        },
    )


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
    await _authorize(request, body.project_id)
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


@router.post("/context/docs/index", response_model=None)
async def index_docs(body: DocIndexRequest, request: Request):
    """F020: ingest documentation from an explicit folder path or URL list.

    Exactly one source per request; unauthenticated callers are denied BEFORE
    any filesystem or network work; a tier cap answers the same structured
    402 as repo indexing.
    """
    await _authorize(request, body.project_id)
    has_folder = body.folder_path is not None
    has_urls = bool(body.urls)
    if has_folder == has_urls:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "INVALID_SOURCE",
                    "message": "provide exactly one of folderPath or urls",
                }
            },
        )
    for url in body.urls or []:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": {
                        "code": "INVALID_URL",
                        "message": f"only http(s) document URLs are accepted: {url}",
                    }
                },
            )
    try:
        report = await _svc(request).index_docs(
            body.project_id, folder=body.folder_path, urls=body.urls
        )
    except TierCapError as exc:
        return JSONResponse(status_code=402, content=exc.wire)
    return {"projectId": body.project_id, **report.to_wire()}


@router.get("/context/index/{job_id}", response_model=IndexStatus)
async def index_status(job_id: str, request: Request) -> IndexStatus:
    # Authenticate FIRST — an unauthenticated caller must not be able to
    # probe job-id existence by observing 404/401 differences. Then resolve
    # the job (404 for genuinely unknown ids), then prove ownership of the
    # job's project BEFORE any of its progress is returned.
    principal = await _principal_or_401(request)
    st = _svc(request).get_job(job_id)
    if st is None:
        raise HTTPException(status_code=404, detail=f"unknown index job: {job_id}")
    await _authorize_principal(request, principal, st.project_id)
    return st


@router.post("/context/search", response_model=SearchResponse)
async def search(body: SearchRequest, request: Request) -> SearchResponse:
    await _authorize(request, body.project_id)
    svc = _svc(request)
    async with svc.session_factory() as session:
        results = await svc.search(
            await session.connection(), body.query, body.project_id, body.top_k
        )
    return SearchResponse(project_id=body.project_id, query=body.query, results=results)


@router.post("/context/search-multi", response_model=MultiSearchResponse)
async def search_multi(body: MultiSearchRequest, request: Request) -> MultiSearchResponse:
    seen: list[str] = []
    for pid in body.project_ids:
        if pid not in seen:
            seen.append(pid)
    for pid in seen:
        await _authorize(request, pid)
    svc = _svc(request)
    async with svc.session_factory() as session:
        by_project = await svc.search_multi(
            await session.connection(), body.query, seen, body.top_k
        )
    return MultiSearchResponse(
        query=body.query,
        results=[
            ProjectResults(project_id=pid, results=by_project.get(pid, [])) for pid in seen
        ],
    )


@router.post("/context/rank", response_model=None)
async def rank(body: RankRequest, request: Request):
    await _authorize(request, body.project_id)
    svc = _svc(request)
    async with svc.session_factory() as session:
        bundle = await svc.rank(
            await session.connection(),
            body.goal,
            body.project_id,
            max_tokens=body.max_tokens,
            top_k=body.top_k,
        )
    return bundle.to_wire()


@router.post("/context/git-search", response_model=GitSearchResponse)
async def git_search(body: GitSearchRequest, request: Request) -> GitSearchResponse:
    await _authorize(request, body.project_id)
    svc = _svc(request)
    root = svc.latest_job_root(body.project_id)
    if root is None:
        raise _no_indexed_repo(body.project_id)
    try:
        commits, reason = search_history(
            root, body.query or None, limit=body.limit, path=body.path
        )
    except InvalidRepoPathError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return GitSearchResponse(
        project_id=body.project_id,
        query=body.query,
        reason=reason,
        commits=[
            HistoryCommitModel(
                sha=c.sha,
                subject=c.subject,
                author=c.author,
                date_iso=c.date_iso,
                files=[CommitFileModel(status=f.status, path=f.path) for f in c.files],
                files_truncated=c.files_truncated,
            )
            for c in commits
        ],
    )


@router.get("/context/freshness/{project_id}", response_model=None)
async def freshness(project_id: str, request: Request):
    await _authorize(request, project_id)
    svc = _svc(request)
    root = svc.latest_job_root(project_id)
    if root is None:
        raise _no_indexed_repo(project_id)
    report = await svc.freshness(project_id, root)
    return report.to_wire()


async def _fetch_contents(svc: ContextService, project_id: str):
    """Indexed text rows grouped by path + the stored lang tag per path."""
    async with svc.session_factory() as session:
        by_path = await svc.store.fetch_text(await session.connection(), project_id)
    langs = {path: rows[0]["lang"] for path, rows in by_path.items() if rows}
    return by_path, langs


@router.get("/context/graph/{project_id}", response_model=None)
async def graph(project_id: str, request: Request):
    await _authorize(request, project_id)
    by_path, langs = await _fetch_contents(_svc(request), project_id)
    dep_graph = build_dependency_graph(project_id, by_path, nodes_lang=langs)
    return dep_graph.to_wire()


@router.get("/context/report/graph/{project_id}", response_class=HTMLResponse)
async def graph_report(project_id: str, request: Request) -> HTMLResponse:
    await _authorize(request, project_id)
    by_path, langs = await _fetch_contents(_svc(request), project_id)
    dep_graph = build_dependency_graph(project_id, by_path, nodes_lang=langs)
    page = render_dependency_graph_html(
        dep_graph.to_wire(), title="Dependency graph"
    )
    return HTMLResponse(content=page)


@router.get("/context/symbols/{project_id}", response_model=SymbolsResponse)
async def symbols(
    project_id: str, request: Request, name: str | None = None
) -> SymbolsResponse:
    await _authorize(request, project_id)
    by_path, langs = await _fetch_contents(_svc(request), project_id)
    if name is None:
        defs, truncated = list_symbols(by_path, langs)
        return SymbolsResponse(
            project_id=project_id,
            definitions=[d.to_wire() for d in defs],
            truncated=truncated,
            note="definitions only; pass ?name= to also locate references",
        )
    try:
        defs, refs, truncated = find_symbol(by_path, langs, name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SymbolsResponse(
        project_id=project_id,
        name=name,
        definitions=[d.to_wire() for d in defs],
        references=[
            SymbolReferenceModel(
                path=r.path, start_line=r.start_line, kind=r.kind, snippet=r.snippet
            )
            for r in refs
        ],
        truncated=truncated,
        note=(
            "text-occurrence based (no scope analysis);"
            " endLine is null for regex-extracted TS/JS symbols"
        ),
    )


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
