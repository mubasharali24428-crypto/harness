from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HARNESS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://localhost/harness_dev"
    cors_origins: list[str] = ["http://localhost:3000"]
    master_key: bytes = b"dev-master-key-change-me-in-prod!"

    # H2r: when True, passwordless /auth/login is refused (403 OWNERSHIP_REQUIRED)
    # and callers must prove inbox ownership via /auth/request-login + /auth/verify-otp.
    require_ownership: bool = False
    otp_ttl_minutes: int = 10
    otp_max_attempts: int = 5
