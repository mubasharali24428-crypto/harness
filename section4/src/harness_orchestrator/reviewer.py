from pydantic import BaseModel, ConfigDict, Field


class ReviewFindings(BaseModel):
    """Wire format is camelCase per api-conventions.md; python kwargs stay snake_case."""

    model_config = ConfigDict(populate_by_name=True)

    passed: bool
    modelUsed: str | None = None
    syntax_ok: bool = Field(default=True, alias="syntaxOk")
    conflicts_detected: bool = Field(default=False, alias="conflictsDetected")
    brief_aligned: bool = Field(default=True, alias="briefAligned")
    summary: str = ""
    issues: list[str] = []


class ReviewerSubAgent:
    """Reviewer sub-agent triggered on explicit human done-signals.

    Deterministic heuristics gate pass/fail; when a Section 3 ModelRouter is wired,
    the review summary is generated through call_model on the REVIEWER role.
    """

    def __init__(self, model_router=None) -> None:
        self.model_router = model_router

    def evaluate_changes(
        self,
        brief_summary: str,
        diff_text: str,
        test_results_summary: str = "passed",
        agent_id: str = "reviewer",
        budget_blocked_reason: str | None = None,
    ) -> ReviewFindings:
        issues = []
        syntax_ok = True
        conflicts_detected = False

        # Simple static evaluation heuristics
        if "<<<<<<< HEAD" in diff_text or ">>>>>>>" in diff_text:
            conflicts_detected = True
            issues.append("Git merge conflict markers detected in diff.")

        if "SyntaxError" in diff_text:
            syntax_ok = False
            issues.append("Syntax errors detected in changes.")

        if test_results_summary == "failed":
            issues.append("Automated test suite failed.")

        passed = syntax_ok and not conflicts_detected and (test_results_summary != "failed")

        summary = "Review passed cleanly." if passed else f"Review failed: {', '.join(issues)}"
        model_used = None
        # Wave 9 node B: over-budget ⇒ NO model call, honestly recorded. The
        # deterministic heuristics above still gate pass/fail — the review
        # itself never depended on the model; only its summary does.
        if budget_blocked_reason is not None:
            summary = (
                f"Model call withheld — team budget cap reached ({budget_blocked_reason}). "
                + summary
            )
            issues.append(f"budget_cap_withheld_model_call: {budget_blocked_reason}")
        elif self.model_router is not None:
            from harness_contracts.signatures import ModelCallOpts, ModelRole

            result = self.model_router.call_model(
                agent_id=agent_id,
                role=ModelRole.REVIEWER,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a code reviewer sub-agent. "
                            "Summarize findings in one sentence."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Brief: {brief_summary}\n"
                            f"Tests: {test_results_summary}\n"
                            f"Diff:\n{diff_text[:4000]}"
                        ),
                    },
                ],
                opts=ModelCallOpts(maxTokens=512),
            )
            summary = result.text.strip()
            model_used = result.model

        return ReviewFindings(
            passed=passed,
            syntax_ok=syntax_ok,
            conflicts_detected=conflicts_detected,
            brief_aligned=True,
            summary=summary,
            issues=issues,
            modelUsed=model_used,
        )
