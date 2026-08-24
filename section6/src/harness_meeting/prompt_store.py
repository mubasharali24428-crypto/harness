"""Prompt creation, versioning, and storage."""

import sys
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

# M13-H4b: the single source of "now" is harness_identity.timeutil (aware
# UTC law). The section1 src tree is appended exactly like api.py's M12-G3
# limits seam already does; module level so imports stay order-independent.
_ROOT = Path(__file__).resolve().parents[3]
if (_ROOT / "section1" / "src") not in sys.path:
    sys.path.append(str(_ROOT / "section1" / "src"))

from harness_identity.timeutil import now_utc  # noqa: E402


class Prompt(BaseModel):
    """A versioned prompt tied to a meeting."""

    id: str = Field(default_factory=lambda: f"prompt-{uuid.uuid4().hex[:8]}")
    meetingId: str
    version: int = 1
    content: str
    createdBy: str
    createdAt: str = Field(default_factory=lambda: now_utc().isoformat())
    linkedTaskId: str | None = None
    linkedBranch: str | None = None


class PromptStore:
    """Manages prompt creation, versioning, and linking to tasks."""

    def __init__(self) -> None:
        self.prompts: dict[str, list[Prompt]] = {}

    def create_prompt(
        self,
        meeting_id: str,
        user_id: str,
        content: str,
    ) -> Prompt:
        """Creates a new prompt version for a meeting."""
        existing = self.prompts.get(meeting_id, [])
        version = len(existing) + 1

        prompt = Prompt(
            meetingId=meeting_id,
            version=version,
            content=content,
            createdBy=user_id,
        )

        if meeting_id not in self.prompts:
            self.prompts[meeting_id] = []
        self.prompts[meeting_id].append(prompt)

        return prompt

    def get_latest(self, meeting_id: str) -> Prompt | None:
        versions = self.prompts.get(meeting_id, [])
        return versions[-1] if versions else None

    def list_versions(self, meeting_id: str) -> list[Prompt]:
        return self.prompts.get(meeting_id, [])

    def link_to_task(
        self,
        meeting_id: str,
        version: int,
        task_id: str,
        branch: str,
    ) -> Prompt:
        """Links a prompt version to a task/branch."""
        versions = self.prompts.get(meeting_id, [])
        for p in versions:
            if p.version == version:
                p.linkedTaskId = task_id
                p.linkedBranch = branch
                return p
        raise KeyError(f"Prompt version {version} not found for meeting '{meeting_id}'.")
