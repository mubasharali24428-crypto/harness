"""Wave9-E: baseline security headers for every harness FastAPI app.

Pure ASGI middleware (no BaseHTTPMiddleware task overhead): sets static
anti-sniff / anti-framing headers on every response, adds HSTS only when the
deployment is TLS-terminated (HARNESS_TLS_ENABLED=1), and attaches a
report-only Content-Security-Policy to HTML responses so browsers surface
violations without breaking section7 pages.

Wire-up contract (one line per app, APPEND pattern):

    install_security_headers(app)

The factory returns the concrete class so callers can use either spelling:
``install_security_headers(app)`` or ``app.add_middleware(install_security_headers(app))``.
"""

import os

__all__ = ["SecurityHeadersMiddleware", "install_security_headers"]

# Static headers, set on every response.
_X_CONTENT_TYPE_OPTIONS = ("x-content-type-options", "nosniff")
_X_FRAME_OPTIONS = ("x-frame-options", "DENY")
_REFERRER_POLICY = ("referrer-policy", "strict-origin-when-cross-origin")

# Report-only CSP default: nothing is blocked, violations are visible in the
# browser console. Tightening to enforcing is a later wave behind a flag.
_CSP_REPORT_ONLY = (
    "content-security-policy-report-only",
    "default-src 'self'; frame-ancestors 'none'; object-src 'none'; "
    "base-uri 'self'; form-action 'self'",
)
_HTML_TYPES = frozenset({b"text/html", b"application/xhtml+xml"})
_HSTS_VALUE = b"max-age=31536000; includeSubDomains"


def _tls_enabled() -> bool:
    """HSTS only when the deployment says it serves over TLS.

    Read per-request-context (not import time) so tests and multi-env deploys
    can flip HARNESS_TLS_ENABLED without reloading the module. Any of
    1/true/yes/on enables, matching pydantic-settings bool parsing.
    """
    return os.environ.get("HARNESS_TLS_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class SecurityHeadersMiddleware:
    """Wrap the ASGI callable directly and stamp headers onto http responses."""

    def __init__(self, app) -> None:  # noqa: ANN001 — pure ASGI signature
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        tls_on = _tls_enabled()

        async def send_with_headers(message) -> None:  # noqa: ANN001
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                for name, value in (
                    _X_CONTENT_TYPE_OPTIONS,
                    _X_FRAME_OPTIONS,
                    _REFERRER_POLICY,
                ):
                    headers.append((name.encode(), value.encode()))
                if tls_on:
                    # includeSubDomains: every harness subdomain is first-party;
                    # no preload yet so a misconfigured deploy can back out fast.
                    headers.append((b"strict-transport-security", _HSTS_VALUE))
                ctype = b""
                for name, value in headers:
                    if name.lower() == b"content-type":
                        ctype = value.lower()
                        break
                if ctype.split(b";", 1)[0].strip() in _HTML_TYPES:
                    headers.append(
                        (_CSP_REPORT_ONLY[0].encode(), _CSP_REPORT_ONLY[1].encode())
                    )
            await send(message)

        await self.app(scope, receive, send_with_headers)


def install_security_headers(app):
    """Register the middleware stack entry and return the class.

    Safe to call exactly once per app at factory time, BEFORE the app builds
    its middleware stack (i.e. before startup), same constraint as
    add_middleware.
    """
    app.add_middleware(SecurityHeadersMiddleware)
    return SecurityHeadersMiddleware
