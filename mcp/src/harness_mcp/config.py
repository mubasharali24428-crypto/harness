"""Settings for the MCP server (pydantic-settings, HARNESS_ prefix like siblings)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HARNESS_MCP_", env_file=".env", extra="ignore")

    #: Bearer token minted by Section 1 (POST /auth/login) that the server
    #: presents to every upstream section API. All four tools act as THIS
    #: principal — team scoping and role gates apply to it exactly as to a
    #: human session. Required: without it the server starts but every tool
    #: call answers an honest UNAUTHORIZED error result (never a crash).
    service_token: str = Field(default="")

    #: Upstream base URLs. Defaults match deploy/docker-compose.yml's host
    #: port map (identity 8001, orchestrator 8004); context rides the
    #: all-in-one backend on 8000 in compose, localhost:8010 when run bare.
    identity_base_url: str = "http://localhost:8001"
    orchestrator_base_url: str = "http://localhost:8004"
    context_base_url: str = "http://localhost:8000"

    #: Per-call upstream timeout in seconds.
    upstream_timeout_s: float = 30.0

    #: Server name reported in the MCP initialize handshake.
    server_name: str = "harness"

    #: Server version reported in the MCP initialize handshake.
    server_version: str = "0.1.0"
