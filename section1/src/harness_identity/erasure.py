"""Account erasure (master prompt parts 31 and 58). See docs/data-map.md.

A person erases their own account. The ``users`` row is pseudonymised, never deleted: ``audit_log``
and the collaboration evidence reference people by opaque id, and deleting the row would either be
refused (RESTRICT) or detach held evidence. Erasure therefore removes everything that lets someone
sign in or identifies the person to a reader (credentials, personal inbox state, addressed mail,
the readable name and email), and keeps ids. Authored text is part 50's deletion request.

Two refusals, both before any change: the only admin of a team or the only owner of an organization
(the group would be left with nobody in charge), and a person who took part in a run under
retention hold (the hold preserves that run's evidence; erasure can be asked again after release).
Everything else happens in one transaction together with the audit row.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from harness_contracts import Principal, error_body
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from .audit import write_audit
from .auth import get_principal

router = APIRouter(prefix="/api/v1/account")
Auth = Annotated[Principal, Depends(get_principal)]

# Deleted outright, matched on the person's id: credentials, per-person settings, inbox state.
PURGE_BY_USER = (
    ("auth_sessions", "user_id"), ("ws_tickets", "user_id"), ("oauth_states", "user_id"),
    ("github_connections", "user_id"), ("user_settings", "user_id"),
    ("notification_inbox", "user_id"), ("notification_preferences", "user_id"),
    ("notification_mutes", "user_id"), ("notification_dedupe", "user_id"),
    ("notification_unsub_tokens", "user_id"), ("participant_prefs", "user_id"),
    ("presence_records", "user_id"), ("policy_stepup", "user_id"),
    ("policy_assignments", "user_id"), ("org_members", "user_id"),
    ("workspace_members", "user_id"),
)
# Deleted outright, matched on the account's email: mail and invites addressed to it.
PURGE_BY_EMAIL = (
    ("login_otps", "email"), ("team_invites", "email"), ("org_invites", "email"),
    ("notification_outbox", "to_email"),
)
# Rows kept, readable name replaced by "Deleted user".
ANONYMISED = ("team_members", "session_members")
_AUTHORED = "authored content: removed by a part 50 deletion request, which honors holds"
_EVIDENCE = "collaboration or governance evidence: keeps only the opaque id of an erased account"
# Tables that mention a person and are deliberately left alone; the guard test fails for a table
# with a personal column that is in none of these lists.
KEPT = {
    "comments": _AUTHORED, "comment_mentions": _AUTHORED, "comment_reactions": _AUTHORED,
    "plan_comments": _AUTHORED, "review_comments": _AUTHORED, "task_specs": _AUTHORED,
    "task_spec_revisions": _AUTHORED,
    "collab_sessions": _EVIDENCE, "control_conflicts": _EVIDENCE, "control_instructions": _EVIDENCE,
    "control_leases": _EVIDENCE, "control_requests": _EVIDENCE, "deletion_requests": _EVIDENCE,
    "policy_emergency_grants": _EVIDENCE, "run_completion_history": _EVIDENCE,
    "run_participants": _EVIDENCE, "run_collab_events": _EVIDENCE, "workspaces": _EVIDENCE,
}
_GONE = "Deleted user"


class ErasureBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmEmail: str = Field(max_length=320)


def _err(status: int, code: str, msg: str) -> HTTPException:
    return HTTPException(status_code=status, detail=error_body(code, msg))


@router.post("/erasure")
async def erase_account(body: ErasureBody, request: Request, p: Auth) -> dict:
    u = {"u": p.userId}
    async with request.app.state.session_factory() as db:
        async def rows(sql: str) -> list:
            return list((await db.execute(text(sql), u)).all())

        email = (await db.execute(text("SELECT email FROM users WHERE id = :u"), u)).scalar_one()
        if body.confirmEmail.strip().lower() != email.lower():
            raise _err(422, "VALIDATION_ERROR", "confirmEmail must be the email of this account")
        lone_teams = await rows(
            "SELECT m.team_id FROM team_members m WHERE m.user_id = :u AND lower(m.role) = 'admin'"
            " AND NOT EXISTS (SELECT 1 FROM team_members o WHERE o.team_id = m.team_id"
            " AND lower(o.role) = 'admin' AND o.user_id IS NOT NULL AND o.user_id <> :u)")
        lone_orgs = await rows(
            "SELECT m.org_id FROM org_members m JOIN organizations o ON o.id = m.org_id"
            " WHERE m.user_id = :u AND m.role = 'owner' AND m.removed_at IS NULL"
            " AND o.status = 'active' AND NOT EXISTS (SELECT 1 FROM org_members x"
            " WHERE x.org_id = m.org_id AND x.role = 'owner' AND x.removed_at IS NULL"
            " AND x.user_id <> :u)")
        if lone_teams or lone_orgs:
            raise _err(409, "SOLE_ADMIN", "you are the only admin or owner of "
                       f"{len(lone_teams)} team(s) and {len(lone_orgs)} organization(s): hand "
                       "them over or delete them first")
        seat = (await db.execute(
            text("SELECT id FROM team_members WHERE user_id = :u AND team_id = :t"),
            {**u, "t": p.teamId})).scalar_one_or_none()
        held = (await db.execute(text(
            "SELECT COUNT(*) FROM run_retention_holds h WHERE EXISTS (SELECT 1 FROM"
            " run_collab_events e WHERE e.run_id = h.run_id AND e.actor_user_id = :u) OR EXISTS"
            " (SELECT 1 FROM run_participants x WHERE x.run_id = h.run_id AND x.user_id = :u)"
            " OR EXISTS (SELECT 1 FROM audit_log a JOIN team_members m ON m.id = a.actor_member_id"
            " WHERE a.target_id = h.run_id AND m.user_id = :u)"), u)).scalar()
        if held:
            await write_audit(db, "account.erasure_deferred", actor_member_id=seat,
                              target_type="user", target_id=p.userId, detail={"heldRuns": held})
            raise _err(409, "ERASURE_ON_HOLD", f"{held} run(s) you took part in are under a "
                       "retention hold; ask again after the hold is released")

        for table, col in PURGE_BY_USER:
            await db.execute(text(f"DELETE FROM {table} WHERE {col} = :u"), u)
        for table, col in PURGE_BY_EMAIL:
            await db.execute(text(f"DELETE FROM {table} WHERE {col} = :e"), {"e": email})
        await db.execute(text("UPDATE session_members SET display_name = :g WHERE user_id = :u"),
                         {**u, "g": _GONE})
        seats = (await db.execute(
            text("UPDATE team_members SET user_id = NULL, display_name = :g, role = 'viewer'"
                 " WHERE user_id = :u"), {**u, "g": _GONE})).rowcount
        await db.execute(
            text("UPDATE users SET email = :e, name = '', is_active = false WHERE id = :u"),
            {**u, "e": f"erased-{p.userId}@erased.invalid"})
        await write_audit(db, "account.erased", actor_member_id=seat, target_type="user",
                          target_id=p.userId, detail={"teams": seats})  # commits it all at once
    return {"erased": True}
