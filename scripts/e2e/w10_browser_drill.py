#!/usr/bin/env python3
"""W10 live gate: a UI bug is seen, fixed and re-checked by the agent itself.

The roadmap's gate is "a UI bug is reproduced, fixed and re-verified by the agent
with before/after screenshots attached to the run". The fixture is built so that
reading the source gives the WRONG answer: style.css says the swatch is red, and
a white "veil" element covers it. Only a rendered screenshot shows white, and the
fix is to remove the veil. A model that reasons from the CSS instead of looking
reports red/red — and the drill's pixel sampler proves the bytes say otherwise.

Legs, all in one evidence file:

  1. the run itself (plan -> approve -> start -> poll), confined container mode;
  2. the agent's tool trail: two browser screenshots and two reads of the PNGs
     (`agent_events`), plus its own BEFORE/AFTER statement;
  3. the bytes: the two PNGs are pulled from the workspace, decoded, and the
     swatch pixel is sampled — white before, red after — and both are attached
     to the run as artifacts with their sha256;
  4. the boundary, probed outside the model so it cannot be a lucky answer: the
     same gate, in the same image, on a fresh `--internal` network, renders a
     page whose JS fetches a host. Without the proxy the fetch fails; with the
     proxy the allow-listed host resolves and an unlisted one still fails.

    python3 scripts/e2e/w10_browser_drill.py [--timeout 480] [--skip-network]
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import json
import re
import struct
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "briefs" / "evidence" / "pilot-prep"
P = "/api/v1/orchestrator"
WORKSPACE_ROOT = "/var/lib/harness-runs/workspace"
WORKER_IMAGE = "harness-engine-worker:vdev-local"
SETTLED = {"done", "error", "failed", "execution_failed", "stopped", "cancelled", "blocked",
           "awaiting_done_signal"}

spec = importlib.util.spec_from_file_location(
    "w4_template", REPO / "scripts" / "e2e" / "w4_web_fetch_engine_drill.py"
)
tpl = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(tpl)

README = """# Demo page

The brand swatch must render as **pure red** (#ff0000) and fill the panel.

Known regression: a "veil" overlay was added during a theming experiment. If the
swatch looks white on screen, the veil is still covering it.
"""

PAGE_HTML = """<!doctype html>
<html>
<head><meta charset="utf-8"><title>Demo</title><link rel="stylesheet" href="style.css"></head>
<body>
  <h1>Demo panel</h1>
  <div class="panel">
    <div class="swatch"></div>
    <div class="veil"></div>
  </div>
</body>
</html>
"""

STYLE_CSS = """:root {
  --brand: #ff0000;
}

body {
  font-family: sans-serif;
  margin: 40px;
}

.panel {
  position: relative;
  width: 240px;
  height: 160px;
  border: 1px solid #333;
}

.swatch {
  width: 100%;
  height: 100%;
  background: var(--brand);
}

/* Theming experiment: covers the panel. */
.veil {
  position: absolute;
  inset: 0;
  background: #ffffff;
}
"""

GOAL = """A UI bug is visible on the page at {page}, and the stylesheet can mislead you:
read it, but the truth is only in the rendered screenshot.

Do these steps IN ORDER, none skipped:
1. Read README.md (same directory).
2. BEFORE changing anything: browser tool, action="screenshot", url="{page}",
   path="shots/before.png".
3. Open shots/before.png with the read tool and look at the image. Describe what
   you SEE there, not what the CSS says the colour should be.
4. Fix the bug by editing style.css only.
5. Second screenshot: action="screenshot", url="{page}", path="shots/after.png".
6. Open shots/after.png with the read tool and look at it.
7. Reply with exactly two lines and nothing else:
BEFORE: <swatch colour you saw in shots/before.png>
AFTER: <swatch colour you saw in shots/after.png>
"""

PROBE_HTML = """<!doctype html>
<html><body><div id="out">PENDING</div>
<script>
const out = document.getElementById('out');
fetch(%s, {mode: 'no-cors'})
  .then(() => { out.textContent = 'FETCH OK'; })
  .catch(() => { out.textContent = 'FETCH FAILED'; });
</script></body></html>
"""


# ------------------------------------------------------------------ PNG reading


def png_pixels(data: bytes) -> tuple[int, int, int, list[bytearray]]:
    """(width, height, bytes-per-pixel, rows) for the 8-bit RGB/RGBA, non-interlaced PNGs
    Chromium writes. Raises ValueError for anything else instead of guessing."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    width, height = struct.unpack(">II", data[16:24])
    bit_depth, colour, _, _, interlace = data[24], data[25], data[26], data[27], data[28]
    if bit_depth != 8 or interlace != 0 or colour not in (2, 6):
        raise ValueError(f"unsupported PNG (depth {bit_depth}, colour {colour}, "
                         f"interlace {interlace})")
    bpp = 3 if colour == 2 else 4
    idat = bytearray()
    offset = 8
    while offset < len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        if kind == b"IDAT":
            idat += data[offset + 8:offset + 8 + length]
        if kind == b"IEND":
            break
        offset += 12 + length
    raw = zlib.decompress(bytes(idat))
    stride = width * bpp
    rows: list[bytearray] = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        filt = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if filt == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif filt == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filt == 3:  # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filt == 4:  # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        elif filt != 0:
            raise ValueError(f"unknown PNG filter {filt}")
        rows.append(line)
        prev = line
    return width, height, bpp, rows


def pixel(rows: list[bytearray], bpp: int, x: int, y: int) -> tuple[int, int, int]:
    row = rows[y]
    r, g, b = row[x * bpp], row[x * bpp + 1], row[x * bpp + 2]
    return r, g, b


def near(colour: tuple[int, int, int], expected: tuple[int, int, int], tol: int = 8) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(colour, expected))


WHITE, RED = (255, 255, 255), (255, 0, 0)


# ------------------------------------------------------------------ workspace I/O


def _orchestrator_cid() -> str:
    cid = tpl.sh("docker", "ps", "-q", "--filter",
                 "label=com.docker.compose.service=orchestrator").stdout.split()
    if not cid:
        raise SystemExit("orchestrator container not found")
    return cid[0]


def in_orchestrator(py: str) -> str:
    """Run a python snippet in the orchestrator (uid 999, owns the runs volume).

    Seeding must happen as the SAME user that creates the run directories: the
    worker is launched as the run workspace's owner (`confine._worker_user`), so a
    workspace owned by another uid makes the worker unable to write its own
    execution policy (measured: `EACCES ... harness-execution-policy.yml`).
    """
    proc = tpl.sh("docker", "exec", _orchestrator_cid(), "python3", "-c", py, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"orchestrator exec failed: {proc.stderr[-600:]}")
    return proc.stdout


def seed_workspace(run_id: str) -> None:
    """Write the fixture before the run starts; provision keeps an existing directory."""
    code = (
        "import os\n"
        f"d = {WORKSPACE_ROOT + '/' + run_id!r}\n"
        "os.makedirs(d + '/shots', exist_ok=True)\n"
        f"open(d + '/index.html', 'w').write({PAGE_HTML!r})\n"
        f"open(d + '/style.css', 'w').write({STYLE_CSS!r})\n"
        f"open(d + '/README.md', 'w').write({README!r})\n"
    )
    in_orchestrator(code)


def read_workspace_png(run_id: str, rel: str) -> bytes:
    """The PNG as the orchestrator sees it, base64 over the exec boundary."""
    code = (
        "import base64\n"
        f"p = {WORKSPACE_ROOT + '/' + run_id!r} + '/' + {rel!r}\n"
        "print(base64.b64encode(open(p, 'rb').read()).decode())\n"
    )
    import base64 as b64

    return b64.b64decode(in_orchestrator(code).strip())


def workspace_file(run_id: str, rel: str) -> str | None:
    code = (
        "import os\n"
        f"p = {WORKSPACE_ROOT + '/' + run_id!r} + '/' + {rel!r}\n"
        "print(open(p).read() if os.path.isfile(p) else '')\n"
    )
    return in_orchestrator(code)


# ------------------------------------------------------------------ API helpers


def api(method: str, path: str, *, token: str, body: dict | None = None):
    return tpl.http(method, f"{tpl.ORCHESTRATOR}{P}{path}", token=token, body=body)


def upload(run_id: str, token: str, name: str, data: bytes) -> tuple[int, dict | str]:
    req = urllib.request.Request(
        f"{tpl.ORCHESTRATOR}{P}/runs/{run_id}/artifacts?name={name}",
        data=data, method="POST",
    )
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw, status = resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    try:
        return status, json.loads(raw) if raw else {}
    except ValueError:
        return status, raw.decode("utf-8", "replace")


def oversize_upload(run_id: str, token: str, size: int = 11 * 1024 * 1024) -> dict:
    """The artifact cap, probed with headers only.

    The route refuses on Content-Length before reading the stream, so a client
    that actually writes 11 MiB gets a broken pipe from the server's early close.
    Sending the headers and reading the response states the same fact without a
    race about who closes first.
    """
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", 8004, timeout=30)
    try:
        conn.putrequest("POST", f"{P}/runs/{run_id}/artifacts?name=too-big.bin")
        conn.putheader("Authorization", f"Bearer {token}")
        conn.putheader("Content-Type", "application/octet-stream")
        conn.putheader("Content-Length", str(size))
        conn.endheaders()
        resp = conn.getresponse()
        raw = resp.read()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        return {"status": resp.status, "declaredBytes": size,
                "code": ((body or {}).get("detail") or {}).get("error", {}).get("code")}
    finally:
        conn.close()


# ------------------------------------------------------------------ network legs


def probe(url_literal: str, *, proxy: str | None) -> dict:
    """Render a fetching page with the real gate in the real worker image.

    A fresh ``--internal`` network is the same shape the engine gives a run: no
    gateway, nothing resolvable except what is attached. The proxy, when used, is
    attached under the alias the product uses.
    """
    net = f"w10-probe-{int(time.time())}-{abs(hash(url_literal)) % 1000}"
    page_file = OUT / f"_probe-{net}.html"
    cid = ""
    proxy_cid = ""
    connected = False
    try:
        tpl.sh("docker", "network", "create", "--internal", net)
        cid = tpl.sh("docker", "run", "-d", "--network", net, "--name", net,
                     "--entrypoint", "sleep", WORKER_IMAGE, "1800").stdout.strip()
        if proxy:
            # ContainerBoundary attaches the EGRESS CONTAINER to the run network
            # under this alias (and disconnects it afterwards); the run's own
            # container never joins the egress network.
            proxy_cid = tpl.sh("docker", "ps", "-q", "--filter",
                               "label=com.docker.compose.service=sandbox-egress").stdout.split()
            if not proxy_cid:
                raise SystemExit("sandbox-egress container not found")
            proxy_cid = proxy_cid[0]
            tpl.sh("docker", "network", "connect", "--alias", "harness-egress",
                   net, proxy_cid)
            connected = True
        page_file.parent.mkdir(parents=True, exist_ok=True)
        page_file.write_text(PROBE_HTML % json.dumps(url_literal))
        tpl.sh("docker", "cp", str(page_file), f"{cid}:/tmp/probe.html")
        request = json.dumps({"action": "dump", "url": "probe.html", "workspace": "/tmp",
                              "timeoutSeconds": 40})
        shell = ("printf '%s' " + _sh_quote(request)
                 + " | python -I /opt/harness-shell/browser_gate.py")
        result = tpl.sh("docker", "exec", "-e", f"HTTPS_PROXY={proxy or ''}", cid,
                        "sh", "-lc", shell, check=False)
        lines = (result.stdout or "").strip().splitlines()
        try:
            parsed = json.loads(lines[-1]) if lines else {}
        except ValueError:
            parsed = {"error": (result.stdout or result.stderr or "")[-400:]}
        text = str(parsed.get("text") or "")
        # The inline script also contains the strings "FETCH OK"/"FETCH FAILED";
        # only the #out div's content is the page's verdict.
        out_div = re.search(r'<div id="out">([^<]*)</div>', text)
        verdict = out_div.group(1).strip() if out_div else None
        return {"network": net, "proxy": proxy, "verdict": verdict,
                "gateOk": parsed.get("ok"), "gateError": parsed.get("error"),
                "domChars": len(text)}
    finally:
        if connected and proxy_cid:
            tpl.sh("docker", "network", "disconnect", net, proxy_cid, check=False)
        if cid:
            tpl.sh("docker", "rm", "-f", cid, check=False)
        else:
            tpl.sh("docker", "rm", "-f", net, check=False)
        tpl.sh("docker", "network", "rm", net, check=False)
        page_file.unlink(missing_ok=True)


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=480.0)
    ap.add_argument("--skip-network", action="store_true")
    args = ap.parse_args()

    stamp = int(time.time())
    run_id = f"w10-{stamp}"
    email = f"w10-{stamp}@example.com"
    evidence: dict = {
        "date": time.strftime("%Y-%m-%d"),
        "week": "W10",
        "claim": "a UI bug is reproduced, fixed and re-verified by the agent with "
                 "before/after screenshots; the browser's network stays confined",
        "runId": run_id,
        "drive": "HTTP only",
    }

    token = tpl.login(email)
    status, project = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"w10 {run_id}"})
    if status != 201:
        raise SystemExit(f"create project failed: {status} {project!r}")
    page_url = f"file://{WORKSPACE_ROOT}/{run_id}/index.html"
    goal = GOAL.format(page=page_url)
    status, plan = tpl.http("POST", f"{tpl.ORCHESTRATOR}{P}/runs/{run_id}/plan", token=token,
                            body={"goal": goal, "projectId": project["id"]})
    if status != 201 or not plan.get("planId"):
        raise SystemExit(f"propose plan failed: {status} {plan!r}")
    evidence["goal"] = goal
    tpl.http("PUT", f"{tpl.ORCHESTRATOR}{P}/runs/{run_id}/execution-limits", token=token,
             body={"maxToolCalls": 24})
    tpl.http("POST", f"{tpl.ORCHESTRATOR}{P}/runs/{run_id}/plan/approve", token=token,
             body={"planId": plan["planId"]})

    seed_workspace(run_id)
    status, _ = api("POST", f"/runs/{run_id}/start", token=token)
    evidence["startStatus"] = status
    if status != 200:
        raise SystemExit(f"start failed: {status}")

    state = None
    states: list[str] = []
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        _, body = api("GET", f"/runs/{run_id}/status", token=token)
        state = body.get("state") if isinstance(body, dict) else None
        if state and (not states or states[-1] != state):
            states.append(state)
        if state in SETTLED:
            break
        time.sleep(3)
    evidence["states"] = states

    _, engine = api("GET", f"/runs/{run_id}/engine", token=token)
    engine = engine if isinstance(engine, dict) else {}
    answer = str(engine.get("finalResponse") or "")
    evidence["engine"] = {k: engine.get(k) for k in ("engine", "status", "finalResponse")}

    events = tpl.audit_tool_events(run_id)
    browser_calls = [e for e in events if e["type"] == "tool_call" and e["tool"] == "browser"]
    reads = [e for e in events if e["type"] == "tool_call" and e["tool"] == "read"]
    reads_before = [r for r in reads if "before.png" in str(r.get("arguments") or "")]
    reads_after = [r for r in reads if "after.png" in str(r.get("arguments") or "")]
    shot_calls = [c for c in browser_calls
                  if '"screenshot"' in str(c.get("arguments") or "")]
    calls_before = [c for c in shot_calls if "before.png" in str(c.get("arguments") or "")]
    calls_after = [c for c in shot_calls if "after.png" in str(c.get("arguments") or "")]
    evidence["toolTrail"] = {
        "browserCalls": len(browser_calls),
        "screenshotCalls": len(shot_calls),
        "screenshotArguments": [c["arguments"] for c in shot_calls],
        "beforeScreenshot": len(calls_before),
        "afterScreenshot": len(calls_after),
        "readCalls": len(reads),
        "beforeRead": len(reads_before),
        "afterRead": len(reads_after),
        "readArguments": [r["arguments"] for r in reads],
        "toolsUsed": sorted({e["tool"] for e in events if e["tool"]}),
    }

    # The change a client would report: the fixture stylesheet vs what is on disk.
    # Sending it through the done-gate is what gives the run a reviewable diff.
    current_css = workspace_file(run_id, "style.css") or ""
    change_record: dict = {}
    if current_css and current_css != STYLE_CSS:
        diff = "".join(difflib.unified_diff(
            STYLE_CSS.splitlines(keepends=True), current_css.splitlines(keepends=True),
            "a/style.css", "b/style.css"))
        status, _ = api("POST", f"/runs/{run_id}/done-signal", token=token,
                        body={"diffText": diff})
        _, changes = api("GET", f"/runs/{run_id}/changes", token=token)
        items = (changes or {}).get("items", []) if isinstance(changes, dict) else []
        change_record = {
            "doneSignalStatus": status,
            "paths": [i.get("path") for i in items],
            "digest": (changes or {}).get("changesDigest") if isinstance(changes, dict) else None,
        }
    evidence["changeRecord"] = change_record

    before = after = None
    pixels: dict = {}
    artifacts: dict = {}
    try:
        before, after = read_workspace_png(run_id, "shots/before.png"), read_workspace_png(
            run_id, "shots/after.png")
    except SystemExit as exc:
        evidence["pngError"] = str(exc)
    for label, blob in (("before", before), ("after", after)):
        if blob is None:
            continue
        w, h, bpp, rows = png_pixels(blob)
        # The swatch is the whole panel: 240x160 at (40,40). Sample its centre.
        colours = {
            "centre": pixel(rows, bpp, 40 + 120, 40 + 80),
            "topLeft": pixel(rows, bpp, 45, 45),
        }
        pixels[label] = {"size": [w, h], "bpp": bpp,
                         "centre": list(colours["centre"]),
                         "topLeft": list(colours["topLeft"]),
                         "sha256": hashlib.sha256(blob).hexdigest(),
                         "bytes": len(blob)}
        if bpp == 4:
            pixels[label]["alpha"] = rows[40 + 80][(40 + 120) * 4 + 3]
    evidence["pixels"] = pixels

    if before:
        st, body = upload(run_id, token, "before.png", before)
        artifacts["before"] = {"status": st, "id": (body or {}).get("artifact", {}).get("id")
                               if isinstance(body, dict) else None,
                               "sha256": (body or {}).get("artifact", {}).get("sha256")
                               if isinstance(body, dict) else None}
    if after:
        st, body = upload(run_id, token, "after.png", after)
        artifacts["after"] = {"status": st, "id": (body or {}).get("artifact", {}).get("id")
                              if isinstance(body, dict) else None,
                              "sha256": (body or {}).get("artifact", {}).get("sha256")
                              if isinstance(body, dict) else None}
    big = oversize_upload(run_id, token)
    artifacts["tooBig"] = big
    evidence["artifacts"] = artifacts

    fresh_css = workspace_file(run_id, "style.css") or ""
    evidence["fix"] = {
        "veilStillCovering": "#ffffff" in fresh_css and ".veil" in fresh_css,
        "styleChars": len(fresh_css),
    }

    network: dict = {"skipped": args.skip_network}
    if not args.skip_network:
        no_proxy = probe("https://example.com/", proxy=None)
        allowed = probe("https://api-docs.deepseek.com/", proxy="http://harness-egress:3129")
        unlisted = probe("https://example.com/", proxy="http://harness-egress:3129")
        network = {
            "skipped": False,
            "noNetworkFetch": no_proxy,
            "allowListedThroughProxy": allowed,
            "unlistedThroughProxy": unlisted,
        }
    evidence["network"] = network

    def says(text: str, *needles: str) -> bool:
        low = text.lower()
        return any(n in low for n in needles)

    before_line = next((ln for ln in answer.splitlines() if ln.upper().startswith("BEFORE:")), "")
    after_line = next((ln for ln in answer.splitlines() if ln.upper().startswith("AFTER:")), "")
    px_before = pixels.get("before", {}).get("centre")
    px_after = pixels.get("after", {}).get("centre")

    checks = {
        "runSettled": state in SETTLED,
        "tookABeforeScreenshot": len(calls_before) >= 1,
        "tookAnAfterScreenshot": len(calls_after) >= 1,
        "lookedAtBothImages": len(reads_before) >= 1 and len(reads_after) >= 1,
        "beforeRendersWhite": bool(px_before) and near(tuple(px_before), WHITE),
        "afterRendersRed": bool(px_after) and near(tuple(px_after), RED),
        "fixRemovedTheVeil": not evidence["fix"]["veilStillCovering"],
        "agentSaysBeforeWhite": says(before_line, "white", "#fff", "ffffff"),
        "agentSaysAfterRed": says(after_line, "red", "#ff0000", "#f00"),
        "agentAgreesWithTheBytes": bool(px_before and px_after)
        and says(before_line, "white", "#fff")
        and says(after_line, "red", "#ff0000", "#f00"),
        "bothScreenshotsAttached": (
            artifacts.get("before", {}).get("status") == 201
            and artifacts.get("after", {}).get("status") == 201
            and artifacts["before"]["sha256"] == pixels.get("before", {}).get("sha256")
            and artifacts["after"]["sha256"] == pixels.get("after", {}).get("sha256")
        ),
        "fixRecordedAsReviewableChange": (
            change_record.get("doneSignalStatus") == 200
            and change_record.get("paths") == ["style.css"]
        ),
        "artifactLimitEnforced": (
            artifacts["tooBig"]["status"] == 413
            and artifacts["tooBig"]["code"] == "ARTIFACT_TOO_LARGE"
        ),
        "browserHasNoNetworkWithoutProxy": (
            network["skipped"] or network["noNetworkFetch"].get("verdict") == "FETCH FAILED"
        ),
        "allowListedPageLoadsWithProxy": (
            network["skipped"] or network["allowListedThroughProxy"].get("verdict") == "FETCH OK"
        ),
        "unlistedHostStillRefusedWithProxy": (
            network["skipped"] or network["unlistedThroughProxy"].get("verdict") == "FETCH FAILED"
        ),
    }
    evidence["checks"] = checks
    evidence["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    evidence["failedChecks"] = [k for k, v in checks.items() if not v]
    evidence["answer"] = answer[:1200]

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"w10-browser-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n")
    print(json.dumps({"verdict": evidence["verdict"], "failed": evidence["failedChecks"],
                      "evidence": str(path)}, indent=2))
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
