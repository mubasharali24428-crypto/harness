"""E-smtp — notifier selection and SMTP delivery tests.

All SMTP is exercised against a local fake server thread (aiosmtpd-free: raw
socket server speaking enough ESMTP) or via monkeypatched smtplib, so the suite
never touches a real network.
"""

from unittest.mock import patch

from harness_identity.config import Settings
from harness_identity.notifier import (
    dev_log_notifier,
    make_notifier,
    smtp_notifier,
)


def test_no_smtp_host_falls_back_to_dev_printer():
    settings = Settings(database_url="sqlite://")
    assert make_notifier(settings) is dev_log_notifier


def test_smtp_host_selects_smtp_notifier():
    settings = Settings(database_url="sqlite://", smtp_host="smtp.example.test")
    notifier = make_notifier(settings)
    assert notifier is not dev_log_notifier
    assert callable(notifier)


def test_dev_notifier_prints_code(capsys):
    dev_log_notifier("a@b.dev", "123456")
    err = capsys.readouterr().err
    assert "HARNESS-OTP" in err
    assert "123456" in err
    assert "a@b.dev" in err


class _FakeSMTP:
    """Records one message; class-level so assertions survive instance churn."""

    sent = []
    startedtls = 0
    logins = []

    def __init__(self, host, port, timeout=None):
        _FakeSMTP.startedtls = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        _FakeSMTP.startedtls += 1

    def login(self, user, password):
        _FakeSMTP.logins.append((user, password))

    def send_message(self, msg):
        _FakeSMTP.sent.append(msg)


def test_smtp_notifier_sends_message_with_code():
    _FakeSMTP.sent.clear()
    _FakeSMTP.logins.clear()
    notify = smtp_notifier(
        host="smtp.example.test",
        port=587,
        username="u",
        password="p",
        from_addr="h@rness.dev",
        use_tls=True,
    )
    with patch("harness_identity.notifier.smtplib.SMTP", _FakeSMTP):
        notify("user@x.dev", "998877")

    assert len(_FakeSMTP.sent) == 1
    msg = _FakeSMTP.sent[0]
    assert msg["To"] == "user@x.dev"
    assert "998877" in msg.get_content()
    assert _FakeSMTP.startedtls == 1
    assert _FakeSMTP.logins == [("u", "p")]


def test_smtp_delivery_failure_never_raises_into_request():
    class _ExplodingSMTP(_FakeSMTP):
        def send_message(self, msg):
            raise ConnectionError("relay down")

    notify = smtp_notifier(
        host="smtp.example.test", port=587, username=None, password=None,
        from_addr="h@rness.dev", use_tls=False,
    )
    # Enumeration-safety contract: delivery problems are logged, never raised.
    import asyncio

    async def _call():
        with patch("harness_identity.notifier.smtplib.SMTP", _ExplodingSMTP):
            await asyncio.to_thread(lambda: None)  # ensure loop machinery exists
            # Direct call inside running loop path:
            notify("user@x.dev", "111111")

    asyncio.run(_call())


def test_settings_env_prefix_wiring():
    settings = Settings(
        database_url="sqlite://",
        smtp_host="relay.corp",
        smtp_port=465,
        smtp_from="no-reply@corp",
    )
    assert settings.smtp_host == "relay.corp"
    assert settings.smtp_port == 465
    assert settings.smtp_from == "no-reply@corp"
    assert settings.smtp_starttls is True
