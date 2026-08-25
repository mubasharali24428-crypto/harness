"""Security headers middleware — S8's local copy (M17-B3 inline).

S8's app factory previously imported section1's
``harness_identity.security_headers.install_security_headers`` at assembly
time (an s8→s1 package import the isolation guard could not see). The only
behavior S8 needs is the Wave9-E baseline: static anti-sniff / anti-framing /
referrer headers on every response, HSTS when TLS-terminated, and a
report-only CSP on HTML responses (S8 serves JSON, so the CSP branch is
dormant here but kept for parity). This file inlines exactly that slice with
no section imports; deploy/runners/services.py keeps importing the richer S1
original (deploy is outside section isolation by design).

Pure ASGI middleware (no BaseHTTPMiddleware task overhead).
"""

from __future__ import annotations

import os

__all__ = ["SecurityHeadersMiddleware", "install_security_headers"]

# Static headers, set on every response.
_X_CONTENT_TYPE_OPTIONS = ("x-content-type-options", "nosniff")
_X_FRAME_OPTIONS = ("x-frame-options", "DENY")
_REFERRER_POLICY = ("referrer-policy", "strict-origin-when-cross-origin")

_HTML_TYPES = frozenset({b"text/html", b"application/xhtml+xml"})
_HSTS_VALUE = b"max-age=31536000; includeSubDomains"

CSP_REPORT_ONLY_HEADER = "content-security-policy-report-only"

# Report-only policy matching the platform default (connect-src covers the
# section API origins + ws/wss for the event-stream upgrade). S8 serves no
# HTML today; kept so an HTML route can never ship without a policy.
_CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self'; "
    "connect-src 'self' http://localhost:8001 http://localhost:8002 "
    "http://localhost:8004 http://localhost:8005 http://localhost:8006 "
    "http://localhost:8008 ws: wss:; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _tls_enabled() -> bool:
    return os.environ.get("HARNESS_TLS_ENABLED", "").strip().lower() in _TRUE_VALUES


class SecurityHeadersMiddleware:
    """Wrap the ASGI callable directly and stamp headers onto http responses."""

    def __init__(self, app) -> None:  # noqa: ANN001 — pure ASGI signature
        self.app = app
        self._csp_value = _CSP_POLICY.encode()

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        tls_on = _tls_enabled()
        csp_header_name = CSP_REPORT_ONLY_HEADER.encode()

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
                    headers.append((csp_header_name, self._csp_value))
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
