"""Section 8 — FastAPI router for the Review Area.

Endpoints:
  GET  /reviews/{prId}          — full review data (diff, files, risks, summary,
                                 comments, test results)
  POST /reviews/{prId}/approve                  — approve a PR (no auto-merge)
  POST /reviews/{prId}/request-changes          — request changes with inline comments
  POST /reviews/{prId}/comments                 — add an inline review comment
  GET  /reviews/{prId}/comments                 — list inline review comments
  GET  /reviews/{prId}/risks                   — get risk flags for a PR
  GET  /reviews/{prId}/deep-link               — deep-link to GitHub merge button
  GET  /reviews/{prId}/verdict                 — pass/fail verdict under the
                                 PR team's reviewer thresholds (M15-G6b)
  GET  /reviews/settings/thresholds            — caller's team thresholds view
                                 (admin writes go through Section 1)

All endpoints use Section 1's auth middleware via make_bearer_dep.
"""

from typing import Annotated, Any

import harness_contracts as contracts
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from harness_contracts import KeyVault, make_bearer_dep
from harness_contracts.roles import Principal, Role, can
from pydantic import BaseModel, Field

from .config import Settings
from .heuristic import DEFAULT_REVIEWER_THRESHOLDS, REVIEWER_THRESHOLD_KEY
from .kpi import blind_approval_kpi
from .models import ChangedFile, ReviewData, ReviewStatus, RiskFlag
from .service import ReviewService
from .team_thresholds import get_team_reviewer_thresholds

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


RequirePrincipal = Annotated[Principal, Depends(get_principal)]


def _require_approver(principal: Principal) -> None:
    """Approve/request-changes is a governance action (S1-R01d role gate)."""
    if not can(principal, "approve"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "ROLE_DENIED", "message": "role may not approve"}},
        )


def _request_diff(request: Request, diff_text: str | None) -> str | None:
    """Caller-supplied diffs are a test-only escape hatch.

    Production reviews must use stored/provider evidence; otherwise the party
    under review can show the reviewer a different diff than the PR. Explicitly
    reject the parameter when disabled instead of silently ignoring it.
    """
    settings: Settings = request.app.state.settings
    if diff_text is None:
        return None
    if not settings.allow_request_diff:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "DIFF_OVERRIDE_DISABLED",
                    "message": "diff_text override is disabled; review evidence comes from the PR",
                }
            },
        )
    return diff_text


class ApproveRequest(BaseModel):
    reviewer_user_id: str = Field(..., min_length=1)
    # X-07 KPI signal from the client: was the diff on screen before Approve? None = the client
    # did not measure it (counted as unknown, never as blind).
    viewed_detail: bool | None = None


class RequestChangesRequest(BaseModel):
    reviewer_user_id: str = Field(..., min_length=1)
    comments: list[dict] = Field(default_factory=list)


class InlineCommentRequest(BaseModel):
    file_path: str | None = None
    line: int | None = None
    body: str = Field(..., min_length=1)


class ReviewDataResponse(BaseModel):
    prId: str
    projectId: str
    githubPrNumber: int
    prUrl: str
    headBranch: str
    baseBranch: str
    state: str
    reviewStatus: str
    approvedBy: str | None = None
    diff: str
    changedFiles: list[ChangedFile]
    unchangedAreasNote: str | None = None
    riskFlags: list[RiskFlag]
    summary: dict[str, Any] | None = None
    reviewComments: list[dict[str, Any]]
    testResults: list[dict[str, Any]]
    model_config = {"protected_namespaces": ()}


def make_app(session_factory, settings: Settings):
    app = FastAPI(title="harness-review", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Wave9-E: baseline security headers. M17-B3: the middleware is a LOCAL
    # module now (S8 may not import section1's package; deploy keeps wiring
    # the richer S1 original for its own assemblies).
    from .security_headers import install_security_headers

    install_security_headers(app)
    app.state.session_factory = session_factory
    app.state.settings = settings
    app.state.vault = KeyVault(settings.master_key)

    get_principal = make_bearer_dep(session_factory)
    app.state.get_principal = get_principal

    # The ReviewService is wired up here with injected dependencies.
    # In production, github_service and model_router come from Sections 2 & 3.
    # diff_provider and write_audit come from the deployment wiring.
    app.state.review_service = ReviewService(
        session_factory=session_factory,
        github_service=None,
        model_router=None,
        diff_provider=None,
        write_audit=None,
    )

    @app.get("/health")
    async def health():
        return {"ok": True}

    app.include_router(router)
    return app


@router.get("/reviews/{pr_id}", response_model=ReviewDataResponse)
async def get_review(
    pr_id: str, request: Request, principal: RequirePrincipal, diff_text: str | None = None
) -> ReviewDataResponse:
    svc: ReviewService = request.app.state.review_service
    resolved_diff = _request_diff(request, diff_text)
    try:
        data = await svc.get_review_data(
            pr_id,
            diff_text=resolved_diff,
            team_id=principal.teamId,
            actor_user_id=principal.userId,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "REVIEW_DATA_ERROR", "message": str(exc)}},
        ) from exc
    return ReviewDataResponse(**_to_response_dict(data))


@router.post("/reviews/{pr_id}/approve", status_code=status.HTTP_200_OK)
async def approve_pr(
    pr_id: str, request: Request, body: ApproveRequest, principal: RequirePrincipal
) -> dict[str, Any]:
    _require_approver(principal)
    svc: ReviewService = request.app.state.review_service
    try:
        await svc.approve(pr_id, principal.userId, team_id=principal.teamId,
                          viewed_detail=body.viewed_detail)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "prId": pr_id,
        "status": ReviewStatus.APPROVED.value,
        "reviewerUserId": principal.userId,
    }


@router.post("/reviews/{pr_id}/request-changes", status_code=status.HTTP_200_OK)
async def request_changes(
    pr_id: str, request: Request, body: RequestChangesRequest, principal: RequirePrincipal
) -> dict[str, Any]:
    _require_approver(principal)
    svc: ReviewService = request.app.state.review_service
    try:
        await svc.request_changes(
            pr_id, body.comments, principal.userId, team_id=principal.teamId
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "prId": pr_id,
        "status": ReviewStatus.CHANGES_REQUESTED.value,
        "reviewerUserId": principal.userId,
    }


@router.post("/reviews/{pr_id}/comments", status_code=status.HTTP_201_CREATED)
async def add_comment(
    pr_id: str, request: Request, body: InlineCommentRequest, principal: RequirePrincipal
) -> dict[str, Any]:
    svc: ReviewService = request.app.state.review_service
    try:
        comment = await svc.add_inline_comment(
            pr_id,
            body.file_path,
            body.line,
            body.body,
            principal.userId,
            team_id=principal.teamId,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return comment


@router.get("/reviews/{pr_id}/comments")
async def list_comments(
    pr_id: str, request: Request, principal: RequirePrincipal
) -> list[dict[str, Any]]:
    svc: ReviewService = request.app.state.review_service
    try:
        return await svc.get_comments(pr_id, team_id=principal.teamId)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/reviews/{pr_id}/risks")
async def get_risk_flags(
    pr_id: str, request: Request, principal: RequirePrincipal, diff_text: str | None = None
) -> list[dict[str, Any]]:
    svc: ReviewService = request.app.state.review_service
    resolved_diff = _request_diff(request, diff_text)
    try:
        flags = await svc.get_risk_flags(
            pr_id,
            diff_text=resolved_diff,
            team_id=principal.teamId,
            actor_user_id=principal.userId,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "RISK_ANALYSIS_ERROR", "message": str(exc)}},
        ) from exc
    return [f.model_dump() for f in flags]


@router.get("/reviews/{pr_id}/deep-link")
async def deep_link_to_github(
    pr_id: str, request: Request, principal: RequirePrincipal
) -> dict[str, str]:
    """Return a deep-link URL to GitHub's PR page + merge button.

    The app itself never merges — this is always a manual GitHub action.
    """
    svc: ReviewService = request.app.state.review_service
    try:
        data = await svc.get_review_data(
            pr_id, team_id=principal.teamId, actor_user_id=principal.userId
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "githubUrl": data.pr_url,
        "mergeButtonUrl": f"{data.pr_url}/new_branch",
        "note": "Merge is ALWAYS a manual action on GitHub. This app never calls a merge API.",
    }


@router.get("/reviews/{pr_id}/verdict")
async def get_verdict(
    pr_id: str, request: Request, principal: RequirePrincipal, diff_text: str | None = None
) -> dict[str, Any]:
    """Reviewer pass/fail verdict under the PR's team thresholds (M15-G6b).

    ``thresholdsSource`` is ``"team"`` when a well-formed team config was
    applied, ``"defaults"`` when none/malformed — tuning is observable.
    """
    svc: ReviewService = request.app.state.review_service
    resolved_diff = _request_diff(request, diff_text)
    try:
        verdict = await svc.evaluate_verdict(
            pr_id,
            diff_text=resolved_diff,
            team_id=principal.teamId,
            actor_user_id=principal.userId,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "REVIEW_VERDICT_ERROR", "message": str(exc)}},
        ) from exc
    return verdict.model_dump(by_alias=True)


@router.get("/reviews/settings/thresholds")
async def get_reviewer_threshold_settings(
    request: Request, principal: RequirePrincipal
) -> dict[str, Any]:
    """Read-only view of the caller's team reviewer thresholds (M15-G6b).

    The AUTHORITATIVE admin write path is Section 1's
    ``GET/PUT /api/v1/teams/{teamId}/settings/reviewer-thresholds``; this
    endpoint exists so the Review Area UI can show what's in force without
    reaching across sections. Missing config answers the documented defaults
    (never an error).
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as db:
        thresholds, source = await get_team_reviewer_thresholds(db, principal.teamId)
    return {
        "teamId": principal.teamId,
        "key": REVIEWER_THRESHOLD_KEY,
        "thresholdsSource": source,
        "thresholds": thresholds.model_dump(by_alias=True),
        "defaults": DEFAULT_REVIEWER_THRESHOLDS.model_dump(by_alias=True),
    }


def _to_response_dict(data: ReviewData) -> dict[str, Any]:
    return {
        "prId": data.prId,
        "projectId": data.project_id,
        "githubPrNumber": data.github_pr_number,
        "prUrl": data.pr_url,
        "headBranch": data.head_branch,
        "baseBranch": data.base_branch,
        "state": data.state,
        "reviewStatus": data.review_status.value,
        "approvedBy": data.approved_by,
        "diff": data.diff,
        "changedFiles": [f.model_dump() for f in data.changed_files],
        "unchangedAreasNote": data.unchanged_areas_note,
        "riskFlags": [f.model_dump() for f in data.risk_flags],
        "summary": data.summary.model_dump() if data.summary else None,
        "reviewComments": data.review_comments,
        "testResults": [t.model_dump() for t in data.test_results],
    }


@router.get("/review-kpi/blind-approval", status_code=status.HTTP_200_OK)
async def blind_approval(request: Request, principal: RequirePrincipal) -> dict[str, Any]:
    """X-07 KPI for the caller's team (admin only): blind-approval rate split by rendering."""
    if principal.role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "ROLE_DENIED", "message": "admins only"}},
        )
    svc: ReviewService = request.app.state.review_service
    async with await svc._session() as db:
        return await blind_approval_kpi(db, principal.teamId)
