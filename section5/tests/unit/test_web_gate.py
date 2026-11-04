"""web_gate.py: the engine's web_fetch tool, run standalone as the worker runs it.

The gate is the only thing standing between a model and an arbitrary URL, so the
tests cover the refusals first (non-allow-listed host, plain HTTP, non-443, a
redirect hop that leaves the list) and the one happy path second. No network is
used: the opener is injected.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from urllib.request import Request

import pytest

GATE = Path(__file__).resolve().parents[2] / "src" / "harness_sandbox" / "web_gate.py"


def _gate():
    spec = importlib.util.spec_from_file_location("web_gate", GATE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["web_gate"] = module
    spec.loader.exec_module(module)
    return module


class _Headers:
    def __init__(self, content_type: str = "text/html", charset: str = "utf-8") -> None:
        self._content_type = content_type
        self._charset = charset

    def get_content_type(self) -> str:
        return self._content_type

    def get_content_charset(self) -> str | None:
        return self._charset


class _Response:
    def __init__(self, body: bytes, url: str, *, status: int = 200,
                 content_type: str = "text/html", charset: str = "utf-8") -> None:
        self._body = body
        self._url = url
        self.status = status
        self.headers = _Headers(content_type, charset)

    def read(self, limit: int) -> bytes:
        return self._body[:limit]

    def geturl(self) -> str:
        return self._url

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


class _Opener:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[tuple[str, float | None]] = []

    def open(self, request, timeout=None):
        self.calls.append((request.full_url, timeout))
        return self.response


def test_a_host_outside_the_web_purpose_is_denied_before_dialing():
    gate = _gate()
    opener = _Opener(_Response(b"", "https://evil.test/"))
    result = gate.fetch("https://evil.test/page", opener=opener)
    assert "denied" in result and "not in the web allow-list" in result["denied"]
    assert opener.calls == []


def test_plain_http_is_denied_even_on_an_allowed_host():
    gate = _gate()
    opener = _Opener(_Response(b"", "http://api-docs.deepseek.com/"))
    result = gate.fetch("http://api-docs.deepseek.com/en/docs", opener=opener)
    assert "denied" in result and "https" in result["denied"]
    assert opener.calls == []


def test_non_443_port_is_denied():
    gate = _gate()
    result = gate.fetch(
        "https://api-docs.deepseek.com:8443/en/docs", opener=_Opener(_Response(b"", ""))
    )
    assert "denied" in result and "port 443" in result["denied"]


def test_an_allowed_doc_is_fetched_and_reduced_to_text():
    gate = _gate()
    body = (
        b"<html><head><style>p{color:red}</style><script>alert('x')</script></head>"
        b"<body><h1>Messages API</h1><p>Send a   message.</p></body></html>"
    )
    opener = _Opener(_Response(body, "https://api-docs.deepseek.com/en/api/messages"))
    result = gate.fetch("https://api-docs.deepseek.com/en/api/messages", opener=opener)
    assert result["status"] == 200
    assert result["contentType"] == "text/html"
    assert result["truncated"] is False
    assert "Messages API" in result["text"] and "Send a message." in result["text"]
    assert "alert" not in result["text"] and "color:red" not in result["text"]
    assert opener.calls == [("https://api-docs.deepseek.com/en/api/messages", 15)]


def test_oversized_body_is_truncated_at_the_byte_budget():
    gate = _gate()
    opener = _Opener(
        _Response(b"x" * 50, "https://api-docs.deepseek.com/big", content_type="text/plain")
    )
    result = gate.fetch("https://api-docs.deepseek.com/big", max_bytes=10, opener=opener)
    assert result["truncated"] is True
    assert len(result["text"]) <= 10


def test_a_redirect_hop_out_of_the_allow_list_is_refused():
    gate = _gate()
    handler = gate._CheckedRedirect()
    with pytest.raises(gate.EgressDenied):
        handler.redirect_request(
            Request("https://api-docs.deepseek.com/x"), None, 302, "Found", {}, "https://evil.test/x"
        )
    allowed = handler.redirect_request(
        Request("https://api-docs.deepseek.com/x"), None, 302, "Found", {},
        "https://api.anthropic.com/v1/messages",
    )
    assert allowed.full_url == "https://api.anthropic.com/v1/messages"


def test_html_to_text_keeps_blocks_apart():
    gate = _gate()
    text = gate.html_to_text("<div>one</div><div>two</div><script>nope</script>")
    assert [line for line in text.splitlines() if line.strip()] == ["one", "two"]
    assert "nope" not in text
