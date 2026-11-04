"""W15 slice 2 — the limiter hooks must be INSTALLED, not just importable.

The S3 and S6 hook modules shipped long ago, but neither service factory ever
called them, so `/models/call` and `/meetings/{id}/brief` answered unthrottled
in the deployed stack. This test pins the assembly wiring itself: the hook call
must live inside the factory, and the factory must give the middleware a
session store (otherwise the limiter resolves `app.state.session_factory` at
first request and raises).
"""

from pathlib import Path

SERVICES = Path(__file__).resolve().parents[2] / "deploy" / "runners" / "services.py"


def _factory_body(source: str, name: str) -> str:
    start = source.index(f"def {name}(")
    following = source.find("\ndef app_", start + 1)
    return source[start:] if following == -1 else source[start:following]


def test_models_app_installs_the_router_limiter_with_a_store():
    body = _factory_body(SERVICES.read_text(encoding="utf-8"), "app_models")
    assert "install_router_rate_limit(" in body
    assert "app.state.session_factory" in body


def test_meeting_app_installs_the_meeting_limiter_with_a_store():
    body = _factory_body(SERVICES.read_text(encoding="utf-8"), "app_meeting")
    assert "install_meeting_rate_limit(" in body
    assert "session_factory=" in body
