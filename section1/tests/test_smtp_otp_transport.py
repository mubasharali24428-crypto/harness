"""Real loopback SMTP transport; all addresses, codes and credentials are synthetic."""
import asyncio
import socketserver
import threading
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace

import pytest

from harness_identity.notifier import dev_log_notifier, make_notifier


@pytest.fixture
def smtp_server():
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b"220 test relay\r\n")
            while line := self.rfile.readline():
                verb = line.split()[0].upper()
                if verb == b"EHLO":
                    reply = b"250-test\r\n250 AUTH PLAIN\r\n"
                elif verb == b"AUTH":
                    reply = b"235 authenticated\r\n"
                elif verb == b"RCPT":
                    self.server.recipients.append(line.decode().strip())
                    reply = b"250 ok\r\n"
                elif verb == b"DATA":
                    self.wfile.write(b"354 send data\r\n")
                    body = []
                    while (part := self.rfile.readline()) != b".\r\n":
                        if not part:
                            return
                        body.append(part[1:] if part.startswith(b"..") else part)
                    self.server.messages.append(b"".join(body))
                    reply = self.server.data_reply
                elif verb == b"QUIT":
                    self.wfile.write(b"221 bye\r\n")
                    return
                else:
                    reply = b"250 ok\r\n"
                self.wfile.write(reply)

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        server.messages = []
        server.recipients = []
        server.data_reply = b"250 accepted\r\n"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=2)


def settings(server=None):
    return SimpleNamespace(
        smtp_host="127.0.0.1" if server else "",
        smtp_port=server.server_address[1] if server else 25,
        smtp_username="synthetic-user",
        smtp_password="synthetic-password",
        smtp_from="sender@example.test",
        smtp_starttls=False,
        smtp_allow_insecure=True,  # the loopback fake relay has no TLS
    )


def assert_private(text):
    for value in ("731942", "synthetic-user", "synthetic-password"):
        assert value not in text


def test_delivery_recipient_body_and_private_logs(smtp_server, caplog, capsys):
    notify = make_notifier(settings(smtp_server))
    assert notify is not dev_log_notifier
    notify("recipient@example.test", "731942")
    msg = BytesParser(policy=policy.default).parsebytes(smtp_server.messages[0])
    assert msg["To"] == "recipient@example.test"
    assert "sender@example.test" in msg["From"]
    assert "731942" in msg.get_content()
    assert smtp_server.recipients == ["rcpt to:<recipient@example.test>"]
    assert_private(caplog.text + capsys.readouterr().err)


def test_sync_failure_raises_without_dev_fallback(smtp_server, caplog, capsys):
    import smtplib

    smtp_server.data_reply = b"554 delivery rejected\r\n"
    with pytest.raises(smtplib.SMTPDataError):
        make_notifier(settings(smtp_server))("recipient@example.test", "731942")
    assert_private(caplog.text + capsys.readouterr().err)


async def failed_delivery(server, caplog):
    make_notifier(settings(server))("recipient@example.test", "731942")
    for _ in range(200):
        if "OTP email delivery failed" in caplog.text:
            return
        await asyncio.sleep(0.01)
    pytest.fail("SMTP completion callback did not log failure within 2 seconds")


def test_async_failure_logged_without_dev_fallback(smtp_server, caplog, capsys):
    smtp_server.data_reply = b"554 delivery rejected\r\n"
    asyncio.run(failed_delivery(smtp_server, caplog))
    assert "SMTPDataError" in caplog.text
    assert_private(caplog.text + capsys.readouterr().err)


def test_relay_error_echo_does_not_leak_secrets(smtp_server, caplog, capsys):
    smtp_server.data_reply = b"554 731942 synthetic-user synthetic-password\r\n"
    asyncio.run(failed_delivery(smtp_server, caplog))
    assert_private(caplog.text + capsys.readouterr().err)


def test_unconfigured_smtp_visibly_uses_dev_printer(capsys):
    notify = make_notifier(settings())
    assert notify is dev_log_notifier
    notify("recipient@example.test", "731942")
    output = capsys.readouterr().err
    assert "HARNESS-OTP login code for recipient@example.test: 731942" in output
    assert "synthetic-user" not in output
    assert "synthetic-password" not in output


# --- TLS enforcement: refuse to send a code or credentials unencrypted unless opted out ---

def _secure_settings(server, **over):
    return SimpleNamespace(**{**vars(settings(server)), "smtp_starttls": True,
                              "smtp_allow_insecure": False, **over})


def _relay_untouched(server):
    assert server.messages == [] and server.recipients == []


def test_refused_starttls_aborts_before_auth_and_data(smtp_server, caplog, capsys):
    from harness_identity.notifier import TLSRequiredError

    # the fake relay does not offer STARTTLS, so the upgrade is refused
    with pytest.raises(TLSRequiredError):
        make_notifier(_secure_settings(smtp_server))("recipient@example.test", "731942")
    _relay_untouched(smtp_server)
    assert_private(caplog.text + capsys.readouterr().err)


def test_tls_disabled_without_opt_out_is_refused(smtp_server):
    from harness_identity.notifier import TLSRequiredError

    with pytest.raises(TLSRequiredError, match="ALLOW_INSECURE"):
        make_notifier(_secure_settings(smtp_server, smtp_starttls=False))("r@example.test", "1")
    _relay_untouched(smtp_server)


def test_explicit_opt_out_sends_plaintext_with_visible_warning(smtp_server, caplog):
    caplog.set_level("WARNING")
    cfg = _secure_settings(smtp_server, smtp_allow_insecure=True)
    make_notifier(cfg)("r@example.test", "731942")
    assert len(smtp_server.messages) == 1
    assert "NOT encrypted" in caplog.text and "731942" not in caplog.text


def test_certificate_failure_is_never_downgraded_even_with_opt_out(smtp_server, monkeypatch):
    import smtplib
    import ssl

    def bad_cert(self, *a, **k):
        raise ssl.SSLCertVerificationError("synthetic: certificate verify failed")

    monkeypatch.setattr(smtplib.SMTP, "starttls", bad_cert)
    cfg = _secure_settings(smtp_server, smtp_allow_insecure=True)
    with pytest.raises(ssl.SSLCertVerificationError):
        make_notifier(cfg)("r@example.test", "1")
    _relay_untouched(smtp_server)


def test_starttls_gets_a_verifying_context(smtp_server, monkeypatch):
    import smtplib
    import ssl

    seen = {}

    def spy(self, *a, context=None, **k):
        seen["ctx"] = context
        raise smtplib.SMTPNotSupportedError("stop here")

    monkeypatch.setattr(smtplib.SMTP, "starttls", spy)
    with pytest.raises(smtplib.SMTPException):
        make_notifier(_secure_settings(smtp_server))("r@example.test", "1")
    assert seen["ctx"].verify_mode == ssl.CERT_REQUIRED and seen["ctx"].check_hostname


def test_general_notification_sender_has_the_same_policy(smtp_server):
    from harness_identity.notifier import NotifierDelivery, TLSRequiredError

    d = NotifierDelivery(_secure_settings(smtp_server))
    with pytest.raises(TLSRequiredError):
        d._send_sync("r@example.test", "s", "<p>h</p>", "h")
    _relay_untouched(smtp_server)
    ok = NotifierDelivery(_secure_settings(smtp_server, smtp_allow_insecure=True))
    ok._send_sync("r@example.test", "s", "<p>h</p>", "h")
    assert len(smtp_server.messages) == 1
