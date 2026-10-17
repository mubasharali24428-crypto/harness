"""S1-R02 — secret handling in Section 6: vault gating + S1-owned dual-read KDF.

Covers the Section-6 half of the R-02 remediation:

* R-02a: stored keys are decrypted through the contracts-owned KDF read
  (derived window first, legacy second) whenever the vault carries a
  ``master_key`` marker, so ``reencrypt_vault.py`` can never brick key
  resolution. Contracts ``KeyVault`` (no ``master_key`` attribute) keeps the
  plain single-window decrypt — byte-compatible with pre-remediation.
  Derivation: ``harness_contracts.security``; params: frozen
  ``key_derivation_params`` row read via raw SQL (M17-B3 isolation).
* R-02b: no silent dev-key vault. Construction is explicit:
  passed vault → ``HARNESS_MASTER_KEY`` → ``HARNESS_ALLOW_DEV_MASTER_KEY=1``
  (local only) → ``None``, which resolves as an honest "not configured" reply.
* Honest failures: unreadable key => ``MalformedTeamKeyError``; the error and
  logs name the condition, never the secret.
"""

import base64
import hashlib
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from harness_contracts.security import derive_fernet_key

from harness_meeting.ai_participant import (
    MalformedTeamKeyError,
    _load_kdf_params,
    pick_team_key,
)
from harness_meeting.service import EnvMasterKeyVault, MeetingService
from tests.unit.test_ai_llm_wiring import VAULT, fake_factory, make_key_row


def _fernet_from(master: bytes, salt: bytes = b"") -> Fernet:
    digest = hashlib.sha256(master + salt).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypted_row(provider: str, fernet: Fernet, secret: str):
    row = make_key_row(f"k-{provider}", provider, secret)
    row["key_encrypted"] = fernet.encrypt(secret.encode()).decode()
    return row


# ---------------------------------------------------------------------------
# R-02b — vault construction is explicit, never a silent dev constant
# ---------------------------------------------------------------------------


def test_no_env_no_vault_is_honest_none(monkeypatch):
    for var in ("HARNESS_MASTER_KEY", "HARNESS_ALLOW_DEV_MASTER_KEY"):
        monkeypatch.delenv(var, raising=False)
    svc = MeetingService()
    assert svc.vault is None  # resolves as "no AI key configured", never a bogus decrypt


def test_master_key_env_wires_marker_vault(monkeypatch):
    monkeypatch.delenv("HARNESS_ALLOW_DEV_MASTER_KEY", raising=False)
    monkeypatch.setenv("HARNESS_MASTER_KEY", "prod-master-key")
    svc = MeetingService()
    assert isinstance(svc.vault, EnvMasterKeyVault)
    assert svc.vault.master_key == b"prod-master-key"


def test_dev_master_key_requires_explicit_optin(monkeypatch):
    monkeypatch.delenv("HARNESS_MASTER_KEY", raising=False)
    monkeypatch.setenv("HARNESS_ALLOW_DEV_MASTER_KEY", "1")
    svc = MeetingService()
    assert svc.vault is not None  # local dev only, must be asked for explicitly


async def test_store_without_vault_answers_not_configured(monkeypatch):
    monkeypatch.delenv("HARNESS_MASTER_KEY", raising=False)
    monkeypatch.delenv("HARNESS_ALLOW_DEV_MASTER_KEY", raising=False)
    svc = MeetingService(team_key_store=fake_factory([make_key_row("k1", "openai", "sk-1")]))
    reply = svc.invoke_ai(svc.create_meeting("proj-1", "gated").id, "u1", "@ai hi")
    assert reply is not None
    assert "no AI key configured" in reply.content


# ---------------------------------------------------------------------------
# R-02a — dual-read through Section 1's KDF (derived window, then legacy)
# ---------------------------------------------------------------------------


async def test_dual_read_prefers_s1_derived_window(monkeypatch):
    derived = _fernet_from(b"mk", salt=b"install-salt")
    legacy = _fernet_from(b"mk")
    row = _encrypted_row("openai", derived, "sk-derived-window")

    async def fake_load(_session, master_key):
        assert master_key == b"mk"
        return (None, derived, legacy)

    monkeypatch.setattr("harness_meeting.ai_participant._load_kdf_params", fake_load)
    vault = EnvMasterKeyVault(b"mk")  # marker vault
    secret = await pick_team_key(fake_factory([row]), vault, "openai", "t1")
    assert secret == "sk-derived-window"


async def test_dual_read_falls_back_to_legacy_window(monkeypatch):
    derived = _fernet_from(b"mk", salt=b"install-salt")
    legacy = _fernet_from(b"mk")
    row = _encrypted_row("openai", legacy, "sk-legacy-payload")

    async def fake_load(_session, _master_key):
        return (None, derived, legacy)

    monkeypatch.setattr("harness_meeting.ai_participant._load_kdf_params", fake_load)
    vault = EnvMasterKeyVault(b"mk")
    secret = await pick_team_key(fake_factory([row]), vault, "openai", "t1")
    assert secret == "sk-legacy-payload"


async def test_dual_read_wrong_master_key_is_honest_error(monkeypatch):
    derived = _fernet_from(b"mk", salt=b"install-salt")
    legacy = _fernet_from(b"mk")
    row = _encrypted_row("openai", _fernet_from(b"other-key"), "sk-secret-value")

    async def fake_load(_session, _master_key):
        return (None, derived, legacy)

    monkeypatch.setattr("harness_meeting.ai_participant._load_kdf_params", fake_load)
    vault = EnvMasterKeyVault(b"mk")
    with pytest.raises(MalformedTeamKeyError) as excinfo:
        await pick_team_key(fake_factory([row]), vault, "openai", "t1")
    assert "sk-secret-value" not in str(excinfo.value)


async def test_contracts_vault_single_window_still_works():
    """Pre-remediation vaults (no .master_key) keep exact old behavior."""
    row = make_key_row("k1", "openai", "sk-plain")
    secret = await pick_team_key(fake_factory([row]), VAULT, "openai", "t1")
    assert secret == "sk-plain"


# ---------------------------------------------------------------------------
# R-02a — raw-SQL params read rebuilds the derived window (M17-B3 promotion)
# ---------------------------------------------------------------------------


class _ParamsSession:
    def __init__(self, row=None) -> None:
        self._row = row

    async def execute(self, _sql):
        row = self._row

        class _Result:
            def first(self):
                return row

        return _Result()


async def test_load_kdf_params_rebuilds_derived_window_from_row():
    salt = b"s" * 32
    params = SimpleNamespace(
        algorithm="scrypt", salt=salt, scrypt_n=0, scrypt_r=0, scrypt_p=0
    )
    _row, new, legacy = await _load_kdf_params(_ParamsSession(params), b"mk")

    expected = Fernet(
        base64.urlsafe_b64encode(derive_fernet_key(b"mk", salt, algorithm="scrypt"))
    )
    payload = expected.encrypt(b"sk-real-window")
    assert new.decrypt(payload).decode() == "sk-real-window"
    assert legacy.decrypt(_fernet_from(b"mk").encrypt(b"x")) == b"x"


async def test_load_kdf_params_no_row_is_legacy_only():
    _row, new, legacy = await _load_kdf_params(_ParamsSession(None), b"mk")
    assert new is legacy
    assert legacy.decrypt(_fernet_from(b"mk").encrypt(b"x")) == b"x"
