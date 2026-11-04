"""Readiness must not depend on egress — and must not pretend it does.

A run that never touches the network is executable while the allow-list proxy
is down, so /ready gates on db + migrations only. But "ready" must not be read
as "network reachable", so the proxy state is reported as an informational
field instead of being assumed. These tests pin both halves: the probe's
verdicts, and the rule that the verdict can never enter the failure reasons.
"""

import socket
import subprocess
import sys
from pathlib import Path

SERVICES_DIR = Path(__file__).resolve().parents[3] / "deploy" / "runners"
SERVICES_PY = SERVICES_DIR / "services.py"

_PROBE = (
    "import asyncio, json, services;"
    " print(json.dumps(asyncio.run(services._egress_state())))"
)


def _state(proxy_url: str | None) -> str:
    env = {
        "HARNESS_ALLOW_DEV_MASTER_KEY": "1",
        "PATH": "/usr/bin:/bin",
    }
    if proxy_url is not None:
        env["SANDBOX_EGRESS_PROXY_URL"] = proxy_url
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=SERVICES_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip().strip('"')


def test_no_pairing_means_no_field_at_all():
    assert _state(None) == "null"


def test_unreachable_proxy_reports_down():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # the socket is closed now: connecting must fail fast, not hang
    assert _state(f"http://127.0.0.1:{port}") == "down"


def test_unparseable_port_reports_down():
    assert _state("http://sandbox-egress:not-a-port") == "down"


def test_listening_proxy_reports_up():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert _state(f"http://127.0.0.1:{port}") == "up"


def test_egress_is_never_a_readiness_failure_reason():
    source = SERVICES_PY.read_text(encoding="utf-8")
    assert "reasons.append(\"egress" not in source
    assert "reasons.append('egress" not in source
    # and the probe result is only ever attached to a successful body
    assert 'body["egress"] = egress' in source


def test_only_the_sandbox_app_reports_egress():
    """Every backend container receives SANDBOX_EGRESS_PROXY_URL, so the env
    cannot tell us who uses the proxy: identity reported `egress: up` live
    before this was scoped to the app that owns the runner."""
    source = SERVICES_PY.read_text(encoding="utf-8")
    reporting = [
        line.strip()
        for line in source.splitlines()
        if "_install_probes(app," in line and "reports_egress=True" in line
    ]
    assert reporting == ["_install_probes(app, 8005, reports_egress=True)"]
    assert "egress = await _egress_state() if reports_egress else None" in source
