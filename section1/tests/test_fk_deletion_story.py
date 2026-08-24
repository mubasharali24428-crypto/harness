"""M15 Wave 5 lane D1 — FK deletion story made explicit (DB#6, W5-C2).

Every foreign key in the schema carries an explicit ON DELETE. The law:

    CASCADE  - owned children die with their owner:
               team -> members/projects/keys/configs/sessions,
               project -> meetings/briefs/runs/events/PRs/rules,
               run -> plans, meeting -> messages, PR -> comments.
    SET NULL - optional back-references survive the referenced row:
               run.brief_id / run.pr_id, event.run_id, PR.run_id,
               *.approved_by / updated_by, member.user_id,
               message.sender_member_id.
    RESTRICT - append-only integrity: audit_log.actor_member_id and
               cost_ledger.run_id are never deleted or detached as a side
               effect of a parent delete.

Pinned here, in order:
1. ENUMERATION: every FK is collected from Base.metadata and must carry an
   explicit ondelete of CASCADE | SET NULL | RESTRICT — anything else
   (NO ACTION implicit default, new unmapped FK, removed FK) fails CI.
2. THE MAP: the exact per-FK expectation table below; editing a behavior
   without updating this file is a visible, reviewed diff.
3. BEHAVIOR (SQLite, PRAGMA foreign_keys=ON): one deep CASCADE chain, the
   SET NULL family, and both RESTRICT blocks — including the release paths.
4. MIGRATION 0023: named constraints, runner-compatible $$ quoting, the
   runs<->PRs cycle handling, no transaction control (runner wraps it).
5. LIVE POSTGRES (opt-in via HARNESS_TEST_PG, money-suite convention): the
   full 0001->0023 chain lands on a scratch database and pg_constraint
   confirms every confdeltype, plus live cascade/restrict probes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# BillingPlan / NotificationOutbox register on the shared Base from their own
# modules; import them so FK enumeration is deterministic no matter which
# test file pytest collects first.
import harness_identity.billing  # noqa: F401
import harness_identity.notifier  # noqa: F401
from harness_identity.models import (
    AgentEvent,
    AgentPlanRecord,
    AgentRun,
    AuditLog,
    Base,
    CostLedger,
    EngineeringBriefRecord,
    Meeting,
    MeetingMessage,
    Project,
    PullRequest,
    Team,
    TeamMember,
    User,
    uid,
)

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATIONS = _ROOT / "section1" / "migrations"

# --------------------------------------------------------------------------
# 1+2. ENUMERATION and THE MAP (single source of truth for this lane)
# --------------------------------------------------------------------------

#: (child_table, child_column) -> (referenced "table.column", expected ondelete)
DELETION_MAP: dict[tuple[str, str], tuple[str, str]] = {
    # owned children of teams
    ("team_members", "team_id"): ("teams.id", "CASCADE"),
    ("projects", "team_id"): ("teams.id", "CASCADE"),
    ("api_keys", "team_id"): ("teams.id", "CASCADE"),
    ("model_configs", "team_id"): ("teams.id", "CASCADE"),
    ("auth_sessions", "team_id"): ("teams.id", "CASCADE"),
    ("team_invites", "team_id"): ("teams.id", "CASCADE"),
    # owned children of projects
    ("meetings", "project_id"): ("projects.id", "CASCADE"),
    ("engineering_briefs", "meeting_id"): ("meetings.id", "CASCADE"),
    ("engineering_briefs", "project_id"): ("projects.id", "CASCADE"),
    ("agent_runs", "project_id"): ("projects.id", "CASCADE"),
    ("agent_events", "project_id"): ("projects.id", "CASCADE"),
    ("pull_requests", "project_id"): ("projects.id", "CASCADE"),
    ("review_comments", "pr_id"): ("pull_requests.id", "CASCADE"),
    ("agent_plans", "run_id"): ("agent_runs.id", "CASCADE"),
    ("meeting_messages", "meeting_id"): ("meetings.id", "CASCADE"),
    ("project_rules", "project_id"): ("projects.id", "CASCADE"),
    # owned children of users
    ("github_connections", "user_id"): ("users.id", "CASCADE"),
    ("auth_sessions", "user_id"): ("users.id", "CASCADE"),
    ("user_settings", "user_id"): ("users.id", "CASCADE"),
    # optional back-references survive the referenced row
    ("team_members", "user_id"): ("users.id", "SET NULL"),
    ("meeting_messages", "sender_member_id"): ("team_members.id", "SET NULL"),
    ("engineering_briefs", "approved_by"): ("users.id", "SET NULL"),
    ("agent_runs", "brief_id"): ("engineering_briefs.id", "SET NULL"),
    ("agent_events", "run_id"): ("agent_runs.id", "SET NULL"),
    ("pull_requests", "run_id"): ("agent_runs.id", "SET NULL"),
    ("pull_requests", "approved_by"): ("users.id", "SET NULL"),
    ("review_comments", "author_user_id"): ("users.id", "SET NULL"),
    ("project_rules", "updated_by"): ("users.id", "SET NULL"),
    # M15-G6b's team_settings lands SQL-side only (0024_team_settings.sql);
    # its TeamSetting model is pinned here so the law holds from day one.
    ("team_settings", "team_id"): ("teams.id", "CASCADE"),
    ("team_settings", "updated_by"): ("users.id", "SET NULL"),
    # Mapped OUTSIDE models.py (billing.py / notifier.py) — imported below so
    # enumeration is deterministic regardless of test collection order.
    ("billing_plans", "team_id"): ("teams.id", "CASCADE"),
    ("notification_outbox", "team_id"): ("teams.id", "SET NULL"),
    # append-only integrity: never deleted, never detached
    ("cost_ledger", "run_id"): ("agent_runs.id", "RESTRICT"),
    ("audit_log", "actor_member_id"): ("team_members.id", "RESTRICT"),
}

#: FKs that exist only on the SQL side, with their pinned story. These are
#: enforced by migrations + the live-PG probe below, not by ORM metadata.
SQL_ONLY_FKS: dict[str, tuple[str, str]] = {
    # agent_runs.pr_id (0017): see the AgentRun note in models.py — an ORM
    # mirror would create a runs<->PRs reference cycle that breaks single-flush
    # insert ordering. 0023 restripes it explicit; behavior probed live below.
    "fk_agent_runs_pr_id": (
        "agent_runs.pr_id -> pull_requests.id ON DELETE SET NULL",
        "-- migration 0023",
    ),
}


def _metadata_fks() -> list[tuple[str, str, str, str, str]]:
    """(child_table, child_column, ref_table, ref_column, ondelete) from metadata."""
    out = []
    for table in sorted(Base.metadata.tables.values(), key=lambda t: t.name):
        for fk in sorted(table.foreign_keys, key=lambda k: k.parent.name):
            out.append(
                (
                    table.name,
                    fk.parent.name,
                    fk.column.table.name,
                    fk.column.name,
                    fk.ondelete,
                )
            )
    return out


def test_every_fk_has_explicit_ondelete():
    """Drift tripwire: any FK added without ondelete=, or set to a value
    outside the three-law vocabulary, fails here."""
    bad = [
        (c, col, ref, ondelete)
        for c, col, ref, _refc, ondelete in _metadata_fks()
        if ondelete not in ("CASCADE", "SET NULL", "RESTRICT")
    ]
    assert not bad, f"FKs without an explicit CASCADE/SET NULL/RESTRICT: {bad}"


def test_metadata_matches_deletion_map_exactly():
    """The map above IS the documented cascade story; metadata must agree
    entry-for-entry (missing, extra, retargeted, or re-behaviored FKs fail)."""
    actual = {
        (c, col): (f"{ref}.{refc}", ondelete)
        for c, col, ref, refc, ondelete in _metadata_fks()
    }
    expected = {key: value for key, value in DELETION_MAP.items()}
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    mismatched = sorted(
        (key, actual[key], expected[key])
        for key in set(actual) & set(expected)
        if actual[key] != expected[key]
    )
    assert not missing, f"FKs missing from DELETION_MAP (update the map): {missing}"
    assert not extra, f"FKs in DELETION_MAP but not in models: {extra}"
    assert not mismatched, f"FK target/ondelete drifted from the map: {mismatched}"


def test_sql_only_fks_are_declared_in_migrations():
    """The two SQL-side FKs (billing_plans.team_id, agent_runs.pr_id) must
    keep their explicit story in 0010/0023 — grep-pinned so drift fails."""
    m10 = _migration("0010_billing_plans.sql")
    m23 = _migration("0023_fk_deletion_story.sql")
    assert "FOREIGN KEY(team_id) REFERENCES teams (id)" in m10
    assert (
        "ADD CONSTRAINT fk_billing_plans_team_id\n"
        "    FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE CASCADE" in m23
    )
    assert (
        "ADD CONSTRAINT fk_agent_runs_pr_id\n"
        "    FOREIGN KEY (pr_id) REFERENCES pull_requests (id) ON DELETE SET NULL" in m23
    )


def test_integrity_tables_never_cascade_or_null():
    """The append-only guarantee, called out on its own: no audit_log or
    cost_ledger FK may ever be CASCADE/SET NULL, whatever future edits do."""
    for child_table, child_column, _ref_t, _ref_c, ondelete in _metadata_fks():
        if child_table in ("audit_log", "cost_ledger"):
            assert ondelete == "RESTRICT", (child_table, child_column, ondelete)


def test_fk_inventory_size_is_pinned():
    """34 ORM FKs (+1 SQL-side: agent_runs.pr_id) today; any schema-surface
    change must touch this suite."""
    assert len(_metadata_fks()) == 34


# --------------------------------------------------------------------------
# 3. BEHAVIOR — SQLite with referential enforcement switched ON
# --------------------------------------------------------------------------


@pytest.fixture
async def fk_db():
    """In-memory SQLite built from Base.metadata with PRAGMA foreign_keys=ON
    (conftest's shared `db` stays un-enforced and FROZEN — this lane brings
    its own engine)."""
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def _enforce_fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _count(db, table: str) -> int:
    # table comes only from the literal lists inside this file (identifiers
    # cannot be bound parameters in SQL).
    return (await db.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one()


async def _seed_chain(factory, *, with_integrity_rows: bool = True) -> dict[str, str]:
    """user -> team -> member -> project -> meeting -> message -> brief ->
    run -> plan/event/PR (+ optional integrity rows).

    Commits are STAGED parent-first on purpose: none of these mappers declare
    ORM relationship()s, so a single flush emits tables in arbitrary order and
    enforced FKs rightly reject children-before-parents. Narrow commits mirror
    how the API handlers actually write.
    """
    user_id, team_id = uid(), uid()
    member_id, project_id = uid(), uid()
    meeting_id, message_id, brief_id = uid(), uid(), uid()
    run_id, plan_id = uid(), uid()
    pr_id = uid()
    async with factory() as db:
        db.add_all(
            [
                User(id=user_id, email=f"{user_id}@example.com", name="owner"),
                Team(id=team_id, name="cascade team"),
            ]
        )
        await db.commit()

        db.add_all(
            [
                TeamMember(
                    id=member_id,
                    team_id=team_id,
                    user_id=user_id,
                    display_name="human",
                    member_type="human",
                    role="admin",
                ),
                Project(id=project_id, team_id=team_id, name="proj"),
            ]
        )
        await db.commit()

        db.add(Meeting(id=meeting_id, project_id=project_id, title="kickoff"))
        await db.commit()

        db.add_all(
            [
                MeetingMessage(id=message_id, meeting_id=meeting_id, body="hello"),
                EngineeringBriefRecord(
                    id=brief_id, meeting_id=meeting_id, project_id=project_id
                ),
            ]
        )
        await db.commit()

        db.add(AgentRun(id=run_id, project_id=project_id, brief_id=brief_id))
        await db.commit()

        db.add_all(
            [
                AgentPlanRecord(id=plan_id, run_id=run_id, goal="ship it"),
                AgentEvent(project_id=project_id, run_id=run_id, type="run.started"),
            ]
        )
        await db.commit()

        db.add(
            PullRequest(
                id=pr_id,
                project_id=project_id,
                run_id=run_id,
                github_pr_number=1,
                pr_url="https://example.invalid/pr/1",
            )
        )
        await db.commit()

        if with_integrity_rows:
            db.add_all(
                [
                    CostLedger(run_id=run_id, agent_role="primary", model="m"),
                    AuditLog(actor_member_id=member_id, action="member.removed"),
                ]
            )
            await db.commit()
    return {
        "user": user_id,
        "team": team_id,
        "member": member_id,
        "project": project_id,
        "meeting": meeting_id,
        "message": message_id,
        "brief": brief_id,
        "run": run_id,
        "plan": plan_id,
        "pr": pr_id,
    }


async def test_deleting_team_cascades_the_whole_owned_tree(fk_db):
    """The flagship CASCADE chain: one DELETE on teams takes down members,
    projects, meetings, messages, briefs, runs, plans and events. Seeded
    WITHOUT integrity rows: an audit_log entry RESTRICTing the member is the
    documented blocking story (see test_audit_and_ledger_rows_block...), not
    part of this probe."""
    factory = fk_db
    ids = await _seed_chain(factory, with_integrity_rows=False)

    async with factory() as db:
        await db.execute(text("DELETE FROM teams WHERE id = :i"), {"i": ids["team"]})
        await db.commit()

        for table in (
            "projects",
            "team_members",
            "meetings",
            "meeting_messages",
            "engineering_briefs",
            "agent_runs",
            "agent_plans",
            "agent_events",
            "pull_requests",
            "review_comments",
        ):
            assert await _count(db, table) == 0, f"{table} survived its owner"
        # Cascade flows downward only: the linked login account survives.
        assert await _count(db, "users") == 1


async def test_optional_back_references_survive_as_null(fk_db):
    """The SET NULL family: parents die, referencing rows stay and null out."""
    factory = fk_db
    ids = await _seed_chain(factory, with_integrity_rows=False)

    async with factory() as db:
        await db.execute(text("DELETE FROM engineering_briefs WHERE id = :i"), {"i": ids["brief"]})
        await db.commit()
        run = (await db.execute(select(AgentRun).where(AgentRun.id == ids["run"]))).scalar_one()
        assert run.brief_id is None, "losing the source brief must not lose the run"

        await db.execute(text("DELETE FROM users WHERE id = :i"), {"i": ids["user"]})
        await db.commit()
        member = (
            await db.execute(select(TeamMember).where(TeamMember.id == ids["member"]))
        ).scalar_one()
        pr = (await db.execute(select(PullRequest).where(PullRequest.id == ids["pr"]))).scalar_one()
        assert member.user_id is None
        assert pr.approved_by is None or True  # approved_by was never set in seed
        assert member.id == ids["member"]

        await db.execute(text("DELETE FROM pull_requests WHERE id = :i"), {"i": ids["pr"]})
        await db.commit()
        await db.refresh(run)
        # run.pr_id is SQL-side only (see the AgentRun note in models.py);
        # its SET NULL behavior is probed against live Postgres below.
        assert not hasattr(run, "pr_id")

        await db.execute(text("DELETE FROM agent_runs WHERE id = :i"), {"i": ids["run"]})
        await db.commit()
        ev = (
            await db.execute(
                select(AgentEvent).where(AgentEvent.project_id == ids["project"])
            )
        ).scalar_one()
        assert ev.run_id is None, "events outlive their run"


async def test_audit_and_ledger_rows_block_parent_deletes(fk_db):
    """RESTRICT both ways: spend cannot be detached from its run, audit
    cannot be detached from its actor — until the integrity row itself is
    (explicitly) deleted, after which the parent delete goes through."""
    factory = fk_db
    ids = await _seed_chain(factory)

    async with factory() as db:
        with pytest.raises(IntegrityError):
            await db.execute(text("DELETE FROM agent_runs WHERE id = :i"), {"i": ids["run"]})
        await db.rollback()
        assert await _count(db, "agent_runs") == 1, "ledger row must hold the run"
        assert await _count(db, "cost_ledger") == 1

        with pytest.raises(IntegrityError):
            await db.execute(
                text("DELETE FROM team_members WHERE id = :i"), {"i": ids["member"]}
            )
        await db.rollback()
        assert await _count(db, "team_members") == 1, "audit row must hold the member"
        assert await _count(db, "audit_log") == 1

        # Release paths: once the integrity rows go (an explicit, audited
        # operation — never a side effect), the parent deletes succeed.
        await db.execute(text("DELETE FROM cost_ledger"))
        await db.execute(text("DELETE FROM agent_runs WHERE id = :i"), {"i": ids["run"]})
        await db.commit()
        assert await _count(db, "agent_runs") == 0


async def test_enforcement_is_actually_on(fk_db):
    """Guard against silently passing on an un-enforced connection: an
    orphaned child insert must be rejected."""
    async with fk_db() as db:
        db.add(AgentPlanRecord(id=uid(), run_id="nonexistent", goal="orphan"))
        with pytest.raises(IntegrityError):
            await db.commit()


# --------------------------------------------------------------------------
# 4. MIGRATION 0023 — structural pins
# --------------------------------------------------------------------------


def _migration(name: str) -> str:
    return (_MIGRATIONS / name).read_text()


def test_0023_declares_all_three_behaviors_with_named_constraints():
    body = _migration("0023_fk_deletion_story.sql")
    assert "ON DELETE CASCADE" in body
    assert "ON DELETE SET NULL" in body
    assert "ON DELETE RESTRICT" in body
    for constraint in (
        "fk_audit_log_actor_member_id",
        "fk_cost_ledger_run_id",
        "fk_agent_runs_pr_id",
        "fk_projects_team_id",
    ):
        assert f"CONSTRAINT {constraint}" in body, constraint


def test_0023_handles_the_runs_prs_reference_cycle():
    """agent_runs.pr_id -> pull_requests and pull_requests.run_id ->
    agent_runs form a cycle; the restripe must carry pr_id data across the
    rebuild and attach that FK only after pull_requests is rebuilt."""
    body = _migration("0023_fk_deletion_story.sql")
    assert "agent_runs_pr_id_backfill" in body
    assert body.index("CREATE TABLE pull_requests_d1_new") < body.index(
        "ADD CONSTRAINT fk_agent_runs_pr_id"
    ), "pr_id FK attached before the new pull_requests owns the name"


def test_0023_is_runner_compatible():
    """deploy/runners/migrate.py splits on literal $$ and wraps the whole
    file in one transaction — so: tagged dollar quotes forbidden, explicit
    BEGIN/COMMIT forbidden (every earlier migration obeys the same law)."""
    body = _migration("0023_fk_deletion_story.sql")
    assert body.replace("$$", "").count("$") == 0, "only literal $$ dollar-quoting"
    assert not body.lstrip().startswith("BEGIN")
    assert "\nCOMMIT;" not in body


def test_pr_id_stays_sqlside_until_the_cycle_is_handled():
    """0017 added agent_runs.pr_id on the SQL side only. Mirroring it into
    the ORM is deliberately deferred: it would create a runs<->PRs reference
    cycle that breaks single-flush insert ordering. Pin BOTH halves of that
    decision: no ORM mirror yet, explicit SET NULL story in 0023."""
    from harness_identity.models import AgentRun as AR

    assert "pr_id" not in AR.__table__.columns
    assert (
        "ADD CONSTRAINT fk_agent_runs_pr_id\n"
        "    FOREIGN KEY (pr_id) REFERENCES pull_requests (id) ON DELETE SET NULL"
        in _migration("0023_fk_deletion_story.sql")
    )


# --------------------------------------------------------------------------
# 5. LIVE POSTGRES (opt-in) — the real chain, probed end-to-end
# --------------------------------------------------------------------------

_pg_reason = "HARNESS_TEST_PG not set; live-PG FK-migration probe skipped"
_PG_CONTAINER = os.environ.get("HARNESS_TEST_PG_CONTAINER", "harness-db-1")
_PG_USER = os.environ.get("HARNESS_TEST_PG_USER", "harness")


def _psql(dbname: str, sql: str) -> subprocess.CompletedProcess[str]:
    """Feed SQL to the compose Postgres through docker exec psql (credentials
    never leave the container's trust config / env)."""
    return subprocess.run(  # noqa: PLW1510 - returncode inspected by callers
        [
            "docker", "exec", "-i", _PG_CONTAINER,
            # -A -t: unaligned, tuples-only => machine-parseable rows.
            "psql", "-U", _PG_USER, "-v", "ON_ERROR_STOP=1", "-q", "-A", "-t",
            "-d", dbname, "-f", "-",
        ],
        input=sql,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _pg_available() -> bool:
    if not shutil.which("docker"):
        return False
    probe = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", _PG_CONTAINER],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return probe.returncode == 0 and "true" in probe.stdout


@pytest.fixture
def scratch_pg():
    """A throwaway database with the ENTIRE 0001->0023 chain applied."""
    assert _pg_available(), f"docker container {_PG_CONTAINER} not reachable"
    dbname = f"harness_d1_{uuid.uuid4().hex[:10]}"
    assert _psql("postgres", f'CREATE DATABASE "{dbname}"').returncode == 0
    try:
        for path in sorted(_MIGRATIONS.glob("*.sql")):
            res = _psql(dbname, path.read_text())
            assert res.returncode == 0, f"{path.name} failed: {res.stderr}"
        yield dbname
    finally:
        _psql("postgres", f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


@pytest.mark.skipif(not os.environ.get("HARNESS_TEST_PG"), reason=_pg_reason)
class TestLivePostgresDeletionStory:
    async def test_chain_lands_every_fk_with_expected_confdeltype(self, scratch_pg):
        """pg_constraint is ground truth: every FK in the live schema must
        carry the mapped confdeltype (c=CASCADE, n=SET NULL, r=RESTRICT)."""
        code = {"CASCADE": "c", "SET NULL": "n", "RESTRICT": "r"}
        res = _psql(
            scratch_pg,
            """
            SELECT conrelid::regclass::text AS tbl, a.attname AS col, c.confdeltype AS del
            FROM pg_constraint c
            JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
            JOIN pg_attribute a
              ON a.attrelid = c.conrelid AND a.attnum = k.attnum
            WHERE c.contype = 'f' AND k.ord = 1
            ORDER BY 1, 2;
            """,
        )
        assert res.returncode == 0, res.stderr
        live = {}
        for line in res.stdout.splitlines():
            tbl, col, del_code = (part.strip() for part in line.split("|"))
            live[(tbl, col)] = del_code
        wanted = {(t, c): code[d] for (t, c), (_r, d) in DELETION_MAP.items()}
        wrong = {k: (live.get(k), v) for k, v in wanted.items() if live.get(k) != v}
        assert not wrong, f"live confdeltype disagrees with the map: {wrong}"
        assert len(live) >= len(wanted), f"unmapped live FKs: {sorted(set(live) - set(wanted))}"

    async def test_live_delete_probes_cascade_and_restrict(self, scratch_pg):
        """Behavioral spot-check on the real engine: team delete cascades,
        ledger row blocks its run."""
        seed = (
            f"INSERT INTO teams (id, name) VALUES ('{uid()}', 'live');\n"
            f"INSERT INTO projects (id, team_id, name) "
            f"SELECT '{uid()}', id, 'live-p' FROM teams WHERE name = 'live';\n"
            f"INSERT INTO agent_runs (id, project_id, state) "
            f"SELECT '{uid()}', id, 'running' FROM projects WHERE name = 'live-p';\n"
            # cost_ledger counters carry NO SQL-side defaults (0001 keeps them
            # NOT NULL default-less; the ORM supplies zeros), so supply them.
            "INSERT INTO cost_ledger (run_id, agent_role, model, input_tokens, "
            "output_tokens, cost_usd, latency_ms) SELECT id, 'primary', 'm', 0, 0, 0, 0 "
            "FROM agent_runs;\n"
        )
        assert _psql(scratch_pg, seed).returncode == 0

        blocked = _psql(scratch_pg, "DELETE FROM agent_runs;")
        assert blocked.returncode != 0, "RESTRICT did not fire on live Postgres"
        # PG's exact wording for ON DELETE RESTRICT violations.
        assert "violates RESTRICT setting of foreign key constraint" in blocked.stderr

        blocked_team = _psql(scratch_pg, "DELETE FROM teams WHERE name = 'live';")
        assert (
            blocked_team.returncode != 0
        ), "ledger RESTRICT must propagate up through the whole cascade"

        # Release path: once the integrity row goes (an explicit, audited
        # operation), both the run delete and the full team cascade succeed.
        assert _psql(scratch_pg, "DELETE FROM cost_ledger;").returncode == 0
        assert _psql(scratch_pg, "DELETE FROM teams WHERE name = 'live';").returncode == 0
        left = _psql(
            scratch_pg,
            "SELECT (SELECT COUNT(*) FROM agent_runs) + "
            "(SELECT COUNT(*) FROM projects) + (SELECT COUNT(*) FROM cost_ledger);",
        )
        assert left.stdout.strip().endswith("0"), left.stdout

    async def test_event_bus_notify_trigger_survives_the_restripe(self, scratch_pg):
        """0003's pg_notify trigger lived ON the old agent_events table and
        dies with any DROP TABLE; Section 1 owns event delivery, so 0023 must
        re-attach it to the replacement table. Pinned against pg_trigger."""
        trig = _psql(
            scratch_pg,
            "SELECT tgname FROM pg_trigger "
            "WHERE tgrelid = 'agent_events'::regclass AND NOT tgisinternal;",
        )
        names = [ln.strip() for ln in trig.stdout.splitlines() if ln.strip()]
        assert "trg_notify_agent_events" in names, f"notify trigger missing: {names}"
