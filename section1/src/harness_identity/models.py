import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

JSONVariant = JSON().with_variant(JSONB(), "postgresql")
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")

# M15-D1 (W5-C2): every ForeignKey below carries an explicit ondelete=.
# The deletion law, enforced by tests/test_fk_deletion_story.py:
#   CASCADE  - owned children die with their owner (project -> runs, ...)
#   SET NULL - optional back-references survive the referenced row
#   RESTRICT - append-only integrity (audit_log, cost_ledger) is never
#              deleted or orphaned as a side effect.


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
    # A1-S5 (0038): self-serve workspace close. Non-NULL = nobody can authenticate into it.
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TeamMember(Base, TimestampMixin):
    __tablename__ = "team_members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Membership outlives the linked login account (agents have no User row).
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    member_type: Mapped[str] = mapped_column(
        Enum(MemberType, native_enum=False, length=16), nullable=False, default="human"
    )
    role: Mapped[str] = mapped_column(Enum(Role, native_enum=False, length=16), nullable=False)
    # A1-S4 (0037): SCIM deprovisioning. Non-NULL = cannot authenticate; row (and its
    # audit history) is kept.
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    repo_full_name: Mapped[str | None] = mapped_column(String(400), nullable=True)


class GithubConnection(Base, TimestampMixin):
    __tablename__ = "github_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    github_login: Mapped[str] = mapped_column(String(100), nullable=False)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)


class Meeting(Base, TimestampMixin):
    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")


class MeetingMessage(Base, TimestampMixin):
    __tablename__ = "meeting_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("team_members.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="human")
    body: Mapped[str] = mapped_column(Text, nullable=False)


class EngineeringBriefRecord(Base, TimestampMixin):
    __tablename__ = "engineering_briefs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requirements: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    constraints: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    acceptance_criteria: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    risks: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    open_questions: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Losing the source brief keeps the run (its events/plans/spend stay).
    brief_id: Mapped[str | None] = mapped_column(
        ForeignKey("engineering_briefs.id", ondelete="SET NULL")
    )
    branch: Mapped[str | None] = mapped_column(String(300))
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="idle")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    # NOTE M15-D1: agent_runs.pr_id (migrations 0017 + 0023; -> pull_requests.id
    # ON DELETE SET NULL) deliberately has NO ORM mirror yet: mapping it here
    # creates an ORM-level runs<->PRs reference cycle that breaks single-flush
    # insert ordering (FK violation on every fresh run+PR commit). Adoption
    # belongs with a follow-up lane that adds use_alter/explicit relationships;
    # until then the column stays SQL-side, fully pinned in 0023 and in
    # tests/test_fk_deletion_story.py.


class AgentPlanRecord(Base, TimestampMixin):
    __tablename__ = "agent_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    files_likely_to_change: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    steps: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    tests: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    risks: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    # H-023: money columns carry exact Python Decimal over NUMERIC(12,6).
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal("0")
    )
    forbidden_paths: Mapped[list] = mapped_column(JSONVariant, nullable=False, default=list)
    expected_result: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="proposed")


class AgentEvent(Base):
    __tablename__ = "agent_events"

    seq: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True
    )
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PullRequest(Base, TimestampMixin):
    __tablename__ = "pull_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"))
    github_pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    pr_url: Mapped[str] = mapped_column(String(500), nullable=False)
    summary_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="open")
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ReviewComment(Base, TimestampMixin):
    __tablename__ = "review_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    pr_id: Mapped[str] = mapped_column(
        ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    file_path: Mapped[str | None] = mapped_column(String(500))
    line: Mapped[int | None] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    key_hint: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A1-S2 (migration 0035): NULL = team key; set = personal key of that user.
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )


class KeyShare(Base):
    """A1-S2: owner-offered, recipient-consented grant to use a personal key."""

    __tablename__ = "key_shares"
    __table_args__ = (
        Index(
            "uq_key_shares_live",
            "api_key_id",
            "recipient_user_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'active')"),
            sqlite_where=text("status IN ('pending', 'active')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    api_key_id: Mapped[str] = mapped_column(
        ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    recipient_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    spend_cap_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    committed_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal("0")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KeyShareSpend(Base):
    """A1-S2: one reservation against a share; 'unsettled' = usage unknown."""

    __tablename__ = "key_share_spend"
    __table_args__ = (UniqueConstraint("share_id", "idempotency_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    share_id: Mapped[str] = mapped_column(
        ForeignKey("key_shares.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    actual_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="reserved")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelAllowlistEntry(Base):
    """A1-S3 (migration 0036): org model allowlist row with approval status."""

    __tablename__ = "model_allowlist_entries"
    __table_args__ = (UniqueConstraint("team_id", "model"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    requested_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PackageTrustIssuer(Base):
    """A1-S4 (0037): team-trusted Ed25519 package-signing key."""

    __tablename__ = "package_trust_issuers"
    __table_args__ = (UniqueConstraint("team_id", "key_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    key_id: Mapped[str] = mapped_column(String(120), nullable=False)
    issuer: Mapped[str] = mapped_column(String(200), nullable=False)
    public_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="active")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PackageRevocation(Base):
    __tablename__ = "package_revocations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str | None] = mapped_column(String(200))
    version: Mapped[str | None] = mapped_column(String(100))
    sha256: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ScimToken(Base):
    __tablename__ = "scim_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RetentionPolicy(Base):
    __tablename__ = "retention_policies"
    __table_args__ = (
        Index(
            "uq_retention_project",
            "team_id",
            "project_id",
            "resource",
            unique=True,
            postgresql_where=text("project_id IS NOT NULL"),
            sqlite_where=text("project_id IS NOT NULL"),
        ),
        Index(
            "uq_retention_team",
            "team_id",
            "resource",
            unique=True,
            postgresql_where=text("project_id IS NULL"),
            sqlite_where=text("project_id IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    resource: Mapped[str] = mapped_column(String(40), nullable=False)
    retain_days: Mapped[int] = mapped_column(Integer, nullable=False)
    legal_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hold_reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TeamTrial(Base):
    """A1-S5 (0038): one trial per team, ever."""

    __tablename__ = "team_trials"

    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    plan: Mapped[str] = mapped_column(String(30), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectRule(Base, TimestampMixin):
    """M12-G10: user-authored project rules (.cursorrules equivalent).

    One advisory rules document per project; the S4 planner injects it into
    its prompt at plan-proposal time. Advisory only — Section 5's sandbox
    allow-list and forbidden_paths remain the hard enforcement layer.
    """

    __tablename__ = "project_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    # 64KB cap mirrors MAX_RULES_BYTES in the API layer and the migration CHECK.
    rules_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Explicit (not server_default) so the API can bump it on every save;
    # real datetime object per the asyncpg TIMESTAMPTZ rule, never text-compared.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ModelConfig(Base, TimestampMixin):
    __tablename__ = "model_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_role: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    params: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)


class CostLedger(Base):
    __tablename__ = "cost_ledger"

    seq: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    # M15-D1: ledger rows are append-only money history. RESTRICT (not SET
    # NULL, despite the nullable column) so spend can never be detached from
    # the run that caused it, nor vanish when the run goes.
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), index=True
    )
    agent_role: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CostLedgerDaily(Base):
    """M18-C3: daily per-team spend rollup (migration 0027).

    One row per (team_id, day) for CLOSED days only (created_at < UTC
    midnight, DB clock); today's partial day is never stored -- the budget
    gate always sums that live. Fed by deploy/runners/cost_rollup.py, which
    recomputes closed days from source-of-truth cost_ledger (DELETE+INSERT,
    self-healing against release() row deletions). The gate reads this table
    when it has coverage and falls back to the legacy whole-ledger SUM while
    it does not, so correctness never depends on feed freshness.
    """

    __tablename__ = "cost_ledger_daily"

    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[datetime] = mapped_column(Date(), primary_key=True)
    total_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal("0")
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    # M15-D1: the audit trail is append-only integrity — RESTRICT blocks
    # member deletion while entries reference them (explicit form of the
    # NO ACTION default that already governed Postgres here).
    actor_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("team_members.id", ondelete="RESTRICT")
    )
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
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
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


class TeamInvite(Base):
    """Wave 9 node C: org invite tokens (mirror of 0008_team_invites.sql).

    Raw tokens are shown once at mint time; only SHA-256 digests are stored.
    """

    __tablename__ = "team_invites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # M14-F2a: admin revoke keeps the row for the audit trail (status derivation)
    # instead of deleting it — NULL until an admin revokes.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # M14-F2a: when the invite was sent. Mint stamps it immediately (the raw
    # token is handed to the admin to deliver); a future mailer lane flips
    # rows to draft by clearing it. NULL => status "draft".
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserSetting(Base):
    """M12-G7: per-user settings KV (mirror of 0016_user_settings.sql).

    Deliberately generic (user_id, key) -> text value so future preferences
    land without another migration. Known keys live as module constants next
    to their readers — notifier.py reads ``notify_email`` ("true"/"false");
    absent row means the documented default, never an error.
    """

    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class KeyDerivationParams(Base):
    """M15-W5-S4: per-install salted-KDF parameters (mirror of migration 0022).

    One row, id=1. Holds the random per-install salt and the KDF identity used
    to stretch Settings.master_key into the Fernet payload key. Absent row =>
    legacy unsalted SHA-256 derivation (pre-0022 installs), which is what makes
    the dual-read transition window work with zero data loss.

    This table holds KEY-DERIVATION PARAMETERS ONLY — never key material. The
    salt is not secret (it must be readable to re-derive); it must however be
    stable per install or every stored payload becomes undecryptable.
    """

    __tablename__ = "key_derivation_params"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    algorithm: Mapped[str] = mapped_column(String(20), nullable=False)
    # Salt is a parameter, not a secret; stored raw for exact byte fidelity.
    salt: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    scrypt_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scrypt_r: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scrypt_p: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Transition marker: NULL/False => dual-read window open (legacy payloads
    # may still exist); True => one-shot re-encrypt completed, legacy read
    # path no longer needed.
    reencrypted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TeamSetting(Base):
    """M15-G6b: per-team settings KV (mirror of 0024_team_settings.sql).

    Team-scope sibling of UserSetting. Known key: ``reviewer_thresholds``
    (JSON document consumed by Section 8's reviewer heuristic); the writer is
    the admin-gated PUT in settings_api.py, readers degrade to documented
    defaults when the row is absent or malformed.
    """

    __tablename__ = "team_settings"

    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # M15-D1 sweep: optional back-reference survives the referenced user.
    updated_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
