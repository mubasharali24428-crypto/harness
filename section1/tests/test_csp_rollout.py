"""M15-W5-S1 tests: CSP rollout ladder + violation collector.

Node S1 owns ONLY this file (conftest.py frozen this wave) — every fixture is
defined locally. Four concerns:

1. Rollout ladder through the real identity app: REPORT-ONLY header ships by
   default; HARNESS_CSP_ENFORCE=true swaps to the enforcing header (same
   policy); JSON responses carry neither.
2. Policy coverage proof: build_csp_policy output covers every resource
   pattern section7 actually loads (self scripts/styles, React inline style
   attributes, data:/blob: images, the six local section API origins, ws:/wss:
   event stream) AND stays restrictive (injected scripts, foreign APIs,
   plugins fail the allowlist).
3. Collector: accepts real browser csp-report bodies (unauthed, 204), logs
   ONE allow-listed bounded line per violation, drops junk/oversize payloads,
   and leaks no PII/secrets into logs.
4. Rate limiting: the documented DB-backed limiter caps the unauth endpoint.
"""

import json
import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness_identity.app import make_app
from harness_identity.config import Settings
from harness_identity.csp_report import summarize_csp_report
from harness_identity.models import Base
from harness_identity.ratelimit import install_rate_limit
from harness_identity.security_headers import (
    CSP_ENFORCE_ENV,
    CSP_ENFORCE_HEADER,
    CSP_EXTRA_ORIGINS_ENV,
    CSP_REPORT_ONLY_HEADER,
    CSP_REPORT_PATH,
    DEFAULT_API_ORIGINS,
    SecurityHeadersMiddleware,
    build_csp_policy,
    csp_enforce_enabled,
    extra_api_origins_from_env,
)

MASTER = b"test-master-key-0123456789abcdef"
DOC_ORIGIN = "http://localhost:3000"


def _settings(**kw) -> Settings:
    return Settings(master_key=MASTER, cors_origins=[], **kw)


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def client(db):
    app = make_app(db, _settings())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.app = app
        yield c


async def _html_probe(client: AsyncClient):
    """Register (idempotently) and hit an HTML probe route; return its headers."""
    if not any(getattr(r, "path", None) == "/m15-html-probe" for r in client.app.routes):

        from fastapi.responses import HTMLResponse

        @client.app.get("/m15-html-probe")
        async def m15_html_probe():  # noqa: ANN202 — trivial probe
            return HTMLResponse("<html><body>m15-s1</body></html>")

    return (await client.get("/m15-html-probe")).headers


# ---------------------------------------------------------------------------
# 1. Rollout ladder: report-only by default, enforce behind the flag
# ---------------------------------------------------------------------------


async def test_default_ships_report_only_never_enforce(client):
    headers = await _html_probe(client)
    ro = headers[CSP_REPORT_ONLY_HEADER]  # KeyError would fail the test
    assert ro.startswith("default-src 'self'")
    assert CSP_ENFORCE_HEADER not in headers  # enforcing header MUST be absent
    # Wave9-E pins survive the M15 upgrade:
    assert "frame-ancestors 'none'" in ro
    assert "object-src 'none'" in ro


@pytest.mark.parametrize("value", ["true", "1", "YES", "on"])
async def test_enforce_flag_swaps_to_enforcing_header(client, monkeypatch, value):
    monkeypatch.setenv(CSP_ENFORCE_ENV, value)
    assert csp_enforce_enabled() is True
    headers = await _html_probe(client)
    enforced = headers[CSP_ENFORCE_HEADER]
    assert enforced.startswith("default-src 'self'")
    assert CSP_REPORT_ONLY_HEADER not in headers
    # SAME policy either side of the ladder — flipping the flag must not
    # silently change what the browser evaluates.
    monkeypatch.delenv(CSP_ENFORCE_ENV)
    assert (await _html_probe(client))[CSP_REPORT_ONLY_HEADER] == enforced


@pytest.mark.parametrize("value", ["0", "false", "", "off"])
async def test_falsy_enforce_values_stay_report_only(client, monkeypatch, value):
    monkeypatch.setenv(CSP_ENFORCE_ENV, value)
    headers = await _html_probe(client)
    assert CSP_REPORT_ONLY_HEADER in headers
    assert CSP_ENFORCE_HEADER not in headers


async def test_json_api_responses_carry_no_csp_header(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert CSP_REPORT_ONLY_HEADER not in r.headers
    assert CSP_ENFORCE_HEADER not in r.headers


# ---------------------------------------------------------------------------
# 2. Policy covers section7's real resource patterns — and stays restrictive
# ---------------------------------------------------------------------------


def _directives(policy: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for chunk in policy.split(";"):
        bits = chunk.strip().split()
        if bits:
            out[bits[0]] = bits[1:]
    return out


def _covered(directives: dict[str, list[str]], directive: str, url: str) -> bool:
    """Minimal CSP source-expression match: keywords, scheme sources, and
    origin-prefix sources — exactly enough to prove the audit claims."""
    sources = directives.get(directive) or []
    for src in sources:
        if src == "'self'" and url.startswith(DOC_ORIGIN + "/"):
            return True
        if src.startswith("'"):
            continue
        if src.endswith(":"):  # scheme-part source: data:, blob:, ws:, wss:
            if url.startswith(src):
                return True
        elif url.startswith(f"{src}/") or url == src:
            return True
    return False


def test_policy_covers_every_legit_section7_load():
    policy = build_csp_policy()
    d = _directives(policy)

    legit = [
        # Next.js bundles + css assets served same-origin (section7/app/**).
        ("script-src", f"{DOC_ORIGIN}/_next/static/chunks/main-app-a1b2.js"),
        ("style-src", f"{DOC_ORIGIN}/_next/static/css/globals-99.css"),
        # Token-driven CSS-in-JS images: inline data URIs + blob canvases.
        ("img-src", "data:image/svg+xml;base64,PHN2Zy8+"),
        ("img-src", "blob:http://localhost:3000/8f14e45f"),
        ("img-src", f"{DOC_ORIGIN}/favicon.ico"),
        # Web fonts stay same-origin (globals.css uses system stacks today).
        ("font-src", f"{DOC_ORIGIN}/_next/static/media/inter.woff2"),
        # Cross-port API calls to every local section base (src/lib/api.ts).
        *[
            ("connect-src", f"{origin}/api/v1/anything")
            for origin in DEFAULT_API_ORIGINS
        ],
        # Event-stream upgrade (useEventStream -> ws://identity/…).
    ]
    ws_legit = [
        ("connect-src", "ws://localhost:8001/api/v1/ws/proj-1?afterSeq=5"),
        ("connect-src", "wss://harness.example.com/api/v1/ws/proj-1"),
    ]
    blocked = [
        ("script-src", "https://evil.example/inject.js"),  # injected script tag
        # Same-origin upload dir: 'self' covers the WHOLE origin by design —
        # listed to document that boundary explicitly (not an accident).
        ("self-covers-uploads-by-design", f"{DOC_ORIGIN}/uploads/user.js"),
        ("connect-src", "http://localhost:9999/exfil"),
        ("connect-src", "https://telemetry.thirdparty.dev/collect"),
    ]

    for directive, url in legit:
        assert _covered(d, directive, url), f"LEGIT load would VIOLATE: {directive} {url}"
    # ws:/wss: scheme sources cover the event stream regardless of port/host.
    for directive, url in ws_legit:
        assert _covered(d, directive, url), f"LEGIT stream would VIOLATE: {directive} {url}"
    assert _covered(d, "connect-src", "ws://localhost:8001/api/v1/ws/p")
    assert _covered(d, "connect-src", "wss://any.host/api/v1/ws/p")
    for directive, url in blocked[:1] + blocked[2:]:
        assert not _covered(d, directive, url), f"BLOCKED load passes?! {directive} {url}"

    # Keyword-level guarantees the URL checker cannot express:
    assert "'unsafe-inline'" in d["style-src"]  # React style attributes pass
    assert "'unsafe-inline'" not in d["script-src"]
    assert "'unsafe-eval'" not in d["script-src"]
    assert d["object-src"] == ["'none'"]
    assert d["frame-ancestors"] == ["'none'"]
    assert d["base-uri"] == ["'self'"]
    assert d["form-action"] == ["'self'"]
    # Every local section API origin is listed verbatim.
    joined = " ".join(d["connect-src"])
    for origin in DEFAULT_API_ORIGINS:
        assert origin in joined


def test_policy_carries_report_uri_through_middleware_value():
    mw = SecurityHeadersMiddleware(_noop_app())
    assert f"report-uri {CSP_REPORT_PATH}" in mw._csp_value.decode()


def test_extra_origins_extend_connect_src_only():
    policy = build_csp_policy(["https://api.corp.example"])
    assert "https://api.corp.example" in _directives(policy)["connect-src"]
    # Widening must never shrink: defaults all survive.
    for origin in DEFAULT_API_ORIGINS:
        assert origin in policy
    # Wildcards are refused outright.
    assert "*" not in build_csp_policy(["*", "https://ok.example"])


def test_extra_origins_env_parsing(monkeypatch):
    monkeypatch.delenv(CSP_EXTRA_ORIGINS_ENV, raising=False)
    assert extra_api_origins_from_env() == ()
    monkeypatch.setenv(CSP_EXTRA_ORIGINS_ENV, '["https://a.example","https://b.example"]')
    assert extra_api_origins_from_env() == ("https://a.example", "https://b.example")
    monkeypatch.setenv(CSP_EXTRA_ORIGINS_ENV, "https://c.example, https://d.example")
    assert extra_api_origins_from_env() == ("https://c.example", "https://d.example")
    monkeypatch.setenv(CSP_EXTRA_ORIGINS_ENV, "{not json")
    assert extra_api_origins_from_env() == ()  # garbage degrades, never boots down
    monkeypatch.setenv(CSP_EXTRA_ORIGINS_ENV, '["*","https://keep.example"]')
    assert extra_api_origins_from_env() == ("https://keep.example",)  # wildcard dropped


def test_middleware_picks_up_extra_origins_from_env(monkeypatch):
    monkeypatch.setenv(CSP_EXTRA_ORIGINS_ENV, '["https://x.example"]')
    mw = SecurityHeadersMiddleware(_noop_app())
    assert b"https://x.example" in mw._csp_value


# ---------------------------------------------------------------------------
# 3. /api/v1/csp-report collector
# ---------------------------------------------------------------------------

CANARY_POLICY_SECRET = "original-policy-canary-do-not-log"


def _browser_report(**overrides) -> dict:
    """A violation body shaped like Chrome/Firefox actually send it."""
    report = {
        "document-uri": f"{DOC_ORIGIN}/meeting",
        "referrer": "",
        "violated-directive": "script-src-elem",
        "effectiveDirective": "script-src-elem",
        "originalPolicy": CANARY_POLICY_SECRET,
        "disposition": "report",
        "blockedURL": "https://evil.example/inject.js",
        "lineNumber": 42,
        "sourceFile": f"{DOC_ORIGIN}/_next/static/chunks/main-app.js",
        "statusCode": 200,
    }
    report.update(overrides)
    return {"csp-report": report}


async def test_collector_accepts_real_reports_unauthed_and_logs_them(client, caplog):
    with caplog.at_level(logging.INFO, logger="harness.csp"):
        r = await client.post("/api/v1/csp-report", json=_browser_report())
    assert r.status_code == 204
    assert r.content == b""  # 204 No Content — nothing to say back to a browser
    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "script-src-elem" in text
    assert "evil.example/inject.js" in text
    assert "/meeting" in text


async def test_collector_logs_no_pii_and_no_policy_echo(client, caplog):
    sneaky = _browser_report(
        documentUri=f"{DOC_ORIGIN}/runs?token=SUPER-SECRET-TOKEN",
    )
    sneaky["csp-report"]["authHeader"] = "Bearer leaked-bearer-token"
    sneaky["attackerKey"] = "top-level-junk"
    with caplog.at_level(logging.DEBUG, logger="harness.csp"):
        r = await client.post("/api/v1/csp-report", json=sneaky)
    assert r.status_code == 204
    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "SUPER-SECRET-TOKEN" not in text  # query strings stripped
    assert "leaked-bearer-token" not in text  # unknown fields never echoed
    assert CANARY_POLICY_SECRET not in text  # full policy never echoed
    assert "top-level-junk" not in text


async def test_collector_drops_junk_and_oversize_bodies_silently(client, caplog):
    too_big = {"csp-report": {"blockedURL": "x" * 5000}}
    with caplog.at_level(logging.DEBUG, logger="harness.csp"):
        for payload in (
            None,  # empty body
            too_big,  # over cap
            "just a string",  # wrong shape entirely
            {"not": "csp-report"},
        ):
            r = await client.post(
                "/api/v1/csp-report",
                content=json.dumps(payload).encode() if payload is not None else b"",
                headers={"content-type": "application/json"},
            )
            assert r.status_code == 204
    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "CSP violation:" not in text  # nothing got summarized as a violation
    # Undecodable/odd shapes may warn, but never echo attacker bytes wholesale.
    assert "xxxx" not in text


def test_summarizer_is_bounded_and_allowlisted():
    huge = {"effectiveDirective": "d" * 10_000, "blockedURL": None}
    line = summarize_csp_report(huge)
    assert len(line) < 400  # bounded even for adversarial input
    assert summarize_csp_report({}).startswith("directive=unknown")


# ---------------------------------------------------------------------------
# 4. Rate limiting the unauthed collector (DB-backed, same store as auth edge)
# ---------------------------------------------------------------------------


def _noop_app():  # pragma: no cover — placeholder inner ASGI app
    async def app(scope, receive, send):  # noqa: ANN001
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    return app


# Mirrors migrations/0011_rate_buckets.sql — rate_buckets is NOT in the ORM
# metadata, so limiter tests must create it explicitly (same as
# tests/test_ratelimit.py; SQLite accepts the identical DDL).
RATE_BUCKETS_DDL = """
CREATE TABLE rate_buckets (
    bucket_key VARCHAR(200) PRIMARY KEY,
    window_start TIMESTAMPTZ NOT NULL,
    count INTEGER NOT NULL DEFAULT 0
)
"""


@pytest.fixture
async def rl_db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        from sqlalchemy import text

        await conn.execute(text(RATE_BUCKETS_DDL))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_collector_is_rate_limited_when_installed(rl_db):
    from harness_identity import ratelimit as rl
    from harness_identity.csp_report import router as csp_router

    # The collector ships INSIDE the default protected set — pin the contract,
    # then prove the throttle functionally with the shipped budgets (30/min).
    assert rl.CSP_REPORT_TEMPLATE in rl.DEFAULT_PROTECTED_ROUTES
    assert rl.EXPENSIVE_OP_LIMITS[rl.CSP_REPORT_TEMPLATE] == rl.CSP_REPORT_LIMIT

    app = FastAPI()
    app.include_router(csp_router)
    install_rate_limit(app, session_factory=rl_db)  # shipped defaults
    transport = ASGITransport(app=app)
    statuses = []
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        for _ in range(rl.CSP_REPORT_LIMIT + 1):
            r = await c.post("/api/v1/csp-report", json=_browser_report())
            statuses.append(r.status_code)
    assert statuses[: rl.CSP_REPORT_LIMIT] == [204] * rl.CSP_REPORT_LIMIT
    assert statuses[-1] == 429  # budget+1 -> fixed-window rejection


# ---------------------------------------------------------------------------
# Wiring sanity: the collector rides the shipped app; uuid keeps imports used
# ---------------------------------------------------------------------------


async def test_shipped_app_serves_the_collector(db):
    # NOTE: this FastAPI wraps include_router() results in opaque
    # _IncludedRouter objects (no .path), so wiring is proven FUNCTIONALLY:
    # an unauthed browser-shaped report must reach the handler (204), not
    # fall off the app as a 404.
    app = make_app(db, _settings())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/v1/csp-report", json=_browser_report())
    assert r.status_code == 204
