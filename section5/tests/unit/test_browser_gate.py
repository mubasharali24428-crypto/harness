"""browser_gate.py: the engine's browser tool, run standalone as the worker runs it.

The gate is what turns "spawn Chromium" into "look at a page without leaving the
boundary", so the refusals come first: a path outside the workspace, a file URL
outside the workspace, a non-allow-listed host, a remote URL with no proxy, an
oversize render, a timeout, a non-PNG. No browser is needed — ``HARNESS_CHROMIUM``
points at a stand-in script, which is also how the environment scrub is checked.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

GATE = Path(__file__).resolve().parents[2] / "src" / "harness_sandbox" / "browser_gate.py"
# 1x1 PNG: enough for the gate to read an IHDR, small enough to be deterministic.
PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\r\xefF\xb8"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _gate():
    spec = importlib.util.spec_from_file_location("browser_gate", GATE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["browser_gate"] = module
    spec.loader.exec_module(module)
    return module


def _fake_chromium(tmp_path: Path, *, body: str = "", exit_code: int = 0) -> tuple[Path, Path]:
    """A stand-in ``chromium``: copies the sample PNG to the requested path, dumps its env.

    The PNG is a file, not a shell literal: a literal would carry NUL bytes into the
    script and the kernel would refuse to exec it.
    """
    env_out = tmp_path / "child-env.txt"
    png = tmp_path / "sample.png"
    png.write_bytes(PNG_1X1)
    script = tmp_path / "fake-chromium"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            env > {env_out}
            for arg in "$@"; do
              case "$arg" in
                --screenshot=*) out="${{arg#--screenshot=}}" ;;
                --dump-dom) dump=1 ;;
              esac
            done
            if [ -n "$out" ]; then cp {png} "$out"; fi
            {body}
            if [ -n "$dump" ]; then printf '<html><body>rendered</body></html>'; fi
            exit {exit_code}
            """
        )
    )
    script.chmod(0o755)
    return script, env_out


def _request(tmp_path: Path, **over) -> dict:
    req = {
        "action": "screenshot",
        "url": "file://" + str(tmp_path / "page.html"),
        "path": "shots/before.png",
        "workspace": str(tmp_path),
        "maxBytes": 4096,
        "timeoutSeconds": 10,
    }
    req.update(over)
    return req


def _ready(tmp_path: Path) -> None:
    (tmp_path / "page.html").write_text("<html><body>hi</body></html>")


def test_a_screenshot_lands_in_the_workspace_and_reports_its_shape(tmp_path, monkeypatch):
    _ready(tmp_path)
    script, _ = _fake_chromium(tmp_path)
    monkeypatch.setenv("HARNESS_CHROMIUM", str(script))
    gate = _gate()

    out = gate.render(_request(tmp_path))
    assert out["ok"] is True, out
    assert out["path"] == os.path.join("shots", "before.png")
    assert (out["width"], out["height"]) == (1, 1)
    assert out["bytes"] == len(PNG_1X1)
    assert (tmp_path / "shots" / "before.png").read_bytes() == PNG_1X1


def test_the_browser_never_sees_the_model_key(tmp_path, monkeypatch):
    _ready(tmp_path)
    script, env_out = _fake_chromium(tmp_path)
    monkeypatch.setenv("HARNESS_CHROMIUM", str(script))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("HARNESS_WORKER_DAEMON_TOKEN", "runner-token")
    gate = _gate()

    assert gate.render(_request(tmp_path))["ok"] is True
    seen = env_out.read_text()
    assert "sk-should-not-leak" not in seen
    assert "runner-token" not in seen
    assert "PATH=" in seen and "HOME=" in seen


def test_a_path_outside_the_workspace_is_refused(tmp_path, monkeypatch):
    _ready(tmp_path)
    script, _ = _fake_chromium(tmp_path)
    monkeypatch.setenv("HARNESS_CHROMIUM", str(script))
    gate = _gate()

    for bad in ("../escape.png", "shots/../../escape.png", "/tmp/absolute.png", "shots/x.txt"):
        out = gate.render(_request(tmp_path, path=bad))
        assert "denied" in out and "error" not in out, (bad, out)


def test_a_symlink_out_of_the_workspace_is_refused(tmp_path, monkeypatch):
    _ready(tmp_path)
    script, _ = _fake_chromium(tmp_path)
    monkeypatch.setenv("HARNESS_CHROMIUM", str(script))
    gate = _gate()
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir(exist_ok=True)
    (tmp_path / "shots").mkdir()
    (tmp_path / "shots" / "link.png").symlink_to(outside / "target.png")

    out = gate.render(_request(tmp_path, path="shots/link.png"))
    assert out["denied"].startswith("path "), out


def test_a_file_url_outside_the_workspace_is_refused(tmp_path, monkeypatch):
    _ready(tmp_path)
    script, _ = _fake_chromium(tmp_path)
    monkeypatch.setenv("HARNESS_CHROMIUM", str(script))
    gate = _gate()
    (tmp_path.parent / "secret.html").write_text("secret")

    out = gate.render(_request(tmp_path, url="file://" + str(tmp_path.parent / "secret.html")))
    assert "outside the workspace" in out["denied"], out


def test_a_remote_page_needs_the_proxy_and_the_allow_list(tmp_path, monkeypatch):
    _ready(tmp_path)
    script, _ = _fake_chromium(tmp_path)
    monkeypatch.setenv("HARNESS_CHROMIUM", str(script))
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    gate = _gate()

    no_proxy = gate.render(_request(
        tmp_path, url="https://api-docs.deepseek.com/"))
    assert "egress proxy" in no_proxy["denied"], no_proxy

    unlisted = gate.render(_request(
        tmp_path, url="https://example.com/x", workspace=str(tmp_path)))
    assert "denied" in unlisted, unlisted

    monkeypatch.setenv("HTTPS_PROXY", "http://harness-egress:3129")
    with_proxy = gate.render(_request(tmp_path, url="https://api-docs.deepseek.com/"))
    assert with_proxy.get("ok") is True, with_proxy


def test_plain_http_and_odd_port_are_refused(tmp_path, monkeypatch):
    _ready(tmp_path)
    script, _ = _fake_chromium(tmp_path)
    monkeypatch.setenv("HARNESS_CHROMIUM", str(script))
    gate = _gate()

    for url in ("http://api-docs.deepseek.com/", "https://api-docs.deepseek.com:8443/"):
        out = gate.render(_request(tmp_path, url=url))
        assert "denied" in out, (url, out)


def test_an_oversize_render_is_refused_and_removed(tmp_path, monkeypatch):
    _ready(tmp_path)
    script, _ = _fake_chromium(tmp_path)
    monkeypatch.setenv("HARNESS_CHROMIUM", str(script))
    gate = _gate()

    out = gate.render(_request(tmp_path, maxBytes=10))
    assert "cap" in out["error"], out
    assert not (tmp_path / "shots" / "before.png").exists()


def test_output_that_is_not_a_png_is_refused(tmp_path, monkeypatch):
    _ready(tmp_path)
    script, _ = _fake_chromium(tmp_path, body='printf "<html>not an image</html>" > "$out"')
    monkeypatch.setenv("HARNESS_CHROMIUM", str(script))
    gate = _gate()

    out = gate.render(_request(tmp_path))
    assert "not a PNG" in out["error"], out
    assert not (tmp_path / "shots" / "before.png").exists()


def test_a_hung_render_is_killed(tmp_path, monkeypatch):
    _ready(tmp_path)
    script, _ = _fake_chromium(tmp_path, body='rm -f "$out"; sleep 30')
    monkeypatch.setenv("HARNESS_CHROMIUM", str(script))
    gate = _gate()

    out = gate.render(_request(tmp_path, timeoutSeconds=1))
    assert "timed out" in out["error"], out
    assert not (tmp_path / "shots" / "before.png").exists()


def test_a_slow_render_that_finished_is_still_evidence(tmp_path, monkeypatch):
    """Cold Chromium under the worker's limits outlives the deadline but writes the PNG.

    Killing it and reporting a timeout would delete a perfectly good screenshot and
    teach the model to retry; the gate hands over the valid image and says it was slow.
    """
    _ready(tmp_path)
    script, _ = _fake_chromium(tmp_path, body="sleep 30")
    monkeypatch.setenv("HARNESS_CHROMIUM", str(script))
    gate = _gate()

    out = gate.render(_request(tmp_path, timeoutSeconds=1))
    assert out.get("ok") is True and out["slowRender"] is True, out
    assert (tmp_path / "shots" / "before.png").read_bytes() == PNG_1X1


def test_a_nonzero_exit_without_output_is_an_error(tmp_path, monkeypatch):
    _ready(tmp_path)
    script, _ = _fake_chromium(tmp_path, body='rm -f "$out"', exit_code=3)
    monkeypatch.setenv("HARNESS_CHROMIUM", str(script))
    gate = _gate()

    out = gate.render(_request(tmp_path))
    assert "exited 3" in out["error"], out


def test_dump_returns_the_dom(tmp_path, monkeypatch):
    _ready(tmp_path)
    script, _ = _fake_chromium(tmp_path)
    monkeypatch.setenv("HARNESS_CHROMIUM", str(script))
    gate = _gate()

    out = gate.render(_request(tmp_path, action="dump"))
    assert out["ok"] is True and "rendered" in out["text"], out
    assert not (tmp_path / "shots" / "before.png").exists()


def test_the_cli_seam_reads_stdin_and_writes_one_json_line(tmp_path, monkeypatch):
    _ready(tmp_path)
    script, _ = _fake_chromium(tmp_path)
    env = {**os.environ, "HARNESS_CHROMIUM": str(script)}
    proc = subprocess.run(
        [sys.executable, "-I", str(GATE)],
        input=json.dumps(_request(tmp_path)),
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"] is True and out["path"] == os.path.join("shots", "before.png")


def test_an_unknown_action_is_refused(tmp_path, monkeypatch):
    _ready(tmp_path)
    script, _ = _fake_chromium(tmp_path)
    monkeypatch.setenv("HARNESS_CHROMIUM", str(script))
    gate = _gate()

    out = gate.render(_request(tmp_path, action="click"))
    assert "action must be" in out["denied"], out


def test_a_workspace_relative_page_needs_no_absolute_path(tmp_path, monkeypatch):
    """The agent knows the file, not the mount point: 'index.html' is the page."""
    _ready(tmp_path)
    script, _ = _fake_chromium(tmp_path)
    monkeypatch.setenv("HARNESS_CHROMIUM", str(script))
    gate = _gate()

    out = gate.render(_request(tmp_path, url="page.html"))
    assert out.get("ok") is True, out

    listed = gate.render(_request(tmp_path, url="page.html", action="dump"))
    assert "rendered" in listed["text"]

    missing = gate.render(_request(tmp_path, url="nope.html"))
    assert "does not exist" in missing["denied"], missing

    escape = gate.render(_request(tmp_path, url="../outside.html"))
    assert "outside the workspace" in escape["denied"], escape
