from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class Role(StrEnum):
    ADMIN = "admin"
    ENGINEER = "engineer"
    VIEWER = "viewer"
    GUEST = "guest"


CAN_APPROVE_PLANS = {Role.ADMIN, Role.ENGINEER}
CAN_REVIEW = {Role.ADMIN, Role.ENGINEER, Role.VIEWER, Role.GUEST}
READ_ONLY = {Role.VIEWER, Role.GUEST}

PrincipalId = str


class Principal(BaseModel):
    """Auth/session contract: attached to every backend call by shared middleware."""

    userId: PrincipalId
    teamId: str
    role: Role


TeamScopedAction = Literal["read", "write", "approve", "merge_gate"]


def can(principal: Principal, action: TeamScopedAction) -> bool:
    if action == "read":
        return True
    if principal.role in READ_ONLY:
        return False
    if action == "approve":
        return principal.role in CAN_APPROVE_PLANS
    return True
