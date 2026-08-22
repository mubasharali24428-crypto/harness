"""Security checklist shown before connecting a private repository — brief §23 Q17.

Plain, auditable statements of what the sandbox can and cannot do, that secrets
never reach a model, and that merges are always a manual human action.
"""

from pydantic import BaseModel


class ChecklistItem(BaseModel):
    id: str
    category: str
    statement: str
    # The guarantee the user is counting on; cited where it is enforced.
    enforcedBy: str


ITEMS: list[ChecklistItem] = [
    ChecklistItem(
        id="sandbox_allowlist",
        category="What the sandbox can do",
        statement=(
            "Agent commands run in an ephemeral Docker container, one per task, "
            "destroyed on completion or timeout. Allowed commands are read-only "
            "inspection, test runners, linters, builds, and lockfile-based installs."
        ),
        enforcedBy="Section 5 command allow-list (enforced in code, not docs)",
    ),
    ChecklistItem(
        id="sandbox_blocklist",
        category="What the sandbox cannot do",
        statement=(
            "Destructive deletes, force-push, secret-file reads, package publishing, "
            "infrastructure-changing commands and arbitrary network pipelines are "
            "blocked, as is anything touching the plan's forbidden paths."
        ),
        enforcedBy="Section 5 block-list + forbidden_paths hard constraint",
    ),
    ChecklistItem(
        id="network_egress",
        category="What the sandbox cannot do",
        statement=(
            "Network egress is restricted to pinned official package registries "
            "(npmjs.com, pypi.org) or a self-hosted mirror — no other destinations."
        ),
        enforcedBy="Section 5 egress pinning",
    ),
    ChecklistItem(
        id="secrets_never_reach_model",
        category="Secrets",
        statement=(
            "API keys are encrypted at rest and decrypted only inside the model "
            "routing layer — they never enter model context, prompts, or logs."
        ),
        enforcedBy="Section 1 key vault + Section 3 decrypt boundary",
    ),
    ChecklistItem(
        id="no_auto_merge",
        category="Merging",
        statement=(
            "Agents never merge. The system contains no merge call at all: you "
            "approve the review in-app, then click merge yourself on GitHub."
        ),
        enforcedBy="Section 2 merge guard + Section 8 review gate",
    ),
    ChecklistItem(
        id="plan_gate",
        category="Before any code runs",
        statement=(
            "No agent command executes until a human approves the plan that names "
            "the files it will touch and the paths it must not touch."
        ),
        enforcedBy="Section 4 state machine (plan_approved precedes executing)",
    ),
    ChecklistItem(
        id="audit_log",
        category="Visibility",
        statement=(
            "Every approval, pause, takeover, key change, and blocked command is "
            "written to an append-only audit log you can inspect."
        ),
        enforcedBy="Section 1 audit log (append-only guard)",
    ),
]


class SecurityChecklist(BaseModel):
    title: str
    intro: str
    items: list[ChecklistItem]


CHECKLIST = SecurityChecklist(
    title="Before you connect a private repository",
    intro=(
        "Read this once before pointing the harness at a real repo. Everything below "
        "is enforced in code by the deployment you are running — this checklist "
        "tells you where."
    ),
    items=ITEMS,
)


def checklist_payload() -> dict:
    return CHECKLIST.model_dump()
