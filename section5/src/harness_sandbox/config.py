from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class SandboxConfig(BaseSettings):
    default_timeout_seconds: int = 300
    default_docker_image: str = "python:3.11-slim"
    max_memory_mb: int = 2048
    max_cpus: float = 2.0
    storage_root: Path = Path("/tmp/harness_sandboxes")
    pinned_registries: list[str] = [
        "registry.npmjs.org",
        "npmjs.com",
        "pypi.org",
        "files.pythonhosted.org",
    ]
    use_docker: bool = False

    model_config = SettingsConfigDict(env_prefix="SANDBOX_")


config = SandboxConfig()
