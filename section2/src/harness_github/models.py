"""S2-local mappings of ONLY the frozen-schema tables this service touches."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))


class TeamMember(Base):
    __tablename__ = "team_members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    member_type: Mapped[str] = mapped_column(String(16), default="human")
    role: Mapped[str] = mapped_column(String(16), default="engineer")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    team_id: Mapped[str] = mapped_column(String(36))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(200))
    repo_full_name: Mapped[str | None] = mapped_column(String(400))


class GithubConnection(Base):
    __tablename__ = "github_connections"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    github_login: Mapped[str] = mapped_column(String(100))
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    scopes: Mapped[str] = mapped_column(Text, default="[]")


class OAuthState(Base):
    """Mirror of migration 0005's oauth_states (TEXT timestamps, ISO-8601 UTC)."""

    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    expires_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class PullRequest(Base):
    """Mirror of the frozen pull_requests table (0001). Written by the PR-open
    producer (pr_open.py); FKs exist in SQL but are intentionally not declared
    here — same convention as TeamMember above."""

    __tablename__ = "pull_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36))
    github_pr_number: Mapped[int] = mapped_column(Integer)
    pr_url: Mapped[str] = mapped_column(String(500))
    summary_md: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="open")
    approved_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentRun(Base):
    """Mirror of the frozen agent_runs table (0001 + 0017's pr_id column).
    Read/written by the PR-open producer; never migrated here — column set
    tracks section1/migrations, FKs live in SQL only (see PullRequest note)."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    brief_id: Mapped[str | None] = mapped_column(String(36))
    branch: Mapped[str | None] = mapped_column(String(300))
    state: Mapped[str] = mapped_column(String(40), default="idle")
    # migration 0017: opened PR for this run (NULL until then)
    pr_id: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
