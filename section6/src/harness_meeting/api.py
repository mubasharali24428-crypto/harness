"""REST API endpoints for Section 6: Meeting Area."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from harness_contracts.roles import Principal
from pydantic import BaseModel

from harness_meeting.service import MeetingService

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
    projectId: str
    title: str


class PostMessageRequest(BaseModel):
    userId: str
    content: str


class InvokeAIRequest(BaseModel):
    userId: str
    content: str


class ApproveBriefRequest(BaseModel):
    userId: str


class CreatePromptRequest(BaseModel):
    userId: str
    content: str


@router.post("", status_code=status.HTTP_201_CREATED)
def create_meeting(req: CreateMeetingRequest, principal: RequirePrincipal) -> dict[str, Any]:
    meeting = _service.create_meeting(req.projectId, req.title)
    return meeting.model_dump()


@router.get("/{meetingId}", status_code=status.HTTP_200_OK)
def get_meeting(meetingId: str, principal: RequirePrincipal) -> dict[str, Any]:
    try:
        meeting = _service.get_meeting(meetingId)
        return meeting.model_dump()
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e)}},
        ) from e


@router.post("/{meetingId}/messages", status_code=status.HTTP_201_CREATED)
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


@router.get("/{meetingId}/messages", status_code=status.HTTP_200_OK)
def get_messages(meetingId: str, principal: RequirePrincipal) -> list[dict[str, Any]]:
    try:
        messages = _service.get_messages(meetingId)
        return [m.model_dump() for m in messages]
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e)}},
        ) from e


@router.post("/{meetingId}/ai", status_code=status.HTTP_200_OK)
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


@router.post("/{meetingId}/brief", status_code=status.HTTP_201_CREATED)
def generate_brief(meetingId: str, principal: RequirePrincipal) -> dict[str, Any]:
    try:
        brief = _service.generate_brief(meetingId)
        return brief.model_dump()
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e)}},
        ) from e


@router.get("/{meetingId}/brief", status_code=status.HTTP_200_OK)
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


@router.post("/{meetingId}/summary", status_code=status.HTTP_201_CREATED)
def generate_summary(meetingId: str, principal: RequirePrincipal) -> dict[str, Any]:
    try:
        summary = _service.generate_summary(meetingId)
        return summary.model_dump()
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e)}},
        ) from e


@router.post("/{meetingId}/prompts", status_code=status.HTTP_201_CREATED)
def create_prompt(
    meetingId: str, req: CreatePromptRequest, principal: RequirePrincipal
) -> dict[str, Any]:
    prompt = _service.create_prompt(meetingId, principal.userId, req.content)
    return prompt.model_dump()


@router.get("/{meetingId}/prompts", status_code=status.HTTP_200_OK)
def list_prompts(meetingId: str, principal: RequirePrincipal) -> list[dict[str, Any]]:
    prompts = _service.list_prompts(meetingId)
    return [p.model_dump() for p in prompts]
