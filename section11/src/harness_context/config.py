"""Section 11 settings (pydantic-settings, HARNESS_ env prefix like siblings)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HARNESS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://localhost/harness_dev"
    master_key: bytes = b"dev-master-key-change-me-in-prod!"
    cors_origins: list[str] = ["http://localhost:3000"]

    # Pluggable embedding backend. Default "ollama" (local models, AGENTS.md
    # environment); CI/tests use "hashing" (deterministic, zero network).
    embedding_backend: str = "ollama"
    ollama_model: str = "nomic-embed-text"
    ollama_base_url: str = "http://127.0.0.1:11434"

    # Chunk geometry (line windows; see chunking.py).
    chunk_window: int = 120
    chunk_overlap: int = 20
