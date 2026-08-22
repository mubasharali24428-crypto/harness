"""Onboarding order — brief §11, §16 Q9.

Frozen order: connect GitHub → other integrations (none required for MVP) →
create a team → invite members → start a meeting → open the guided demo.

Each step names the section that fulfils it plus a health probe URL so the UI can
show availability without pretending a service exists. Steps owned by sections
that are not in the deployment yet (6/8/9) are flagged so the UI degrades
gracefully instead of dead-ending the first-run flow.
"""

from pydantic import BaseModel


class OnboardingStep(BaseModel):
    id: str
    order: int
    title: str
    description: str
    section: int
    # Service key probed for availability; None = purely informational step.
    serviceKey: str | None
    required: bool = True
    availableInDeployment: bool = True


class ServiceProbe(BaseModel):
    key: str
    name: str
    healthUrl: str


STEPS: list[OnboardingStep] = [
    OnboardingStep(
        id="connect_github",
        order=1,
        title="Connect GitHub",
        description=(
            "OAuth into GitHub. The app only ever asks for repo/PR scopes it needs; "
            "the token stays server-side inside the GitHub Integration Service."
        ),
        section=2,
        serviceKey="github",
    ),
    OnboardingStep(
        id="other_integrations",
        order=2,
        title="Other integrations",
        description="None required beyond GitHub for the MVP. Skip this step.",
        section=10,
        serviceKey=None,
        required=False,
    ),
    OnboardingStep(
        id="create_team",
        order=3,
        title="Create a team",
        description=(
            "A team can be exactly 1 human + N agent seats — solo developers are "
            "first-class, never a minimum team size."
        ),
        section=1,
        serviceKey="identity",
    ),
    OnboardingStep(
        id="invite_members",
        order=4,
        title="Invite members",
        description=(
            "Invite teammates by email with roles: admin, engineer, viewer, or guest "
            "(guests can review agent output but never approve plans or merge)."
        ),
        section=1,
        serviceKey="identity",
    ),
    OnboardingStep(
        id="start_meeting",
        order=5,
        title="Start a meeting",
        description=(
            "Open the Meeting Area, discuss the task with the team, optionally invoke "
            "the AI participant for research or a summary, then generate and approve "
            "an engineering brief."
        ),
        section=6,
        serviceKey="meeting",
        availableInDeployment=False,
    ),
    OnboardingStep(
        id="guided_demo",
        order=6,
        title="Open the guided demo",
        description=(
            "Run the whole loop once on the seeded demo repository before connecting "
            "a real private repo. See the Guided Demo walkthrough."
        ),
        section=10,
        serviceKey="onboarding",
    ),
]


def steps_payload() -> list[dict]:
    return [s.model_dump() for s in STEPS]
