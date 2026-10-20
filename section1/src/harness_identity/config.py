from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HARNESS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://localhost/harness_dev"
    cors_origins: list[str] = ["http://localhost:3000"]
    master_key: bytes = b"dev-master-key-change-me-in-prod!"

    # H2r: when True, passwordless /auth/login is refused (403 OWNERSHIP_REQUIRED)
    # and callers must prove inbox ownership via /auth/request-login + /auth/verify-otp.
    # C-001 (M11-W1): default ON — passwordless /auth/login is refused unless
    # inbox ownership is proven via /auth/request-login + /auth/verify-otp.
    require_ownership: bool = True
    otp_ttl_minutes: int = 10
    otp_max_attempts: int = 5

    # E-smtp: OTP delivery. Empty smtp_host => dev log printer (visible in docker logs).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "harness@noreply.local"
    smtp_starttls: bool = True
    # Local dev only: allow sending without a verified STARTTLS (default: refuse).
    smtp_allow_insecure: bool = False

    # M17-B4 (MAC2-F8) proxy-header policy: X-Forwarded-For is honored ONLY
    # when the direct peer is inside one of these CIDRs; otherwise the peer
    # address is used verbatim and XFF is ignored (spoof-proof default).
    # Override per deployment with HARNESS_TRUSTED_PROXY_CIDRS (JSON array).
    trusted_proxy_cidrs: list[str] = [
        "127.0.0.0/8",
        "::1/128",
        "::/128",
        "fc00::/7",
        "fe80::/10",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
    ]
