"""S5 -> browser-worker capability tokens.

Wire format shared with workers/browser/src/capability.ts.

Token: bcap1.<b64url(payload json)>.<b64url(HMAC-SHA256 over "bcap1.<payload segment>")>
Payload JSON: sorted keys, compact separators, UTF-8 (matches the TS canonicalJson).
Bearer, expiring, bound to one task / team / project / run and a bounded origin set.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

PREFIX = "bcap1"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def mint(secret: str, *, task_id: str, team_id: str, project_id: str, run_id: str,
         allowed_origins: list[str], ttl_seconds: int = 300, now: int | None = None,
         nonce: str) -> str:
    iat = int(time.time()) if now is None else now
    payload = {
        "v": 1, "op": "browser", "taskId": task_id, "teamId": team_id,
        "projectId": project_id, "runId": run_id, "allowedOrigins": allowed_origins,
        "iat": iat, "exp": iat + ttl_seconds, "nonce": nonce,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    body = _b64(raw.encode())
    sig = hmac.new(secret.encode(), f"{PREFIX}.{body}".encode(), hashlib.sha256).digest()
    return f"{PREFIX}.{body}.{_b64(sig)}"


def verify(token: str, secret: str, *, team_id: str, project_id: str, run_id: str,
           now: int | None = None) -> dict:
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise ValueError("malformed capability token")
    expected = hmac.new(secret.encode(), f"{PREFIX}.{parts[1]}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(_unb64(parts[2]), expected):
        raise ValueError("capability signature mismatch")
    payload = json.loads(_unb64(parts[1]))
    if payload.get("v") != 1 or payload.get("op") != "browser":
        raise ValueError("unsupported capability op/version")
    if (int(time.time()) if now is None else now) >= payload["exp"]:
        raise ValueError("capability token has expired")
    if (payload["teamId"], payload["projectId"], payload["runId"]) != (team_id, project_id, run_id):
        raise ValueError("capability token is for a different run")
    return payload
