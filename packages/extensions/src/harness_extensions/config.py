"""Settings for the extensions plane (HARNESS_EXTENSIONS_ prefix)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HARNESS_EXTENSIONS_", env_file=".env", extra="ignore"
    )

    #: Durable registry store. SQLite file; ":memory:" only for throwaway tests.
    db_path: str = str(Path("~/.harness-extensions/registry.db").expanduser())

    #: Directory holding the curated plugin catalog (index.json + payloads).
    #: Empty string = the catalog bundled inside the installed package.
    catalog_dir: str = ""

    #: MCP initialize + tools/list timeout for discovery/health checks.
    discover_timeout_s: float = 15.0

    #: Per tool-call timeout enforced by the caller side.
    call_timeout_s: float = 30.0

    #: Timeout for one hook delivery attempt (webhook POST or target tool call).
    hook_timeout_s: float = 5.0

    #: Hard cap on a single tool call's serialized observation (bytes, UTF-8).
    #: Oversized results are truncated with an explicit marker, never dropped.
    max_result_bytes: int = 64 * 1024

    #: Schema hardening bounds applied to every discovered tool inputSchema.
    max_schema_bytes: int = 64 * 1024
    max_schema_depth: int = 12
    max_schema_nodes: int = 2000

    #: Discovered tool descriptions above this size mark the tool rejected
    #: (a megabyte description is a payload bomb aimed at our API clients).
    max_tool_description_bytes: int = 8 * 1024

    #: Skill/playbook content bound (bytes, UTF-8).
    max_skill_bytes: int = 64 * 1024
    max_skill_versions: int = 50

    #: DENY-BY-DEFAULT network posture: loopback/private/link-local targets are
    #: refused unless the operator explicitly opts in (self-hosted MCP servers
    #: on localhost/IANA-private space). See url_policy for the exact ranges.
    allow_private_hosts: bool = False

    #: Standalone deployment (python -m harness_extensions): DB for bearer auth.
    database_url: str = "postgresql+asyncpg://localhost/harness_dev"

    #: HTTP port for the standalone service.
    http_port: int = Field(default=8012)

    #: CORS origins for the standalone app (exact-match, no wildcards).
    cors_origins: list[str] = ["http://localhost:3000"]
