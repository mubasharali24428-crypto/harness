"""Webhook authenticity: GitHub HMAC-SHA256, GitLab secret token and Standard-Webhooks signing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

TOLERANCE_S = 300


class SignatureError(ValueError):
    pass


def verify_github(secret: str, body: bytes, header: str | None) -> None:
    if not header or not header.startswith("sha256="):
        raise SignatureError("missing or malformed X-Hub-Signature-256")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, header.strip()):
        raise SignatureError("signature mismatch")


def verify_gitlab_token(secret: str, header: str | None) -> None:
    """GitLab's classic secret token header (shared secret, constant-time compare)."""
    if not header or not hmac.compare_digest(
        hashlib.sha256(secret.encode()).digest(), hashlib.sha256(header.encode()).digest()
    ):
        raise SignatureError("invalid X-Gitlab-Token")


def verify_standard_webhooks(
    secret: str,
    msg_id: str | None,
    timestamp: str | None,
    body: bytes,
    signature: str | None,
    *,
    now: float | None = None,
) -> None:
    """GitLab signing tokens (Standard Webhooks): HMAC over `id.timestamp.body`, replay-windowed."""
    if not (msg_id and timestamp and signature):
        raise SignatureError("missing webhook-id/timestamp/signature")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise SignatureError("bad webhook-timestamp") from exc
    if abs((now if now is not None else time.time()) - ts) > TOLERANCE_S:
        raise SignatureError("timestamp outside the replay window")
    raw = secret.removeprefix("whsec_")
    try:
        key = base64.b64decode(raw)
    except ValueError as exc:
        raise SignatureError("signing secret is not base64") from exc
    mac = hmac.new(key, f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode()
    for part in signature.split():
        version, _, value = part.partition(",")
        if version == "v1" and hmac.compare_digest(expected, value):
            return
    raise SignatureError("signature mismatch")


def sign_standard_webhooks(secret: str, msg_id: str, timestamp: int, body: bytes) -> str:
    """Test/producer helper mirroring the verifier."""
    key = base64.b64decode(secret.removeprefix("whsec_"))
    mac = hmac.new(key, f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


def verify_bitbucket(secret: str, body: bytes, header: str | None) -> None:
    """Bitbucket Cloud secret: `X-Hub-Signature: sha256=<hmac>` over the raw body."""
    verify_github(secret, body, header)


def verify_azure(secret: str, token_header: str | None, authorization: str | None) -> None:
    """Azure DevOps service hooks cannot sign bodies; they send a custom header or Basic auth.

    Weaker than HMAC (a bearer secret, not body-bound), so the registration is TLS-only by
    operator policy and the report records it as such.
    """
    candidate = token_header
    if not candidate and authorization and authorization.lower().startswith("basic "):
        try:
            candidate = base64.b64decode(authorization[6:]).decode().partition(":")[2]
        except (ValueError, UnicodeDecodeError):
            candidate = None
    verify_gitlab_token(secret, candidate)
