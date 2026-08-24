-- M15-W5-S4: salted KDF upgrade for master-key derivation.
--
-- Before this migration every Fernet payload at rest (api_keys.key_encrypted)
-- was wrapped under the unsalted SHA-256 stretch of HARNESS_MASTER_KEY
-- (contracts.KeyVault derivation). That derivation is deterministic and
-- unsalted: identical master keys across installs produce identical payload
-- keys, and precomputation against the master key applies to every install.
--
-- This migration adds a single-row parameter table holding a per-install
-- random salt plus the KDF identity used to stretch the master key:
--
--   algorithm = 'scrypt'   -> scrypt(n=32768, r=8, p=1) [this install;
--                            OWASP-accepted alternative where argon2-cffi is
--                            unavailable and NO-INSTALL rules apply]
--   algorithm = 'argon2id' -> argon2id(t=3, m=64MiB, p=4) [installs with the
--                            optional dependency present]
--   (row absent)           -> legacy sha256 stretch; dual-read window open
--
-- The row also carries reencrypted_at, stamped by the one-shot re-encrypt
-- command (section1/scripts/reencrypt_vault.py) when every stored payload has
-- been re-wrapped under the derived key — the end of the dual-read window.
--
-- SECURITY NOTE: this table holds key-DERIVATION PARAMETERS ONLY. The salt is
-- public by design (KDF salts are); it must be stable per install or every
-- stored payload becomes undecryptable. No key material is ever stored here,
-- and nothing in the write path logs it.
--
-- Rollout order: apply migration -> deploy code (writes upgrade automatically,
-- reads accept both derivations) -> run scripts/reencrypt_vault.py once ->
-- reencrypted_at stamps completion.

CREATE TABLE IF NOT EXISTS key_derivation_params (
    id INTEGER PRIMARY KEY,
    algorithm VARCHAR(20) NOT NULL CHECK (algorithm IN ('argon2id', 'scrypt')),
    salt BYTEA NOT NULL CHECK (octet_length(salt) >= 32),
    scrypt_n INTEGER NOT NULL DEFAULT 0,
    scrypt_r INTEGER NOT NULL DEFAULT 0,
    scrypt_p INTEGER NOT NULL DEFAULT 0,
    reencrypted_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT key_derivation_params_singleton CHECK (id = 1)
);

-- The singleton row is inserted lazily by the application on first write after
-- this migration (crypto.ensure_kdf_params mints the per-install salt via
-- os.urandom(32)); no seed data here so dev databases and production installs
-- each generate their own salt.
