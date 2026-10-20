"""T13b: the two decisions in `verify_otp` are single conditional UPDATEs, not read-then-write.

The parallel behaviour is proved on a real PostgreSQL (`test_session11_pg.py`). These two cases are
deterministic and need no database: the code is consumed by someone else between this caller's guess
and its redeem, and a stored row is already at the attempt limit.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text

from harness_identity import auth
from harness_identity.config import Settings
from harness_identity.models import AuthSession

EMAIL = "otp@a.io"


def settings():
    return Settings(
        master_key=b"test-master-key-0123456789abcdef", cors_origins=[], require_ownership=False
    )


async def issue(db):
    sent: list[str] = []
    await auth.issue_otp(db, settings(), lambda _e, c: sent.append(c), EMAIL)
    return sent[0]


class Rival:
    """A session that lets 'another request' consume the code right after this one's guess is
    claimed (its first UPDATE), which is the window the old read-then-write code left open."""

    def __init__(self, session):
        self._s, self._done = session, False

    def __getattr__(self, name):
        return getattr(self._s, name)

    async def execute(self, stmt, *args, **kw):
        out = await self._s.execute(stmt, *args, **kw)
        if not self._done and str(stmt).lstrip().upper().startswith("UPDATE"):
            self._done = True
            await self._s.execute(text("UPDATE login_otps SET consumed_at = CURRENT_TIMESTAMP"))
        return out


async def refusal(coro):
    with pytest.raises(HTTPException) as e:
        await coro
    return e.value.detail["error"]


async def sessions(db):
    async with db() as s:
        return (await s.execute(select(func.count()).select_from(AuthSession))).scalar_one()


async def test_a_code_consumed_between_the_guess_and_the_redeem_mints_nothing(db):
    code = await issue(db)
    async with db() as s:
        err = await refusal(auth.verify_otp(Rival(s), settings(), EMAIL, code))
    assert (err["code"], err["message"]) == ("INVALID_CODE", "no active code")
    assert await sessions(db) == 0


async def test_a_row_already_at_the_attempt_limit_is_refused_even_for_the_right_code(db):
    code = await issue(db)
    async with db() as s:  # e.g. written by an older version: at the limit but never consumed
        await s.execute(text("UPDATE login_otps SET attempts = :n"),
                        {"n": settings().otp_max_attempts})
        await s.commit()
    async with db() as s:
        err = await refusal(auth.verify_otp(s, settings(), EMAIL, code))
    assert err["code"] == "LOCKED"
    assert await sessions(db) == 0
