"""F095 — short-lived, audience/run/action-bound capability grants.

A capability grant authorizes exactly ONE (team, run, operation, audience)
tuple for a bounded time, optionally pinning the canonical digest of the
operation arguments. Tokens are HMAC-SHA256 signed with a key derived from
the deployment master key (domain-separated — never the master key itself).

Verification is fail-closed and constant-time on the signature. Anything
malformed, expired, replayed beyond ``maxUses``, or bound to a different
team/run/operation/audience/arguments is a DENY with a stable reason code.

The token is a bearer object: anyone holding it can present it, so it must
travel only over authenticated server-to-server transports and must never
enter prompts, logs, or model context.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from .decisions import Decision, Outcome

TOKEN_PREFIX = "cap1"
SIGNING_DOMAIN = b"harness-capability-v1|"
KEY_DOMAIN = b"harness-capability-key-v1"
MAX_TOKEN_CHARS = 4096
MAX_TTL_SECONDS = 86_400
CLOCK_SKEW_SECONDS = 30
UNSET: Final = object()


def derive_capability_key(master_key: bytes) -> bytes:
    """Domain-separated grant-signing key from the deployment master key."""
    if not master_key:
        raise ValueError("master_key must be non-empty")
    return hmac.new(master_key, KEY_DOMAIN, hashlib.sha256).digest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def args_digest(args: Any) -> str:
    """Stable sha256 over the canonical JSON of operation arguments."""
    return hashlib.sha256(canonical_json(args)).hexdigest()


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


@dataclass(frozen=True)
class IssuedCapability:
    token: str
    token_id: str
    team_id: str
    run_id: str
    project_id: str | None
    operation: str
    audience: str
    args_digest: str
    issued_at: int
    expires_at: int
    max_uses: int


def _check_bounded(name: str, value: str, limit: int) -> None:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"{name} must be a non-empty string of <= {limit} characters")


class CapabilitySigner:
    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("capability signing key must be >= 32 bytes")
        self._key = key

    def _sign(self, payload_bytes: bytes) -> str:
        return _b64url_encode(
            hmac.new(self._key, SIGNING_DOMAIN + payload_bytes, hashlib.sha256).digest()
        )

    def issue(
        self,
        *,
        team_id: str,
        run_id: str,
        operation: str,
        audience: str,
        args: Any = None,
        project_id: str | None = None,
        ttl_seconds: int = 300,
        max_uses: int = 1,
        issuer: str = "harness",
        now: float | None = None,
    ) -> IssuedCapability:
        _check_bounded("team_id", team_id, 64)
        _check_bounded("run_id", run_id, 128)
        _check_bounded("operation", operation, 128)
        _check_bounded("audience", audience, 128)
        _check_bounded("issuer", issuer, 128)
        if project_id is not None:
            _check_bounded("project_id", project_id, 64)
        if not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
            raise ValueError(f"ttl_seconds must be within 1..{MAX_TTL_SECONDS}")
        if not 1 <= max_uses <= 1_000_000:
            raise ValueError("max_uses must be within 1..1000000")

        issued_at = int(now if now is not None else time.time())
        payload = {
            "version": 1,
            "tokenId": base64.urlsafe_b64encode(secrets.token_bytes(12)).rstrip(b"=").decode(),
            "teamId": team_id,
            "runId": run_id,
            "operation": operation,
            "audience": audience,
            "argsDigest": args_digest(args),
            "issuedAt": issued_at,
            "expiresAt": issued_at + ttl_seconds,
            "maxUses": max_uses,
            "issuer": issuer,
        }
        if project_id is not None:
            payload["projectId"] = project_id
        payload_bytes = canonical_json(payload)
        token = f"{TOKEN_PREFIX}.{_b64url_encode(payload_bytes)}.{self._sign(payload_bytes)}"
        return IssuedCapability(
            token=token,
            token_id=payload["tokenId"],
            team_id=team_id,
            run_id=run_id,
            project_id=project_id,
            operation=operation,
            audience=audience,
            args_digest=payload["argsDigest"],
            issued_at=issued_at,
            expires_at=payload["expiresAt"],
            max_uses=max_uses,
        )

    def verify(
        self,
        token: str,
        *,
        required: RequiredBinding,
        now: float | None = None,
        replay_store: ReplayStore | None = None,
    ) -> Decision:
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_CHARS:
            return Decision(Outcome.DENY, "capability.malformed")
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
            return Decision(Outcome.DENY, "capability.malformed")
        try:
            payload_bytes = _b64url_decode(parts[1])
            signature = _b64url_decode(parts[2])
        except (ValueError, TypeError):
            return Decision(Outcome.DENY, "capability.malformed")
        expected_signature = hmac.new(
            self._key, SIGNING_DOMAIN + payload_bytes, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(signature, expected_signature):
            return Decision(Outcome.DENY, "capability.bad_signature")
        try:
            payload = json.loads(payload_bytes)
        except (ValueError, UnicodeDecodeError):
            return Decision(Outcome.DENY, "capability.malformed")
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return Decision(Outcome.DENY, "capability.unsupported_version")

        for key in ("tokenId", "teamId", "runId", "operation", "audience", "argsDigest", "issuer"):
            if not isinstance(payload.get(key), str) or not payload[key]:
                return Decision(Outcome.DENY, "capability.malformed")
        if not isinstance(payload.get("issuedAt"), int) or not isinstance(
            payload.get("expiresAt"), int
        ):
            return Decision(Outcome.DENY, "capability.malformed")
        if not isinstance(payload.get("maxUses"), int) or payload["maxUses"] < 1:
            return Decision(Outcome.DENY, "capability.malformed")

        current = now if now is not None else time.time()
        if current >= payload["expiresAt"]:
            return Decision(
                Outcome.DENY, "capability.expired", details={"tokenId": payload["tokenId"]}
            )
        if current < payload["issuedAt"] - CLOCK_SKEW_SECONDS:
            return Decision(
                Outcome.DENY, "capability.not_yet_valid", details={"tokenId": payload["tokenId"]}
            )
        if payload["teamId"] != required.team_id:
            return Decision(Outcome.DENY, "capability.wrong_team")
        if required.project_id is not None and payload.get("projectId") != required.project_id:
            return Decision(Outcome.DENY, "capability.wrong_project")
        if payload["runId"] != required.run_id:
            return Decision(Outcome.DENY, "capability.wrong_run")
        if payload["operation"] != required.operation:
            return Decision(Outcome.DENY, "capability.wrong_operation")
        if payload["audience"] != required.audience:
            return Decision(Outcome.DENY, "capability.wrong_audience")
        if required.args is not UNSET and args_digest(required.args) != payload["argsDigest"]:
            return Decision(Outcome.DENY, "capability.arguments_mismatch")
        if replay_store is not None and not replay_store.consume(
            payload["tokenId"], max_uses=payload["maxUses"], expires_at=payload["expiresAt"]
        ):
            return Decision(
                Outcome.DENY,
                "capability.uses_exhausted",
                details={"tokenId": payload["tokenId"]},
            )
        return Decision(
            Outcome.ALLOW,
            "capability.valid",
            details={
                "tokenId": payload["tokenId"],
                "expiresAt": payload["expiresAt"],
                "maxUses": payload["maxUses"],
                "issuer": payload["issuer"],
            },
        )


@dataclass(frozen=True)
class RequiredBinding:
    """What the enforcement point demands of a presented grant.

    ``args=UNSET`` skips the argument-digest check; ``args=None`` binds to
    the digest of JSON ``null`` — the issue-side default.
    """

    team_id: str
    run_id: str
    operation: str
    audience: str
    project_id: str | None = None
    args: Any = UNSET


class ReplayStore(Protocol):
    def consume(self, token_id: str, *, max_uses: int, expires_at: float) -> bool:
        """Atomically record a use; False when the budget is exhausted."""
        ...


@dataclass
class InMemoryReplayStore:
    """Thread-safe standalone store for tests and single-process use.

    The section1 HTTP adapter uses a Postgres-backed store instead so replay
    protection survives restarts and spans replicas. Entries are only ever
    pruned once EXPIRED — pruning a live entry would silently reset its use
    budget.
    """

    _uses: dict[str, tuple[int, float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def consume(self, token_id: str, *, max_uses: int, expires_at: float) -> bool:
        with self._lock:
            now = time.time()
            uses, stored_expiry = self._uses.get(token_id, (0, expires_at))
            if stored_expiry <= now:
                uses = 0
            if uses >= max_uses:
                return False
            self._uses[token_id] = (uses + 1, expires_at)
            if len(self._uses) > 4096:
                for key in [k for k, (_, expiry) in self._uses.items() if expiry <= now]:
                    self._uses.pop(key, None)
            return True
