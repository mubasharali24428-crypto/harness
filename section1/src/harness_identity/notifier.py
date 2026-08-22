"""E-smtp — pluggable OTP code delivery.

The notifier hook signature is `notify(email: str, code: str) -> None`.
Selection happens in make_app(): SMTP when HARNESS_SMTP_HOST is configured,
dev log-printer otherwise (never silently — dev delivery must stay visible).

SMTP uses stdlib smtplib synchronously inside a worker thread (OTP dispatch is
not on the request critical path; a slow relay must not stall the API call).
No third-party dependencies.
"""

import logging
import smtplib
from collections.abc import Callable
from email.message import EmailMessage
from email.utils import formataddr

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
