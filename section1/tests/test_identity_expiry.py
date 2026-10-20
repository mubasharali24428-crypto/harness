"""Parts 31/58: expired identity rows leave the database.

Sign-in codes, sessions, OAuth/SSO states and WebSocket tickets are useless once expired, but only
some were swept as a side effect of minting a new one, so an idle system kept them (and the user and
team ids in them) indefinitely. `events_retention.prune_identity_expired_once` removes them in
batches, after a one-day grace for clock skew, and never touches a live or recently expired row.
"""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from harness_identity.models import (  # noqa: F401 - the sweep reads every table it prunes
    AgentEvent,
    AuditLog,
    AuthSession,
    LoginOtp,
    Team,
    User,
)
from harness_identity.notifier import NotificationOutbox  # noqa: F401
from harness_identity.sso import SsoLoginState

_RUNNER = Path(__file__).resolve().parents[2] / "deploy" / "runners" / "events_retention.py"
_spec = importlib.util.spec_from_file_location("events_retention_identity", _RUNNER)
assert _spec is not None and _spec.loader is not None  # noqa: S101
er = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(er)

NOW = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
LONG_GONE = NOW - timedelta(days=3)
JUST_GONE = NOW - timedelta(hours=1)  # inside the one-day grace
LIVE = NOW + timedelta(hours=1)
HAND_MADE = (  # created by migrations only, not by the ORM models the sqlite fixture builds
    "CREATE TABLE ws_tickets (token_hash VARCHAR(64) PRIMARY KEY, expires_at TEXT NOT NULL)",
    "CREATE TABLE oauth_states (state VARCHAR(64) PRIMARY KEY, expires_at TEXT NOT NULL)",
)


async def seed(db, *, with_hand_made=True):
    """One expired-long-ago, one expired-just-now and one live row in every table; returns ids."""
    async with db() as s:
        team, user = Team(name="t"), User(email="u@a.io", name="u")
        s.add_all([team, user])
        await s.flush()
        for tag, when in (("long", LONG_GONE), ("just", JUST_GONE), ("live", LIVE)):
            s.add(LoginOtp(email=f"{tag}@a.io", code_hash="h", expires_at=when))
            s.add(AuthSession(token_hash=f"sess-{tag}", user_id=user.id, team_id=team.id,
                              expires_at=when))
            s.add(SsoLoginState(state_hash=f"sso-{tag}", nonce_hash="n", pkce_verifier_hash="p",
                                team_id=team.id, redirect_uri="https://x/cb",
                                expires_at=when.isoformat(), created_at=NOW.isoformat()))
        await s.commit()
        if with_hand_made:
            for stmt in HAND_MADE:
                await s.execute(text(stmt))
            for tag, when in (("long", LONG_GONE), ("just", JUST_GONE), ("live", LIVE)):
                await s.execute(text("INSERT INTO ws_tickets VALUES (:k, :e)"),
                                {"k": f"tkt-{tag}", "e": when.isoformat()})
                await s.execute(text("INSERT INTO oauth_states VALUES (:k, :e)"),
                                {"k": f"oas-{tag}", "e": when.isoformat()})
            await s.commit()


async def survivors(db, table, key):
    async with db() as s:
        return sorted(r[0].split("-")[-1] if "-" in r[0] else r[0].split("@")[0]
                      for r in await s.execute(text(f"SELECT {key} FROM {table}")))


async def test_long_expired_rows_go_and_everything_else_stays(db):
    await seed(db)
    out = await er.prune_identity_expired_once(db, batch_size=100, now=NOW)
    assert out == {"login_otps": 1, "auth_sessions": 1, "ws_tickets": 1, "oauth_states": 1,
                   "sso_login_states": 1}
    for table, key in (("login_otps", "email"), ("auth_sessions", "token_hash"),
                       ("ws_tickets", "token_hash"), ("oauth_states", "state"),
                       ("sso_login_states", "state_hash")):
        assert await survivors(db, table, key) == ["just", "live"], table  # grace + live remain


async def test_a_second_pass_finds_nothing(db):
    await seed(db)
    await er.prune_identity_expired_once(db, batch_size=100, now=NOW)
    again = await er.prune_identity_expired_once(db, batch_size=100, now=NOW)
    assert set(again.values()) == {0}


async def test_batches_loop_until_nothing_is_left(db):
    async with db() as s:
        for i in range(5):
            s.add(LoginOtp(email=f"old{i}@a.io", code_hash="h", expires_at=LONG_GONE))
        s.add(LoginOtp(email="live@a.io", code_hash="h", expires_at=LIVE))
        await s.commit()
    out = await er.prune_identity_expired_once(db, batch_size=2, now=NOW)
    assert out["login_otps"] == 5
    assert await survivors(db, "login_otps", "email") == ["live"]


async def test_a_table_the_schema_lacks_is_skipped_not_an_error(db):
    await seed(db, with_hand_made=False)  # no ws_tickets / oauth_states here
    out = await er.prune_identity_expired_once(db, batch_size=100, now=NOW)
    assert (out["ws_tickets"], out["oauth_states"]) == (0, 0)
    assert out["login_otps"] == 1 and out["auth_sessions"] == 1


async def test_the_full_sweep_includes_it(db):
    await seed(db)
    summary = await er.run_sweep(db, retention_days=30, batch_size=100, outbox_retention_days=30,
                                 audit_retention_days=365, now=NOW)
    assert {k: v for k, v in summary.items() if k.endswith("_expired_deleted")} == {
        "login_otps_expired_deleted": 1, "auth_sessions_expired_deleted": 1,
        "ws_tickets_expired_deleted": 1, "oauth_states_expired_deleted": 1,
        "sso_login_states_expired_deleted": 1}
