# SMTP OTP transport tests — 2026-10-18

Status: done (tests only; one confirmed production defect remains expected-failing).

New file: `section1/tests/test_smtp_otp_transport.py`. A dependency-free in-process stdlib socketserver relay binds an ephemeral loopback port and implements SMTP EHLO/AUTH/MAIL/RCPT/DATA/QUIT. Tests exercise actual smtplib transport, recipient envelope/header, sender and OTP body, synthetic username/password authentication, private normal logs, synchronous exception propagation, asynchronous worker-thread error logging without dev fallback, and visible dev-printer selection only with an empty SMTP host. No real credentials are read.

**FIXED 2026-10-18** (was a CONFIRMED defect; `_log_smtp_failure` now logs only the exception type and SMTP status, and the test is a normal passing test, 5 passed). Original finding: `section1/src/harness_identity/notifier.py:111` logs raw SMTP exception text. A relay's 554 response containing synthetic code/credentials leaks them through `_log_smtp_failure`. `test_relay_error_echo_does_not_leak_secrets` is `xfail(strict=True, raises=AssertionError)` so an unrelated transport/setup error is not silently accepted. Fix recommendation: log a safe error category/status, never raw relay text. Production code unchanged.

Commands from repository root:

```
section1/.venv/bin/python -m pytest section1/tests/test_smtp_otp_transport.py -q -rx
section1/.venv/bin/python -m pytest section1/tests/test_smtp_otp_transport.py -q -rx --tb=short
HARNESS_TEST_PG=postgresql://pilot:pilot@127.0.0.1:5815/pilot HARNESS_REQUIRE_OWNERSHIP=true HARNESS_DATABASE_URL=postgresql+asyncpg://pilot:pilot@127.0.0.1:5815/pilot section1/.venv/bin/python -m pytest section1/tests -q -ra --junit-xml=docs/briefs/evidence/pilot-prep/section1.junit.xml
section1/.venv/bin/ruff check section1/src section1/tests section1/scripts
```

Full suite: **654 passed, 0 failed, 0 skipped, 1 xfailed**, 72 warnings, 57.84 seconds; exit 0. This includes the four new passing tests and one expected defect against the 650-pass baseline. Full output: `evidence/pilot-prep/section1.log`; JUnit: `evidence/pilot-prep/section1.junit.xml`. Targeted final run: see `smtp-final.log` (4 passed, 1 xfailed). Lint: all checks passed after wrapping one long decorator.

Development failures, not concealed: initial sandboxed run could not bind loopback (`PermissionError: [Errno 1] Operation not permitted`; 1 passed, 1 xfailed, 3 errors). The original xfail also masked a setup error; it was restricted to AssertionError. First unsandboxed run found an incorrect uppercase RCPT expectation in the new test: `rcpt to:<recipient@example.test>` versus expected `rcpt TO:<recipient@example.test>` (1 failed, 3 passed, 1 xfailed). Corrected test expectation; full failure output retained in `smtp-targeted.log`. No production behavior was changed to make tests pass.

Not verified: external inbox receipt, real credentials/provider authentication, successful TLS/certificate negotiation, retries, bounce processing, and end-to-end browser sign-in. The relay accepts synthetic AUTH but is not an authentication implementation. Source inspection also shows STARTTLS failure can fall back to plaintext; that pre-existing behavior is outside these test assertions and needs a separate security decision before deployment.
