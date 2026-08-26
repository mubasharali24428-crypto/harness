from pydantic import BaseModel, Field


class AgentPlan(BaseModel):
    """Exactly the fields required by the brief (§21 Q20-25, §103-109)."""

    goal: str
    filesLikelyToChange: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    estimatedCostUsd: float = 0.0
    forbiddenPaths: list[str] = Field(default_factory=list)
    expectedResult: str
