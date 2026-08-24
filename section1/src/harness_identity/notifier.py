"""E-smtp — pluggable OTP code delivery.

The notifier hook signature is `notify(email: str, code: str) -> None`.
Selection happens in make_app(): SMTP when HARNESS_SMTP_HOST is configured,
dev log-printer otherwise (never silently — dev delivery must stay visible).

SMTP uses stdlib smtplib synchronously inside a worker thread (OTP dispatch is
not on the request critical path; a slow relay must not stall the API call).
No third-party dependencies.
"""

import asyncio
import hashlib
import html as _html
import json
import logging
import os
import smtplib
import sys
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formataddr
from typing import Protocol

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, JSONVariant, uid

logger = logging.getLogger(__name__)

FROM_ADDR_DEFAULT = "harness@noreply.local"

Notifier = Callable[[str, str], None]


def dev_log_notifier(email: str, code: str) -> None:
    """Dev delivery ONLY: prints so `docker logs` shows it regardless of level."""
    import sys

    print(f"HARNESS-OTP login code for {email}: {code}", file=sys.stderr, flush=True)


def smtp_notifier(
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    from_addr: str,
    use_tls: bool,
    subject: str = "Your Harness login code",
) -> Notifier:
    """Builds the SMTP notify callable. Credentials come from settings/env only —
    never logged, never returned in errors."""

    def _send(email: str, code: str) -> None:
        msg = EmailMessage()
        msg["From"] = formataddr(("Harness", from_addr))
        msg["To"] = email
        msg["Subject"] = subject
        body = (
            f"Your Harness login code is {code}.\n\n"
            f"It expires shortly and can be used once. "
            f"If you did not request it, ignore this email."
        )
        msg.set_content(body)

        def _smtp() -> None:
            # Runs in a thread; sync smtplib is fine here.
            with smtplib.SMTP(host, port, timeout=10) as server:
                if use_tls:
                    try:
                        server.starttls()
                    except smtplib.SMTPException:
                        logger.warning("STARTTLS refused by %s; continuing plaintext", host)
                if username and password:
                    server.login(username, password)
                server.send_message(msg)

        import asyncio

        try:
            loop = asyncio.get_running_loop()
            # Fire-and-forget off the event loop; failures logged, never raised
            # into the request (enumeration-safety: delivery problems must not
            # leak whether an address exists).
            task = loop.run_in_executor(None, _smtp)
            task.add_done_callback(_log_smtp_failure)
        except RuntimeError:
            # No running loop (tests/scripts): send inline.
            _smtp()

    return _send


def _log_smtp_failure(task) -> None:
    exc = task.exception() if not task.cancelled() else None
    if exc is not None:
        logger.error("OTP email delivery failed: %s: %s", type(exc).__name__, exc)


def make_notifier(settings):
    """Pick the notifier from settings. SMTP requires HARNESS_SMTP_HOST;
    everything else falls back to the visible dev printer."""
    host = getattr(settings, "smtp_host", "") or ""
    if not host:
        return dev_log_notifier
    return smtp_notifier(
        host=host,
        port=settings.smtp_port,
        username=getattr(settings, "smtp_username", "") or None,
        password=getattr(settings, "smtp_password", "") or None,
        from_addr=getattr(settings, "smtp_from", "") or FROM_ADDR_DEFAULT,
        use_tls=bool(getattr(settings, "smtp_starttls", True)),
    )


# ---------------------------------------------------------------------------
# Wave 9 node D — notification templates + durable notification_outbox.
#
# Canonical schema lives in migrations/0009_notification_outbox.sql. The
# declarative model below is registered on the shared Base purely so the
# section1 sqlite test harness (conftest Base.metadata.create_all) can create
# the table without touching frozen files; Postgres deployments get the real
# DDL (incl. the status CHECK constraint) from the numbered migration.
#
# Flow: bus events (engine_completed/review_failed/pr_opened published by S4)
# → enqueue (queued row) → drain_outbox / NotificationWorker delivers with
# backoff (max 3 attempts) → sent | failed. No SMTP configured => dev mode:
# content is logged visibly and rows are marked sent, so CI stays offline.
# ---------------------------------------------------------------------------

OUTBOX_MAX_ATTEMPTS = 3


class _Delivery(Protocol):
    """Structural type so drain/send can take any duck-typed deliverer."""

    @property
    def dev_mode(self) -> bool: ...

    async def deliver(
        self, to_email: str, subject: str, html_body: str, plain: str
    ) -> tuple[bool, str | None]: ...


@dataclass(frozen=True)
class NotificationTemplate:
    """Subject + HTML + plain-text bodies rendered via str.format(context)."""

    name: str
    subject: str
    heading: str
    lines: tuple[str, ...]  # plain-text paragraphs

    def render(self, context: dict) -> tuple[str, str, str]:
        subject = self.subject.format(**context)
        plain = "\n\n".join(line.format(**context) for line in self.lines)
        body_html = "".join(
            f"<p>{_html.escape(line.format(**context))}</p>" for line in self.lines
        )
        html_body = (
            "<!DOCTYPE html><html><body style=\"font-family:sans-serif\">"
            f"<h2>{_html.escape(self.heading.format(**context))}</h2>"
            f"{body_html}"
            '<p style="color:#888;font-size:12px">Sent by Harness — do not reply.</p>'
            "</body></html>"
        )
        return subject, html_body, plain


TEMPLATES: dict[str, NotificationTemplate] = {
    # S4 has no literal run_finished event type; its run-completion signal is
    # engine_completed (AgentStateMachine.finish → ENGINE_COMPLETED). The
    # template keeps the graph-mandated run_finished name.
    "run_finished": NotificationTemplate(
        name="run_finished",
        subject="Harness run {run_id} finished",
        heading="Run finished ✅",
        lines=(
            "Run {run_id} on project {project_id} completed successfully.",
            "{summary}",
        ),
    ),
    "review_failed": NotificationTemplate(
        name="review_failed",
        subject="Harness review failed for run {run_id}",
        heading="Review failed ⚠️",
        lines=(
            "The reviewer rejected run {run_id} on project {project_id}.",
            "Findings: {findings}",
        ),
    ),
    "pr_opened": NotificationTemplate(
        name="pr_opened",
        subject="Harness opened PR #{pr_number} for run {run_id}",
        heading="Pull request opened 🚀",
        lines=("PR #{pr_number} is ready for review.", "URL: {pr_url}"),
    ),
    # M12-G7 (raw-13 #14): honest failure/block notices for the remaining run
    # terminal transitions. run_finished deliberately keeps its Wave-9 shape
    # (its context is pinned by tests); G7 appends the deep link to its
    # summary text instead of adding a required placeholder here.
    "run_failed": NotificationTemplate(
        name="run_failed",
        subject="Harness run {run_id} FAILED",
        heading="Run failed ❌",
        lines=(
            "Run {run_id} on project {project_id} failed.",
            "Reason: {reason}",
            "Open: {run_link}",
        ),
    ),
    "run_blocked": NotificationTemplate(
        name="run_blocked",
        subject="Harness run {run_id} blocked — action needed",
        heading="Run blocked ⏸️",
        lines=(
            "Run {run_id} on project {project_id} is blocked and needs human input.",
            "{detail}",
            "Open: {run_link}",
        ),
    ),
}

# Bus event type (harness_contracts.events.EventType values) -> template name.
# M12-G7 extends the map to every run-terminal transition (engine failure,
# clarification block); review_failed keeps its dedicated findings template.
EVENT_TEMPLATE_MAP: dict[str, str] = {
    "engine_completed": "run_finished",
    "review_failed": "review_failed",
    "pr_opened": "pr_opened",
    "engine_failed": "run_failed",
    "blocked_awaiting_clarification": "run_blocked",
}


def render_template(template: str, context: dict) -> tuple[str, str, str]:
    """Render (subject, html, plain). Strict: unknown template names or
    contexts missing required keys raise ValueError (never a silent send)."""
    tpl = TEMPLATES.get(template)
    if tpl is None:
        raise ValueError(f"unknown notification template: {template!r}")
    try:
        return tpl.render(context)
    except KeyError as exc:
        raise ValueError(f"missing context key for template {template!r}: {exc}") from exc
    except (IndexError, AttributeError, TypeError) as exc:
        raise ValueError(f"context unsuitable for template {template!r}: {exc}") from exc


class NotificationOutbox(Base):
    """Mirror of migrations/0009_notification_outbox.sql (see block header)."""

    __tablename__ = "notification_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    # M15-D1 sweep (W5-C2): queued mail outlives the team (mirrors 0009).
    team_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("teams.id", ondelete="SET NULL"), index=True
    )
    to_email: Mapped[str] = mapped_column(String(255), nullable=False)
    template: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONVariant, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


async def enqueue(
    session_factory: async_sessionmaker[AsyncSession],
    to_email: str,
    template: str,
    context: dict | None = None,
    team_id: str | None = None,
) -> NotificationOutbox:
    """Persist a queued outbox row. Renders eagerly so malformed contexts are
    rejected here (ValueError) instead of poisoning the queue."""
    render_template(template, dict(context or {}))  # validation; bodies discarded
    async with session_factory() as session:
        row = NotificationOutbox(
            team_id=team_id,
            to_email=to_email,
            template=template,
            payload=dict(context or {}),
            status="queued",
        )
        session.add(row)
        # No refresh(): callers only need identity/status fields, all set
        # client-side; a post-commit refresh would force an extra round-trip.
        await session.commit()
        return row


class NotifierDelivery:
    """Delivers one rendered email: real SMTP when configured, visible dev log
    otherwise. Never raises — failures come back as (ok, error) tuples."""

    def __init__(self, settings) -> None:
        self.host = (getattr(settings, "smtp_host", "") or "").strip()
        self.port = int(getattr(settings, "smtp_port", 587) or 587)
        self.username = getattr(settings, "smtp_username", "") or None
        self.password = getattr(settings, "smtp_password", "") or None
        self.from_addr = getattr(settings, "smtp_from", "") or FROM_ADDR_DEFAULT
        self.use_tls = bool(getattr(settings, "smtp_starttls", True))

    @property
    def dev_mode(self) -> bool:
        return not self.host

    def _send_sync(self, to_email: str, subject: str, html_body: str, plain: str) -> None:
        msg = EmailMessage()
        msg["From"] = formataddr(("Harness", self.from_addr))
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(plain)
        msg.add_alternative(html_body, subtype="html")
        with smtplib.SMTP(self.host, self.port, timeout=10) as server:
            if self.use_tls:
                try:
                    server.starttls()
                except smtplib.SMTPException:
                    logger.warning("STARTTLS refused by %s; continuing plaintext", self.host)
            if self.username and self.password:
                server.login(self.username, self.password)
            server.send_message(msg)

    async def deliver(
        self, to_email: str, subject: str, html_body: str, plain: str
    ) -> tuple[bool, str | None]:
        if self.dev_mode:
            print(
                f"HARNESS-NOTIFY to={to_email} subject={subject!r}\n{plain}",
                file=sys.stderr,
                flush=True,
            )
            return True, None
        try:
            await asyncio.to_thread(self._send_sync, to_email, subject, html_body, plain)
        except Exception as exc:  # noqa: BLE001 — delivery failure is data, not a crash
            return False, f"{type(exc).__name__}: {exc}"
        return True, None


async def send_template(
    to: str,
    template: str,
    context: dict,
    *,
    delivery: _Delivery | None = None,
    settings=None,
) -> dict:
    """Render + deliver one notification immediately. Returns
    {ok, subject, error}; raises ValueError on unrenderable input."""
    if delivery is None:
        delivery = NotifierDelivery(settings if settings is not None else _settings())
    subject, html_body, plain = render_template(template, dict(context))
    ok, error = await delivery.deliver(to, subject, html_body, plain)
    return {"ok": ok, "subject": subject, "error": error}


def _settings():
    from .config import Settings

    return Settings()


async def drain_outbox(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    delivery: NotifierDelivery | None = None,
    settings=None,
    max_attempts: int = OUTBOX_MAX_ATTEMPTS,
    limit: int = 50,
) -> dict:
    """One pass over queued rows. Per-row outcome is honest: sent gets sent_at;
    exhausted rows become failed with last_error; rows still under the attempt
    cap stay queued (the worker spaces retries with exponential backoff).
    Malformed payloads/contexts fail the row gracefully — never the batch."""
    if delivery is None:
        delivery = NotifierDelivery(settings if settings is not None else _settings())
    summary = {"sent": 0, "failed": 0, "retried": 0, "scanned": 0}
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(NotificationOutbox)
                    .where(
                        NotificationOutbox.status == "queued",
                        NotificationOutbox.attempts < max_attempts,
                    )
                    .order_by(NotificationOutbox.created_at)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            summary["scanned"] += 1
            row.attempts += 1
            error: str | None
            try:
                result = await send_template(
                    row.to_email, row.template, dict(row.payload or {}), delivery=delivery
                )
                ok, error = result["ok"], result["error"]
            except Exception as exc:  # noqa: BLE001 — one bad row must not kill the batch
                ok, error = False, f"{type(exc).__name__}: {exc}"
            if ok:
                row.status = "sent"
                row.sent_at = datetime.now(UTC)
                row.last_error = None
                summary["sent"] += 1
            else:
                row.last_error = (error or "delivery failed")[:2000]
                if row.attempts >= max_attempts:
                    row.status = "failed"
                    summary["failed"] += 1
                else:
                    summary["retried"] += 1
        await session.commit()
    return summary


class NotificationWorker:
    """Background drainer with exponential backoff on unhealthy passes
    (mirrors RunQueueWorker semantics; never lets one bad pass kill it)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings=None,
        poll_interval: float = 30.0,
        max_attempts: int = OUTBOX_MAX_ATTEMPTS,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.poll_interval = poll_interval
        self.max_attempts = max_attempts
        self._task: asyncio.Task | None = None
        self._unhealthy_streak = 0

    async def _loop(self) -> None:
        while True:
            unhealthy = False
            try:
                summary = await drain_outbox(
                    self.session_factory,
                    settings=self.settings,
                    max_attempts=self.max_attempts,
                )
                unhealthy = bool(summary["retried"] or summary["failed"])
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one bad drain must never kill the worker
                logger.exception("notification drain failed")
                unhealthy = True
            self._unhealthy_streak = self._unhealthy_streak + 1 if unhealthy else 0
            delay = self.poll_interval * (2 ** min(self._unhealthy_streak, 5))
            await asyncio.sleep(delay)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task is not None and self._task.done() is False:
            self._task.cancel()

    async def wait_until_idle(self, timeout: float = 5.0) -> None:
        """Test helper: waits for a clean (non-backoff) pass to complete."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self._unhealthy_streak == 0 and self._task is not None:
                return
            await asyncio.sleep(0.01)


# ----------------------------------------------------------- bus event glue

def event_to_template(event_type: str | object) -> str | None:
    """Map a bus event (EventType value or enum member) to a template name."""
    value = getattr(event_type, "value", event_type)
    return EVENT_TEMPLATE_MAP.get(str(value))


def run_deep_link(project_id: str | object, run_id: str | object) -> str:
    """M12-G7: canonical UI deep link for a run. Pure string building — no
    secrets, no host config, just the app-relative path the frontend routes."""
    pid = str(project_id or "unknown")
    rid = str(run_id or "unknown")
    return f"/projects/{pid}/runs/{rid}"


def _link_line(project_id: str, run_id: str) -> str:
    return f"Open: {run_deep_link(project_id, run_id)}"


def build_notification_context(
    event_type, project_id: str, payload: dict, run_id: str | None
) -> dict:
    """Bus payload (camelCase per api-conventions) -> template context with
    safe fallbacks, so real-world sparse payloads never break rendering."""
    payload = dict(payload or {})
    run = str(run_id or payload.get("runId") or "unknown")
    ctx: dict[str, str] = {"run_id": run, "project_id": str(project_id or "unknown")}
    kind = str(getattr(event_type, "value", event_type))
    if kind == "engine_completed":
        summary = payload.get("summary")
        text = str(summary) if summary else "All execution steps completed."
        # M12-G7: the Wave-9 template shape is pinned downstream (stored
        # payloads + tests), so the deep link rides in the free-text field.
        ctx["summary"] = f"{text}\n{_link_line(ctx['project_id'], run)}"
    elif kind == "review_failed":
        findings = payload.get("findings")
        if isinstance(findings, dict) and findings:
            parts = []
            for key, val in findings.items():
                parts.append(f"{key}: {_compact(val)}")
            ctx["findings"] = "; ".join(parts)
        elif findings:
            ctx["findings"] = _compact(findings)
        else:
            ctx["findings"] = "Reviewer reported unresolved issues (no details provided)."
        ctx["findings"] = f"{ctx['findings']}\n{_link_line(ctx['project_id'], run)}"
    elif kind == "pr_opened":
        pr = payload.get("pr") if isinstance(payload.get("pr"), dict) else {}
        ctx["pr_number"] = str(pr.get("number") or pr.get("prNumber") or "?")
        ctx["pr_url"] = str(pr.get("url") or pr.get("prUrl") or pr.get("htmlUrl") or "(no url)")
    elif kind == "engine_failed":
        # M12-G7: honest failure notice. Reason text is compacted and can be
        # empty — the sentence stays truthful either way.
        reason = payload.get("reason")
        ctx["reason"] = _compact(reason) if reason else "No failure reason was provided."
        ctx["run_link"] = run_deep_link(ctx["project_id"], run)
    elif kind == "blocked_awaiting_clarification":
        question = payload.get("question")
        if question:
            ctx["detail"] = f"Question: {_compact(question)}"
        elif str(payload.get("reason") or "") == "human_edit":
            ctx["detail"] = "A human edit paused the run; it resumes when re-engaged."
        else:
            ctx["detail"] = "The run needs input to continue."
        ctx["run_link"] = run_deep_link(ctx["project_id"], run)
    return ctx


def _compact(value, width: int = 300) -> str:
    try:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= width else text[: width - 1] + "…"


def default_recipient(payload: dict | None = None) -> str:
    """Recipient resolution: explicit payload override first, then the
    HARNESS_NOTIFY_EMAIL env knob. Empty string means 'notifications off'."""
    override = (payload or {}).get("notifyEmail") or ""
    if override:
        return str(override)
    return os.environ.get("HARNESS_NOTIFY_EMAIL", "")


# M12-G7: per-user opt-out preference. Default is notify_email = True
# (raw-13 #14 asks for a boolean defaulting true). Stored in the existing
# user_settings KV table (no new migration needed); absent row => default on.
NOTIFY_EMAIL_DEFAULT = True


async def get_notify_email_pref(session_factory, user_id: str) -> bool:
    """Read one user's notify_email preference. Missing user/row => default;
    any storage trouble degrades to the default (preference lookup must
    never break notification routing)."""
    from .models import UserSetting

    try:
        async with session_factory() as session:
            row = await session.get(UserSetting, (user_id, "notify_email"))
    except Exception:  # noqa: BLE001 — degraded read must not block routing
        return NOTIFY_EMAIL_DEFAULT
    if row is None or row.value in (None, ""):
        return NOTIFY_EMAIL_DEFAULT
    return str(row.value).strip().lower() in ("1", "true", "yes", "on")


async def resolve_admin_recipients(
    session_factory: async_sessionmaker[AsyncSession], team_id: str | None
) -> list[str]:
    """M12-G7 recipient fan-out for run-terminal events: every ACTIVE admin of
    the owning team (users join), falling back to the env knob when the team
    has no mailable admin. Deduplicated case-insensitively; empty list means
    'notifications off'. Storage trouble yields [] — callers skip quietly."""
    if not team_id:
        return []
    from sqlalchemy import select

    from .models import Role, TeamMember, User

    emails: list[str] = []
    try:
        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(User.email)
                        .join(TeamMember, TeamMember.user_id == User.id)
                        .where(
                            TeamMember.team_id == team_id,
                            TeamMember.role == Role.ADMIN.value,
                            TeamMember.member_type == "human",
                            User.is_active.is_(True),
                        )
                        .order_by(User.email)
                    )
                )
                .scalars()
                .all()
            )
    except Exception:  # noqa: BLE001 — roster lookup failure must not raise up
        return []
    seen: set[str] = set()
    for email in rows:
        key = str(email).strip().lower()
        if key and key not in seen:
            seen.add(key)
            emails.append(str(email).strip())
    return emails


def _dedup_key(envelope) -> str:
    """One email per transition per run (raw-13 #14): identity is the event's
    own id — bus seq for real envelopes, stable hash fallback otherwise."""
    payload = getattr(envelope, "payload", None)
    event_id = None
    if isinstance(payload, dict):
        event_id = payload.get("eventId") or payload.get("id")
    if not event_id:
        seq = getattr(envelope, "seq", None)
        event_id = str(seq) if seq is not None else ""
    project_id = getattr(envelope, "projectId", None) or ""
    run_id = getattr(envelope, "runId", None) or ""
    kind = str(getattr(getattr(envelope, "type", ""), "value", getattr(envelope, "type", "")))
    if event_id:
        return f"{project_id}|{run_id}|{kind}|{event_id}"
    digest = hashlib.sha256(repr(sorted(vars(envelope).items())).encode()).hexdigest()[:32]
    return f"{project_id}|{run_id}|{kind}|{digest}"


class NotificationDedup:
    """In-process idempotency guard for enqueue-side replays.

    The bus persists an event once and assigns a unique seq, so within a
    process this set fully answers 'did we already notify this transition?'.
    Cross-process replay protection rides on the same fact: another process
    would be tapping a DIFFERENT envelope instance only via its own listener,
    and each committed row carries a globally-unique seq — so seq-keyed
    suppression cannot collide across processes either. Bounded LRU keeps the
    guard memory-flat on long-lived workers; a process restart merely re-reads
    history without re-sending because replays are served to WS clients, not
    re-tapped through add_event_listener.
    """

    def __init__(self, maxsize: int = 4096) -> None:
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._maxsize = max(1, int(maxsize))

    def seen(self, key: str) -> bool:
        """True when `key` was already enqueued by THIS process."""
        if key in self._seen:
            self._seen.move_to_end(key)
            return True
        self._seen[key] = None
        while len(self._seen) > self._maxsize:
            self._seen.popitem(last=False)
        return False


def make_event_notifier(session_factory: async_sessionmaker[AsyncSession], settings=None):
    """Build a fire-and-forget bus-event hook for deploy/runners/services.py.

    The returned callable takes one committed harness_contracts EventEnvelope
    (wired via EventBus.add_event_listener), NEVER raises and NEVER awaits on
    the publisher's critical path: it spawns a task that enqueues outbox row(s)
    (which the NotificationWorker later drains). Unmappable events, absent
    recipients and malformed contexts are logged-and-skipped by design.

    M12-G7: run-terminal transitions (engine_completed / engine_failed /
    blocked_awaiting_clarification) are routed through notification_rules —
    one email per transition per run (event-id dedup) to every active team
    admin whose notify_email preference is on. Other mapped events
    (review_failed / pr_opened) keep the exact Wave-9 single-recipient path.
    """
    from .notification_rules import RUN_TERMINAL_EVENTS, enqueue_run_transition

    legacy = _make_legacy_event_notifier(session_factory)

    def _on_envelope(envelope):
        try:
            kind = str(
                getattr(getattr(envelope, "type", ""), "value", getattr(envelope, "type", ""))
            )
            if kind in RUN_TERMINAL_EVENTS:
                # M12-G7 path: dedup + team-admin fan-out. Same never-raise /
                # fire-and-forget contract as below; see notification_rules.
                # Task keeps the historical `_enqueue_row` name — test helpers
                # (and humans) recognize pending enqueues by it.
                loop = asyncio.get_running_loop()

                async def _enqueue_row():
                    try:
                        await enqueue_run_transition(
                            session_factory,
                            envelope,
                            dedup_seen=_g7_dedup.seen,
                        )
                    except Exception:  # noqa: BLE001 — must not surface into runs
                        logger.exception("run-terminal notification failed")

                loop.create_task(_enqueue_row())
                return

            legacy(envelope)
        except Exception:  # noqa: BLE001 — telemetry must never break the event flow
            logger.exception("notification hook failed")

    return _on_envelope


_g7_dedup = NotificationDedup()


def _make_legacy_event_notifier(
    session_factory: async_sessionmaker[AsyncSession],
):
    """Wave-9 hook body, unchanged: single recipient from payload override or
    HARNESS_NOTIFY_EMAIL; kept for review_failed / pr_opened so their pinned
    semantics survive the G7 routing split."""

    def _on_envelope(envelope):
        try:
            template = event_to_template(getattr(envelope, "type", ""))
            if template is None:
                return
            payload = dict(getattr(envelope, "payload", None) or {})
            recipient = default_recipient(payload)
            if not recipient:
                return
            project_id = str(getattr(envelope, "projectId", "") or "")
            context = build_notification_context(
                getattr(envelope, "type", ""), project_id, payload, getattr(envelope, "runId", None)
            )
            team_id = payload.get("teamId") or None

            async def _enqueue_row():
                try:
                    await enqueue(session_factory, recipient, template, context, team_id=team_id)
                except ValueError as exc:
                    logger.warning("notification skipped (%s): %s", template, exc)
                except Exception:  # noqa: BLE001 — enqueue trouble must not surface
                    logger.exception("notification enqueue failed")

            asyncio.get_running_loop().create_task(_enqueue_row())
        except Exception:  # noqa: BLE001 — telemetry must never break the event flow
            logger.exception("notification hook failed")

    return _on_envelope
