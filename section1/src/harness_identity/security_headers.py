"""Security headers for every harness FastAPI app — Wave9-E base + M15-W5-S1 CSP.

Pure ASGI middleware (no BaseHTTPMiddleware task overhead): sets static
anti-sniff / anti-framing headers on every response, adds HSTS only when the
deployment is TLS-terminated (HARNESS_TLS_ENABLED=1), and attaches a
Content-Security-Policy to HTML responses.

M15-W5-S1 replaces the placeholder Wave9-E policy with a real one built from
section7's audited resource needs, and ships the rollout ladder CSP guidance
prescribes:

* DEFAULT — ``Content-Security-Policy-Report-Only``: browsers evaluate the
  policy and surface violations (console + report-uri POST to the S1
  ``/api/v1/csp-report`` collector) but block NOTHING, so shipping cannot
  break the UI while the report stream is being watched.
* OPT-IN ENFORCE — ``HARNESS_CSP_ENFORCE=true`` (also 1/yes/on, matching
  pydantic-settings bool parsing) switches the SAME policy onto the enforcing
  ``Content-Security-Policy`` header. Flip it only after a clean report-only
  window; violations are then actually blocked.

The policy (build_csp_policy) covers what section7 actually loads: scripts
only from 'self' (Next.js bundles — no inline scripts), styles 'self' +
'unsafe-inline' (React style props compile to inline style *attributes*, and
style attributes need 'unsafe-inline' in style-src), images/fonts limited to
self + inline data/blob URIs, and connect-src listing the section API origins
plus ws:/wss: for the event-stream upgrade. A nonce-based script allowlist
must land on the section7 rendering side — this app serves no HTML, so a
nonce minted here could never appear in a served document; until that lands,
enforce mode keeps script-src 'self' (which already blocks every injected
inline <script>, the injection class CSP-nonces exist to re-admit safely).

Wire-up contract (one line per app, APPEND pattern):

    install_security_headers(app)

The factory returns the concrete class so callers can use either spelling:
``install_security_headers(app)`` or ``app.add_middleware(install_security_headers(app))``.
"""

import json
import logging
import os

__all__ = [
    "CSP_ENFORCE_ENV",
    "CSP_ENFORCE_HEADER",
    "CSP_EXTRA_ORIGINS_ENV",
    "CSP_REPORT_ONLY_HEADER",
    "CSP_REPORT_PATH",
    "DEFAULT_API_ORIGINS",
    "DEFAULT_CSP_POLICY",
    "SecurityHeadersMiddleware",
    "build_csp_policy",
    "csp_enforce_enabled",
    "extra_api_origins_from_env",
    "install_security_headers",
]

logger = logging.getLogger(__name__)

# Static headers, set on every response.
_X_CONTENT_TYPE_OPTIONS = ("x-content-type-options", "nosniff")
_X_FRAME_OPTIONS = ("x-frame-options", "DENY")
_REFERRER_POLICY = ("referrer-policy", "strict-origin-when-cross-origin")

_HTML_TYPES = frozenset({b"text/html", b"application/xhtml+xml"})
_HSTS_VALUE = b"max-age=31536000; includeSubDomains"

# M15-W5-S1 rollout flag: OFF by default — report-only ships first. Only an
# explicit HARNESS_CSP_ENFORCE=true moves violations from observed to blocked.
CSP_ENFORCE_ENV = "HARNESS_CSP_ENFORCE"

CSP_REPORT_ONLY_HEADER = "content-security-policy-report-only"
CSP_ENFORCE_HEADER = "content-security-policy"

# Browser reports POST here (collector lives in harness_identity.csp_report).
CSP_REPORT_PATH = "/api/v1/csp-report"

# Where section7 points its fetches/event stream by default (src/lib/api.ts:
# identity 8001, orchestrator 8004, github 8002, sandbox 8005, meeting 8006,
# review 8008). connect-src lists them so the SPA's cross-port calls pass in
# both modes; ws:/wss: cover the /api/v1/ws/{projectId} event-stream upgrade.
DEFAULT_API_ORIGINS: tuple[str, ...] = (
    "http://localhost:8001",
    "http://localhost:8002",
    "http://localhost:8004",
    "http://localhost:8005",
    "http://localhost:8006",
    "http://localhost:8008",
)

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _env_flag(name: str) -> bool:
    """Truthy env parsing shared by the HSTS and CSP-enforce reads.

    Read per-request-context (not import time) so tests and multi-env deploys
    can flip flags without reloading the module. Any of 1/true/yes/on enables,
    matching pydantic-settings bool parsing.
    """
    return os.environ.get(name, "").strip().lower() in _TRUE_VALUES


def _tls_enabled() -> bool:
    """HSTS only when the deployment says it serves over TLS."""
    return _env_flag("HARNESS_TLS_ENABLED")


def csp_enforce_enabled() -> bool:
    """True only when HARNESS_CSP_ENFORCE is explicitly truthy.

    Default OFF: the stack ships report-only until an operator opts into
    enforcement (M15 hard rule — enforce-mode OFF by default).
    """
    return _env_flag(CSP_ENFORCE_ENV)


# Compose passes JSON arrays through verbatim, so the documented shape is a
# JSON array; a bare comma list is accepted as a courtesy.
CSP_EXTRA_ORIGINS_ENV = "HARNESS_CSP_EXTRA_ORIGINS"


def _origin_like(candidate: str) -> bool:
    """Accept only scheme-prefixed origin strings (scheme://host[:port]).

    CSP connect-src entries that are not keywords/schemes are origin strings;
    anything else here is garbage input, and letting it through would put
    attacker-influenced junk into a security header.
    """
    from urllib.parse import urlsplit

    parts = urlsplit(candidate)
    return bool(parts.scheme) and bool(parts.netloc)


def extra_api_origins_from_env(env: str | None = None) -> tuple[str, ...]:
    """Parse HARNESS_CSP_EXTRA_ORIGINS for deployments with non-default APIs.

    Accepts a JSON array (compose convention) or comma-separated origins.
    Garbage never breaks boot: malformed input logs a warning and yields only
    the parseable subset (empty when nothing parses). Wildcards are refused —
    CSP sources are exact-match strings, so "*" would silently disable the
    connect-src allowlist it rides in.
    """
    raw = os.environ.get(env or CSP_EXTRA_ORIGINS_ENV, "")
    if not raw.strip():
        return ()
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            parsed = [item.strip() for item in str(raw).split(",")]
    except ValueError:
        parsed = [item.strip() for item in raw.split(",")]
    origins: list[str] = []
    for item in parsed:
        origin = str(item).strip()
        if not origin or origin == "*" or not _origin_like(origin):
            if origin not in ("", "*"):
                logger.warning(
                    "%s: refusing non-origin value %r", CSP_EXTRA_ORIGINS_ENV, origin[:40]
                )
            continue
        if origin not in origins:
            origins.append(origin)
    if "*" in raw:
        logger.warning(
            "%s: wildcard refused; listing explicit origins only", CSP_EXTRA_ORIGINS_ENV
        )
    return tuple(origins)


def build_csp_policy(api_origins: list[str] | tuple[str, ...] | None = None) -> str:
    """Build the harness CSP string.

    Directives mirror section7's audited needs (module docstring);
    ``api_origins`` EXTENDS — never shrinks — the shipped connect-src set so
    a custom deployment (different ports, a reverse-proxy host) widens only
    the network directives. Wildcards never pass this door.
    """
    extra: list[str] = []
    for origin in api_origins or ():
        cleaned = str(origin).strip()
        if cleaned and cleaned != "*" and cleaned not in extra:
            extra.append(cleaned)
    origins = [*DEFAULT_API_ORIGINS, *extra]
    return (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self'; "
        f"connect-src 'self' {' '.join(origins)} ws: wss:; "
        "frame-ancestors 'none'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )


# Compose-time snapshot of the shipped default (handy for docs/tests; the
# middleware builds its own copy so flag flips never need a module reload).
DEFAULT_CSP_POLICY = build_csp_policy()


class SecurityHeadersMiddleware:
    """Wrap the ASGI callable directly and stamp headers onto http responses."""

    def __init__(
        self,
        app,  # noqa: ANN001 — pure ASGI signature
        *,
        report_uri: str = CSP_REPORT_PATH,
        api_origins: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self.app = app
        if api_origins is None:
            api_origins = extra_api_origins_from_env()
        self._policy = build_csp_policy(api_origins)
        # report-uri rides along in BOTH modes: during report-only rollout it
        # is the observability feed, and after the enforce flip operators keep
        # seeing what gets blocked instead of flying blind.
        self._csp_value = f"{self._policy}; report-uri {report_uri}".encode()

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        tls_on = _tls_enabled()
        # Header choice made once per request (env may flip between requests).
        csp_header_name = (
            CSP_ENFORCE_HEADER if csp_enforce_enabled() else CSP_REPORT_ONLY_HEADER
        ).encode()

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


def install_security_headers(
    app,
    *,
    report_uri: str = CSP_REPORT_PATH,
    api_origins: list[str] | tuple[str, ...] | None = None,
):
    """Register the middleware stack entry and return the class.

    Safe to call exactly once per app at factory time, BEFORE the app builds
    its middleware stack (i.e. before startup), same constraint as
    add_middleware. With the default options the plain class registers
    (byte-compatible with Wave9-E wiring); passing options swaps in a small
    factory that forwards them.
    """
    if report_uri == CSP_REPORT_PATH and api_origins is None:
        app.add_middleware(SecurityHeadersMiddleware)
        return SecurityHeadersMiddleware

    def _factory(inner):  # noqa: ANN001 — pure ASGI signature
        return SecurityHeadersMiddleware(inner, report_uri=report_uri, api_origins=api_origins)

    app.add_middleware(_factory)
    return SecurityHeadersMiddleware
