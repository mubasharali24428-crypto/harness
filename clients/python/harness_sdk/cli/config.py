"""Offline session-token storage for the CLI.

One file, owner-only. The token is the user's session token (never a service
token): it is minted by the identity OTP flow and sent as a bearer on every call.
``HARNESS_TOKEN`` wins over the file so CI can inject a token without writing
anything to disk.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

TOKEN_ENV = "HARNESS_TOKEN"
CONFIG_DIR_ENV = "HARNESS_CONFIG_DIR"


def config_dir() -> Path:
    explicit = os.environ.get(CONFIG_DIR_ENV)
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "harness"


def credentials_path() -> Path:
    return config_dir() / "credentials.json"


@dataclass(frozen=True)
class Credentials:
    token: str
    identityUrl: str | None = None
    orchestratorUrl: str | None = None


def load(path: Path | None = None) -> Credentials | None:
    target = path or credentials_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    token = str(raw.get("token") or "")
    if not token:
        return None
    return Credentials(
        token=token,
        identityUrl=raw.get("identityUrl"),
        orchestratorUrl=raw.get("orchestratorUrl"),
    )


def save(credentials: Credentials, path: Path | None = None) -> Path:
    """Write atomically with 0600, creating the directory as 0700."""
    target = path or credentials_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    fd, tmp = tempfile.mkstemp(prefix=".credentials-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(credentials), handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def clear(path: Path | None = None) -> bool:
    try:
        (path or credentials_path()).unlink()
    except FileNotFoundError:
        return False
    return True


def resolve_token(environ: dict[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    token = env.get(TOKEN_ENV)
    if token:
        return token
    stored = load()
    return stored.token if stored else None
