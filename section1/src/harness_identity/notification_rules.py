"""M12-W2.2 lane G7 (raw-13 #14) — run-terminal notification rules.

raw-13 #14: "zero notifications when runs finish/fail/block". This module is
the RULE layer that decides WHICH bus events notify and WHO gets the email;
the mechanics (durable outbox row -> NotificationWorker drain with backoff)
stay in notifier.py untouched.

Rules:
* Triggers on the three run-terminal transitions — finish (bus type
  ``engine_completed``, S4's completion signal), fail (``engine_failed``) and
  block (``blocked_awaiting_clarification``). ``review_failed`` keeps its own
  dedicated template from Wave 9; ``engine_completed`` maps to the existing
  ``run_finished`` template.
* Recipients are the team's ACTIVE human admins (users.email via
  team_members), each individually filtered by their per-user ``notify_email``
  preference (default true). A payload ``notifyEmail`` override or the
  ``HARNESS_NOTIFY_EMAIL`` env knob still wins when present (Wave-9 contract).
* One email per transition per run: envelopes are deduped by event id
  (payload eventId/id, else the bus's globally-unique seq) before any row is
  written.
* Never raises, never awaits on the publisher's critical path: every DB touch
  runs inside a fire-and-forget task whose failures are logged, never thrown
  into the run flow (outbox pattern = retry-safe by construction).

Content safety: subjects/bodies come from frozen templates fed only ids,
compacted status text and the app-relative deep link /projects/{pid}/runs/
{runId}. No secrets, tokens or credentials ever enter a template context.
"""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .notifier import (
    _dedup_key,
    build_notification_context,
    default_recipient,
    enqueue,
    event_to_template,
    get_notify_email_pref,
    resolve_admin_recipients,
)

logger = logging.getLogger(__name__)

# The three terminal transitions this lane owns. engine_completed IS S4's
# run-completion signal (AgentStateMachine.execution_completed → DONE-gate);
# the template keeps the graph-mandated run_finished name.
RUN_FINISHED_EVENT = "engine_completed"
RUN_FAILED_EVENT = "engine_failed"
RUN_BLOCKED_EVENT = "blocked_awaiting_clarification"
RUN_TERMINAL_EVENTS = frozenset({RUN_FINISHED_EVENT, RUN_FAILED_EVENT, RUN_BLOCKED_EVENT})


def _event_value(event_type) -> str:
    return str(getattr(event_type, "value", event_type))


def is_run_terminal_event(event_type) -> bool:
    """True when the event is one of the three run-terminal transitions."""
    return _event_value(event_type) in RUN_TERMINAL_EVENTS


async def recipients_for_envelope(
    session_factory: async_sessionmaker[AsyncSession], envelope
) -> list[str]:
    """Resolve who should hear about this transition.

    Order: explicit payload override → env knob → team admins (each filtered
    by their notify_email preference). Empty result means 'nothing to send'.
    """
    payload = getattr(envelope, "payload", None)
    payload = dict(payload) if isinstance(payload, dict) else {}
    override = default_recipient(payload)
    if override:
        return [override]
    team_id = payload.get("teamId") or None
    if not team_id:
        return []
    admins = await resolve_admin_recipients(session_factory, str(team_id))
    if not admins:
        return []
    opted_in: list[str] = []
    for email in admins:
        # resolve_admin_recipients returns users rows keyed by email; the
        # preference lookup needs user ids, so re-resolve them here.
        from sqlalchemy import select

        from .models import TeamMember, User

        try:
            async with session_factory() as session:
                uid_row = (
                    (
                        await session.execute(
                            select(User.id)
                            .join(TeamMember, TeamMember.user_id == User.id)
                            .where(
                                User.email == email,
                                TeamMember.team_id == str(team_id),
                            )
                            .limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
        except Exception:  # noqa: BLE001 — pref lookup must not break routing
            uid_row = None
        if uid_row is None:
            opted_in.append(email)  # unresolvable member: default-on wins
            continue
        if await get_notify_email_pref(session_factory, str(uid_row)):
            opted_in.append(email)
    return opted_in


async def enqueue_run_transition(
    session_factory: async_sessionmaker[AsyncSession],
    envelope,
    dedup_seen=None,
) -> list[str]:
    """Enqueue outbox rows for one committed run-terminal envelope.

    Returns the recipient addresses actually enqueued (empty when skipped:
    non-terminal event, no recipient, or already-notified duplicate).
    Raises nothing — malformed contexts and storage trouble degrade to [].
    """
    kind = _event_value(getattr(envelope, "type", ""))
    if kind not in RUN_TERMINAL_EVENTS:
        return []
    template = event_to_template(getattr(envelope, "type", ""))
    if template is None:  # defensive: map drift between the two modules
        return []

    key = _dedup_key(envelope) if dedup_seen is not None else None
    if key is not None and dedup_seen is not None and dedup_seen(key):
        return []  # one email per transition per run: replay suppressed

    project_id = str(getattr(envelope, "projectId", "") or "")
    run_id = getattr(envelope, "runId", None)
    payload = dict(getattr(envelope, "payload", None) or {})
    try:
        context = build_notification_context(
            getattr(envelope, "type", ""), project_id, payload, run_id
        )
        team_id = str(payload.get("teamId") or "")
        recipients = await recipients_for_envelope(session_factory, envelope)
    except Exception:  # noqa: BLE001 — rule evaluation must never raise up
        logger.exception("notification rule failed for run event %s", kind)
        return []

    enqueued: list[str] = []
    for to_email in recipients:
        try:
            await enqueue(
                session_factory, to_email, template, dict(context), team_id=team_id or None
            )
            enqueued.append(to_email)
        except ValueError as exc:
            logger.warning("notification skipped (%s): %s", template, exc)
        except Exception:  # noqa: BLE001 — one bad recipient must not kill the rest
            logger.exception("notification enqueue failed for %s", to_email)
    return enqueued


def make_run_event_rule(
    session_factory: async_sessionmaker[AsyncSession],
    settings=None,
    dedup=None,
):
    """Bus-listener factory: sync tap for EventBus.add_event_listener.

    Fires on every run-terminal transition, dedupes by event id, resolves
    team-admin recipients (per-user notify_email honored), and persists one
    outbox row per recipient inside a fire-and-forget task. NEVER raises and
    NEVER blocks the publisher — identical contract to notifier.make_event_notifier.
    """

    from .notifier import NotificationDedup

    guard = dedup if dedup is not None else NotificationDedup()

    def _on_envelope(envelope):
        try:
            kind = _event_value(getattr(envelope, "type", ""))
            if kind not in RUN_TERMINAL_EVENTS:
                return
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no running loop (sync tests/scripts): skip quietly
        except Exception:  # noqa: BLE001 — telemetry must never break the bus
            logger.exception("run-notification hook failed")
            return

        async def _enqueue_row():
            try:
                delivered = await enqueue_run_transition(
                    session_factory, envelope, dedup_seen=guard.seen
                )
                if delivered:
                    logger.info(
                        "run %s notification queued (%s) -> %s",
                        getattr(envelope, "runId", None) or "?",
                        kind,
                        ",".join(delivered),
                    )
            except Exception:  # noqa: BLE001 — the run flow must never see this
                logger.exception("run-notification task failed")

        loop.create_task(_enqueue_row())

    return _on_envelope
