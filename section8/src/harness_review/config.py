from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HARNESS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://localhost/harness_dev"
    master_key: bytes = b"dev-master-key-change-me-in-prod!"
    cors_origins: list[str] = ["http://localhost:3000"]
    github_app_url: str = "https://github.com"
