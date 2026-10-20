"""Connector core: evidence-first primitives shared by every provider.

Design rules baked in here (SESSION A5-S3 acceptance evidence):

* inbound webhooks are verified BEFORE parsing (tamper/replay -> 401);
* tenants are mapped explicitly — unknown tenant is refused (no oracle);
* deliveries are idempotent: a replayed delivery never proposes a second plan;
  the dedupe marker is consumed ONLY on success/terminal outcomes, so an
  upstream outage does not eat the provider's retry;
* rate limits are per tenant;
* secret-looking material in incident text is redacted before it reaches the
  plan gate or any log;
* incident text is untrusted context: connectors can ONLY propose plans.
  There is no code path from intake to approve/start/execute.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any


class IntakeRejected(Exception):
    """A delivery is refused. ``code`` is stable and machine-readable."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(f"{code}: {message}")


# --------------------------------------------------------------- signatures


@dataclass(frozen=True)
class SignatureScheme:
    """HMAC request signing, parameterized per provider convention."""

    name: str
    header: str  # signature header
    timestamp_header: str | None  # replay window is enforced when set
    tolerance_s: int = 300
    prefix: str = ""  # e.g. Slack's "v0="
    timestamped_body: str | None = None  # e.g. Slack: "v0:{ts}:{body}"


def verify_signature(
    scheme: SignatureScheme,
    secret: str,
    raw_body: bytes,
    headers: dict[str, str],
    *,
    now: int | None = None,
) -> None:
    headers_l = {k.lower(): v for k, v in headers.items()}
    provided = headers_l.get(scheme.header.lower())
    if not provided:
        raise IntakeRejected("SIGNATURE_MISSING", "signature header absent", 401)

    signed_body = raw_body
    if scheme.timestamp_header is not None:
        ts = headers_l.get(scheme.timestamp_header.lower())
        if not ts or not ts.isdigit():
            raise IntakeRejected("SIGNATURE_INVALID", "missing/invalid timestamp", 401)
        drift = abs((now if now is not None else int(time.time())) - int(ts))
        if drift > scheme.tolerance_s:
            raise IntakeRejected(
                "SIGNATURE_STALE", f"timestamp outside {scheme.tolerance_s}s window", 401
            )
        if scheme.timestamped_body is not None:
            tmpl = scheme.timestamped_body
            signed_body = tmpl.format(ts=ts, body=raw_body.decode("utf-8")).encode("utf-8")

    expected = hmac.new(secret.encode("utf-8"), signed_body, hashlib.sha256).hexdigest()
    provided_hex = provided[len(scheme.prefix):] if scheme.prefix else provided
    if not hmac.compare_digest(expected, provided_hex):
        raise IntakeRejected("SIGNATURE_INVALID", "signature mismatch", 401)


def sign(scheme: SignatureScheme, secret: str, raw_body: bytes, timestamp: str) -> str:
    """Test/peer-side helper producing the header value a provider would send."""
    signed_body = raw_body
    if scheme.timestamped_body is not None:
        signed_body = scheme.timestamped_body.format(
            ts=timestamp, body=raw_body.decode("utf-8")
        ).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), signed_body, hashlib.sha256).hexdigest()
    return f"{scheme.prefix}{digest}"


# --------------------------------------------------------------- idempotency


class IdempotencyStore:
    """SQLite-backed delivery dedupe that survives service restarts.

    Claim/settle/release semantics:
    * ``claim`` is an atomic INSERT OR IGNORE — of N concurrent copies of the
      same delivery, exactly one wins and may proceed;
    * ``settle`` records the terminal outcome (plan proposed);
    * ``release`` removes an in-flight claim after upstream failure, so the
      provider's retry re-enters intake instead of being swallowed as a
      duplicate (an outage must not eat incidents).
    """

    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS deliveries (
                   provider TEXT NOT NULL,
                   delivery_id TEXT NOT NULL,
                   outcome TEXT NOT NULL,
                   plan_id TEXT,
                   claimed_at REAL NOT NULL,
                   PRIMARY KEY (provider, delivery_id)
               )"""
        )
        self._db.commit()

    def claim(self, provider: str, delivery_id: str) -> bool:
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO deliveries VALUES (?,?,?,?,?)",
                (provider, delivery_id, "processing", None, time.time()),
            )
            self._db.commit()
        return cur.rowcount == 1

    def settle(self, provider: str, delivery_id: str, outcome: str, plan_id: str | None) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE deliveries SET outcome=?, plan_id=? WHERE provider=? AND delivery_id=?",
                (outcome, plan_id, provider, delivery_id),
            )
            self._db.commit()

    def release(self, provider: str, delivery_id: str) -> None:
        with self._lock:
            self._db.execute(
                "DELETE FROM deliveries WHERE provider=? AND delivery_id=? AND outcome='processing'",
                (provider, delivery_id),
            )
            self._db.commit()

    def get_outcome(self, provider: str, delivery_id: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT outcome FROM deliveries WHERE provider=? AND delivery_id=?",
                (provider, delivery_id),
            ).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self._db.close()


def delivery_id_from(provider: str, raw_body: bytes, candidates: list[str | None]) -> str:
    """First non-empty provider-supplied id, else a content hash (a replayed
    identical body must dedupe even when the provider sent no id)."""
    for candidate in candidates:
        if candidate:
            return str(candidate)
    return hashlib.sha256(raw_body).hexdigest()


# --------------------------------------------------------------- tenant map


@dataclass
class TenantConfig:
    team_id: str
    enabled: bool = True
    rate_limit_per_minute: int = 30


@dataclass
class TenantMap:
    """provider -> external tenant identifier -> harness team.

    Unknown tenant refuses (403). Disabled tenant refuses (403). The two are
    indistinguishable to the caller on purpose (no oracle).
    """

    tenants: dict[str, dict[str, TenantConfig]] = field(default_factory=dict)

    def resolve(self, provider: str, external_tenant: str | None) -> TenantConfig:
        if not external_tenant:
            raise IntakeRejected("TENANT_UNKNOWN", "delivery has no tenant identifier", 403)
        config = self.tenants.get(provider, {}).get(external_tenant)
        if config is None or not config.enabled:
            raise IntakeRejected("TENANT_UNKNOWN", "tenant not configured", 403)
        return config

    @classmethod
    def from_json(cls, path: str) -> TenantMap:
        raw = json.loads(open(path, encoding="utf-8").read())  # noqa: SIM115
        tenants: dict[str, dict[str, TenantConfig]] = {}
        for provider, entries in raw.items():
            tenants[provider] = {
                ext: TenantConfig(
                    team_id=e["teamId"],
                    enabled=e.get("enabled", True),
                    rate_limit_per_minute=e.get("rateLimitPerMinute", 30),
                )
                for ext, e in entries.items()
            }
        return cls(tenants=tenants)


# ---------------------------------------------------------------- rate limit


class RateLimiter:
    """Per-(provider,tenant) sliding-window counter; in-memory by design —
    a restart resetting the window is acceptable (fail-safe direction: the
    expensive path is plan creation, which is also idempotent)."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: tuple[str, str], limit: int, *, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        window_start = now - 60.0
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if t >= window_start]
            if len(hits) >= limit:
                self._hits[key] = hits
                raise IntakeRejected(
                    "RATE_LIMITED", f"more than {limit} deliveries/minute", 429
                )
            hits.append(now)
            self._hits[key] = hits


# ------------------------------------------------------------------ redaction

_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-+=\/]{12,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"]?[A-Za-z0-9._\-+=\/]{10,}"),
]


def redact_secrets(text: str) -> str:
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(
            lambda m: (m.group(1) if m.groups() else "") + "[REDACTED]", out
        )
    return out


# --------------------------------------------------------------- intake core


@dataclass(frozen=True)
class IntakeEvent:
    provider: str
    deliveryId: str
    externalTenantId: str
    externalId: str
    eventType: str
    title: str
    body: str
    url: str | None = None


@dataclass
class IntakeOutcome:
    status: str  # plan_proposed | duplicate
    planId: str | None
    teamId: str
    goal: str


def build_goal(event: IntakeEvent, *, max_chars: int = 2000) -> str:
    """Deterministic goal text for the plan gate.

    Explicit untrusted-content banner: the incident text is CONTEXT for a
    human-approved plan, never an instruction source.
    """
    body = redact_secrets(event.body)[:max_chars]
    title = redact_secrets(event.title)[:200]
    lines = [
        (
            f"[connector intake | provider={event.provider} | external_id={event.externalId}"
            f" | untrusted context, requires human plan approval]"
        ),
        title,
    ]
    if event.url:
        lines.append(f"source: {event.url}")
    lines.append("")
    lines.append(body)
    return "\n".join(lines).strip()


class IntakePipeline:
    """verify -> parse -> tenant -> rate limit -> dedupe -> propose.

    Propose is the ONLY action this pipeline can take; there is deliberately
    no reference to approve/start/exec on any object it touches.
    """

    def __init__(
        self,
        *,
        provider: str,
        scheme: SignatureScheme,
        secret: str,
        tenants: TenantMap,
        idempotency: IdempotencyStore,
        limiter: RateLimiter,
        plan_gate: Any,  # ProposePlanGate protocol: async propose_goal(goal)->planId
        parse: Any,  # callable(raw_body, headers) -> IntakeEvent
    ) -> None:
        self.provider = provider
        self.scheme = scheme
        self.secret = secret
        self.tenants = tenants
        self.idempotency = idempotency
        self.limiter = limiter
        self.plan_gate = plan_gate
        self.parse = parse

    async def handle(self, raw_body: bytes, headers: dict[str, str]) -> IntakeOutcome:
        verify_signature(self.scheme, self.secret, raw_body, headers)
        event = self.parse(raw_body, headers)
        tenant = self.tenants.resolve(self.provider, event.externalTenantId)

        if not self.idempotency.claim(self.provider, event.deliveryId):
            # Already proposed (or concurrently in flight): never a second plan.
            return IntakeOutcome("duplicate", None, tenant.team_id, "")

        try:
            self.limiter.check(
                (self.provider, event.externalTenantId), tenant.rate_limit_per_minute
            )
        except IntakeRejected:
            self.idempotency.settle(self.provider, event.deliveryId, "rate_limited", None)
            raise

        goal = build_goal(event)
        try:
            plan_id = await self.plan_gate.propose_goal(goal)  # raises on upstream failure
        except Exception:
            self.idempotency.release(self.provider, event.deliveryId)
            raise
        self.idempotency.settle(self.provider, event.deliveryId, "plan_proposed", plan_id)
        return IntakeOutcome("plan_proposed", plan_id, tenant.team_id, goal)
