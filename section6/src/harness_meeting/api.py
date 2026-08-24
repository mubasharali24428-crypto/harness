"""REST API endpoints for Section 6: Meeting Area."""

import sys
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from harness_contracts.roles import Principal
from pydantic import BaseModel, Field

# M12-G3: shared cap constants live in section1; this venv does not carry
# harness_identity as an installed distribution, so the src tree is appended
# exactly like section4's wave-1 audit seam already does at call time.
_ROOT = Path(__file__).resolve().parents[3]
if (_ROOT / "section1" / "src") not in sys.path:
    sys.path.append(str(_ROOT / "section1" / "src"))

from harness_identity.limits import (  # noqa: E402
    MEETING_PROJECT_ID_MAX,
    MEETING_TITLE_MAX,
    MEETING_USER_ID_MAX,
    MESSAGE_CONTENT_MAX,
)

from harness_meeting.service import MeetingService  # noqa: E402

router = APIRouter(prefix="/api/v1/meetings", tags=["meetings"])
_service = MeetingService()


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


def get_meeting_service() -> MeetingService:
    return _service


class CreateMeetingRequest(BaseModel):
    # M12-G3: bounded fields (limits are additive; see harness_identity.limits).
    projectId: str = Field(max_length=MEETING_PROJECT_ID_MAX)
    title: str = Field(max_length=MEETING_TITLE_MAX)


class PostMessageRequest(BaseModel):
    # M12-G3: bounded message bodies (additive; see harness_identity.limits).
    userId: str = Field(max_length=MEETING_USER_ID_MAX)
    content: str = Field(max_length=MESSAGE_CONTENT_MAX)


class InvokeAIRequest(BaseModel):
    # M12-G3: bounded message bodies (additive; see harness_identity.limits).
    userId: str = Field(max_length=MEETING_USER_ID_MAX)
    content: str = Field(max_length=MESSAGE_CONTENT_MAX)


class ApproveBriefRequest(BaseModel):
    # M12-G3: bounded ids (additive; see harness_identity.limits).
    userId: str = Field(max_length=MEETING_USER_ID_MAX)


class CreatePromptRequest(BaseModel):
    # M12-G3: bounded prompt bodies (additive; see harness_identity.limits).
    userId: str = Field(max_length=MEETING_USER_ID_MAX)
    content: str = Field(max_length=MESSAGE_CONTENT_MAX)


# ---------------------------------------------------------------------------
# M13-G5a OpenAPI typing islands — concrete response schemas for the
# highest-traffic meeting routes.
# ---------------------------------------------------------------------------


class MeetingOut(BaseModel):
    id: str
    projectId: str
    title: str
    createdAt: str
    briefId: str | None = None


class ChatMessageOut(BaseModel):
    id: str
    meetingId: str
    userId: str
    content: str
    timestamp: str
    isAiGenerated: bool = False


class AiInvokeResponse(ChatMessageOut):
    """200 body of /ai — the AI reply message, or an explicit not-invoked note."""


class NotInvokedResponse(BaseModel):
    status: Literal["not_invoked"]
    message: str


class _ErrorDetail(BaseModel):
    """M13-G5a: frozen contract error entry for responses={} documentation."""

    code: str
    message: str


class _ErrorEnvelope(BaseModel):
    error: _ErrorDetail


class BriefOut(BaseModel):
    """Mirror of the FROZEN contracts EngineeringBrief (camelCase wire)."""

    id: str
    meetingId: str
    projectId: str
    requirements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    acceptanceCriteria: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    openQuestions: list[Any] = Field(default_factory=list)
    status: str = "draft"


class SummaryOut(BaseModel):
    """M13-G5a typing island: a stored meeting summary."""

    id: str
    meetingId: str
    content: str
    createdAt: str
    format: str = "markdown"


class PromptOut(BaseModel):
    """M13-G5a typing island: one versioned prompt row."""

    id: str
    meetingId: str
    version: int = 1
    content: str
    createdBy: str
    createdAt: str
    linkedTaskId: str | None = None
    linkedBranch: str | None = None


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=MeetingOut,
    responses={401: {"description": "bad or missing token"}},
)
def create_meeting(req: CreateMeetingRequest, principal: RequirePrincipal) -> dict[str, Any]:
    meeting = _service.create_meeting(req.projectId, req.title)
    return meeting.model_dump()


@router.get(
    "/{meetingId}",
    status_code=status.HTTP_200_OK,
    response_model=MeetingOut,
    responses={404: {"model": _ErrorEnvelope, "description": "unknown meeting"}},
)
def get_meeting(meetingId: str, principal: RequirePrincipal) -> dict[str, Any]:
    try:
        meeting = _service.get_meeting(meetingId)
        return meeting.model_dump()
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e)}},
        ) from e


@router.post(
    "/{meetingId}/messages",
    status_code=status.HTTP_201_CREATED,
    response_model=ChatMessageOut,
    responses={404: {"model": _ErrorEnvelope, "description": "unknown meeting"}},
)
def post_message(
    meetingId: str, req: PostMessageRequest, principal: RequirePrincipal
) -> dict[str, Any]:
    try:
        msg = _service.post_message(meetingId, principal.userId, req.content)
        return msg.model_dump()
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e)}},
        ) from e


@router.get(
    "/{meetingId}/messages",
    status_code=status.HTTP_200_OK,
    response_model=list[ChatMessageOut],
    responses={404: {"model": _ErrorEnvelope, "description": "unknown meeting"}},
)
def get_messages(meetingId: str, principal: RequirePrincipal) -> list[dict[str, Any]]:
    try:
        messages = _service.get_messages(meetingId)
        return [m.model_dump() for m in messages]
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e)}},
        ) from e


@router.post(
    "/{meetingId}/ai",
    status_code=status.HTTP_200_OK,
    response_model=AiInvokeResponse | NotInvokedResponse,
    responses={404: {"model": _ErrorEnvelope, "description": "unknown meeting"}},
)
def invoke_ai(meetingId: str, req: InvokeAIRequest, principal: RequirePrincipal) -> dict[str, Any]:
    try:
        result = _service.invoke_ai(meetingId, principal.userId, req.content)
        if result is None:
            return {"status": "not_invoked", "message": "AI was not invoked."}
        return result.model_dump()
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e)}},
        ) from e


@router.post(
    "/{meetingId}/brief",
    status_code=status.HTTP_201_CREATED,
    response_model=BriefOut,
    responses={404: {"model": _ErrorEnvelope, "description": "unknown meeting"}},
)
def generate_brief(meetingId: str, principal: RequirePrincipal) -> dict[str, Any]:
    try:
        brief = _service.generate_brief(meetingId)
        return brief.model_dump()
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e)}},
        ) from e


@router.get(
    "/{meetingId}/brief",
    status_code=status.HTTP_200_OK,
    response_model=BriefOut,
    responses={
        404: {"model": _ErrorEnvelope, "description": "unknown meeting or no brief yet"},
    },
)
def get_brief(meetingId: str, principal: RequirePrincipal) -> dict[str, Any]:
    try:
        brief = _service.get_brief(meetingId)
        if brief is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NO_BRIEF", "message": "No brief generated yet."}},
            )
        return brief.model_dump()
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e)}},
        ) from e


@router.post(
    "/{meetingId}/brief/{briefId}/approve",
    status_code=status.HTTP_200_OK,
    response_model=BriefOut,
    responses={
        400: {"model": _ErrorEnvelope, "description": "INVALID_STATE"},
        404: {"model": _ErrorEnvelope, "description": "unknown meeting/brief"},
    },
)
def approve_brief(
    meetingId: str, briefId: str, req: ApproveBriefRequest, principal: RequirePrincipal
) -> dict[str, Any]:
    try:
        brief = _service.approve_brief(meetingId, briefId, principal.userId)
        return brief.model_dump()
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e)}},
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_STATE", "message": str(e)}},
        ) from e


@router.post(
    "/{meetingId}/brief/{briefId}/reject",
    status_code=status.HTTP_200_OK,
    response_model=BriefOut,
    responses={404: {"model": _ErrorEnvelope, "description": "unknown meeting/brief"}},
)
def reject_brief(
    meetingId: str, briefId: str, req: ApproveBriefRequest, principal: RequirePrincipal
) -> dict[str, Any]:
    try:
        brief = _service.reject_brief(meetingId, briefId, principal.userId)
        return brief.model_dump()
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e)}},
        ) from e


@router.post(
    "/{meetingId}/summary",
    status_code=status.HTTP_201_CREATED,
    response_model=SummaryOut,
    responses={404: {"model": _ErrorEnvelope, "description": "unknown meeting"}},
)
def generate_summary(meetingId: str, principal: RequirePrincipal) -> dict[str, Any]:
    try:
        summary = _service.generate_summary(meetingId)
        return summary.model_dump()
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e)}},
        ) from e


@router.post(
    "/{meetingId}/prompts",
    status_code=status.HTTP_201_CREATED,
    response_model=PromptOut,
)
def create_prompt(
    meetingId: str, req: CreatePromptRequest, principal: RequirePrincipal
) -> dict[str, Any]:
    prompt = _service.create_prompt(meetingId, principal.userId, req.content)
    return prompt.model_dump()


@router.get(
    "/{meetingId}/prompts",
    status_code=status.HTTP_200_OK,
    response_model=list[PromptOut],
)
def list_prompts(meetingId: str, principal: RequirePrincipal) -> list[dict[str, Any]]:
    prompts = _service.list_prompts(meetingId)
    return [p.model_dump() for p in prompts]
