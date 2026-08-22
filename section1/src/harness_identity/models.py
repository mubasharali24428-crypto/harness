import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

JSONVariant = JSON().with_variant(JSONB(), "postgresql")
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


def uid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class MemberType(enum.StrEnum):
    HUMAN = "human"
    AGENT = "agent"


class Role(enum.StrEnum):
    ADMIN = "admin"
    ENGINEER = "engineer"
    VIEWER = "viewer"
    GUEST = "guest"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Team(Base, TimestampMixin):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class TeamMember(Base, TimestampMixin):
    __tablename__ = "team_members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    member_type: Mapped[str] = mapped_column(
        Enum(MemberType, native_enum=False, length=16), nullable=False, default="human"
    )
    role: Mapped[str] = mapped_column(Enum(Role, native_enum=False, length=16), nullable=False)


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    repo_full_name: Mapped[str | None] = mapped_column(String(400), nullable=True)


class GithubConnection(Base, TimestampMixin):
    __tablename__ = "github_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    github_login: Mapped[str] = mapped_column(String(100), nullable=False)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)


class Meeting(Base, TimestampMixin):
    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")


class MeetingMessage(Base, TimestampMixin):
    __tablename__ = "meeting_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), nullable=False, index=True)
    sender_member_id: Mapped[str | None] = mapped_column(ForeignKey("team_members.id"))
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="human")
    body: Mapped[str] = mapped_column(Text, nullable=False)


class EngineeringBriefRecord(Base, TimestampMixin):
    __tablename__ = "engineering_briefs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    requirements: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    constraints: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    acceptance_criteria: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    risks: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    open_questions: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    brief_id: Mapped[str | None] = mapped_column(ForeignKey("engineering_briefs.id"))
    branch: Mapped[str | None] = mapped_column(String(300))
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="idle")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AgentPlanRecord(Base, TimestampMixin):
    __tablename__ = "agent_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), nullable=False, index=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    files_likely_to_change: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    steps: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    tests: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    risks: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    estimated_cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    forbidden_paths: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    expected_result: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="proposed")


class AgentEvent(Base):
    __tablename__ = "agent_events"

    seq: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PullRequest(Base, TimestampMixin):
    __tablename__ = "pull_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"))
    github_pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    pr_url: Mapped[str] = mapped_column(String(500), nullable=False)
    summary_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="open")
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class ReviewComment(Base, TimestampMixin):
    __tablename__ = "review_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    pr_id: Mapped[str] = mapped_column(ForeignKey("pull_requests.id"), nullable=False, index=True)
    author_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    file_path: Mapped[str | None] = mapped_column(String(500))
    line: Mapped[int | None] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    key_hint: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelConfig(Base, TimestampMixin):
    __tablename__ = "model_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    agent_role: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    params: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)


class CostLedger(Base):
    __tablename__ = "cost_ledger"

    seq: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    agent_role: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    actor_member_id: Mapped[str | None] = mapped_column(ForeignKey("team_members.id"))
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="human")
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    target_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    detail: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LoginOtp(Base, TimestampMixin):
    """H2r: hashed single-use login codes proving inbox ownership.

    One active row per email — issuing a new code invalidates prior ones by
    replacement (issue_otp deletes older rows for the same address).
    """

    __tablename__ = "login_otps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
