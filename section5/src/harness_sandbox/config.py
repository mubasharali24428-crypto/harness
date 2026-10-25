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
    # Opt-in sandbox networking goes through the allow-list proxy on this
    # internal Docker network; both must be set or the runner fails closed.
    egress_network: str = ""
    egress_proxy_url: str = ""

    model_config = SettingsConfigDict(env_prefix="SANDBOX_")


config = SandboxConfig()
