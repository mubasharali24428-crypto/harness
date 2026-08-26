from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel


class ModelRole(StrEnum):
    PRIMARY = "primary"
    REVIEWER = "reviewer"


class ModelResult(BaseModel):
    text: str
    costUsd: float
    model: str
    latencyMs: int

    model_config = {"protected_namespaces": ()}


class ModelCallOpts(BaseModel):
    maxTokens: int = 4096
    temperature: float | None = None
    extra: dict[str, Any] | None = None

    model_config = {"protected_namespaces": ()}


@runtime_checkable
class ModelRouter(Protocol):
    """Contract owned by Section 3. No other section talks to a provider directly."""

    def call_model(
        self,
        agent_id: str,
        role: ModelRole,
        messages: list[dict[str, str]],
        opts: ModelCallOpts | None = None,
    ) -> ModelResult: ...

    def estimate_cost(self, role: ModelRole, input_tokens: int, output_tokens: int) -> float: ...

    def list_available_models(self) -> list[str]: ...


class TaskHandle(BaseModel):
    taskId: str
    containerId: str
    branch: str


@runtime_checkable
class Sandbox(Protocol):
    """Contract owned by Section 5. No other section spawns a container."""

    def run_task(
        self,
        task_id: str,
        repo_ref: str,
        branch: str,
        forbidden_paths: list[str],
    ) -> TaskHandle: ...

    def exec(self, handle: TaskHandle, command: str) -> Any:  # streams output chunks
        ...

    def apply_edit(self, handle: TaskHandle, path: str, diff: str) -> None: ...

    def destroy(self, handle: TaskHandle) -> None: ...


@runtime_checkable
class GitHubService(Protocol):
    """Contract owned by Section 2. No other section calls the GitHub API.

    Hard rule: no function here ever merges a PR or force-pushes.
    """

    def connect_repo(self, project_id: str, repo_full_name: str) -> None: ...

    def read_repo_tree(self, project_id: str, ref: str) -> list[dict]: ...

    def read_file(self, project_id: str, ref: str, path: str) -> str: ...

    def create_branch(self, project_id: str, base_ref: str, new_branch: str) -> None: ...

    def commit_files(
        self,
        project_id: str,
        branch: str,
        message: str,
        files: list[dict],
    ) -> str: ...  # returns commit sha

    def open_draft_pr(
        self,
        project_id: str,
        branch: str,
        base_branch: str,
        title: str,
        body_md: str,
    ) -> dict: ...

    def get_pr_status(self, project_id: str, pr_number: int) -> dict: ...


class PlanState(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    PAUSED = "paused"
    AWAITING_DONE_SIGNAL = "awaiting_done_signal"
    REVIEWING = "reviewing"
    BLOCKED = "blocked"
    PR_OPENED = "pr_opened"
    DONE = "done"


RunEventName = Literal[
    "plan_proposed",
    "plan_approved",
    "execution_started",
    "paused",
    "resumed",
    "blocked_awaiting_clarification",
    "done_signal_received",
    "review_passed",
    "review_failed",
    "pr_opened",
]


@runtime_checkable
class Orchestrator(Protocol):
    """Contract owned by Section 4 (wraps DeepSeek Harness). Owns event semantics only;
    delivery transport is Section 1's event bus."""

    def propose_plan(self, run_id: str) -> str: ...  # plan id

    def approve_plan(self, run_id: str, plan_id: str, principal_user_id: str) -> None: ...

    def reject_plan(self, run_id: str, plan_id: str, principal_user_id: str) -> None: ...

    def start_execution(self, run_id: str) -> None: ...

    def pause_agent(self, run_id: str) -> None: ...

    def resume_agent(self, run_id: str) -> None: ...

    def stop_agent(self, run_id: str) -> None: ...

    def submit_human_edit(self, run_id: str, path: str, content: str) -> None: ...


@runtime_checkable
class ReviewGate(Protocol):
    """Contract owned by Section 8. Merge is ALWAYS a manual GitHub action."""

    def approve(self, pr_id: str, reviewer_user_id: str) -> None: ...

    def request_changes(
        self,
        pr_id: str,
        comments: list[dict],
        reviewer_user_id: str,
    ) -> None: ...
