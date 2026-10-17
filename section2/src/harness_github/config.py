from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HARNESS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://localhost/harness_dev"
    master_key: bytes = b"dev-master-key-change-me-in-prod!"
    cors_origins: list[str] = ["http://localhost:3000"]
    github_client_id: str = ""
    github_client_secret: str = ""
    github_redirect_uri: str = "http://localhost:8002/api/v1/github/oauth/callback"
    # Where the browser lands after the OAuth callback completes or fails.
    frontend_base: str = "http://localhost:3000"
    # A4-S3 issue-intake store (lane-owned SQLite). Empty = in-memory: NOT durable across restarts.
    intake_db_path: str = ""
