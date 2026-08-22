"""Guided demo — brief §11, §16 Q10, §23 Q19.

Walks the exact MVP loop end to end on the demo repo. Steps that correspond to a
frozen event type carry it, so the UI can light the walkthrough up from the real
event stream (Section 1's WebSocket with replay) instead of guessing.
"""

from harness_contracts.events import EventType
from pydantic import BaseModel


class DemoStep(BaseModel):
    id: str
    order: int
    title: str
    area: str  # "onboarding" | "meeting" | "working" | "review" | "github"
    description: str
    expectedEvent: str | None = None
    section: int


class GuidedDemo(BaseModel):
    title: str
    intro: str
    repoHint: str
    steps: list[DemoStep]


DEMO = GuidedDemo(
    title="Guided demo: one task, end to end",
    intro=(
        "This walks the full MVP loop once on the seeded demo repository: meeting → "
        "brief → plan → approval → sandboxed execution → human edit → done signal → "
        "reviewer → draft PR → human review → manual merge. Nothing here touches a "
        "real repo, and every step is visible in the UI."
    ),
    repoHint=(
        "Push the demo repo (deploy/scripts/push_demo_to_github.sh) to a scratch "
        "GitHub repository, then connect it like any other repo."
    ),
    steps=[
        DemoStep(
            id="meeting_discuss",
            order=1,
            title="Discuss the task in the Meeting Area",
            area="meeting",
            description=(
                "Create a meeting for the demo task. The AI participant stays muted "
                "until explicitly invoked — invite it for quick research or a summary, "
                "not to fill the channel."
            ),
            section=6,
        ),
        DemoStep(
            id="brief_generate",
            order=2,
            title="Generate and approve the engineering brief",
            area="meeting",
            description=(
                "Invoke 'turn this into a brief'. Check the five fields — requirements, "
                "constraints, acceptance criteria, risks, open questions — then approve. "
                "The brief is what the agent plans against, so read it before approving."
            ),
            section=6,
        ),
        DemoStep(
            id="plan_proposed",
            order=3,
            title="Review the agent's proposed plan",
            area="working",
            description=(
                "The agent proposes a plan: goal, files likely to change, steps, tests, "
                "risks, estimated cost, forbidden paths, expected result. Nothing "
                "executes until you approve."
            ),
            expectedEvent=EventType.PLAN_PROPOSED,
            section=4,
        ),
        DemoStep(
            id="plan_approved",
            order=4,
            title="Approve the plan",
            area="working",
            description=(
                "Approving is the governance gate: this approval is audit-logged and "
                "the sandbox will refuse commands until it exists."
            ),
            expectedEvent=EventType.PLAN_APPROVED,
            section=4,
        ),
        DemoStep(
            id="execution_watch",
            order=5,
            title="Watch execution — terminal, diff, cost",
            area="working",
            description=(
                "The agent works on its own branch in an ephemeral sandbox. Watch live "
                "terminal output, the growing diff, test results, and cost metering."
            ),
            expectedEvent=EventType.EXECUTION_STARTED,
            section=7,
        ),
        DemoStep(
            id="pause_edit",
            order=6,
            title="Pause and take over with a human edit",
            area="working",
            description=(
                "Start editing a file in the Working Area — the agent pauses the "
                "moment you touch the buffer. No timer, no forced handover."
            ),
            expectedEvent=EventType.PAUSED,
            section=7,
        ),
        DemoStep(
            id="done_signal",
            order=7,
            title="Send the explicit done signal",
            area="working",
            description=(
                "Press the Done button (or Cmd+S / Cmd+Enter). Only this signal — "
                "never per-keystroke analysis — triggers the reviewer sub-agent."
            ),
            expectedEvent=EventType.DONE_SIGNAL_RECEIVED,
            section=4,
        ),
        DemoStep(
            id="reviewer_pass",
            order=8,
            title="Read the reviewer's verdict",
            area="working",
            description=(
                "The reviewer sub-agent checks syntax, conflicts with existing code, "
                "and brief alignment on a cheaper model. Its verdict is visible to the "
                "whole team before the agent resumes."
            ),
            expectedEvent=EventType.REVIEW_PASSED,
            section=4,
        ),
        DemoStep(
            id="draft_pr",
            order=9,
            title="Open the draft PR with the AI summary",
            area="review",
            description=(
                "The GitHub service opens a draft PR carrying the AI-written summary: "
                "what changed, why, what didn't change, what needs review, what's risky."
            ),
            expectedEvent=EventType.PR_OPENED,
            section=2,
        ),
        DemoStep(
            id="review_gate",
            order=10,
            title="Review in the Review Area",
            area="review",
            description=(
                "See the diff with high-risk zones highlighted (auth, payments, "
                "migrations, secrets). Comment inline; approve or request changes."
            ),
            section=8,
        ),
        DemoStep(
            id="manual_merge",
            order=11,
            title="Merge manually on GitHub",
            area="github",
            description=(
                "The final act is yours: click Merge on GitHub. The app cannot do it "
                "for you — by design."
            ),
            section=10,
        ),
    ],
)


def demo_payload() -> dict:
    return DEMO.model_dump()
