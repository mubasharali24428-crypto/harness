"""W23 F2: ``harness doctor --probe`` sends one paid request, and says exactly what it learned.

The sender is injected, so every provider answer is a plain tuple; the route cases use a real
local HTTP server so the wire format that leaves the process is the one that is asserted.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from harness_contracts.roles import Role

from harness_orchestrator import readiness

from .test_readiness import KEY, LIVE_ENV, _client
from .test_readiness import db_service as db_service  # noqa: F401  (fixture)

ANTHROPIC_ENV = {k: v for k, v in LIVE_ENV.items() if k != "DSH_PATCH_FILES"} | {
    "DEEPSEEK_BASE_URL": "https://api.example-anthropic.test/anthropic"
}
OK_BODY = json.dumps({"choices": [{"message": {"content": "p"}}],
                      "usage": {"prompt_tokens": 9, "completion_tokens": 1}}).encode()


@pytest.fixture(autouse=True)
def fresh_spend_window(monkeypatch):
    monkeypatch.setattr(readiness, "_last_probe", None)


class Sender:
    def __init__(self, status=200, body=OK_BODY, error=None):
        self.status, self.body, self.error, self.calls = status, body, error, []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, body, timeout))
        if self.error:
            raise self.error
        return self.status, self.body


def probe(env=LIVE_ENV, sender=None, **kw):
    sender = sender or Sender()
    return readiness.probe_provider(env, send=sender, **kw), sender


def test_a_patched_engine_is_probed_in_the_openai_format_with_one_token():
    entry, sender = probe()
    (url, headers, body, timeout), = sender.calls
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert headers["Authorization"] == f"Bearer {KEY}" and "x-api-key" not in headers
    assert body["model"] == "anthropic/claude-haiku-5.5" and body["max_tokens"] == 1
    assert entry["status"] == "ok" and entry["reason"] == "PROVIDER_ACCEPTED"
    assert "9 in / 1 out" in entry["detail"] and "not metered" in entry["detail"]
    assert KEY not in json.dumps(entry)
    assert timeout <= 30  # one request, no retry: a hung provider cannot hold the call open


def test_an_unpatched_engine_is_probed_in_the_anthropic_format():
    usage = {"input_tokens": 8, "output_tokens": 1}
    body = json.dumps({"content": [{"text": "p"}], "usage": usage})
    entry, sender = probe(ANTHROPIC_ENV, Sender(body=body.encode()))
    (url, headers, sent, _), = sender.calls
    assert url == "https://api.example-anthropic.test/anthropic/v1/messages"
    assert headers["x-api-key"] == KEY and headers["anthropic-version"]
    assert "Authorization" not in headers
    assert sent["max_tokens"] == 1 and entry["status"] == "ok"


def test_a_model_with_no_price_says_so_instead_of_showing_zero():
    entry, _ = probe({**LIVE_ENV, "DSH_MODEL": "someone/unlisted-model"})
    assert entry["status"] == "ok" and "cost unknown" in entry["detail"]
    assert "$0" not in entry["detail"]


@pytest.mark.parametrize("status, reason, outcome", [
    (401, "PROVIDER_REJECTED_KEY", "fail"),
    (403, "PROVIDER_REJECTED_KEY", "fail"),
    (402, "PROVIDER_NO_CREDIT", "fail"),
    (400, "MODEL_REJECTED", "fail"),
    (404, "MODEL_REJECTED", "fail"),
    (429, "PROVIDER_RATE_LIMITED", "unknown"),
    (503, "PROVIDER_ERROR", "unknown"),
])
def test_each_refusal_is_named_and_a_busy_provider_is_not_a_failure(status, reason, outcome):
    entry, _ = probe(sender=Sender(status, json.dumps({"error": {"message": "nope"}}).encode()))
    assert (entry["reason"], entry["status"]) == (reason, outcome)
    assert f"HTTP {status}" in entry["detail"] and '"nope"' in entry["detail"]


def test_a_provider_that_echoes_the_key_never_gets_it_into_the_report():
    echo = json.dumps({"error": {"message": f"bad key {KEY}"}}).encode()
    entry, _ = probe(sender=Sender(401, echo))
    assert KEY not in json.dumps(entry) and "[key]" in entry["detail"]


def test_an_unreachable_provider_is_unknown_not_failed_and_is_not_retried():
    entry, sender = probe(sender=Sender(error=TimeoutError("slow")))
    assert (entry["status"], entry["reason"]) == ("unknown", "PROVIDER_UNREACHABLE")
    assert len(sender.calls) == 1


def test_a_200_that_is_not_a_completion_is_not_a_pass():
    entry, _ = probe(sender=Sender(200, b'{"status": "maintenance"}'))
    assert (entry["status"], entry["reason"]) == ("unknown", "PROVIDER_ODD_RESPONSE")


def test_nothing_is_sent_unless_the_configuration_is_already_live():
    entry, sender = probe({**LIVE_ENV, "ENGINE_LIVE_MODE": "dry"})
    assert entry["reason"] == "NOT_CHECKED" and sender.calls == []
    entry, sender = probe({**LIVE_ENV, "DSH_PATCH_FILES": "/nope/missing.yml"})
    assert entry["reason"] == "NOT_CHECKED" and sender.calls == []  # a bad format is free to find


def test_a_second_probe_inside_the_minute_is_refused_before_anything_is_sent():
    clock = iter([100.0, 130.0, 161.0])
    first, _ = probe(clock=lambda: next(clock))
    sender = Sender()
    with pytest.raises(readiness.ProbeTooSoon) as soon:
        readiness.probe_provider(LIVE_ENV, send=sender, clock=lambda: next(clock))
    assert sender.calls == [] and soon.value.retry_after == 30
    again, _ = probe(sender=sender, clock=lambda: next(clock))
    assert first["status"] == again["status"] == "ok" and len(sender.calls) == 1


# ------------------------------------------------------------------------------- the route


class Provider:
    """A local stand-in for the provider: what it was asked, and where it is."""

    def __init__(self):
        self.seen, self.url = [], ""


@pytest.fixture()
def provider():
    stub = Provider()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            size = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(size))
            stub.seen.append((self.path, self.headers.get("Authorization"), body))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(OK_BODY)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stub.url = f"http://127.0.0.1:{server.server_address[1]}/api/v1"
    yield stub
    server.shutdown()


def test_the_route_probes_once_and_a_second_call_waits(monkeypatch, db_service, provider):  # noqa: F811
    client = _client(monkeypatch, db_service)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", provider.url)
    first = client.post("/api/v1/orchestrator/readiness/probe")
    assert first.status_code == 200 and first.json()["ok"] is True
    assert [c["check"] for c in first.json()["checks"]] == ["provider-call"]
    assert KEY not in first.text
    assert provider.seen[0][0] == "/api/v1/chat/completions"
    assert provider.seen[0][1] == f"Bearer {KEY}"
    second = client.post("/api/v1/orchestrator/readiness/probe")
    assert second.status_code == 429
    assert second.json()["detail"]["error"]["code"] == "PROBE_TOO_SOON"
    assert int(second.headers["Retry-After"]) >= 1 and len(provider.seen) == 1  # nothing was sent


def test_a_caller_who_may_not_start_runs_cannot_spend_on_the_provider(  # noqa: F811
    monkeypatch, db_service, provider
):
    client = _client(monkeypatch, db_service, role=Role.VIEWER)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", provider.url)
    refused = client.post("/api/v1/orchestrator/readiness/probe")
    assert refused.status_code == 403
    assert refused.json()["detail"]["error"]["code"] == "ROLE_DENIED"
    assert provider.seen == []
