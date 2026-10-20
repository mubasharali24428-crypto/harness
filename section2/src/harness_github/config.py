from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HARNESS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://localhost/harness_dev"
    master_key: bytes = b"dev-master-key-change-me-in-prod!"
    cors_origins: list[str] = ["http://localhost:3000"]
    github_client_id: str = ""
    github_client_secret: str = ""
    github_redirect_uri: str = "http://localhost:8002/api/v1/github/oauth/callback"
    # OAuth Apps cannot request read-only repository access. Public mode avoids
    # granting account-wide private-repository access; private mode is opt-in.
    github_scope_mode: Literal["public", "private"] = "public"
    # Where the browser lands after the OAuth callback completes or fails.
    frontend_base: str = "http://localhost:3000"
