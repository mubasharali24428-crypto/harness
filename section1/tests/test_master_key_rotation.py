"""W15 slice 3 — rotating HARNESS_MASTER_KEY re-wraps every stored secret.

The shipped re-encrypt script only converts the SHA-256 derivation to the
salted one UNDER THE SAME key. Changing the key leaves every payload
undecryptable, so this is a different operation: decrypt with the old key's
read set (salted + legacy), re-encrypt with the new key's write key, verify
the round-trip, and never destroy a row the old key cannot read.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity import sso as _sso  # noqa: F401 — registers team_sso_configs
from harness_identity.crypto import (
    ENCRYPTED_COLUMNS,
    KeyVault,
    ensure_kdf_params,
    rotate_master_key,
)
from harness_identity.models import Base

OLD = b"w15-old-master-key"
NEW = b"w15-new-master-key"


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(factory):
    async with factory() as session:
        params, _fernet = await ensure_kdf_params(session, OLD)
        salt = bytes(params.salt)
        salted = KeyVault.salted(OLD, salt)
        legacy = KeyVault(OLD)
        await session.execute(
            text(
                "INSERT INTO api_keys (id, team_id, provider, label, key_encrypted, key_hint)"
                " VALUES ('k-salted','t1','openai','a',:v,''),"
                " ('k-legacy','t1','openai','b',:l,''),"
                " ('k-corrupt','t1','openai','c','not-a-token','')"
            ),
            {"v": salted.encrypt("sk-alpha"), "l": legacy.encrypt("sk-legacy")},
        )
        await session.execute(
            text(
                "INSERT INTO integration_webhooks (id, team_id, endpoint_url, event_types,"
                " secret_encrypted, secret_hint, active, version, payload_format,"
                " created_by, created_at, updated_at)"
                " VALUES ('w1','t1','https://example.test/hook','[]',:v,'',1,1,'harness',"
                " 'u1','2026-01-01','2026-01-01')"
            ),
            {"v": salted.encrypt("whsec-beta")},
        )
        await session.execute(
            text(
                "INSERT INTO github_connections (id, user_id, github_login,"
                " access_token_encrypted, scopes)"
                " VALUES ('g1','u1','octo',:v,'[]')"
            ),
            {"v": salted.encrypt("gho-gamma")},
        )
        await session.execute(
            text(
                "INSERT INTO team_sso_configs (team_id, issuer_url, client_id,"
                " client_secret_encrypted, enabled, idp_display_name)"
                " VALUES ('t1','https://idp.test','cid',:v,1,'IdP')"
            ),
            {"v": salted.encrypt("sso-delta")},
        )
        await session.commit()
        return salt


async def test_rotation_rewraps_every_declared_column(session_factory):
    salt = await _seed(session_factory)
    async with session_factory() as session:
        summary = await rotate_master_key(session, OLD, NEW)

    assert summary["failed"] == 1  # the unreadable token, counted not destroyed
    assert summary["rewrapped"] == 5
    assert {name for name, _ in summary["perTable"].items()} == {t for t, _, _ in ENCRYPTED_COLUMNS}

    new_vault = KeyVault.salted(NEW, salt)
    old_vault = KeyVault.salted(OLD, salt)
    async with session_factory() as session:
        api = dict(
            (
                await session.execute(text("SELECT id, key_encrypted FROM api_keys"))
            ).all()
        )
        hook = (
            await session.execute(text("SELECT secret_encrypted FROM integration_webhooks"))
        ).scalar_one()
        gh = (
            await session.execute(
                text("SELECT access_token_encrypted FROM github_connections")
            )
        ).scalar_one()
        sso = (
            await session.execute(
                text("SELECT client_secret_encrypted FROM team_sso_configs")
            )
        ).scalar_one()

    assert new_vault.decrypt(api["k-salted"]) == "sk-alpha"
    assert new_vault.decrypt(api["k-legacy"]) == "sk-legacy"
    assert new_vault.decrypt(hook) == "whsec-beta"
    assert new_vault.decrypt(gh) == "gho-gamma"
    assert new_vault.decrypt(sso) == "sso-delta"

    # The old key no longer reads what it wrote: rotation actually happened.
    with pytest.raises(ValueError):
        old_vault.decrypt(api["k-salted"])
    # A row the old key could not read is left byte-identical.
    assert api["k-corrupt"] == "not-a-token"


async def test_dry_run_writes_nothing(session_factory):
    await _seed(session_factory)
    async with session_factory() as session:
        before = (
            await session.execute(text("SELECT key_encrypted FROM api_keys WHERE id='k-salted'"))
        ).scalar_one()
        summary = await rotate_master_key(session, OLD, NEW, dry_run=True)
        after = (
            await session.execute(text("SELECT key_encrypted FROM api_keys WHERE id='k-salted'"))
        ).scalar_one()
    assert before == after
    assert summary["rewrapped"] == 0
    assert summary["readable"] == 5
    assert summary["failed"] == 1


def test_declared_columns_match_every_encrypted_column():
    """A new Fernet column must be declared, or rotation would skip it."""
    from harness_identity import webhooks as _webhooks  # noqa: F401
    from harness_identity.models import Base

    declared = {(table, column) for table, _pk, column in ENCRYPTED_COLUMNS}
    actual = {
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.name.endswith("_encrypted")
    }
    assert actual == declared
    for table, pk, column in ENCRYPTED_COLUMNS:
        assert pk in Base.metadata.tables[table].columns
        assert column in Base.metadata.tables[table].columns


async def test_same_key_is_refused(session_factory):
    await _seed(session_factory)
    async with session_factory() as session:
        with pytest.raises(ValueError):
            await rotate_master_key(session, OLD, OLD)
