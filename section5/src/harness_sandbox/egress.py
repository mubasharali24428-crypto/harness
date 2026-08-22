from urllib.parse import urlparse

from harness_sandbox.config import config


def is_allowed_destination(url_or_host: str) -> bool:
    """Checks whether an outbound network target is in pinned registries or git remotes."""
    parsed = urlparse(url_or_host)
    host = parsed.netloc or parsed.path
    host = host.split(":")[0].lower()

    if host in config.pinned_registries:
        return True
    if host in ("github.com", "api.github.com", "gitlab.com"):
        return True
    return False


class EgressPolicy:
    """Boolean facade over is_allowed_destination."""

    @staticmethod
    def is_url_allowed(url_or_host: str) -> bool:
        return is_allowed_destination(url_or_host)
