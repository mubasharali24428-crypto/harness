"""M15-W5-S4: salted KDF upgrade + re-encryption transition tests.

Covers the five mission-mandated proofs:

1. legacy payloads decrypt BEFORE migration (dual-read back-compat),
2. post-migration writes use the new salted-KDF derivation,
3. the one-shot re-encrypt command converts every stored row,
4. the dual-read window loses no data across mixed old/new payloads,
5. wrong passphrase fails honestly and zero secret material reaches logs.

Also pins the documented KDF identity/parameters so they cannot drift
silently, and guards models.py against drifting from migration 0022.
"""

import base64
import hashlib
import logging
import os

import pytest
from cryptography.fernet import Fernet, InvalidToken
from harness_contracts import KeyVault as ContractsKeyVault
from harness_contracts.security import HAS_ARGON2
from sqlalchemy import select

from harness_identity.crypto import (
    KDF_ALGO_ARGON2ID,
    KDF_ALGO_SCRYPT,
    SALT_LENGTH,
    SCRYPT_N,
    SCRYPT_P,
    SCRYPT_R,
    KeyVault,
    derive_fernet_key,
    read_api_key,
    reencrypt_all_payloads,
    store_api_key,
)
from harness_identity.models import ApiKey, KeyDerivationParams

MASTER = b"test-master-key-0123456789abcdef"
WRONG_MASTER = b"wrong-master-key-0123456789abcdefgh"
CANARY = "sk-canary-SECRETPREFIX-4242"


def _legacy_vault(master_key: bytes) -> Fernet:
    """Byte-identical reconstruction of the pre-0022 payload key."""
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(master_key).digest()))


async def _seed_legacy_row(db, team_id: str, secret: str = CANARY) -> ApiKey:
    """Insert a row exactly the way PRE-migration code did."""
    row = ApiKey(
        team_id=team_id,
        provider="openai",
        label="legacy",
        key_encrypted=_legacy_vault(MASTER).encrypt(secret.encode()).decode(),
        key_hint=KeyVault.hint(secret),
    )
    async with db() as s:
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return row


# --------------------------------------------------------------------------
# 1. Legacy payloads decrypt pre-migration
# --------------------------------------------------------------------------


async def test_legacy_payload_decrypts_pre_migration(db):
    """A payload written by the OLD code (contracts.KeyVault stretch) reads
    back through the upgraded reader when NO params row exists yet."""
    row = await _seed_legacy_row(db, "team-legacy")
    # Sanity: the seed really is a legacy-wrapped token.
    assert _legacy_vault(MASTER).decrypt(row.key_encrypted.encode()).decode() == CANARY
    vault = KeyVault(MASTER)
    async with db() as s:
        assert await read_api_key(s, vault, row.id) == CANARY


async def test_bare_keyvault_stays_byte_compatible_with_contracts():
    """KeyVault(master) without upgrade remains byte-identical to the frozen
    contracts derivation — cross-section consumers must feel nothing."""
    legacy_token = ContractsKeyVault(MASTER).encrypt(CANARY)
    v = KeyVault(MASTER)
    assert v.decrypt(legacy_token) == CANARY
    assert ContractsKeyVault(MASTER).decrypt(v.encrypt(CANARY)) == CANARY


# --------------------------------------------------------------------------
# 2. Post-migration writes use the new derivation
# --------------------------------------------------------------------------


def test_derive_fernet_key_matches_documented_params():
    salt = os.urandom(SALT_LENGTH)
    expected_algo = KDF_ALGO_ARGON2ID if HAS_ARGON2 else KDF_ALGO_SCRYPT
    key1 = derive_fernet_key(MASTER, salt, algorithm=expected_algo)
    key2 = derive_fernet_key(
        MASTER, salt, algorithm=expected_algo, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P
    )
    assert key1 == key2 and len(key1) == 32  # deterministic + Fernet-sized
    with pytest.raises(ValueError):
        derive_fernet_key(MASTER, b"short")  # salt length enforced


async def test_store_after_migration_uses_salted_kdf(db):
    """First write post-0022 creates the params row; payloads are wrapped under
    the derived key (NOT the legacy stretch) with the per-install salt."""
    team = "team-post"
    vault = KeyVault(MASTER)
    async with db() as s:
        row = await store_api_key(s, vault, team, "openai", "post", CANARY)
    async with db() as s:
        kdf = await s.get(KeyDerivationParams, 1)
        assert kdf is not None and len(kdf.salt) >= SALT_LENGTH
        assert kdf.algorithm in (KDF_ALGO_ARGON2ID, KDF_ALGO_SCRYPT)
        fresh = Fernet(base64.urlsafe_b64encode(derive_fernet_key(MASTER, bytes(kdf.salt))))
        # New payload: decrypts under derived key...
        assert fresh.decrypt(row.key_encrypted.encode()).decode() == CANARY
        legacy = _legacy_vault(MASTER)
        # ...and NOT under the legacy stretch (derivation really changed).
        with pytest.raises(InvalidToken):
            legacy.decrypt(row.key_encrypted.encode())


async def test_params_row_singleton_stable_salt_across_writes(db):
    """The per-install salt is minted once and never regenerated."""
    vault = KeyVault(MASTER)
    async with db() as s:
        await store_api_key(s, vault, "t", "openai", "a", CANARY)
    async with db() as s:
        first = (await s.get(KeyDerivationParams, 1)).salt
    async with db() as s:
        await store_api_key(s, vault, "t", "openai", "b", CANARY)
    async with db() as s:
        second = (await s.get(KeyDerivationParams, 1)).salt
        rows = (await s.execute(select(ApiKey))).scalars().all()
    assert bytes(first) == bytes(second)  # stable forever after mint
    assert len(rows) == 2  # both writes landed


# --------------------------------------------------------------------------
# 3. Re-encrypt command converts all rows
# --------------------------------------------------------------------------


async def test_reencrypt_converts_all_rows_and_marks_completion(db):
    team = "team-reencrypt"
    row = await _seed_legacy_row(db, team)
    summary = {}
    async with db() as s:
        summary = await reencrypt_all_payloads(s, MASTER)
    assert summary["total"] >= 1 and summary["failed"] == 0
    # Counter semantics: a legacy-wrapped row is NOT already-new.
    assert summary["already_new"] == 0
    async with db() as s:
        kdf = await s.get(KeyDerivationParams, 1)
        assert kdf.reencrypted_at is not None  # completion stamped
        fresh = Fernet(base64.urlsafe_b64encode(derive_fernet_key(MASTER, bytes(kdf.salt))))
        stored = (await s.get(ApiKey, row.id)).key_encrypted
        # Converted: decrypts under NEW key only.
        assert fresh.decrypt(stored.encode()).decode() == CANARY
        with pytest.raises(InvalidToken):
            _legacy_vault(MASTER).decrypt(stored.encode())
        # And the normal reader still answers.
        assert await read_api_key(s, KeyVault(MASTER), row.id) == CANARY


async def test_reencrypt_is_idempotent(db):
    team = "team-idem"
    row = await _seed_legacy_row(db, team)
    async with db() as s:
        first = await reencrypt_all_payloads(s, MASTER)
    async with db() as s:
        second = await reencrypt_all_payloads(s, MASTER)
    assert first["rewrapped"] >= 1 and second["total"] == first["total"]
    # Second pass: every row now derives from the new key => all already-new.
    assert second["already_new"] == second["total"]
    async with db() as s:
        assert await read_api_key(s, KeyVault(MASTER), row.id) == CANARY


# --------------------------------------------------------------------------
# 4. Dual-read window proves no data loss
# --------------------------------------------------------------------------


async def test_dual_read_window_mixed_old_and_new_rows(db):
    """Old (legacy-wrapped) and new (KDF-wrapped) payloads coexist; BOTH stay
    readable throughout the window — zero data loss either way."""
    team = "team-window"
    old_row = await _seed_legacy_row(db, team)  # pre-migration payload
    vault = KeyVault(MASTER)
    async with db() as s:
        new_row = await store_api_key(s, vault, team, "openai", "new", CANARY)
    async with db() as s:
        assert await read_api_key(s, vault, old_row.id) == CANARY  # legacy leg
        assert await read_api_key(s, vault, new_row.id) == CANARY  # new leg
    # Salted class-level dual-read: same behavior without a session.
    async with db() as s:
        kdf = await s.get(KeyDerivationParams, 1)
    sv = KeyVault.salted(MASTER, bytes(kdf.salt))
    assert sv.decrypt(old_row.key_encrypted) == CANARY
    assert sv.decrypt(new_row.key_encrypted) == CANARY


# --------------------------------------------------------------------------
# 5. Wrong passphrase fails honestly; zero secret material in logs
# --------------------------------------------------------------------------


async def test_wrong_master_key_fails_honestly_on_both_generations(db):
    team = "team-wrongkey"
    legacy_row = await _seed_legacy_row(db, team)
    vault = KeyVault(MASTER)
    async with db() as s:
        new_row = await store_api_key(s, vault, team, "openai", "n", CANARY)
    wrong = KeyVault(WRONG_MASTER)
    for target in (legacy_row.id, new_row.id):
        with pytest.raises(ValueError, match="decryption failed"):
            async with db() as s2:
                await read_api_key(s2, wrong, target)


def test_wrong_passphrase_fails_at_vault_level():
    v = KeyVault.salted(MASTER, os.urandom(SALT_LENGTH))
    token = v.encrypt(CANARY)
    w = KeyVault.salted(WRONG_MASTER, os.urandom(SALT_LENGTH))
    with pytest.raises(ValueError, match="wrong master key"):
        w.decrypt(token)


async def test_zero_secret_material_in_logs_or_errors(db, caplog):
    team = "team-logs"
    row = await _seed_legacy_row(db, team)
    with caplog.at_level(logging.DEBUG):
        async with db() as s:
            await store_api_key(s, KeyVault(MASTER), team, "openai", "l", CANARY)
        async with db() as s:
            await read_api_key(s, KeyVault(MASTER), row.id)
        with pytest.raises(ValueError):
            async with db() as s:
                await read_api_key(s, KeyVault(WRONG_MASTER), row.id)
    logged = "\n".join(str(r.getMessage()) for r in caplog.records)
    assert CANARY not in logged
    assert CANARY.split("-")[1] not in logged  # not even a fragment
    # The re-encrypt pass must be equally silent.
    with caplog.at_level(logging.DEBUG):
        async with db() as s:
            summary = await reencrypt_all_payloads(s, MASTER)
    assert summary["failed"] == 0
    logged2 = "\n".join(str(r.getMessage()) for r in caplog.records)
    assert CANARY not in logged2 and str(MASTER) not in logged2


# --------------------------------------------------------------------------
# Migration/ORM parity guard
# --------------------------------------------------------------------------


def test_models_match_migration_0022_columns():
    cols = {c.name for c in KeyDerivationParams.__table__.columns}
    assert {
        "id",
        "algorithm",
        "salt",
        "scrypt_n",
        "scrypt_r",
        "scrypt_p",
        "reencrypted_at",
        "created_at",
    } <= cols
