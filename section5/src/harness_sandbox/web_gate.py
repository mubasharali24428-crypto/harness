"""Section 5 web gate for the engine's ``web_fetch`` tool (W4).

The DeepSeek runtime has no web tool of its own in our profile, and a raw fetch
tool would be an unfiltered tunnel out of the worker. This gate is the decision
point, run exactly like ``shell_gate.py``:

    python -I web_gate.py < {"url", "timeoutSeconds", "maxBytes"}

It refuses anything the ``web`` egress purpose does not list, refuses plain HTTP
and non-443 ports, **re-checks every redirect hop** (a redirect is the obvious
way around a host allow-list), and returns readable text rather than raw HTML.
One JSON line out: ``{"denied": reason}``, ``{"error": reason}`` or
``{"url", "status", "contentType", "truncated", "text"}``.

Stdlib only, and ``egress.py`` is loaded by file path, so the worker image needs
these two files plus ``egress_allowlist.json`` — not the ``harness_sandbox``
package, whose ``__init__`` pulls in pydantic and docker.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

MAX_BYTES_DEFAULT = 2_000_000
MAX_CHARS = 40_000
MAX_SECONDS_DEFAULT = 15
_ALLOWED_PORT = 443

_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "template"})
_BLOCK_TAGS = frozenset(
    {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre",
     "section", "article", "header", "footer", "table", "ul", "ol", "blockquote"}
)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    # Register before exec: dataclasses resolve their module through sys.modules.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


egress = _load("egress")


class EgressDenied(Exception):
    """A redirect target outside the allow-list (or a downgrade to HTTP)."""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.chunks.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # pragma: no cover - malformed markup: keep what parsed
        pass
    text = "".join(parser.chunks)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _check_destination(url: str) -> str | None:
    """Why ``url`` is not fetchable, or ``None`` when it is."""
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        return f"only https is reachable (got {parsed.scheme or 'no scheme'!r})"
    if parsed.port not in (None, _ALLOWED_PORT):
        return f"only port {_ALLOWED_PORT} is reachable (got {parsed.port})"
    decision = egress.egress_decision(url, egress.PURPOSE_WEB)
    return None if decision.allowed else decision.reason


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    """Every hop goes back through the allow-list; a denial is not a redirect error."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        reason = _check_destination(newurl)
        if reason is not None:
            raise EgressDenied(f"redirect to {newurl!r} refused: {reason}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _decode(raw: bytes, charset: str) -> str:
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def fetch(
    url: str,
    *,
    timeout: float = MAX_SECONDS_DEFAULT,
    max_bytes: int = MAX_BYTES_DEFAULT,
    opener: urllib.request.OpenerDirector | None = None,
) -> dict[str, object]:
    reason = _check_destination(url)
    if reason is not None:
        return {"denied": reason, "host": egress.host_of(url)}

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "harness-web-fetch/1.0 (+policy-gated)",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
        },
    )
    opener = opener or urllib.request.build_opener(_CheckedRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
            status = getattr(response, "status", None) or response.getcode()
            final_url = response.geturl()
            content_type = response.headers.get_content_type()
            charset = response.headers.get_content_charset() or "utf-8"
    except EgressDenied as exc:
        return {"denied": str(exc)}
    except urllib.error.HTTPError as exc:
        return {"denied": f"destination answered HTTP {exc.code}", "url": url}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"error": f"fetch failed: {exc}", "url": url}

    # Defense in depth: the redirect handler already checked every hop, but the
    # final URL is what the model will be told it read.
    final_reason = _check_destination(final_url)
    if final_reason is not None:
        return {"denied": f"final URL {final_url!r} refused: {final_reason}"}

    truncated = len(raw) > max_bytes
    text = _decode(raw[:max_bytes], charset)
    if content_type in ("text/html", "application/xhtml+xml"):
        text = html_to_text(text)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
        truncated = True
    return {
        "url": final_url,
        "status": status,
        "contentType": content_type,
        "truncated": truncated,
        "text": text,
    }


def main() -> int:
    try:
        request = json.load(sys.stdin)
        url = str(request["url"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(json.dumps({"error": f"malformed gate request: {exc}"}))
        return 0
    result = fetch(
        url,
        timeout=float(request.get("timeoutSeconds") or MAX_SECONDS_DEFAULT),
        max_bytes=int(request.get("maxBytes") or MAX_BYTES_DEFAULT),
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
