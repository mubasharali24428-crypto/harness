"""Shared security primitives. Single source of truth — Section 1 owns this file."""

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken


class KeyVault:
    """Encrypts secrets at rest. Any master key bytes -> derived Fernet key."""

    def __init__(self, master_key: bytes) -> None:
        digest = hashlib.sha256(master_key).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("secret decryption failed: wrong master key?") from exc

    @staticmethod
    def hint(secret: str) -> str:
        return f"…{secret[-4:]}" if len(secret) >= 4 else "…"


# --------------------------------------------------------------------------
# KDF derivation (M17-B3: promoted from Section 1 — R-02a requires S1 writes
# and S6 reads to agree byte-for-byte, so the derivation lives here once)
# --------------------------------------------------------------------------

KDF_ALGO_ARGON2ID = "argon2id"
KDF_ALGO_SCRYPT = "scrypt"

try:  # pragma: no cover - branch taken only where argon2-cffi exists
    from argon2.low_level import Type as _Argon2Type
    from argon2.low_level import hash_secret_raw as _argon2_hash_secret_raw

    HAS_ARGON2 = True
except ImportError:
    HAS_ARGON2 = False

# scrypt parameters when argon2id is unavailable. n=2**15 (32 MiB), r=8, p=1:
# OWASP's recommended scrypt floor for interactive workloads.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1

# Argon2id parameters for installs where argon2-cffi IS available (documented
# parity; not exercised on this deployment — installing is out of bounds here).
ARGON2_T_COST = 3
ARGON2_M_COST_KIB = 65536  # 64 MiB
ARGON2_PARALLELISM = 4

SALT_LENGTH = 32

# hashlib.scrypt's libscrypt default maxmem (~16 MiB) rejects exactly the
# parameter set above — raise it explicitly or every derivation raises
# ValueError. Working set is 128*n*r bytes; headroom covers p>1 scaling.
_SCRYPT_MAXMEM = 128 * SCRYPT_N * SCRYPT_R * SCRYPT_P * 2


def kdf_algo_id() -> str:
    return KDF_ALGO_ARGON2ID if HAS_ARGON2 else KDF_ALGO_SCRYPT


def derive_fernet_key(
    master_key: bytes,
    salt: bytes,
    *,
    algorithm: str | None = None,
    n: int = SCRYPT_N,
    r: int = SCRYPT_R,
    p: int = SCRYPT_P,
) -> bytes:
    """Stretch ``master_key`` with a salted memory-hard KDF to a Fernet key.

    Deterministic given (master_key, salt, params): the persisted parameters
    row reconstructs the payload key at read time without key material ever
    being stored. Raises ValueError on unknown algorithms or short salts.
    """
    if not master_key:
        raise ValueError("master_key must be non-empty")
    if len(salt) < SALT_LENGTH:
        raise ValueError(f"salt must be >= {SALT_LENGTH} bytes")
    algorithm = algorithm or kdf_algo_id()
    if algorithm == KDF_ALGO_ARGON2ID and HAS_ARGON2:
        stretched = _argon2_hash_secret_raw(
            secret=master_key,
            salt=salt,
            time_cost=ARGON2_T_COST,
            memory_cost=ARGON2_M_COST_KIB,
            parallelism=ARGON2_PARALLELISM,
            hash_len=32,
            type=_Argon2Type.ID,
        )
    elif algorithm == KDF_ALGO_SCRYPT:
        stretched = hashlib.scrypt(
            master_key, salt=salt, n=n, r=r, p=p, dklen=32, maxmem=_SCRYPT_MAXMEM
        )
    else:
        raise ValueError(f"unknown kdf algorithm: {algorithm}")
    # HKDF-style extract+expand to exactly Fernet's 32-byte key, domain-
    # separated per KDF so an argon2id install and a scrypt install can never
    # collide on the same output by accident.
    prk = hmac.new(salt, b"harness-kdf-v1|" + algorithm.encode(), hashlib.sha256).digest()
    okm = hmac.new(prk, b"fernet-key\x01", hashlib.sha256).digest()
    return hmac.new(stretched, okm, hashlib.sha256).digest()


def derive_fernet(
    master_key: bytes,
    salt: bytes,
    *,
    algorithm: str | None = None,
    n: int = SCRYPT_N,
    r: int = SCRYPT_R,
    p: int = SCRYPT_P,
) -> Fernet:
    """``derive_fernet_key`` wrapped into a ready Fernet (salt required)."""
    return Fernet(
        base64.urlsafe_b64encode(
            derive_fernet_key(master_key, salt, algorithm=algorithm, n=n, r=r, p=p)
        )
    )


def legacy_fernet(master_key: bytes) -> Fernet:
    """The pre-0022 derivation, byte-identical to ``KeyVault``'s stretch.

    Kept ONLY for the dual-read transition window; never used for writes.
    """
    digest = hashlib.sha256(master_key).digest()
    return Fernet(base64.urlsafe_b64encode(digest))
