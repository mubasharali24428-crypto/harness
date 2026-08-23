"""E-smtp — pluggable OTP code delivery.

The notifier hook signature is `notify(email: str, code: str) -> None`.
Selection happens in make_app(): SMTP when HARNESS_SMTP_HOST is configured,
dev log-printer otherwise (never silently — dev delivery must stay visible).

SMTP uses stdlib smtplib synchronously inside a worker thread (OTP dispatch is
not on the request critical path; a slow relay must not stall the API call).
No third-party dependencies.
"""

import asyncio
import html as _html
import json
import logging
import os
import smtplib
import sys
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
}

# Bus event type (harness_contracts.events.EventType values) -> template name.
EVENT_TEMPLATE_MAP: dict[str, str] = {
    "engine_completed": "run_finished",
    "review_failed": "review_failed",
    "pr_opened": "pr_opened",
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
    team_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("teams.id"), index=True)
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
        ctx["summary"] = str(summary) if summary else "All execution steps completed."
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
    elif kind == "pr_opened":
        pr = payload.get("pr") if isinstance(payload.get("pr"), dict) else {}
        ctx["pr_number"] = str(pr.get("number") or pr.get("prNumber") or "?")
        ctx["pr_url"] = str(pr.get("url") or pr.get("prUrl") or pr.get("htmlUrl") or "(no url)")
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


def make_event_notifier(session_factory: async_sessionmaker[AsyncSession], settings=None):
    """Build a fire-and-forget bus-event hook for deploy/runners/services.py.

    The returned callable takes one committed harness_contracts EventEnvelope
    (wired via EventBus.add_event_listener), NEVER raises and NEVER awaits on
    the publisher's critical path: it spawns a task that enqueues an outbox row
    (which the NotificationWorker later drains). Unmappable events, absent
    recipients and malformed contexts are logged-and-skipped by design.
    """

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
