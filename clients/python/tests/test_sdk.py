"""Unit tests: envelope parsing, retries/backoff, WS frame logic, generator.

No network and no running stack required — the HTTP layer runs against
``httpx.MockTransport``, the WS lifecycle against a fake socket.
"""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

from harness_sdk import ApiError, Harness, HarnessHTTP, parse_api_error
from harness_sdk.runtime import backoff_ceiling
from harness_sdk.ws import WsEventStream, _ws_connect_kwargs


# ---------------------------------------------------------------------------
# Error envelope parsing (extraction of section7 api.ts parseApiError)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "status,body,expected_code,expected_msg",
    [
        # 1. direct contract envelope
        (403, {"error": {"code": "OWNERSHIP_REQUIRED", "message": "prove it"}},
         "OWNERSHIP_REQUIRED", "prove it"),
        # 2. FastAPI-wrapped envelope (both shapes ship today — verified live)
        (403, {"error": {"code": "X", "message": "y"},
               "detail": {"error": {"code": "X", "message": "y"}}},
         "X", "y"),
        (404, {"detail": {"error": {"code": "NOT_FOUND", "message": "nope"}}},
         "NOT_FOUND", "nope"),
        # 3. plain string detail
        (400, {"detail": "bad body"}, "UNKNOWN", "bad body"),
        # 4. junk falls through to generic
        (500, None, "UNKNOWN", "Request failed with status 500"),
        (500, "not a dict", "UNKNOWN", "Request failed with status 500"),
        (500, {}, "UNKNOWN", "Request failed with status 500"),
    ],
)
def test_parse_api_error(status, body, expected_code, expected_msg):
    code, message = parse_api_error(status, body)
    assert code == expected_code
    assert message == expected_msg


def test_missing_fields_default():
    code, msg = parse_api_error(418, {"error": {}})
    assert code == "UNKNOWN"
    assert msg == "Request failed with status 418"


# ---------------------------------------------------------------------------
# HTTP transport over httpx.MockTransport
# ---------------------------------------------------------------------------

def make_client(handler, token: str | None = "tok", **kw) -> HarnessHTTP:
    return HarnessHTTP(token, transport=httpx.MockTransport(handler), **kw)


def test_success_json_and_auth_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    c = make_client(handler)
    assert c.request("http://x", "GET", "/api/v1/me") == {"ok": True}
    assert seen["auth"] == "Bearer tok"


def test_no_auth_flag_suppresses_bearer():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={})

    c = make_client(handler, token=None)
    c.token = None
    c.request("http://x", "POST", "/api/v1/auth/login", json_body={}, auth=False)
    assert seen["auth"] is None


def test_error_envelope_becomes_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"code": "ROLE_DENIED", "message": "no"}},
        )

    c = make_client(handler)
    with pytest.raises(ApiError) as ei:
        c.request("http://x", "GET", "/api/v1/projects")
    err = ei.value
    assert err.code == "ROLE_DENIED"
    assert err.status == 403
    assert str(err) == "no"


def test_none_query_params_are_dropped():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={})

    c = make_client(handler)
    c.request("http://x", "GET", "/v", params={"a": 1, "b": None})
    assert "a=1" in seen["url"] and "b=" not in seen["url"]


def test_retry_on_503_then_success():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"detail": "down"})
        return httpx.Response(200, json={"ok": True})

    c = make_client(handler, backoff_base_s=0.01, backoff_cap_s=0.02)
    assert c.request("http://x", "GET", "/v") == {"ok": True}
    assert calls["n"] == 2


def test_post_not_retried_on_503():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"detail": "down"})

    c = make_client(handler, backoff_base_s=0.01, backoff_cap_s=0.02)
    with pytest.raises(ApiError):
        c.request("http://x", "POST", "/v")
    assert calls["n"] == 1


def test_429_retries_non_idempotent_post_and_honors_retry_after():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"retry-after": "0"},
                                  json={"error": {"code": "RATE_LIMITED",
                                                  "message": "slow down"}})
        return httpx.Response(204)

    c = make_client(handler)
    assert c.request("http://x", "POST", "/v") is None
    assert calls["n"] == 3


def test_transport_error_wrapped_as_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    c = make_client(handler, max_retries=1, backoff_base_s=0.01)
    with pytest.raises(ApiError) as ei:
        c.request("http://x", "GET", "/v")
    assert ei.value.code == "TRANSPORT_ERROR"


def test_text_plain_response_returns_str():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="diff --git a/x b/x",
                              headers={"content-type": "text/plain; charset=utf-8"})

    c = make_client(handler)
    out = c.request("http://x", "GET", "/diff")
    assert isinstance(out, str) and out.startswith("diff --git")


def test_backoff_ceiling_sequence():
    # Extraction pins backoff.ts exponentialDelay: 1s, 2s, 4s ... cap 30s
    assert [backoff_ceiling(n) for n in range(1, 7)] == [1, 2, 4, 8, 16, 30]
    assert backoff_ceiling(20) == 30.0
    assert backoff_ceiling(0) == 0.0


# ---------------------------------------------------------------------------
# WS frame handling + dedupe (extraction of useEventStream handleMessage/mergeEvents)
# ---------------------------------------------------------------------------

def make_stream() -> WsEventStream:
    http = HarnessHTTP(None, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={})))
    return WsEventStream(http)


def test_frame_valid_envelope_passes():
    s = make_stream()
    frame = s._parse_frame('{"seq": 5, "type": "RUN_STARTED"}')
    assert frame is not None and frame["seq"] == 5


def test_frame_malformed_dropped_not_fatal():
    s = make_stream()
    assert s._parse_frame("not json{") is None
    assert s._parse_frame('["array","frame"]') is None
    assert s._parse_frame('{"type": "no-seq"}') is None
    assert s._parse_frame('{"seq": "five"}') is None


def test_replay_truncated_marker_recognized():
    s = make_stream()
    frame = s._parse_frame('{"replayTruncated": true, "oldestSeqSent": 12}')
    assert frame is not None and "replayTruncated" in frame


def test_dedupe_and_ordering_via_seq():
    s = make_stream()
    assert s._accept_frame({"seq": 3}) is True
    assert s._accept_frame({"seq": 5}) is True
    assert s._accept_frame({"seq": 5}) is False      # duplicate dropped
    assert s._accept_frame({"seq": 4}) is False      # stale dropped
    assert s.last_seq == 5
    assert s._accept_frame({"replayTruncated": True}) is True  # control frames pass


def test_ws_connect_kwargs_supported_names():
    kw = _ws_connect_kwargs({"user-agent": "x"})
    assert ("additional_headers" in kw) ^ ("extra_headers" in kw)
    assert list(kw.values())[0] == {"user-agent": "x"}


def test_ws_upgrade_url_and_headers(monkeypatch):
    """The upgrade rides ?ticket= (never the bearer token) on a ws:// URL,
    presents X-WS-Fingerprint when mint echoed one, and pins the SDK UA."""
    import harness_sdk.ws as wsmod

    s = make_stream()
    captured = {}

    def fake_connect(url, open_timeout=None, **kw):
        captured["url"] = url
        captured["headers"] = next(iter(kw.values()))
        raise RuntimeError("stop here")  # stop before any real I/O

    monkeypatch.setattr(wsmod, "_ws_connect", fake_connect)
    stop = threading.Event()
    try:
        list(s._pump_once("proj1", {"ticket": "tkn", "fingerprint": "ab12"},
                          7, stop))
    except Exception:
        pass
    assert captured["url"].startswith(
        "ws://localhost:8001/api/v1/ws/proj1?afterSeq=7&ticket=tkn")
    assert captured["headers"]["user-agent"].startswith("harness-sdk/")
    assert captured["headers"]["x-ws-fingerprint"] == "ab12"

    # No echo (pre-M18-C5 server): header omitted entirely.
    captured.clear()
    try:
        list(s._pump_once("proj1", {"ticket": "tkn2", "fingerprint": None},
                          0, stop))
    except Exception:
        pass
    assert "x-ws-fingerprint" not in captured["headers"]


# ---------------------------------------------------------------------------
# Generated surface sanity
# ---------------------------------------------------------------------------

def test_harness_exposes_all_sections():
    sdk = Harness(None)
    for name in ("identity", "github", "router", "orchestrator",
                 "sandbox", "meeting", "review"):
        client = getattr(sdk, name)
        assert client.base_url.startswith("http://localhost:")
    assert sdk.identity.base_url.endswith(":8001")
    assert sdk.review.base_url.endswith(":8008")
    sdk.close()


def test_generated_methods_exist():
    sdk = Harness(None)
    try:
        for method in ("login", "request_login", "verify_otp_route", "list_projects",
                       "create_project", "issue_ws_ticket", "replay_events"):
            assert callable(getattr(sdk.identity, method))
        for method in ("propose_plan", "get_run_status", "approve_plan", "start_run",
                       "pause_agent", "signal_done"):
            assert callable(getattr(sdk.orchestrator, method))
        assert callable(sdk.sandbox.list_checkpoints)
        assert callable(sdk.meeting.create_meeting)
        assert callable(sdk.review.list_comments)
        assert callable(sdk.router.list_models)
        assert callable(sdk.github.connection_status)
    finally:
        sdk.close()


def test_generated_call_shapes_over_mock_transport():
    """The generated method bodies build correct requests end-to-end."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = request.read()
        return httpx.Response(200, json=[{"id": "p1", "name": "proj",
                                          "repoFullName": None}])

    http = HarnessHTTP("tok", transport=httpx.MockTransport(handler))
    from harness_sdk.identity import IdentityClient

    ic = IdentityClient(http, "http://identity.test")
    ic.list_projects(10)
    assert captured["method"] == "GET"
    assert captured["url"].startswith("http://identity.test/api/v1/projects?")
    assert "limit=10" in captured["url"]
    assert captured["body"] in (b"", b"null")


def test_bracketed_no_proxy_does_not_break_the_client(monkeypatch):
    # Docker Desktop ships NO_PROXY with `[::1]`; httpx 0.28 parses that as a URL
    # and rejects the port. The client sanitizes the value around construction
    # and restores the caller's environment immediately.
    import os

    original = "localhost,127.0.0.1,[::1],.example.com"
    monkeypatch.setenv("NO_PROXY", original)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = HarnessHTTP("tok", transport=httpx.MockTransport(handler))
    assert client.request("http://localhost:8001", "GET", "/x") == {"ok": True}
    assert os.environ["NO_PROXY"] == original


def test_raw_content_body_reaches_the_wire_as_bytes():
    """Artifact upload is a raw-body endpoint ("the body IS the file"); the
    generated stubs cannot express that, so the transport must."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        captured["ctype"] = request.headers.get("content-type")
        captured["url"] = str(request.url)
        return httpx.Response(201, json={"artifact": {"id": "a1"}})

    http = HarnessHTTP("tok", transport=httpx.MockTransport(handler))
    out = http.request(
        "http://orch.test", "POST", "/api/v1/orchestrator/runs/r1/artifacts",
        params={"name": "x.patch", "kind": "diff"}, content="+++ b/a.py\n",
    )
    assert out["artifact"]["id"] == "a1"
    assert captured["body"] == b"+++ b/a.py\n"
    assert captured["ctype"].startswith("text/plain")
    assert "name=x.patch" in captured["url"] and "kind=diff" in captured["url"]
