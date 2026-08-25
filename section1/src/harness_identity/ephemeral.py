"""DB-backed ephemeral auth state (wave 7, cross-process safe).

All timestamps are stored and compared as UTC ISO-8601 strings. SQLite keeps
DATETIME columns as TEXT: binding datetime objects goes through the deprecated
default adapter (space separator) while hand-seeded rows typically use
.isoformat() (T separator), and the two formats do not compare correctly as
strings — an expired row can look valid. Normalizing to ISO text at every
boundary makes comparisons exact regardless of driver.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from harness_contracts import Principal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

WS_TICKET_TTL_SECONDS = 30

# M15-W5-S2 (L3): tickets are bound to the issuing client. The fingerprint is
# a short UA+IP hash stored on the ticket row and recomputed at upgrade time.
# It is a correlation/anti-replay guard, NOT a cryptographic secret: both
# inputs travel on the wire, so its job is to make a stolen ticket worthless
# from another host, not to authenticate the client.
#
# SPOOFING CAVEAT (M17-B4 / MAC2-F5, documented not fixed): an attacker who
# knows BOTH inputs — User-Agent and client IP — can compute this exact value
# and present a "matching" fingerprint. That is realistic when victim and
# attacker share a NAT/egress proxy (same IP) or the ticket+UA leak together
# through access logs. Since M18-C5 (MAC2-F5) the upgrade path REQUIRES
# presenting X-WS-Fingerprint (or the browser `fingerprint` query param) when
# a mint recorded one — the old absent-header grace path is gone — so the
# binding can no longer be evaporated by omission; it remains a correlation
# guard, not an authentication control. What the check DOES stop: blind
# replay of a captured ticket from a different host, and replay with only the
# ticket (no UA knowledge). Raising it to a real control needs a
# secret-bearing credential (e.g. DPoP-style proof or a minted HMAC over the
# upgrade handshake) — out of scope for B4/C5.
FINGERPRINT_HEX_LEN = 16


def compute_client_fingerprint(user_agent: str, client_ip: str) -> str:
    """sha256(UA + client_ip)[:16] — the exact string mint binds and the
    upgrade path re-derives (and the one X-WS-Fingerprint must match when the
    client sends it). Both inputs arrive as headers/peer address, never secrets."""
    return hashlib.sha256(f"{user_agent or ''}{client_ip or ''}".encode()).hexdigest()[
        :FINGERPRINT_HEX_LEN
    ]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _iso(dt: datetime) -> str:
    return dt.isoformat() if dt.tzinfo else dt.replace(tzinfo=UTC).isoformat()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def mint_ws_ticket(
    factory: async_sessionmaker,
    principal: Principal,
    project_id: str,
    ttl: int = WS_TICKET_TTL_SECONDS,
    user_agent: str = "",
    client_ip: str = "",
) -> tuple[str, int]:
    """Mint a single-use ticket, bound to the issuing client's fingerprint
    (M15-W5-S2). user_agent/client_ip default to "" so direct callers (tests,
    internal jobs) keep working; the HTTP route always passes real values."""
    ticket = secrets.token_urlsafe(32)
    now_iso = _now_iso()
    fingerprint = compute_client_fingerprint(user_agent, client_ip)
    async with factory() as db:
        await db.execute(
            text("DELETE FROM ws_tickets WHERE expires_at < :cutoff"), {"cutoff": now_iso}
        )
        await db.execute(
            text(
                "INSERT INTO ws_tickets (token_hash, user_id, team_id, role, project_id,"
                " expires_at, created_at, fingerprint)"
                " VALUES (:h, :u, :t, :r, :p, :e, :c, :f)"
            ),
            {
                "h": _hash(ticket),
                "u": principal.userId,
                "t": principal.teamId,
                "r": str(principal.role),
                "p": project_id,
                "e": _iso(datetime.now(UTC) + timedelta(seconds=ttl)),
                "c": now_iso,
                "f": fingerprint,
            },
        )
        await db.commit()
    return ticket, ttl


class RedeemedTicket:
    """Redemption result: the principal plus the client-binding data needed to
    validate the upgrade (M15-W5-S2). fingerprint is the mint-time value —
    NULL for rows minted before migration 0025 (grace path)."""

    __slots__ = ("principal", "fingerprint")

    def __init__(self, principal: Principal, fingerprint: str | None):
        self.principal = principal
        self.fingerprint = fingerprint


async def redeem_ws_ticket(factory: async_sessionmaker, token: str, project_id: str):
    if not token:
        return None
    async with factory() as db:
        row = (
            await db.execute(
                text(
                    "DELETE FROM ws_tickets"
                    " WHERE token_hash = :h AND project_id = :p AND expires_at > :cutoff"
                    " RETURNING user_id, team_id, role, fingerprint"
                ),
                {"h": _hash(token), "p": project_id, "cutoff": _now_iso()},
            )
        ).first()
        await db.commit()
    if row is None:
        return None
    from harness_contracts.roles import Role

    try:
        role = Role(row.role)
    except ValueError:
        role = Role(row.role.lower())
    return RedeemedTicket(
        principal=Principal(userId=row.user_id, teamId=row.team_id, role=role),
        # Pre-0025 rows (and any DB where 0025 has not run) carry NULL here.
        fingerprint=row.fingerprint,
    )
