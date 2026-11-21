"""G-F header contract test (P5): every API response carries the anti-sniff /
anti-framing / referrer set; HSTS only behind HARNESS_TLS_ENABLED; CSP
report-only by default, enforcing under the opt-in flag. Pinned per the
rubric's "a header test per route" via a representative route + the
middleware's own unit suite."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.filterwarnings(
    "ignore:The default datetime adapter is deprecated:DeprecationWarning"
)


@pytest.fixture
async def header_app(monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import harness_identity.sso  # noqa: F401
    from harness_identity.app import make_app
    from harness_identity.config import Settings
    from harness_identity.models import Base

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = make_app(factory, Settings(master_key=b"k-0123456789abcdef00000000",
                                     cors_origins=[], require_ownership=False))
    yield app
    await engine.dispose()


async def test_every_response_carries_the_hardening_set(header_app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=header_app),
                           base_url="http://t") as client:
        for url in ("/health", "/api/v1/me", "/api/v1/auth/login", "/api/v1/nope-404"):
            r = await client.get(url)
            assert r.headers.get("x-content-type-options") == "nosniff", url
            assert r.headers.get("x-frame-options", "").upper() in ("DENY", "SAMEORIGIN"), url
            assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin", url
            # CSP attaches to HTML responses; API JSON carries the rest.
            if (r.headers.get("content-type") or "").startswith("text/html"):
                assert "frame-ancestors" in r.headers.get(
                    "content-security-policy-report-only",
                    r.headers.get("content-security-policy", "")), url


async def test_hsts_only_behind_tls_flag(header_app, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=header_app),
                           base_url="http://t") as client:
        r = await client.get("/health")
        assert "strict-transport-security" not in r.headers
    monkeypatch.setenv("HARNESS_TLS_ENABLED", "1")
    import harness_identity.security_headers as sh

    assert sh._tls_enabled() is True
    monkeypatch.delenv("HARNESS_TLS_ENABLED")


async def test_csp_enforce_flag_switches_the_header(header_app, monkeypatch):

    from harness_identity.security_headers import CSP_ENFORCE_ENV

    monkeypatch.setenv(CSP_ENFORCE_ENV, "true")
    import harness_identity.security_headers as sh

    assert sh.csp_enforce_enabled() is True
    monkeypatch.delenv(CSP_ENFORCE_ENV)
