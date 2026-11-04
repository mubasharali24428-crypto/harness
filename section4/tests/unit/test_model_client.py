"""The reviewer's model call goes to the models service as the caller, and an outage degrades the
review to its deterministic verdict instead of failing the done signal."""

import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from harness_contracts.signatures import ModelResult, ModelRole

from harness_orchestrator.model_client import ModelsServiceRouter
from harness_orchestrator.reviewer import ReviewerSubAgent

SEEN: list[dict] = []


class _Stub(BaseHTTPRequestHandler):
    def _reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        SEEN.append({"path": self.path, "auth": self.headers.get("authorization"), "body": body})
        if self.path == "/api/v1/models/call":
            if body.get("agentId") == "broken":
                return self._reply(503, {"error": {"code": "UPSTREAM"}})
            return self._reply(
                200,
                {
                    "text": "Looks fine.",
                    "costUsd": 0.001,
                    "model": "openai/gpt-4o-mini",
                    "latencyMs": 9,
                },
            )
        if self.path == "/api/v1/models/estimate-cost":
            return self._reply(200, {"estimatedCostUsd": 0.0042})
        self._reply(404, {"error": {"code": "NOT_FOUND"}})

    def log_message(self, *args):
        return


@pytest.fixture()
def models_url():
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    SEEN.clear()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_call_model_posts_as_the_caller_and_returns_the_result(models_url):
    router = ModelsServiceRouter("harn_caller_token", base_url=models_url)
    result = router.call_model(
        agent_id="reviewer",
        role=ModelRole.REVIEWER,
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result == ModelResult(
        text="Looks fine.", costUsd=0.001, model="openai/gpt-4o-mini", latencyMs=9
    )
    sent = SEEN[-1]
    assert sent["path"] == "/api/v1/models/call"
    assert sent["auth"] == "Bearer harn_caller_token"  # the caller's token, never a service one
    assert sent["body"]["role"] == "reviewer"


def test_estimate_cost_reads_the_priced_figure(models_url):
    router = ModelsServiceRouter("harn_caller_token", base_url=models_url)
    assert router.estimate_cost(ModelRole.REVIEWER, 4096, 512) == pytest.approx(0.0042)


def test_a_failing_models_service_raises_for_the_reviewer_to_handle(models_url):
    router = ModelsServiceRouter("harn_caller_token", base_url=models_url)
    with pytest.raises(urllib.error.HTTPError):
        router.call_model(agent_id="broken", role=ModelRole.REVIEWER, messages=[])


def test_reviewer_uses_the_models_service_summary_when_it_answers(models_url):
    router = ModelsServiceRouter("harn_caller_token", base_url=models_url)
    findings = ReviewerSubAgent(model_router=router).evaluate_changes(
        brief_summary="add a function", diff_text="+x = 1\n"
    )
    assert findings.summary == "Looks fine."
    assert findings.modelUsed == "openai/gpt-4o-mini"


def test_reviewer_marks_an_unavailable_model_without_failing_done():
    class Down:
        def call_model(self, **kwargs):
            raise ConnectionError("models service unreachable")

    findings = ReviewerSubAgent(model_router=Down()).evaluate_changes(
        brief_summary="add a function", diff_text="+x = 1\n"
    )
    assert findings.passed is True
    assert findings.summary == "Review passed cleanly."
    assert findings.modelUsed is None
    assert "model_review_unavailable: ConnectionError" in findings.issues
