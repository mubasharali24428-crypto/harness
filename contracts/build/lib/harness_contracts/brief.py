from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class BriefStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"


class EngineeringBrief(BaseModel):
    """Produced by Section 6 (Meeting Area); consumed by Section 4 (Orchestration)."""

    id: str
    meetingId: str
    projectId: str
    requirements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    acceptanceCriteria: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    openQuestions: list[Any] = Field(default_factory=list)
    status: BriefStatus = BriefStatus.DRAFT
