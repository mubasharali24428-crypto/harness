"""Unit tests for brief generator."""

import pytest
from harness_contracts.brief import BriefStatus, EngineeringBrief

from harness_meeting.brief_generator import BriefGenerator


def test_generate_brief_returns_draft():
    gen = BriefGenerator()
    brief = gen.generate_brief("mtg-1", "proj-1", "We need requirement X")
    assert isinstance(brief, EngineeringBrief)
    assert brief.status == BriefStatus.DRAFT
    assert brief.meetingId == "mtg-1"
    assert brief.projectId == "proj-1"


def test_brief_extracts_requirements():
    gen = BriefGenerator()
    transcript = (
        "[user-1]: We have a requirement for auth\n[user-2]: Another requirement is rate limiting"
    )
    brief = gen.generate_brief("mtg-2", "proj-2", transcript)
    assert len(brief.requirements) >= 2


def test_brief_extracts_risks():
    gen = BriefGenerator()
    transcript = "[user-1]: Risk: breaking the auth middleware"
    brief = gen.generate_brief("mtg-3", "proj-3", transcript)
    assert len(brief.risks) >= 1


def test_approve_brief_transitions_to_approved():
    gen = BriefGenerator()
    brief = gen.generate_brief("mtg-4", "proj-4", "requirement here")
    gen.approve_brief(brief.id, "user-lead")
    assert brief.status == BriefStatus.APPROVED


def test_reject_brief_stays_draft():
    gen = BriefGenerator()
    brief = gen.generate_brief("mtg-5", "proj-5", "requirement here")
    gen.reject_brief(brief.id, "user-lead")
    assert brief.status == BriefStatus.DRAFT


def test_approve_already_approved_raises():
    gen = BriefGenerator()
    brief = gen.generate_brief("mtg-6", "proj-6", "req")
    gen.approve_brief(brief.id, "user-lead")
    with pytest.raises(ValueError):
        gen.approve_brief(brief.id, "user-lead")


def test_get_nonexistent_brief_raises():
    gen = BriefGenerator()
    with pytest.raises(KeyError):
        gen.get_brief("brief-nonexistent")


def test_brief_has_all_five_fields():
    gen = BriefGenerator()
    brief = gen.generate_brief("mtg-7", "proj-7", "discussion text")
    assert hasattr(brief, "requirements")
    assert hasattr(brief, "constraints")
    assert hasattr(brief, "acceptanceCriteria")
    assert hasattr(brief, "risks")
    assert hasattr(brief, "openQuestions")
