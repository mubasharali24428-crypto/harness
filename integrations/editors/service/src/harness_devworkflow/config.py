"""Configuration for the developer-workflow lane service (A4).

All upstream bases are explicit; an unset base means the dependent capability
is honestly reported as unavailable and its operations fail closed with
CAPABILITY_UNAVAILABLE — never a fake success.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration. Construct via Config.from_env()."""

    identity_base: str | None
    sandbox_base: str | None
    orchestrator_base: str | None
    context_base: str | None
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8014
    allowed_origins: tuple[str, ...] = field(default_factory=tuple)
    request_timeout_s: float = 10.0
    # Absolute dirs a session may clone from. Empty = no local clones (fail closed).
    repo_roots: tuple[str, ...] = field(default_factory=tuple)

    @staticmethod
    def from_env() -> Config:
        data_dir = Path(
            _env("DEVWORKFLOW_DATA_DIR", str(Path.cwd() / ".devworkflow-data"))
        ).expanduser()
        origins = _env("DEVWORKFLOW_ALLOWED_ORIGINS", "")
        return Config(
            identity_base=_env("DEVWORKFLOW_IDENTITY_BASE"),
            sandbox_base=_env("DEVWORKFLOW_SANDBOX_BASE"),
            orchestrator_base=_env("DEVWORKFLOW_ORCHESTRATOR_BASE"),
            context_base=_env("DEVWORKFLOW_CONTEXT_BASE"),
            data_dir=data_dir,
            host=_env("DEVWORKFLOW_HOST", "127.0.0.1") or "127.0.0.1",
            port=int(_env("DEVWORKFLOW_PORT", "8014") or "8014"),
            allowed_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
            repo_roots=tuple(
                r.strip()
                for r in (_env("DEVWORKFLOW_REPO_ROOTS", "") or "").split(os.pathsep)
                if r.strip()
            ),
        )

    def require_identity(self) -> str:
        if not self.identity_base:
            raise CapabilityUnavailable("identity", "DEVWORKFLOW_IDENTITY_BASE is not configured")
        return self.identity_base

    def require_sandbox(self) -> str:
        if not self.sandbox_base:
            raise CapabilityUnavailable("sandbox", "DEVWORKFLOW_SANDBOX_BASE is not configured")
        return self.sandbox_base


class CapabilityUnavailable(RuntimeError):
    """A required upstream capability is not configured/reachable."""

    def __init__(self, capability: str, detail: str):
        super().__init__(detail)
        self.capability = capability
        self.detail = detail
