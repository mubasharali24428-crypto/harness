"""Shared test fixtures and helpers for Section 8 tests."""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_review.service import ReviewService
from tests.fixtures.golden_fixtures import (
    GOLDEN_DIFF,
    GOLDEN_GITHUB_PR_NUMBER,
    GOLDEN_PR_ID,
    GOLDEN_PR_URL,
    GOLDEN_PROJECT_ID,
    GOLDEN_RUN_ID,
)


def _seed_db_sql() -> list[str]:
    """SQL statements to seed the DB with a golden PR record."""
    now = datetime.now(UTC).isoformat()
    return [
        "INSERT INTO teams (id, name) VALUES ('t1', 'Team One')",
        "INSERT INTO users (id, email, name, is_active) VALUES ('u1','u@x.com','U',1)",
        "INSERT INTO team_members (id, team_id, user_id, display_name, member_type, role) "
        "VALUES ('m1','t1','u1','U','human','admin')",
        f"INSERT INTO auth_sessions (token_hash, user_id, team_id, expires_at) "
        f"VALUES ('{uuid.uuid4().hex}', 'u1', 't1', '2100-01-01T00:00:00Z')",
        f"INSERT INTO projects (id, team_id, name, repo_full_name) "
        f"VALUES ('{GOLDEN_PROJECT_ID}', 't1', 'demo', 'org/demo-repo')",
        f"INSERT INTO agent_runs (id, project_id, branch, state) "
        f"VALUES ('{GOLDEN_RUN_ID}', '{GOLDEN_PROJECT_ID}', 'agent/demo', 'pr_opened')",
        f"INSERT INTO pull_requests (id, project_id, run_id, github_pr_number, pr_url, "
        f"summary_md, status, approved_by, created_at) "
        f"VALUES ('{GOLDEN_PR_ID}', '{GOLDEN_PROJECT_ID}', '{GOLDEN_RUN_ID}', "
        f"{GOLDEN_GITHUB_PR_NUMBER}, '{GOLDEN_PR_URL}',"
        f"'pending review', 'pending', NULL, '{now}')",
        f"INSERT INTO agent_events (project_id, run_id, type, payload) "
        f"VALUES ('{GOLDEN_PROJECT_ID}', '{GOLDEN_RUN_ID}', 'execution_started', '{{}}')",
    ]


class FakeGitHubService:
    """Minimal stand-in for Section 2's GitHubService for Section 8 tests."""

    def __init__(self, pr_status: dict | None = None):
        self._pr_status = pr_status or {}
        self.requests: list[tuple[str, ...]] = []

    async def getPRStatus(self, user_id: str, project_id: str, pr_number: int) -> dict:
        self.requests.append(("getPRStatus", project_id, str(pr_number)))
        return self._pr_status or {
            "number": GOLDEN_GITHUB_PR_NUMBER,
            "state": "open",
            "draft": True,
            "merged": False,
            "mergeable": True,
            "url": GOLDEN_PR_URL,
            "headBranch": "agent/demo",
            "baseBranch": "main",
        }


class FakeModelRouter:
    """Minimal stand-in for Section 3's ModelRouter for Section 8 tests."""

    def __init__(self, response_text: str | None = None):
        self._response_text = response_text or (
            "WHAT CHANGED\nThe PR adds auth verification.\n\n"
            "WHY\nSecurity hardening was requested.\n\n"
            "WHAT DIDN'T CHANGE\nThe main application loop was untouched.\n\n"
            "WHAT NEEDS REVIEW\nAuth middleware logic and test coverage.\n\n"
            "WHAT'S RISKY\nCritical severity flagged on auth/ directory."
        )
        self.call_history: list[dict] = []

    def call_model(self, agent_id: str, role, messages: list[dict[str, str]], opts=None) -> object:
        self.call_history.append({"agent_id": agent_id, "role": role, "messages": messages})
        from harness_contracts.signatures import ModelResult

        return ModelResult(
            text=self._response_text,
            costUsd=0.001,
            model="fake-model",
            latencyMs=12,
        )

    def estimate_cost(self, role, input_tokens: int, output_tokens: int) -> float:
        return 0.001

    def list_available_models(self) -> list[str]:
        return ["fake-model"]


class FakeAuditWriter:
    """Records audit writes for assertion in tests."""

    def __init__(self):
        self.writes: list[dict] = []

    async def __call__(self, session, action: str, **kwargs):
        entry = {"action": action, **kwargs}
        self.writes.append(entry)


def golden_diff_provider(project_id: str, github_pr_number: int) -> str:
    """A DiffProvider callable that returns the golden diff."""
    return GOLDEN_DIFF


@pytest.fixture
async def db_engine():
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "section1" / "src"))

    engine = create_async_engine("sqlite+aiosqlite://")
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(db_engine):
    from harness_identity.models import Base as S1Base

    async with db_engine.begin() as conn:
        await conn.run_sync(S1Base.metadata.create_all)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    yield factory


@pytest.fixture
async def seeded_db(db):
    async with db() as s:
        for stmt in _seed_db_sql():
            await s.execute(text(stmt))
        await s.commit()
    yield db


@pytest.fixture
def fake_github():
    return FakeGitHubService()


@pytest.fixture
def fake_router():
    return FakeModelRouter()


@pytest.fixture
def fake_audit():
    return FakeAuditWriter()


@pytest.fixture
def review_service(seeded_db, fake_github, fake_router, fake_audit):
    return ReviewService(
        session_factory=seeded_db,
        github_service=fake_github,
        model_router=fake_router,
        diff_provider=golden_diff_provider,
        write_audit=fake_audit,
    )
