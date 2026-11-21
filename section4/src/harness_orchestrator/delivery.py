"""Real-time delivery helpers shared by section-4 callers (master prompt part 46).

Section 1 owns the sockets (``harness_identity.eventbus``); section 4 owns the HTTP side of the
same stream and the mutation routes. Sections may not import each other, so the few rules both
sides must agree on are written down in docs/realtime.md and repeated here as constants:

* the HTTP polling fallback returns the same event shape as the socket (``history_page``);
* mutation routes that cost something carry per-route limits (``MUTATION_ROUTE_LIMITS``);
* a stale client is refused with 409 ``STALE_CLIENT_STATE`` (``require_current_version``);
* ``Reducer`` is the executable form of the client rules (cursor, dedupe, out-of-order buffer,
  gap detection). The browser client is a port of it; the tests here are its specification.

Nothing in this module opens a route. Wiring (lead): ``api.collab_history`` returns
``await delivery.history_page(...)``; ``deploy/runners/services.py`` installs
``MUTATION_ROUTE_LIMITS`` via ``ratelimit_hooks``.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import bindparam, text

from harness_orchestrator import collab

#: Newest collaboration frame schema written today (mirror of eventbus.COLLAB_SCHEMA_VERSION).
SCHEMA_VERSION = 1
NOISE_KINDS = ("guidance.delivered",)  # engine cursor markers, never part of the stream
EPHEMERAL_PREFIXES = ("presence.", "typing.", "cursor.")  # never durable (part 46.007)
PAGE_MAX = 500
PAYLOAD_MAX_BYTES = 8192  # same bound as the socket (BUS_PAYLOAD_MAX_BYTES default)


# ------------------------------------------------------------- polling fallback


def _visible_sql() -> str:
    parts = ["kind NOT IN :noise"]
    parts += [f"kind NOT LIKE :eph{i}" for i in range(len(EPHEMERAL_PREFIXES))]
    return " AND ".join(parts)


def _visible_params() -> dict[str, Any]:
    params: dict[str, Any] = {"noise": list(NOISE_KINDS)}
    for i, prefix in enumerate(EPHEMERAL_PREFIXES):
        params[f"eph{i}"] = prefix + "%"
    return params


def _stmt(sql: str):
    return text(sql).bindparams(bindparam("noise", expanding=True))


def _payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _iso(ts: Any) -> str:
    return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)


async def history_page(
    factory: Any,
    team_id: str,
    run_id: str,
    after: int = 0,
    limit: int = 200,
    client_schema: int = 1,
) -> dict[str, Any]:
    """One page of a run's durable collaboration events: the polling twin of the socket.

    Events are ``seq > after`` in ascending order, team-scoped in SQL, with the same fields as a
    socket frame (``prevSeq`` and ``schemaVersion`` included). Paging by ``nextCursor`` never
    skips or repeats an event: ``seq`` order is commit order (``collab._event`` serialises a run's
    inserts). Payloads are NOT shortened here: this is where a client refetches a body the socket
    replaced with ``{"omitted": true}``, except rows written with a schema newer than
    ``client_schema``, whose payload is withheld exactly as on the socket.
    """
    after = max(0, int(after))
    n = min(max(int(limit), 1), PAGE_MAX)
    base = {"r": run_id, "t": team_id, **_visible_params()}
    async with factory() as db:
        rows = (
            await db.execute(
                _stmt(
                    "SELECT seq, actor_user_id, kind, payload, ts, schema_version"
                    " FROM run_collab_events WHERE run_id = :r AND team_id = :t AND seq > :a"
                    f" AND {_visible_sql()} ORDER BY seq LIMIT :l"
                ),
                {**base, "a": after, "l": n + 1},
            )
        ).all()
        has_more = len(rows) > n
        rows = rows[:n]
        prev = None
        if rows:
            prev = (
                await db.execute(
                    _stmt(
                        "SELECT MAX(seq) FROM run_collab_events WHERE run_id = :r"
                        f" AND team_id = :t AND seq < :s AND {_visible_sql()}"
                    ),
                    {**base, "s": rows[0].seq},
                )
            ).scalar()
        head = (
            await db.execute(
                _stmt(
                    "SELECT MAX(seq) FROM run_collab_events WHERE run_id = :r"
                    f" AND team_id = :t AND {_visible_sql()}"
                ),
                base,
            )
        ).scalar()
        # The engine's delivery cursor: guidance with seq <= this has been handed to the agent.
        marker = (
            await db.execute(
                text(
                    "SELECT payload FROM run_collab_events WHERE run_id = :r AND team_id = :t"
                    " AND kind = 'guidance.delivered' ORDER BY seq DESC LIMIT 1"
                ),
                {"r": run_id, "t": team_id},
            )
        ).scalar()
    events = []
    for r in rows:
        version = int(r.schema_version or 1)
        body = (
            {"omitted": True, "reason": "schema_unsupported"}
            if version > client_schema
            else _payload(r.payload)
        )
        events.append(
            {
                "seq": int(r.seq),
                "prevSeq": int(prev) if prev is not None else None,
                "actor": r.actor_user_id,
                "kind": r.kind,
                "payload": body,
                "ts": _iso(r.ts),
                "schemaVersion": version,
                "runId": run_id,
            }
        )
        prev = r.seq
    return {
        "events": events,
        "nextCursor": events[-1]["seq"] if events else after,
        "hasMore": has_more,
        "headSeq": int(head or 0),
        "deliveredUpTo": int(_payload(marker).get("upTo", 0)) if marker is not None else 0,
        "schemaVersion": SCHEMA_VERSION,
    }


# ------------------------------------------------------------- stale client state

#: Codes a client treats as "my view is behind, re-read and retry". ``STALE_CLIENT_STATE`` is the
#: generic one; the others are the older, more specific refusals that already exist.
STALE_CODES = frozenset(
    {"STALE_CLIENT_STATE", "STALE_INTENT_VERSION", "INTENT_CONFLICT", "DIGEST_MISMATCH"}
)


class StaleClientState(collab.CollabError):
    """409 STALE_CLIENT_STATE; ``extra`` is merged into the HTTP error body by routes that
    support it (control routes do) so the client can re-read without another round trip."""

    def __init__(self, what: str, current_version: int, **extra: Any) -> None:
        super().__init__(409, "STALE_CLIENT_STATE", f"{what} changed since you read it")
        self.extra = {"currentVersion": int(current_version), **extra}


def require_current_version(expected: int | None, current: int, what: str, **extra: Any) -> None:
    """Refuse a mutation whose ``expectedVersion`` is behind the server's version of the object.

    ``expected is None`` means the caller did not carry a version: nothing to compare, nothing is
    refused (a missing version is never treated as stale; the route decides whether one is
    mandatory). A version AHEAD of the server is a client bug, not staleness: 422 BAD_VERSION.
    """
    if expected is None:
        return
    if expected > current:
        raise collab.CollabError(422, "BAD_VERSION", "expectedVersion is ahead of the server")
    if expected < current:
        raise StaleClientState(what, current, **extra)


# ------------------------------------------------------------- mutation rate limits

#: Per-IP, per-minute budgets for the collaboration mutation routes (route TEMPLATES, the key the
#: shared limiter uses). Generous on purpose: this is an abuse guard behind NAT, not a UX limit.
#: ``app_orchestrator`` (deploy/runners/services.py) merges them:
#: ``routes=(*DEFAULT_PROTECTED_ROUTES, *MUTATION_ROUTE_LIMITS)`` and
#: ``route_limits={**ORCHESTRATOR_ROUTE_LIMITS, **MUTATION_ROUTE_LIMITS}``.
#: The limiter keys on the path, not the method, so a template that is also polled with GET must
#: not be listed: ``POST /runs/{runId}/approvals`` shares its path with the 3-second list poll and
#: is therefore absent (the test below fails if one is added).
_P = "/api/v1/orchestrator/runs/{runId}"
MUTATION_ROUTE_LIMITS: dict[str, int] = {
    f"{_P}/redirect": 120,
    f"{_P}/handoff": 60,
    f"{_P}/handoff/{{handoffId}}": 60,
    f"{_P}/approvals/{{approvalId}}": 60,
    f"{_P}/approvals/{{approvalId}}/decision": 60,
    f"{_P}/approvals/{{approvalId}}/claim": 60,
    f"{_P}/approvals/{{approvalId}}/complete": 60,
    f"{_P}/approvals/{{approvalId}}/apply": 60,
    f"{_P}/leader": 60,
}


# ------------------------------------------------------------- client rules, executable


class Reducer:
    """The client rules of docs/realtime.md as code: one run's cursor, dedupe, reordering.

    ``push(frame, now)`` returns the frames that are now deliverable, in order. A frame is
    deliverable when it is newer than the cursor and nothing visible lies between the cursor and
    it, i.e. its ``prevSeq`` is absent (HTTP page without chain info), null, or at most the cursor.
    Otherwise it waits in a buffer of at most ``window`` frames for the missing predecessor.
    ``gap(now)`` is true when the buffer is full or its oldest frame has waited ``max_wait``
    seconds: the caller must then fetch ``history_page(after=cursor)`` and push those frames in.
    """

    def __init__(self, cursor: int = 0, window: int = 256, max_wait: float = 3.0) -> None:
        self.cursor = cursor
        self.window = window
        self.max_wait = max_wait
        self.duplicates = 0
        self._held: dict[int, tuple[dict[str, Any], float]] = {}

    def push(self, frame: dict[str, Any], now: float = 0.0) -> list[dict[str, Any]]:
        seq = frame["seq"]
        if seq <= self.cursor or seq in self._held:
            self.duplicates += 1
            return []
        out: list[dict[str, Any]] = []
        if self._ready(frame):
            self._deliver(frame, out)
        else:
            self._held[seq] = (frame, now)
        progressed = True
        while progressed:  # a delivered frame may unlock buffered successors
            progressed = False
            for held_seq in sorted(self._held):
                held = self._held[held_seq][0]
                if held_seq <= self.cursor:
                    del self._held[held_seq]  # superseded while waiting
                elif self._ready(held):
                    del self._held[held_seq]
                    self._deliver(held, out)
                    progressed = True
                    break
        return out

    def gap(self, now: float = 0.0) -> bool:
        if not self._held:
            return False
        oldest = min(at for _frame, at in self._held.values())
        return len(self._held) >= self.window or now - oldest >= self.max_wait

    def pending(self) -> int:
        return len(self._held)

    def _ready(self, frame: dict[str, Any]) -> bool:
        prev = frame.get("prevSeq")
        return prev is None or prev <= self.cursor

    def _deliver(self, frame: dict[str, Any], out: list[dict[str, Any]]) -> None:
        self.cursor = frame["seq"]
        out.append(frame)
