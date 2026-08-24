"""REST API endpoints for Section 6: Meeting Area."""

import sys
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
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
    # M16-X5 (raw-18 §8): lifecycle status on the wire (open | closed).
    status: str = "open"
    closedAt: str | None = None


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


# ---------------------------------------------------------------------------
# M16-X2 — meeting message pagination (raw-16 H1.06 unbounded-render fix).
#
# GET /{meetingId}/messages used to return the FULL history as a bare JSON
# list; a long-running meeting rendered every message on every load. The
# route now pages with the frozen api-conventions cursor shape:
#   ?limit=<1..500>         page size (default MESSAGES_DEFAULT_LIMIT = 200,
#                           hard-capped at 500; oversized values are clamped,
#                           never rejected)
#   ?before=<messageSeq>    keyset cursor: messages strictly OLDER than
#                           that stable 1-based transcript position
# returning {"items": [...], "hasMore": bool} — items chronological, each
# carrying its messageSeq; hasMore true iff older messages remain beyond
# the returned page.
#
# ONE-RELEASE BEHAVIOR CHANGE (documented per docs/api-versioning.md): a
# NO-param GET returns the LAST MESSAGES_DEFAULT_LIMIT (=200) messages
# instead of the entire history. Legacy bare-list readers must migrate to
# `body.items` this release.
# ---------------------------------------------------------------------------

#: Bounds live in service.py (single source of truth); re-exported here so
#: route defaults, tests, and the service path can never drift apart.
from harness_meeting.service import (  # noqa: E402
    MEETING_CLOSED,  # noqa: F401 -- deliberate re-export (tests pin it)
    MESSAGES_DEFAULT_LIMIT,  # noqa: F401 -- deliberate re-export (tests pin it)
    MESSAGES_MAX_LIMIT,  # noqa: F401 -- deliberate re-export
    MeetingClosedError,  # noqa: F401 -- deliberate re-export
)


def _meeting_closed_410(e: MeetingClosedError) -> HTTPException:
    """M16-X5 (raw-18 #54): traffic to a closed meeting answers honest 410
    GONE — the resource existed and is permanently gone (terminal), which is
    exactly what 410 tells a client not to retry. Unknown meetings stay 404."""
    return HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={"error": {"code": "MEETING_CLOSED", "message": str(e)}},
    )


class PagedChatMessageOut(ChatMessageOut):
    """A page item: chat message PLUS its stable transcript cursor.

    messageSeq is the 1-based position in the room's append-only history —
    monotonic, gap-free, and usable directly as ``?before=``. Kept OUT of
    the plain ChatMessageOut so POST /messages' wire shape is untouched.
    """

    messageSeq: int


class MessagePageOut(BaseModel):
    """Paginated GET-messages body (api-conventions pagination shape)."""

    items: list[PagedChatMessageOut]
    hasMore: bool


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
    except MeetingClosedError as e:  # M16-X5 raw-18 #54: ingest after close
        raise _meeting_closed_410(e) from e


@router.post(
    "/{meetingId}/close",
    status_code=status.HTTP_200_OK,
    response_model=MeetingOut,
    responses={
        404: {"model": _ErrorEnvelope, "description": "unknown meeting"},
    },
)
def close_meeting(meetingId: str, principal: RequirePrincipal) -> dict[str, Any]:
    """Close a meeting (M16-X5, raw-18 §8). Terminal + idempotent (#53): the
    second close returns the SAME stamped meeting — never an error, never a
    moved closedAt. After close, message posts answer 410 MEETING_CLOSED."""
    try:
        meeting = _service.close_meeting(meetingId)
        return meeting.model_dump()
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e)}},
        ) from e


@router.get(
    "/{meetingId}/messages",
    status_code=status.HTTP_200_OK,
    response_model=MessagePageOut,
    responses={404: {"model": _ErrorEnvelope, "description": "unknown meeting"}},
)
def get_messages(
    meetingId: str,
    principal: RequirePrincipal,
    limit: int | None = Query(default=None, gt=0),
    before: str | None = None,
) -> dict[str, Any]:
    """Cursor-paginated transcript (M16-X2).

    ``limit`` (default 100, hard-capped at 500 — larger values are clamped,
    not rejected) sizes the page. ``before=<messageSeq>`` returns the page
    of messages strictly OLDER than that stable 1-based transcript position.
    Body shape is ``{items, hasMore}`` per the frozen api-conventions
    pagination rule; every item carries its ``messageSeq``.

    OLDER-MESSAGES FETCH PATTERN (frontend / X-lane consumers):
      1. First paint:      GET .../messages                -> newest window
         (last 100 by default); render bottom-anchored.
      2. Scroll to top:    if body.hasMore is true, GET
         .../messages?limit=N&before=<items[0].messageSeq>
         and PREPEND the returned items — pages chain with no gap and no
         overlap because every page ends exactly where the previous began.
      3. Repeat until hasMore is false: the transcript head is reached.
      Do NOT cache messageSeq across meetings; seqs are per-transcript
      positions, not global ids. A cursor for a message that no longer
      exists resolves to an honest empty page ({items: [], hasMore: false}).
    """
    try:
        if limit is not None and limit > MESSAGES_MAX_LIMIT:
            limit = MESSAGES_MAX_LIMIT  # clamp, never reject
        items, has_more, first_seq = _service.get_message_page(
            meetingId, limit=limit, before=before
        )
        return {
            "items": [
                {**m.model_dump(), "messageSeq": (first_seq or 0) + i}
                for i, m in enumerate(items)
            ],
            "hasMore": has_more,
        }
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
