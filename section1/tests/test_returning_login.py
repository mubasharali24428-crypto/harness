"""P1: returning teammates must not silently be signed into new solo teams."""

from harness_identity.auth import login_or_register


async def test_returning_login_reuses_the_only_existing_membership(db):
    async with db() as session:
        _, first = await login_or_register(session, "returning@pilot.test")
        _, second = await login_or_register(session, "returning@pilot.test")
    assert second.teamId == first.teamId
