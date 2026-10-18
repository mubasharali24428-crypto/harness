"""Configuration. Unset identity = nothing authenticates (fail closed)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name) or default


@dataclass(frozen=True)
class Config:
    identity_base: str | None
    data_dir: Path
    # Hosts an admin may register. Default deny for loopback/private: opt in explicitly.
    allow_loopback: bool = False
    allow_private: bool = False
    # "disabled" = register/discover/grant only; tool execution must go through the S5-managed
    # runtime (NEEDS INTEGRATION). "direct" is for dev and the test-suite.
    executor_mode: str = "disabled"
    call_timeout_s: float = 10.0
    max_response_bytes: int = 256 * 1024
    # Ed25519 public key (hex) that signs the vetted catalog. Unset = catalog not installable.
    catalog_public_key: str | None = None

    @staticmethod
    def from_env() -> Config:
        return Config(
            identity_base=_env("EXTENSIONS_IDENTITY_BASE") or None,
            data_dir=Path(_env("EXTENSIONS_DATA_DIR", str(Path.cwd() / ".extensions-data"))),
            allow_loopback=_env("EXTENSIONS_ALLOW_LOOPBACK") == "1",
            allow_private=_env("EXTENSIONS_ALLOW_PRIVATE") == "1",
            executor_mode=_env("EXTENSIONS_EXECUTOR_MODE", "disabled"),
            catalog_public_key=_env("EXTENSIONS_CATALOG_PUBKEY") or None,
        )
