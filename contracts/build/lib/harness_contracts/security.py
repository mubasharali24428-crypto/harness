"""Shared security primitives. Single source of truth — Section 1 owns this file."""

import base64
import hashlib

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
