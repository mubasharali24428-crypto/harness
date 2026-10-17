"""A2-S3: handoff manifests (F025), follow-up inbox (F026), bounded retry
(F028) and recovery strategy selection (F029).

Pure, dependency-free helpers. Law shared by all four: fail closed, never
change the configured model (no forced downgrade), never carry a secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import tempfile
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ENVS = frozenset({"local", "cloud"})
_SECRET_KEY = re.compile(r"(key|token|secret|password|credential)", re.I)
_SECRET_VALUE = re.compile(r"(sk-[A-Za-z0-9]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY)")


class HandoffError(ValueError):
    """Manifest rejected; ``code`` is stable for callers."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _has_secret(obj: Any) -> bool:
    if isinstance(obj, dict):
        return any(_SECRET_KEY.search(str(k)) or _has_secret(v) for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return any(_has_secret(v) for v in obj)
    return isinstance(obj, str) and bool(_SECRET_VALUE.search(obj))


class HandoffSigner:
    """HMAC-signed, expiring, single-use, team/run/target-bound manifests."""

    def __init__(self, key: bytes, *, clock: Callable[[], float] = time.time) -> None:
        if len(key) < 16:
            raise ValueError("handoff key must be at least 16 bytes")
        self._key, self._clock = key, clock
        self._used: set[str] = set()  # ponytail: per-process; durable replay set needs a table

    def _mac(self, body: bytes) -> str:
        return _b64(hmac.new(self._key, body, hashlib.sha256).digest())

    def issue(
        self,
        *,
        team_id: str,
        run_id: str,
        source_env: str,
        target_env: str,
        checkpoint: dict[str, Any],
        ttl_seconds: int = 900,
    ) -> str:
        if source_env not in _ENVS or target_env not in _ENVS or source_env == target_env:
            raise HandoffError("handoff.bad_environment")
        if _has_secret(checkpoint):
            raise HandoffError("handoff.secret_in_manifest")
        now = self._clock()
        claims = {
            "team": team_id,
            "run": run_id,
            "src": source_env,
            "dst": target_env,
            "ckpt": checkpoint,
            "iat": now,
            "exp": now + ttl_seconds,
            "nonce": _b64(os.urandom(12)),
        }
        body = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
        return f"{_b64(body)}.{self._mac(body)}"

    def accept(self, token: str, *, team_id: str, run_id: str, target_env: str) -> dict[str, Any]:
        try:
            raw, mac = token.split(".")
            body = _unb64(raw)
            claims = json.loads(body)
        except Exception:  # noqa: BLE001 — any malformed token is the same refusal
            raise HandoffError("handoff.malformed") from None
        if not hmac.compare_digest(mac, self._mac(body)):
            raise HandoffError("handoff.bad_signature")
        if claims["exp"] <= self._clock():
            raise HandoffError("handoff.expired")
        if claims["team"] != team_id or claims["run"] != run_id:
            raise HandoffError("handoff.wrong_scope")
        if claims["dst"] != target_env:
            raise HandoffError("handoff.wrong_target")
        if claims["nonce"] in self._used:
            raise HandoffError("handoff.replayed")
        self._used.add(claims["nonce"])
        return claims["ckpt"]


class FollowupError(ValueError):
    pass


class FollowupInbox:
    """Bounded per-run instruction channel, delivered once at a safe boundary."""

    MAX_PENDING, MAX_LEN = 20, 4000

    def __init__(self) -> None:
        self._q: dict[str, list[dict[str, str]]] = defaultdict(list)
        self._closed: set[str] = set()

    def post(self, run_id: str, author: str, text: str) -> int:
        if run_id in self._closed:
            raise FollowupError("followup.run_not_active")
        text = (text or "").strip()
        if not text or len(text) > self.MAX_LEN:
            raise FollowupError("followup.bad_length")
        if _SECRET_VALUE.search(text):
            raise FollowupError("followup.secret_rejected")
        if len(self._q[run_id]) >= self.MAX_PENDING:
            raise FollowupError("followup.inbox_full")
        self._q[run_id].append({"author": author, "text": text})
        return len(self._q[run_id])

    def drain(self, run_id: str) -> list[dict[str, str]]:
        return self._q.pop(run_id, [])

    def close(self, run_id: str) -> None:
        self._closed.add(run_id)
        self._q.pop(run_id, None)


def render_followups(items: list[dict[str, str]]) -> str:
    """Frame follow-ups as user-authored data, never as system instructions."""
    return "\n".join(f"[follow-up from {i['author']}]: {i['text']}" for i in items)


# --------------------------------------------------------------- retry (F028)

NEVER_RETRY = frozenset({"mode_guard", "confinement", "circuit_open", "cancelled", "denied"})
_TRANSIENT = re.compile(r"(timeout|timed out|429|5\d\d|rate.?limit|temporar|connection)", re.I)


def classify(result: dict[str, Any]) -> str:
    """'ok' | 'transient' | 'permanent' — decided from the result, not guessed."""
    if result.get("status") == "completed":
        return "ok"
    if result.get("engine") in NEVER_RETRY or result.get("errorCode"):
        return "permanent"
    if result.get("status") == "error" and _TRANSIENT.search(str(result.get("finalResponse", ""))):
        return "transient"
    return "permanent"


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0

    def delay(self, attempt: int) -> float:
        return min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))


@dataclass
class AttemptLedger:
    """Idempotency: one stored outcome per key, so a replay never re-executes
    (and never double-bills) a call that already finished."""

    done: dict[str, dict[str, Any]] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=lambda: defaultdict(int))


async def run_with_retry(
    call: Callable[[], Awaitable[dict[str, Any]]],
    *,
    key: str,
    policy: RetryPolicy,
    ledger: AttemptLedger,
    sleep: Callable[[float], Awaitable[None]],
) -> dict[str, Any]:
    if key in ledger.done:
        return ledger.done[key]
    result: dict[str, Any] = {}
    for attempt in range(ledger.attempts[key] + 1, policy.max_attempts + 1):
        ledger.attempts[key] = attempt
        result = await call()
        kind = classify(result)
        if kind != "transient":
            break
        if attempt < policy.max_attempts:
            await sleep(policy.delay(attempt))
    result = {**result, "attempts": ledger.attempts[key]}
    ledger.done[key] = result
    return result


# ------------------------------------------------------------ recovery (F029)

STRATEGIES = ("resume_checkpoint", "retry_same", "escalate_human", "abort")


def choose_strategy(*, kind: str, attempts: int, max_attempts: int, has_checkpoint: bool) -> str:
    """Bounded, deterministic. Never 'switch model' — the model is the user's call."""
    if kind == "denied":
        return "abort"
    if kind == "permanent":
        return "escalate_human"
    if attempts >= max_attempts:
        return "escalate_human"
    return "resume_checkpoint" if has_checkpoint else "retry_same"


class CheckpointStore:
    """Atomic JSON checkpoints per run (completed step ids survive restarts)."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", run_id) or run_id in {".", ".."}:
            raise ValueError("unsafe run id")
        return self.root / f"{run_id}.json"

    def save(self, run_id: str, state: dict[str, Any]) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.root)
        with os.fdopen(fd, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, self._path(run_id))

    def load(self, run_id: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._path(run_id).read_text())
        except FileNotFoundError:
            return None
