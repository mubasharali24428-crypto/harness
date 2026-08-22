import uuid

from harness_contracts.plan import AgentPlan


class PlanNotApprovedError(Exception):
    """Raised when code execution is attempted before plan approval."""

    pass


class PlanService:
    """Generates and manages execution plans matching brief fields."""

    def __init__(self) -> None:
        self.plans: dict[str, AgentPlan] = {}
        self.plan_approvals: dict[str, bool] = {}

    def create_plan(
        self,
        goal: str,
        files_likely_to_change: list[str] | None = None,
        steps: list[str] | None = None,
        tests: list[str] | None = None,
        risks: list[str] | None = None,
        estimated_cost_usd: float = 0.05,
        forbidden_paths: list[str] | None = None,
        expected_result: str = "All tests pass and changes conform to brief.",
    ) -> tuple[str, AgentPlan]:
        plan_id = f"plan-{uuid.uuid4().hex[:8]}"
        plan = AgentPlan(
            goal=goal,
            filesLikelyToChange=files_likely_to_change or [],
            steps=steps or ["Inspect codebase", "Apply edits", "Run unit tests"],
            tests=tests or ["pytest tests/"],
            risks=risks or ["Regression in existing modules"],
            estimatedCostUsd=estimated_cost_usd,
            forbiddenPaths=forbidden_paths or [],
            expectedResult=expected_result,
        )
        self.plans[plan_id] = plan
        self.plan_approvals[plan_id] = False
        return plan_id, plan

    def get_plan(self, plan_id: str) -> AgentPlan:
        if plan_id not in self.plans:
            raise KeyError(f"Plan '{plan_id}' not found.")
        return self.plans[plan_id]

    def approve_plan(self, plan_id: str) -> None:
        if plan_id not in self.plans:
            raise KeyError(f"Plan '{plan_id}' not found.")
        self.plan_approvals[plan_id] = True

    def reject_plan(self, plan_id: str) -> None:
        if plan_id in self.plan_approvals:
            self.plan_approvals[plan_id] = False

    def is_approved(self, plan_id: str) -> bool:
        return self.plan_approvals.get(plan_id, False)
