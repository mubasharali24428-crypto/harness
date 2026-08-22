"""Content conformance to the plan: onboarding order, checklist topics, demo loop."""

from pathlib import Path

from harness_contracts.events import EVENT_TYPES

from harness_onboarding.checklist import CHECKLIST
from harness_onboarding.demo_repo import MANIFEST
from harness_onboarding.guided_demo import DEMO
from harness_onboarding.steps import STEPS

DEMO_REPO_DIR = Path(__file__).resolve().parents[2] / "demo_repo"


class TestOnboardingOrder:
    def test_frozen_order(self):
        assert [s.id for s in STEPS] == [
            "connect_github",
            "other_integrations",
            "create_team",
            "invite_members",
            "start_meeting",
            "guided_demo",
        ]

    def test_steps_are_ordered_and_unique(self):
        orders = [s.order for s in STEPS]
        assert orders == sorted(orders)
        assert len({s.id for s in STEPS}) == len(STEPS)

    def test_only_integrations_step_is_optional(self):
        optional = [s.id for s in STEPS if not s.required]
        assert optional == ["other_integrations"]

    def test_meeting_step_flagged_not_yet_deployed(self):
        meeting = next(s for s in STEPS if s.id == "start_meeting")
        assert meeting.availableInDeployment is False
        assert meeting.section == 6


class TestSecurityChecklist:
    def test_covers_mandated_topics(self):
        ids = {i.id for i in CHECKLIST.items}
        assert {"sandbox_allowlist", "sandbox_blocklist", "network_egress"} <= ids
        assert "secrets_never_reach_model" in ids
        assert "no_auto_merge" in ids

    def test_every_item_names_an_enforcement_point(self):
        for item in CHECKLIST.items:
            assert item.enforcedBy, f"{item.id} must cite where it is enforced"
            assert item.statement


class TestGuidedDemo:
    def test_walks_the_exact_mvp_loop(self):
        ids = [s.id for s in DEMO.steps]
        assert ids == [
            "meeting_discuss",
            "brief_generate",
            "plan_proposed",
            "plan_approved",
            "execution_watch",
            "pause_edit",
            "done_signal",
            "reviewer_pass",
            "draft_pr",
            "review_gate",
            "manual_merge",
        ]

    def test_expected_events_are_real_contract_events(self):
        for step in DEMO.steps:
            if step.expectedEvent is not None:
                assert step.expectedEvent in EVENT_TYPES

    def test_ends_with_manual_merge(self):
        assert DEMO.steps[-1].area == "github"
        assert "Merge manually" in DEMO.steps[-1].title

    def test_done_signal_step_mentions_explicit_signal_only(self):
        step = next(s for s in DEMO.steps if s.id == "done_signal")
        assert "never per-keystroke" in step.description


class TestDemoRepo:
    def test_manifest_files_exist_on_disk(self):
        for rel in MANIFEST.files:
            assert (DEMO_REPO_DIR / rel).exists(), f"demo repo missing {rel}"

    def test_demo_repo_has_no_secretish_files(self):
        names = {p.name.lower() for p in DEMO_REPO_DIR.rglob("*") if p.is_file()}
        assert not any(
            n.startswith(".env") or "secret" in n or n.endswith(".pem") for n in names
        )

    def test_task_forbids_touching_the_tests(self):
        assert "tests/test_stats.py" in MANIFEST.task.suggestedForbiddenPaths
