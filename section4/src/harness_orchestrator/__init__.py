"""Section 4: Agent Orchestration Engine."""

from harness_orchestrator.engine import DeepSeekHarnessWrapper
from harness_orchestrator.planner import PlanNotApprovedError, PlanService
from harness_orchestrator.reviewer import ReviewerSubAgent
from harness_orchestrator.state_machine import AgentState, AgentStateMachine

__all__ = [
    "AgentState",
    "AgentStateMachine",
    "PlanNotApprovedError",
    "PlanService",
    "ReviewerSubAgent",
    "DeepSeekHarnessWrapper",
    "OrchestratorService",
]


def __getattr__(name: str):
    # Workers and relays do not need database services. Keep that dependency
    # lazy so the pinned live extra can run in the minimal worker image.
    if name == "OrchestratorService":
        from harness_orchestrator.service import OrchestratorService

        return OrchestratorService
    raise AttributeError(name)
