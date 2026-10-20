# SMTP TLS policy decision for the supervised pilot

Status: **Option 1 implemented 2026-10-18** (see "Implemented" at the end). The analysis below is the original decision memo; its "current behavior" paragraphs describe the code before this change. No real inbox was contacted.

## Current behavior and risk

`section1/src/harness_identity/config.py:5,19` maps settings to `HARNESS_` variables. `HARNESS_SMTP_HOST` defaults empty; port defaults 587; `HARNESS_SMTP_STARTTLS` defaults true. There is no production-mode setting in this Settings class and no required-TLS switch.

`section1/src/harness_identity/notifier.py:80` opens ordinary SMTP and calls `starttls()` when enabled. At lines 86–90 it catches `smtplib.SMTPException`, warns, then continues to login and send. With STARTTLS disabled, it skips the attempt entirely. Missing STARTTLS support and SMTP refusal can therefore result in an unencrypted message and, depending on the selected authentication mechanism, exposed or recoverable credentials. This is not a catch of every possible TLS/socket failure. An attacker able to observe or alter the connection could steal a sign-in code or interfere with delivery; supervision does not protect the connection.

At `notifier.py:94`, asynchronous OTP failures are logged without changing the request response; synchronous callers receive the exception. There is no dev-printer fallback after SMTP delivery fails. At `notifier.py:108`, failure logging now includes only exception type and SMTP status, excluding relay text. At `notifier.py:116`, missing host selects the visible dev printer, which prints the OTP (`:49`). Transport encryption and log redaction are separate protections.

`section1/tests/test_smtp_otp_transport.py:57` explicitly disables TLS for its local fake relay. Tests cover delivery/recipient/body (`:73`), synchronous failure (`:85`), asynchronous failure (`:103`), malicious relay text redaction (`:110`, now a normal test), and visible unconfigured dev delivery (`:116`). They do not test successful TLS, certificate verification, STARTTLS refusal with TLS enabled, or real inbox delivery. These are source-inspected observations, not newly run SMTP tests.

A second sender, `NotifierDelivery._send_sync` at `notifier.py:329`, has the same downgrade behavior at `:336`. Apply the selected future policy consistently to both senders.

## Options (all new variables below are proposals, not existing features)

| Option | Exact configuration and defaults | On TLS refusal | Test changes needed | Migration risk |
|---|---|---|---|---|
| 1. Require TLS by default; explicit local development exception | Add `HARNESS_SMTP_ALLOW_INSECURE=false`. Keep `HARNESS_SMTP_STARTTLS=true`; reject STARTTLS=false unless ALLOW_INSECURE=true. Exception is for synthetic local testing only. | Default: abort before AUTH or DATA; preserve sanitized asynchronous logging and generic request response. Explicit exception permits plaintext with a warning. Never downgrade on certificate-validation failure. | Enable TLS in fake settings; test missing STARTTLS and refusal both prevent AUTH/DATA; test explicit local opt-out; test validated TLS success, untrusted/wrong-host certificate failure, both senders, and sync/async failure privacy. Existing plaintext fake-relay cases explicitly opt out. | Plaintext-only relays stop delivering until upgraded or explicitly opted out. Existing STARTTLS=false deployments need an intentional migration. |
| 2. Require TLS only in production mode | Add `HARNESS_DEPLOYMENT_MODE=production` (enum production/development; fail on unknown values). In production require `HARNESS_SMTP_STARTTLS=true`; development keeps the existing fallback. | Production aborts before AUTH/DATA. Development warns and continues for SMTP refusal. Certificate errors must still fail. | Same secure-path tests plus production/development matrix, invalid mode, default mode, and production STARTTLS=false startup rejection. | New mode must be wired everywhere. Mislabeling a pilot as development silently restores the downgrade risk; secure default can disrupt old dev fixtures. |
| 3. Keep fallback with conspicuous warnings | Retain `HARNESS_SMTP_STARTTLS=true`; add `HARNESS_SMTP_WARN_PLAINTEXT=true` (warning enabled by default). Log a sanitized structured warning on refusal and explicit STARTTLS=false; show an operator health warning. | Continue plaintext after SMTP refusal, with warning before AUTH/DATA. No credential/OTP/relay text in warning. | Add enabled-TLS refusal tests asserting warning and continued delivery; verify explicit disabled-TLS warning, privacy and health signal; retain existing transport behavior tests. | Lowest delivery disruption; extra alerts and health signal integration. Does not prevent interception, including before an operator reacts. |

## Recommendation and acceptance criteria

Choose option 1 for a pilot with real inboxes. Keep the exception off, configure a real SMTP host, and require successful certificate- and hostname-validated TLS before credentials or OTP content are sent. Use an explicit trusted SSL context in the future implementation; current `starttls()` calls pass no context. A successful encryption handshake alone is not evidence of server identity verification.

Before enabling real users, prove success and refusal behavior with synthetic credentials on a controlled test relay, then verify delivery to an authorized pilot inbox. Treat absent SMTP configuration as a launch-blocking operator check; the current dev-printer fallback remains unchanged by this memo. Preserve the generic sign-in response while making failures visible to the supervisor through sanitized operator logs. A refused connection should cause delivery failure, never a plaintext retry.

The product owner must select the policy; this memo changes no settings, production code or tests. Certificate behavior and real-relay compatibility remain unverified here.

## Implemented (Option 1)

- `HARNESS_SMTP_ALLOW_INSECURE` (default false; `config.py`). Both senders (`smtp_notifier` and `NotifierDelivery`) call `notifier._secure` before AUTH or DATA.
- `HARNESS_SMTP_STARTTLS=true` (default): STARTTLS is attempted with `ssl.create_default_context()` (certificate and hostname verified). A refusal or unsupported STARTTLS raises `TLSRequiredError` and nothing is sent. With `HARNESS_SMTP_STARTTLS=false` the send is also refused.
- Only `HARNESS_SMTP_ALLOW_INSECURE=1` permits plaintext (local development): a warning "SMTP to <host> is NOT encrypted" is logged, without code, user or password.
- A certificate failure (`ssl.SSLError`) is never downgraded, even with the opt-out.
- Failure logging is unchanged (type and SMTP status only); the sign-in response stays generic.
- Tests: `section1/tests/test_smtp_otp_transport.py` (refusal before AUTH/DATA, TLS-disabled refusal, opt-out warning, cert failure not downgraded, verifying context passed, same policy on the general sender) plus updated fakes in `test_notifier.py` and `test_notifications.py`.
- Not verified: a real TLS handshake or certificate chain against a real relay, and real inbox delivery. The tests prove the policy and the context settings, not interoperability. Operators must set the host, keep the opt-out off, and test with a controlled relay before real users.
