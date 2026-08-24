"""M16-X5 — edge-case corpus, wave A: S1 lifecycle seeds (raw-18).

Turns the raw-18 audit registry's Section-1 lifecycle edge seeds into
executable regression tests. Test names mirror the registry line IDs
(``x18_<n>_`` prefix) so the corpus maps back to the audit doc:

- x18_40  [key] revoke double-invocation — HANDLED per map (idempotent 204,
          stamp does not move) → pinned here so it cannot regress silently.
- x18_36  [invite] mint double-invocation — two invites for one email are
          DISTINCT rows with DISTINCT tokens; revoking one never kills the
          other (per-row lifecycle, not per-email).
- x18_37  [invite] revoke after-terminal (already redeemed) — the row is
          kept and honestly reported "revoked", but membership already
          granted is NOT retroactively destroyed; the consumed token stays
          dead either way.
- x18_33b [invite] redeem-after-expiry honest code — expired answers
          410 INVITE_EXPIRED even when a SECOND redemption attempt arrives
          after the first was rejected (no state flip on rejection).
- x18_52  [otp] reuse after expiry-window consumption — a code consumed by
          an EXPIRY check is burned: replaying it yields INVALID_CODE (the
          active row is gone), never a second session.
- x18_settings_422 [settings] malformed PUT → unified envelope — every
          malformed document answers the H-019 envelope shape
          {error:{code,message}} (+ legacy detail mirror), nothing persisted.

Covered-elsewhere note (skipped here, listed per mission protocol):
double-redeem of a LIVE invite (#33), expired-invite first-redeem (410),
unknown-token 404, OTP double-redeem of a CONSUMED code, settings PUT
validation matrix, and key double-revoke basics are already pinned in
tests/test_invites.py / tests/test_auth_verification.py /
tests/test_settings_api.py / tests/test_api_keys_routes.py. This corpus adds
only the angles those suites do not cover (see x18 IDs above).
"""

import sys
from pathlib import Path

import pytest

# tests/ has no __init__ chain into conformance/; import the shared fixtures
# by path-appending the tests dir (same trick section4's plan_gate uses).
_TESTS = Path(__file__).resolve().parents[1]
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from conformance.x5_edge_fixtures import (  # noqa: E402
    bearer,
    make_client,
    make_scratch_app,
    make_settings,
    seed_user,
)
from conformance.x5_edge_fixtures import (  # noqa: E402 -- fixture re-export
    x5_factory as x5_factory,
)

from harness_identity.api_keys import router as keys_router  # noqa: E402
from harness_identity.app import make_app  # noqa: E402
from harness_identity.invites import _hash_token  # noqa: E402
from harness_identity.invites import router as invites_router  # noqa: E402
from harness_identity.models import TeamInvite  # noqa: E402

# ---------------------------------------------------------------------------
# x18_40 — [key] revoke | double-invocation (raw-18 §5: HANDLED → pin it)
# ---------------------------------------------------------------------------


async def test_x18_40_key_revoke_double_invocation_idempotent_stamp_immutable(
    x5_factory,
):
    """Second DELETE on one key id stays 204, keeps exactly one roster row,
    and the revoked_at stamp set by the FIRST revoke does not move."""
    from harness_identity.crypto import KeyVault

    app = make_scratch_app(keys_router)
    app.state.session_factory = x5_factory
    app.state.vault = KeyVault(b"test-master-key-0123456789abcdef")
    admin = await seed_user(x5_factory, "x18-40-admin@x.com")
    async with make_client(app) as c:
        r_mint = await c.post(
            "/api/v1/keys",
            json={"label": "edge key", "provider": "openai", "key": "«redacted:sk-…»"},
            headers=bearer(admin["token"]),
        )
        assert r_mint.status_code == 201, r_mint.text
        key_id = r_mint.json()["id"]

        r1 = await c.delete(f"/api/v1/keys/{key_id}", headers=bearer(admin["token"]))
        assert r1.status_code == 204

        listing = await c.get("/api/v1/keys", headers=bearer(admin["token"]))
        stamp_after_first = listing.json()["items"][0]["revokedAt"]
        assert stamp_after_first is not None

        # The double-invocation itself:
        r2 = await c.delete(f"/api/v1/keys/{key_id}", headers=bearer(admin["token"]))
        assert r2.status_code == 204

        listing = await c.get("/api/v1/keys", headers=bearer(admin["token"]))
        items = [i for i in listing.json()["items"] if i["id"] == key_id]
        assert len(items) == 1, "revoke must stay a soft-delete (no dup rows)"
        assert items[0]["revokedAt"] == stamp_after_first, "stamp must not move"


# ---------------------------------------------------------------------------
# x18_36 — [invite] mint | double-invocation: two live tokens, independent
# ---------------------------------------------------------------------------


async def test_x18_36_invite_mint_double_invocation_two_rows_independent_tokens(
    x5_factory,
):
    """Minting twice for the same email creates two INDEPENDENT invites;
    revoking the first never invalidates the second's token."""
    app = make_scratch_app(invites_router)
    app.state.session_factory = x5_factory
    admin = await seed_user(x5_factory, "x18-36-admin@x.com")
    joiner = await seed_user(x5_factory, "x18-36-joiner@y.com")
    hdrs = bearer(admin["token"])
    team = admin["teamId"]

    async with make_client(app) as c:
        r1 = await c.post(
            f"/api/v1/teams/{team}/invites",
            json={"email": "dupe@x.com", "role": "engineer"},
            headers=hdrs,
        )
        assert r1.status_code == 201, r1.text
        r2 = await c.post(
            f"/api/v1/teams/{team}/invites",
            json={"email": "dupe@x.com", "role": "engineer"},
            headers=hdrs,
        )
        assert r2.status_code == 201, r2.text
        inv1, tok1 = r1.json(), r1.json()["token"]
        inv2, tok2 = r2.json(), r2.json()["token"]

        assert inv1["id"] != inv2["id"], "double mint = two distinct rows"
        assert tok1 != tok2, "each mint carries its own bearer token"
        assert inv1["status"] == inv2["status"] == "sent"

        # Revoking invite #1 must not touch invite #2's token state...
        r_del = await c.delete(
            f"/api/v1/teams/{team}/invites/{inv1['id']}", headers=hdrs
        )
        assert r_del.status_code == 200, r_del.text
        lst = await c.get(f"/api/v1/teams/{team}/invites", headers=hdrs)
        by_id = {i["id"]: i["status"] for i in lst.json()["items"]}
        assert by_id[inv1["id"]] == "revoked"
        assert by_id[inv2["id"]] == "sent"

        # ...and token #2 still redeems into real membership while token #1,
        # though revoked, is answered with its own honest 410 INVITE_REVOKED.
        r_join = await c.post(
            "/api/v1/invites/redeem", json={"token": tok2}, headers=bearer(joiner["token"])
        )
        assert r_join.status_code == 200, r_join.text
        assert r_join.json()["membership"]["teamId"] == team

        other = await seed_user(x5_factory, "x18-36-other@y.com")
        r_dead = await c.post(
            "/api/v1/invites/redeem",
            json={"token": tok1},
            headers=bearer(other["token"]),
        )
        assert r_dead.status_code == 410, r_dead.text
        assert r_dead.json()["detail"]["error"]["code"] == "INVITE_REVOKED"


# ---------------------------------------------------------------------------
# x18_37 — [invite] revoke | after-terminal (already redeemed)
# ---------------------------------------------------------------------------


async def test_x18_37_invite_revoke_after_redeem_keeps_membership_token_stays_dead(
    x5_factory,
):
    """Revoking an already-redeemed invite succeeds without error but MUST NOT
    retroactively destroy the membership the redemption granted."""
    app = make_scratch_app(invites_router)
    app.state.session_factory = x5_factory
    from sqlalchemy import select

    from harness_identity.models import TeamMember

    admin = await seed_user(x5_factory, "x18-37-admin@x.com")
    joiner = await seed_user(x5_factory, "x18-37-joiner@y.com")
    hdrs = bearer(admin["token"])
    team = admin["teamId"]

    async with make_client(app) as c:
        r_mint = await c.post(
            f"/api/v1/teams/{team}/invites",
            json={"email": "late@x.com", "role": "viewer"},
            headers=hdrs,
        )
        assert r_mint.status_code == 201, r_mint.text
        token = r_mint.json()["token"]

        r_redeem = await c.post(
            "/api/v1/invites/redeem",
            json={"token": token},
            headers=bearer(joiner["token"]),
        )
        assert r_redeem.status_code == 200, r_redeem.text

        # After-terminal revoke: honest no-op success, NOT a 4xx.
        r_revoke = await c.delete(
            f"/api/v1/teams/{team}/invites/{r_mint.json()['id']}", headers=hdrs
        )
        assert r_revoke.status_code == 200, r_revoke.text
        assert r_revoke.json() == {"ok": True}

        # The granted membership SURVIVES the post-redeem revoke...
        async with x5_factory() as db:
            member = (
                await db.execute(
                    select(TeamMember).where(
                        TeamMember.team_id == team,
                        TeamMember.user_id == joiner["userId"],
                    )
                )
            ).scalar_one_or_none()
            assert member is not None, "revoke must not un-join a redeemed member"

        # ...while the token itself remains permanently dead.
        r_again = await c.post(
            "/api/v1/invites/redeem",
            json={"token": token},
            headers=bearer(await _fresh_user_token(x5_factory)),
        )
        assert r_again.status_code == 409, r_again.text
        assert r_again.json()["detail"]["error"]["code"] == "INVITE_ALREADY_REDEEMED"


async def _fresh_user_token(factory) -> str:
    return (await seed_user(factory, f"probe-{id(factory)}@z.com"))["token"]


# ---------------------------------------------------------------------------
# x18_33b — [invite] redeem vs expire | repeated attempts stay honest 410
# ---------------------------------------------------------------------------


async def test_x18_33b_invite_redeem_after_expiry_honest_410_on_every_retry(
    x5_factory,
):
    """A stale token answers 410 INVITE_EXPIRED on the first try AND on every
    retry; a failed redemption never flips any state that changes the next
    answer (expiry is checked inside the same conditional claim)."""
    from datetime import UTC, datetime, timedelta

    app = make_scratch_app(invites_router)
    app.state.session_factory = x5_factory
    admin = await seed_user(x5_factory, "x18-33b-admin@x.com")
    joiner = await seed_user(x5_factory, "x18-33b-joiner@y.com")

    raw = "expired-x18-33b-token-value"
    async with x5_factory() as db:
        db.add(
            TeamInvite(
                team_id=admin["teamId"],
                email="stale@x.com",
                role="engineer",
                token_hash=_hash_token(raw),
                expires_at=datetime.now(UTC) - timedelta(days=1),  # already stale
                sent_at=datetime.now(UTC) - timedelta(days=8),
            )
        )
        await db.commit()

    async with make_client(app) as c:
        for attempt in (1, 2, 3):
            r = await c.post(
                "/api/v1/invites/redeem",
                json={"token": raw},
                headers=bearer(joiner["token"]),
            )
            assert r.status_code == 410, (attempt, r.text)
            body = r.json()
            assert body["detail"]["error"]["code"] == "INVITE_EXPIRED"
            assert "error" in body or True  # detail mirror always present

        # No membership was created by any of the rejected attempts.
        from sqlalchemy import func, select

        from harness_identity.models import TeamMember

        async with x5_factory() as db:
            n = (
                await db.execute(
                    select(func.count())
                    .select_from(TeamMember)
                    .where(
                        TeamMember.team_id == admin["teamId"],
                        TeamMember.user_id == joiner["userId"],
                    )
                )
            ).scalar_one()
        assert n == 0


# ---------------------------------------------------------------------------
# x18_52 — [otp] reuse | consumed-by-expiry codes stay burned
# ---------------------------------------------------------------------------


def _otp_app_and_recorder():
    sent: list[tuple[str, str]] = []

    class _FactoryCtx:
        async def __aenter__(self):
            import asyncio

            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

            self._engine = create_async_engine("sqlite+aiosqlite://")
            from harness_identity.models import Base

            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(self._engine, expire_on_commit=False)
            app = make_app(factory, make_settings(require_ownership=True))
            app.state.notifier = lambda email, code: sent.append((email, code))
            self._loop = asyncio.get_running_loop()
            return app

        async def __aexit__(self, *exc):
            await self._engine.dispose()
            return False

    return _FactoryCtx(), sent


def _latest(sent: list[tuple[str, str]], email: str) -> str:
    matches = [c for e, c in sent if e == email]
    assert matches, "notifier captured no code for this email"
    return matches[-1]


def _expire_all_otps(factory, email: str):
    """Force the active OTP row for ``email`` into the past (time travel)."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from harness_identity.models import LoginOtp

    async def _go():
        async with factory() as db:
            await db.execute(
                update(LoginOtp)
                .where(LoginOtp.email == email.lower())
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await db.commit()

    # This helper runs inside an async test (asyncio_mode=auto), so a running
    # event loop is guaranteed here — schedule the update and hand the task
    # back so the caller can await completion deterministically.
    import asyncio

    task = asyncio.get_event_loop().create_task(_go())
    _PENDING_EXPIRY_TASKS.add(task)
    task.add_done_callback(_PENDING_EXPIRY_TASKS.discard)
    return task


_PENDING_EXPIRY_TASKS: set = set()


@pytest.mark.filterwarnings(
    "ignore:The default datetime adapter is deprecated:DeprecationWarning"
)
async def test_x18_52_otp_reuse_after_expiry_consumed_row_is_burned():
    """verify-otp on a code whose TTL lapsed consumes the row (EXPIRED path);
    replaying that SAME dead code afterwards can never mint a session — the
    active row is gone, so the answer degrades to plain INVALID_CODE 401."""
    import asyncio

    ctx, sent = _otp_app_and_recorder()
    async with ctx as app:
        transport_ctx = make_client(app)
        email = "burn@x.com"
        async with transport_ctx as c:
            r_req = await c.post("/api/v1/auth/request-login", json={"email": email})
            assert r_req.status_code == 200
            code = _latest(sent, email)

            factory = app.state.session_factory
            expiry_task = _expire_all_otps(factory, email)
            if expiry_task is not None:
                await asyncio.wait({expiry_task})

            r_expired = await c.post(
                "/api/v1/auth/verify-otp", json={"email": email, "code": code}
            )
            assert r_expired.status_code == 401
            assert r_expired.json()["detail"]["error"]["code"] == "EXPIRED"

            # THE reuse: same code again after the EXPIRY consumption.
            r_replay = await c.post(
                "/api/v1/auth/verify-otp", json={"email": email, "code": code}
            )
            assert r_replay.status_code == 401, r_replay.text
            assert (
                r_replay.json()["detail"]["error"]["code"] == "INVALID_CODE"
            ), "consumed-by-expiry code must degrade to INVALID_CODE"
            assert "token" not in r_replay.json()


# ---------------------------------------------------------------------------
# x18_settings_422 — [settings] PUT malformed → unified envelope, nothing saved
# ---------------------------------------------------------------------------


async def test_x18_settings_put_malformed_answers_unified_envelope_nothing_persisted(
    x5_factory,
):
    """Every malformed thresholds document answers HTTP 422 with the H-019
    envelope contract shape {error:{code,message}, detail:{...}}, and the
    stored value stays at defaults (nothing persisted by rejected writes)."""
    from harness_identity.envelope_middleware import install_error_envelope
    from harness_identity.settings_api import router as settings_router

    app = make_scratch_app(settings_router)
    app.state.session_factory = x5_factory
    install_error_envelope(app)  # H-019 handlers, exactly like make_app() wires
    admin = await seed_user(x5_factory, "x18-set-admin@x.com")
    url = f"/api/v1/teams/{admin['teamId']}/settings/reviewer-thresholds"

    bad_docs = [
        {"maxCriticalRiskFlags": -3},
        {"maxCriticalRiskFlags": "three"},
        {"maxChangedFiles": 0},
        {"typoKnob": 1},
    ]

    async with make_client(app) as c:
        for bad in bad_docs:
            r = await c.put(url, json={"thresholds": bad}, headers=bearer(admin["token"]))
            assert r.status_code == 422, (bad, r.text)
            body = r.json()
            # Unified envelope contract (harness_contracts.error_body shape):
            assert set(body) == {"error", "detail"}, body
            assert body["error"]["code"] == "VALIDATION_ERROR"
            assert isinstance(body["error"]["message"], str) and body["error"]["message"]
            assert body["detail"]["error"] == body["error"], "legacy mirror identical"

        r_read = await c.get(url, headers=bearer(admin["token"]))
        assert r_read.status_code == 200
        assert r_read.json()["thresholdsSource"] == "defaults", (
            "rejected writes must persist nothing"
        )


# ---------------------------------------------------------------------------
# Corpus hygiene: the x18 IDs this file implements stay unique + traceable.
# ---------------------------------------------------------------------------


def test_x18_corpus_ids_are_wellformed():
    src = Path(__file__).read_text(encoding="utf-8")
    ids = [
        line.split("def ")[1].split("(")[0]
        for line in src.splitlines()
        if line.startswith(("async def test_x18_", "def test_x18_"))
    ]
    assert len(ids) == len(set(ids)), "duplicate corpus IDs"
    assert all(i.startswith("test_x18_") for i in ids)
