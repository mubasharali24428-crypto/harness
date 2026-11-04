"""Section 1 crypto module.

M15-W5-S4 upgrades master-key derivation from the legacy unsalted
``SHA-256(master_key)`` stretch (contracts.KeyVault) to a **salted memory-hard
KDF**:

* ``argon2id`` via ``argon2-cffi`` when importable (first choice);
* otherwise scrypt via :func:`hashlib.scrypt` with documented parameters
  ``n=2**15, r=8, p=1`` — an OWASP-accepted alternative. This deployment has no
  argon2-cffi wheel and NO-INSTALL rules apply, so the shipped configuration is
  scrypt(2^15, 8, 1). The KDF identity is persisted in
  ``key_derivation_params.algorithm`` so installs that later gain argon2id can
  never silently mix derivations for one payload generation.

Why not contracts.KeyVault anymore? contracts/ is FROZEN and other sections may
still construct it; its unsalted derivation stays valid there. Everything in
section1 resolves ``KeyVault`` from this module (app.py already imported it from
here), so the app upgrades transparently with zero call-site edits.

Transition design (dual-read window):

* Writes ALWAYS use the new salted derivation once params exist.
* Reads try new first, then legacy — payloads wrapped under the old key keep
  decrypting during the migration window. Zero data loss.
* A one-shot re-encrypt pass (:func:`reencrypt_all_payloads`) re-wraps every
  stored Fernet token under the derived key and stamps completion; afterwards
  the legacy read path remains only as a safety net until a hardening mission
  removes it.
"""

import base64
import os
from datetime import UTC, datetime

from cryptography.fernet import Fernet, InvalidToken
from harness_contracts.security import (
    KDF_ALGO_ARGON2ID,
    KDF_ALGO_SCRYPT,
    SALT_LENGTH,
    SCRYPT_N,
    SCRYPT_P,
    SCRYPT_R,
    derive_fernet_key,
)
from harness_contracts.security import (
    kdf_algo_id as _kdf_algo_id,
)
from harness_contracts.security import (
    legacy_fernet as _legacy_fernet,
)
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ApiKey

__all__ = [
    "KeyVault",
    "store_api_key",
    "read_api_key",
    "KDF_ALGO_ARGON2ID",
    "KDF_ALGO_SCRYPT",
    "LEGACY_ALGO_ID",
    "SCRYPT_N",
    "SCRYPT_R",
    "SCRYPT_P",
    "SALT_LENGTH",
    "derive_fernet_key",
    "ensure_kdf_params",
    "load_kdf_params",
    "reencrypt_all_payloads",
]

# --------------------------------------------------------------------------
# KDF identity + parameters
# --------------------------------------------------------------------------

# M17-B3 isolation: the derivation math lives in harness_contracts.security
# and is re-exported here (names above) for Section-1 callers and tests.
# Section 1 owns only the persistence side — minting/reading the parameter row.
# Pre-0022 derivation id: the unsalted SHA-256 stretch inside contracts.
LEGACY_ALGO_ID = "sha256"


# --------------------------------------------------------------------------
# Per-install KDF parameter persistence
# --------------------------------------------------------------------------


async def load_kdf_params(session, master_key: bytes):
    """Return ``(row_or_None, new_fernet, legacy_fernet)`` for this install.

    ``row_or_None`` is None only when no params exist yet (pre-0022 state).
    Never logs anything derived from key material.
    """
    from .models import KeyDerivationParams

    row = await session.get(KeyDerivationParams, 1)
    legacy = _legacy_fernet(master_key)
    if row is None:
        return None, legacy, legacy
    new = Fernet(
        base64.urlsafe_b64encode(
            derive_fernet_key(
                master_key,
                bytes(row.salt),
                algorithm=row.algorithm,
                n=row.scrypt_n or SCRYPT_N,
                r=row.scrypt_r or SCRYPT_R,
                p=row.scrypt_p or SCRYPT_P,
            )
        )
    )
    return row, new, legacy


async def ensure_kdf_params(session, master_key: bytes):
    """Return the active ``(row, fernet)`` for WRITES, creating params on first use.

    The per-install salt is minted exactly once via ``os.urandom`` and then
    immutable — losing or rotating it silently would make every stored payload
    undecryptable. Returns the row plus the Fernet built from the derived key.
    """
    from .models import KeyDerivationParams

    row = await session.get(KeyDerivationParams, 1)
    if row is None:
        row = KeyDerivationParams(
            id=1,
            algorithm=_kdf_algo_id(),
            salt=os.urandom(SALT_LENGTH),
            scrypt_n=SCRYPT_N,
            scrypt_r=SCRYPT_R,
            scrypt_p=SCRYPT_P,
        )
        session.add(row)
        await session.flush()
    fernet = Fernet(
        base64.urlsafe_b64encode(
            derive_fernet_key(
                master_key,
                bytes(row.salt),
                algorithm=row.algorithm,
                n=row.scrypt_n or SCRYPT_N,
                r=row.scrypt_r or SCRYPT_R,
                p=row.scrypt_p or SCRYPT_P,
            )
        )
    )
    return row, fernet


# --------------------------------------------------------------------------
# Master-key rotation (W15)
# --------------------------------------------------------------------------

#: Every column that stores a Fernet token, with its table and primary key.
#: A new encrypted column MUST be added here — test_master_key_rotation.py
#: pins this list against the models.
ENCRYPTED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("api_keys", "id", "key_encrypted"),
    ("integration_webhooks", "id", "secret_encrypted"),
    ("github_connections", "id", "access_token_encrypted"),
    ("team_sso_configs", "team_id", "client_secret_encrypted"),
    ("siem_sinks", "id", "secret_encrypted"),
)


async def rotate_master_key(
    session: AsyncSession, old_key: bytes, new_key: bytes, *, dry_run: bool = False
) -> dict:
    """Re-wrap every stored secret from ``old_key`` to ``new_key``.

    Unlike :func:`reencrypt_all_payloads` (same key, derivation upgrade), this
    changes the key itself: decrypt with the old key's read set (salted, then
    legacy), encrypt with the new key's salted write key. The cleartext salt
    row is reused, so no payload becomes undecryptable by construction.

    A row the old key cannot read is counted ``failed`` and left byte-identical
    — rotation must never destroy a secret it cannot read. The caller decides
    the exit posture (the script refuses to report success when ``failed`` is
    non-zero). Counts only; no secret material is returned or logged.
    """
    from sqlalchemy import text

    from .models import KeyDerivationParams

    if not old_key or not new_key:
        raise ValueError("old and new master keys must be non-empty")
    if old_key == new_key:
        raise ValueError("old and new master keys are identical; nothing to rotate")

    row = await session.get(KeyDerivationParams, 1)
    if row is None:
        raise ValueError(
            "key_derivation_params is missing; run the 0022 upgrade (or this "
            "install predates salted KDF and must be migrated) before rotating"
        )
    salt = bytes(row.salt)
    kwargs = {
        "algorithm": row.algorithm,
        "n": row.scrypt_n or SCRYPT_N,
        "r": row.scrypt_r or SCRYPT_R,
        "p": row.scrypt_p or SCRYPT_P,
    }
    old_vault = KeyVault.salted(old_key, salt, **kwargs)
    new_vault = KeyVault.salted(new_key, salt, **kwargs)

    summary = {
        "total": 0,
        "readable": 0,
        "rewrapped": 0,
        "failed": 0,
        "dryRun": dry_run,
        "perTable": {},
    }
    for table, pk, column in ENCRYPTED_COLUMNS:
        stats = {"total": 0, "readable": 0, "rewrapped": 0, "failed": 0}
        rows = (
            await session.execute(text(f"SELECT {pk}, {column} FROM {table}"))
        ).all()
        for key, ciphertext in rows:
            stats["total"] += 1
            summary["total"] += 1
            try:
                plaintext = old_vault.decrypt(ciphertext)
            except (ValueError, TypeError):
                stats["failed"] += 1
                summary["failed"] += 1
                continue
            stats["readable"] += 1
            summary["readable"] += 1
            if dry_run:
                continue
            rewrapped = new_vault.encrypt(plaintext)
            if new_vault.decrypt(rewrapped) != plaintext:
                # Never write a token we cannot read back.
                stats["failed"] += 1
                summary["failed"] += 1
                continue
            await session.execute(
                text(f"UPDATE {table} SET {column} = :value WHERE {pk} = :key"),
                {"value": rewrapped, "key": key},
            )
            stats["rewrapped"] += 1
            summary["rewrapped"] += 1
        summary["perTable"][table] = stats
    if not dry_run:
        await session.commit()
    return summary


# --------------------------------------------------------------------------
# KeyVault (salted-KDF edition)
# --------------------------------------------------------------------------


class KeyVault:
    """Encrypts secrets at rest under a salted memory-hard KDF-derived key.

    Two construction modes (M15-W5-S4):

    * ``KeyVault(master_key)`` — LEGACY-COMPATIBLE: byte-identical to
      ``contracts.KeyVault`` (unsalted SHA-256 stretch). Kept so existing
      call sites and cross-section consumers behave exactly as before.
    * ``KeyVault.salted(master_key, salt, ...)`` — the upgraded vault: writes
      use the salted KDF; decryption is DUAL-READ (new key first, legacy
      second), so pre-migration payloads keep working through the transition
      window with zero data loss.

    Wrong master key => honest ``ValueError("secret decryption failed")`` —
    never a silent None, never an echo of secret material.
    """

    def __init__(self, master_key: bytes) -> None:
        if not master_key:
            raise ValueError("master_key must be non-empty")
        self.master_key = master_key
        self.salt: bytes | None = None
        self._write = _legacy_fernet(master_key)
        self._reads = (self._write,)

    @classmethod
    def salted(
        cls,
        master_key: bytes,
        salt: bytes,
        *,
        algorithm: str | None = None,
        n: int = SCRYPT_N,
        r: int = SCRYPT_R,
        p: int = SCRYPT_P,
    ) -> "KeyVault":
        """Upgraded vault: salted-KDF writes + dual-read (new then legacy)."""
        vault = cls.__new__(cls)
        if not master_key:
            raise ValueError("master_key must be non-empty")
        vault.master_key = master_key
        vault.salt = bytes(salt)
        derived = derive_fernet_key(master_key, bytes(salt), algorithm=algorithm, n=n, r=r, p=p)
        vault._write = Fernet(base64.urlsafe_b64encode(derived))
        vault._reads = (vault._write, _legacy_fernet(master_key))
        return vault

    def encrypt(self, plaintext: str) -> str:
        return self._write.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        last_exc: InvalidToken | None = None
        for fernet in self._reads:
            try:
                return fernet.decrypt(ciphertext.encode()).decode()
            except InvalidToken as exc:
                last_exc = exc
        raise ValueError("secret decryption failed: wrong master key?") from last_exc

    @staticmethod
    def hint(secret: str) -> str:
        return f"…{secret[-4:]}" if len(secret) >= 4 else "…"


# --------------------------------------------------------------------------
# Storage helpers (write = salted KDF; read = dual-read window)
# --------------------------------------------------------------------------


async def store_api_key(
    session: AsyncSession,
    vault: KeyVault,
    team_id: str,
    provider: str,
    label: str,
    secret: str,
) -> ApiKey:
    """Persist an API key encrypted under the CURRENT derivation.

    M15-W5-S4: writes go through ``ensure_kdf_params`` — the first write after
    migration 0022 creates the per-install salted-KDF parameter row, and every
    payload from then on is wrapped under the derived key (never the legacy
    stretch). Only the vault's master key is consumed; its own mode is ignored.
    """
    _row, fernet = await ensure_kdf_params(session, vault.master_key)
    row = ApiKey(
        team_id=team_id,
        provider=provider,
        label=label,
        key_encrypted=fernet.encrypt(secret.encode()).decode(),
        key_hint=KeyVault.hint(secret),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def read_api_key(session: AsyncSession, vault: KeyVault, api_key_id: str) -> str:
    """Decrypt a stored API key under the dual-read window.

    Tries the current salted-KDF derivation first, then the legacy unsalted
    stretch — payloads written before migration 0022 keep decrypting until the
    one-shot re-encrypt pass converts them. Failure raises an honest
    ValueError that never echoes secret material.
    """
    row = await session.get(ApiKey, api_key_id)
    if row is None:
        raise LookupError(f"api key {api_key_id} not found")
    params_row, new_fernet, legacy_fernet = await load_kdf_params(session, vault.master_key)
    fernets = [new_fernet] if params_row is None else [new_fernet, legacy_fernet]
    last_exc: InvalidToken | None = None
    for fernet in fernets:
        try:
            return fernet.decrypt(row.key_encrypted.encode()).decode()
        except InvalidToken as exc:
            last_exc = exc
    raise ValueError("secret decryption failed: wrong master key?") from last_exc


# --------------------------------------------------------------------------
# One-shot re-encryption
# --------------------------------------------------------------------------


async def reencrypt_all_payloads(session: AsyncSession, master_key: bytes) -> dict:
    """Re-wrap every stored Fernet payload under the current salted-KDF key.

    One-shot transition command (exposed via scripts/reencrypt_vault.py):
    ensures the parameter row exists, decrypts each api_keys.key_encrypted
    under the dual-read window, re-encrypts under the derived write key, and
    stamps ``key_derivation_params.reencrypted_at`` to mark completion.
    Idempotent: already-new tokens re-encrypt to fresh random-IV tokens that
    decrypt identically; running twice is harmless. The returned summary
    counts rows only — no secret material ever appears in it or in logs.
    """
    from sqlalchemy import select

    from .models import ApiKey as _ApiKey  # noqa: F401 — keeps parity with models

    row, write_fernet = await ensure_kdf_params(session, master_key)
    _, new_read, legacy_read = await load_kdf_params(session, master_key)

    total = rewrapped = already_new = failed = 0
    keys = (await session.execute(select(ApiKey).with_for_update(skip_locked=True))).scalars()
    for item in keys:
        total += 1
        token = item.key_encrypted
        plaintext = None
        for fernet in (new_read, legacy_read):
            try:
                plaintext = fernet.decrypt(token.encode()).decode()
                break
            except InvalidToken:
                continue
        if plaintext is None:
            failed += 1
            continue
        try:
            was_already_new = legacy_read.decrypt(token.encode()).decode() != plaintext
        except InvalidToken:
            # Legacy key cannot read this token at all => already under the
            # derived key from a previous pass.
            was_already_new = True
        if was_already_new:
            already_new += 1
        item.key_encrypted = write_fernet.encrypt(plaintext.encode()).decode()
        rewrapped += 1

    if not row.reencrypted_at:
        row.reencrypted_at = datetime.now(UTC)
    await session.commit()
    return {
        "total": total,
        "rewrapped": rewrapped,
        "already_new": already_new,
        "failed": failed,
    }
