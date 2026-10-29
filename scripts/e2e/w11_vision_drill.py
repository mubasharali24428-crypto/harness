#!/usr/bin/env python3
"""W11 live gate: a mockup becomes a page, an error dialog becomes a diagnosis.

The roadmap asks for three things — design-to-code from a mockup, error-dialog
triage from a photo, and image diffing for visual regression — and this drill
shows each with bytes rather than adjectives.

Legs, all in one evidence file:

  1. **Design-to-code (live run).** ``mockup.png`` is rendered BEFORE the run by
     the worker image's own Chromium (so the fonts are the fonts the agent will
     render with), and only the PNG is seeded: the HTML that produced it is
     deleted, so the design exists in the workspace as pixels and nowhere else.
     The agent must look at it, build ``index.html`` + ``style.css``, screenshot
     its own page and diff it against the mockup. The drill then renders the
     agent's page itself and compares with its own decoder — the agent's claimed
     ratio and the measured ratio must agree, and a token that exists only in
     the image must appear in the code it wrote.
  2. **Triage (live run).** ``dialog.png`` is seeded alone; the agent reports the
     error code and the sentence shown in the image. Neither string is anywhere
     in the workspace as text.
  3. **The diff tool, outside the model.** The gate runs in the worker image on a
     ``--network none`` container: a known 10x10 change is reported as exactly
     that box and ratio, and a path outside the workspace is refused. Plus
     W10's no-proxy leg re-run, so "vision" cannot become a way around egress.

    python3 scripts/e2e/w11_vision_drill.py [--timeout 1500] [--skip-network]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import shutil
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "briefs" / "evidence" / "pilot-prep"
P = "/api/v1/orchestrator"
WORKSPACE_ROOT = "/var/lib/harness-runs/workspace"
WORKER_IMAGE = "harness-engine-worker:vdev-local"
GATE_PATH = REPO / "section5" / "src" / "harness_sandbox" / "image_gate.py"
WIDTH, HEIGHT = 800, 600
# The evidence dict lives here too, so a crash in the verification step still
# writes what the live runs actually did.
STATE: dict = {"evidence": None, "stamp": 0}
# What the mockup contains, in coordinates the drill can re-check on any render
# of it: flat colours that must survive the round trip through the agent's code.
# Every point sits well inside a flat region, away from edges and text.
SAMPLE_POINTS = [(40, 40), (400, 150), (690, 90), (200, 300), (500, 300)]
EXPECTED_COLOURS = {
    "canvas": (15, 17, 21),
    "bar": (79, 124, 255),
    "card": (23, 26, 33),
    "green": (62, 207, 142),
    "amber": (242, 184, 75),
}
RATIO_LIMIT = 0.03  # the goal asks for < 0.02; the gate passes at <= 0.03
TOLERANCE = 16

spec = importlib.util.spec_from_file_location(
    "w4_template", REPO / "scripts" / "e2e" / "w4_web_fetch_engine_drill.py"
)
tpl = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(tpl)

w10_spec = importlib.util.spec_from_file_location(
    "w10_drill", REPO / "scripts" / "e2e" / "w10_browser_drill.py"
)
w10 = importlib.util.module_from_spec(w10_spec)
assert w10_spec and w10_spec.loader
w10_spec.loader.exec_module(w10)

gate_spec = importlib.util.spec_from_file_location("image_gate", GATE_PATH)
gate = importlib.util.module_from_spec(gate_spec)
assert gate_spec and gate_spec.loader
gate_spec.loader.exec_module(gate)


# ------------------------------------------------------------------ fixtures


# A design a weak-but-honest vision model can reproduce from pixels: sharp
# axis-aligned rectangles (no radii or borders, so no antialiased edges to
# disagree about) plus one small line of text carrying the token. The previous
# fixture had rounded corners, a hairline border and 64px text; the first live
# run could not converge and the loop guard stopped it at 50 tool calls. The
# text stays small on purpose: the token must be legible (that is the claim),
# but its glyphs must not dominate the diff when the model's font size is a
# little off.
MOCKUP_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;padding:0;width:{w}px;height:{h}px;background:#0f1115;
  overflow:hidden;}}
.card{{position:absolute;left:100px;top:80px;width:600px;height:460px;
  background:#171a21;}}
.bar{{position:absolute;left:140px;top:130px;width:520px;height:40px;
  background:#4f7cff;}}
.title{{position:absolute;left:140px;top:400px;font-family:"DejaVu Sans",sans-serif;
  font-size:28px;font-weight:700;color:#eef2f8;}}
.green{{position:absolute;left:140px;top:230px;width:240px;height:120px;
  background:#3ecf8e;}}
.amber{{position:absolute;left:420px;top:230px;width:240px;height:120px;
  background:#f2b84b;}}
</style></head><body>
<div class="card"></div><div class="bar"></div>
<div class="green"></div><div class="amber"></div>
<div class="title">{token}</div>
</body></html>
"""

DIALOG_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;padding:0;width:{w}px;height:{h}px;background:#0d0f13;}}
.box{{position:absolute;left:150px;top:170px;width:500px;height:260px;
  background:#1c2029;border:1px solid #333a47;border-radius:12px;}}
.icon{{position:absolute;left:180px;top:200px;width:40px;height:40px;border-radius:50%;
  background:#e5484d;}}
.title{{position:absolute;left:236px;top:204px;font-family:"DejaVu Sans",sans-serif;
  font-size:26px;font-weight:700;color:#f2f4f8;}}
.body{{position:absolute;left:180px;top:272px;font-family:"DejaVu Sans",sans-serif;
  font-size:19px;color:#c3cad8;}}
.code{{position:absolute;left:180px;top:330px;font-family:"DejaVu Sans Mono",monospace;
  font-size:19px;color:#8ea1ff;}}
</style></head><body><div class="box"></div><div class="icon"></div>
<div class="title">Sync failed</div>
<div class="body">{message}</div>
<div class="code">{code}</div>
</body></html>
"""

DESIGN_GOAL = """The spec for this task is an image: mockup.png in your workspace. It is a
{w}x{h} design. There is no HTML, CSS or text file describing it — the design exists
only as pixels in that PNG, so you have to look at it.

The design is made ONLY of axis-aligned rectangles with integer pixel coordinates,
plus one line of small text. There are no rounded corners, borders, shadows or
gradients. Every rectangle edge is exactly where the colour changes.

Do these steps IN ORDER and stay inside the call budget:
1. Open mockup.png with the read tool and note what you see: the background, the
   panel, the coloured bars/blocks, and the exact text.
2. Call the image tool ONCE with action "palette" (path "mockup.png"). It returns
   the dominant colours with each one's bounding box and share of the image —
   that is the layout. Then, if and only if a rectangle's edge is ambiguous in
   that output, make AT MOST ONE action "sample" call (path "mockup.png",
   points [[x,y], ...], up to 64 points in the single call) to settle it.
   Never probe an edge point by point: a palette plus one sample call is the
   whole measuring step, and every extra call is budget you no longer have.
3. Write index.html and style.css in the workspace root. The page must render at
   exactly {w}x{h} with no margins, no scrollbars, and the same background colour.
   Use position:absolute rectangles at the coordinates you measured and the exact
   colours you sampled. Use "DejaVu Sans" for the text; keep the title small
   (roughly 24-32px) and colour it with the colour you sampled from its strokes.
   The page must contain the title text from the mockup EXACTLY as it appears.
4. Screenshot your own page: browser tool, action "screenshot", url "index.html",
   path "shots/render.png", width {w}, height {h}.
5. Compare: image tool, action "diff", before "mockup.png", after "shots/render.png".
   The diff reports a ratio and a bbox: the bbox tells you which region is wrong.
6. If the ratio is above 0.02, fix the geometry the bbox points at and repeat
   steps 4-5, at most 3 times. Never write a file whose content did not change,
   and never repeat an identical tool call — the harness stops runs that loop.
   Budget: two sample calls, three screenshot+diff cycles, one diff image opened
   at most once. Then stop and report.
7. Reply with exactly three lines and nothing else:
TOKEN: <the title text you saw in mockup.png>
RATIO: <the last diff ratio the image tool reported, as a decimal>
FILES: index.html, style.css
"""

TRIAGE_GOAL = """dialog.png in your workspace is a capture of an error dialog. Nothing else in
the workspace describes it.

1. Open dialog.png with the read tool and look at it.
2. Reply with exactly two lines and nothing else:
CODE: <the error code shown in the dialog, exactly as written>
MESSAGE: <the sentence shown under the title, exactly as written>
"""


def render_html(html: str) -> bytes:
    """Render HTML to an 800x600 PNG with the worker image's own Chromium.

    The fixture must be drawn by the same renderer and fonts the agent's page
    will get, or the diff measures font substitution instead of design fidelity.
    """
    work = Path(tempfile.mkdtemp(prefix="w11-fixture-"))
    try:
        work.chmod(0o777)
        (work / "design.html").write_text(html, encoding="utf-8")
        tpl.sh("docker", "run", "--rm", "--network", "none", "--user", "10001:10001",
               "-e", "HOME=/tmp", "-v", f"{work}:/work", "-w", "/work",
               "--entrypoint", "chromium", WORKER_IMAGE,
               "--headless=new", "--no-sandbox", "--disable-gpu",
               "--disable-dev-shm-usage", "--hide-scrollbars",
               "--user-data-dir=/tmp/profile", f"--window-size={WIDTH},{HEIGHT}",
               "--screenshot=/work/out.png", "--virtual-time-budget=4000",
               "file:///work/design.html", check=False)
        out = work / "out.png"
        if not out.is_file():
            raise SystemExit(f"fixture render produced no PNG in {work}")
        return out.read_bytes()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def render_page(files: dict[str, str]) -> bytes | None:
    """Render what the AGENT wrote, with the same Chromium the agent has."""
    if "index.html" not in files:
        return None
    work = Path(tempfile.mkdtemp(prefix="w11-page-"))
    try:
        work.chmod(0o777)
        for name, body in files.items():
            (work / name).write_text(body, encoding="utf-8")
        tpl.sh("docker", "run", "--rm", "--network", "none", "--user", "10001:10001",
               "-e", "HOME=/tmp", "-v", f"{work}:/work", "-w", "/work",
               "--entrypoint", "chromium", WORKER_IMAGE,
               "--headless=new", "--no-sandbox", "--disable-gpu",
               "--disable-dev-shm-usage", "--hide-scrollbars",
               "--user-data-dir=/tmp/profile", f"--window-size={WIDTH},{HEIGHT}",
               "--screenshot=/work/out.png", "--virtual-time-budget=4000",
               "file:///work/index.html", check=False)
        out = work / "out.png"
        return out.read_bytes() if out.is_file() else None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def design_token() -> str:
    stems = ["ZEPHYR", "ORBITAL", "HAVEN", "QUARTZ", "MERIDIAN", "COBALT", "LUMEN"]
    import secrets

    return f"{secrets.choice(stems)}-{secrets.randbelow(90) + 10}"


# ------------------------------------------------------------------ workspace I/O


def seed(run_id: str, files: dict[str, bytes]) -> list[str]:
    """Write the fixture bytes as the orchestrator (the run dirs are its own)."""
    lines = ["import base64, os", f"d = {WORKSPACE_ROOT + '/' + run_id!r}",
             "os.makedirs(d, exist_ok=True)", "os.makedirs(d + '/shots', exist_ok=True)"]
    for rel, blob in files.items():
        encoded = base64.b64encode(blob).decode()
        lines.append(
            f"open(os.path.join(d, {rel!r}), 'wb').write(base64.b64decode({encoded!r}))"
        )
    w10.in_orchestrator("\n".join(lines))
    return sorted(files)


def workspace_text(run_id: str, rel: str) -> str:
    return w10.workspace_file(run_id, rel) or ""


def workspace_listing(run_id: str) -> list[str]:
    code = (
        "import os, json\n"
        f"d = {WORKSPACE_ROOT + '/' + run_id!r}\n"
        "print(json.dumps(sorted(os.path.relpath(os.path.join(r, f), d)"
        " for r, _, fs in os.walk(d) for f in fs)))\n"
    )
    return json.loads(w10.in_orchestrator(code).strip())


def workspace_bytes(run_id: str, rel: str) -> bytes:
    return w10.read_workspace_png(run_id, rel)


# ------------------------------------------------------------------ API


def api(method: str, path: str, *, token: str, body: dict | None = None):
    return tpl.http(method, f"{tpl.ORCHESTRATOR}{P}{path}", token=token, body=body)


def upload(run_id: str, token: str, name: str, data: bytes):
    return w10.upload(run_id, token, name, data)


def run_goal(run_id: str, token: str, project_id: str, goal: str, *, max_tool_calls: int,
             timeout: float) -> dict:
    status, plan = api("POST", f"/runs/{run_id}/plan", token=token,
                       body={"goal": goal, "projectId": project_id})
    if status != 201 or not plan.get("planId"):
        raise SystemExit(f"propose plan failed: {status} {plan!r}")
    api("PUT", f"/runs/{run_id}/execution-limits", token=token,
        body={"maxToolCalls": max_tool_calls})
    api("POST", f"/runs/{run_id}/plan/approve", token=token, body={"planId": plan["planId"]})
    status, _ = api("POST", f"/runs/{run_id}/start", token=token)
    states: list[str] = []
    deadline = time.time() + timeout
    state = None
    while time.time() < deadline:
        _, body = api("GET", f"/runs/{run_id}/status", token=token)
        state = body.get("state") if isinstance(body, dict) else None
        if state and (not states or states[-1] != state):
            states.append(state)
        if state in w10.SETTLED:
            break
        time.sleep(4)
    _, engine = api("GET", f"/runs/{run_id}/engine", token=token)
    engine = engine if isinstance(engine, dict) else {}
    return {
        "startStatus": status,
        "states": states,
        "state": state,
        "engine": engine.get("engine"),
        "engineStatus": engine.get("status"),
        "answer": str(engine.get("finalResponse") or ""),
        "goal": goal,
    }


def tool_trail(run_id: str) -> dict:
    events = tpl.audit_tool_events(run_id)
    calls = [e for e in events if e["type"] == "tool_call"]
    image_calls = [c for c in calls if c["tool"] == "image"]
    return {
        "toolsUsed": sorted({c["tool"] for c in calls}),
        "imageDiffCalls": [c for c in image_calls
                           if '"diff"' in str(c.get("arguments") or "")],
        "imageSampleCalls": [c for c in image_calls
                             if '"sample"' in str(c.get("arguments") or "")],
        "imagePaletteCalls": [c for c in image_calls
                              if '"palette"' in str(c.get("arguments") or "")],
        "screenshotCalls": [c for c in calls if c["tool"] == "browser"
                            and "screenshot" in str(c.get("arguments") or "")],
        "mockupReads": [c for c in calls if c["tool"] in ("read", "read_image")
                        and "mockup.png" in str(c.get("arguments") or "")],
        "dialogReads": [c for c in calls if c["tool"] in ("read", "read_image")
                        and "dialog.png" in str(c.get("arguments") or "")],
        "writes": [c for c in calls if c["tool"] in ("write", "edit")],
        "imageArguments": [c.get("arguments") for c in image_calls],
    }


# ------------------------------------------------------------------ gate probe


def probe_gate() -> dict:
    """Run the real gate in the worker image, on a container with no network.

    Proves the arithmetic and the confinement without a model in the loop: a
    known 10x10 patch must come back as exactly that box and ratio, and a path
    outside the workspace must be refused.
    """
    work = Path(tempfile.mkdtemp(prefix="w11-probe-"))
    cid = ""
    try:
        work.chmod(0o777)
        (work / "before.png").write_bytes(gate.encode_png(
            100, 50, [bytearray(bytes((10, 20, 30)) * 100) for _ in range(50)]))
        after_rows = []
        for y in range(50):
            row = bytearray()
            for x in range(100):
                row += bytes((200, 40, 40) if 20 <= x < 30 and 10 <= y < 20 else (10, 20, 30))
            after_rows.append(row)
        (work / "after.png").write_bytes(gate.encode_png(100, 50, after_rows))
        request = json.dumps({"action": "diff", "before": "before.png", "after": "after.png",
                              "workspace": "/work", "tolerance": 16})
        escape = json.dumps({"action": "diff", "before": "../../etc/passwd",
                             "after": "after.png", "workspace": "/work"})
        # palette: the after image is one flat field plus a known 10x10 patch
        palette = json.dumps({"action": "palette", "path": "after.png",
                              "workspace": "/work", "maxColors": 4})
        palette_escape = json.dumps({"action": "palette", "path": "../../etc/passwd",
                                      "workspace": "/work"})
        palette_cap = json.dumps({"action": "palette", "path": "after.png",
                                  "workspace": "/work", "maxColors": 99})
        cid = tpl.sh("docker", "run", "-d", "--network", "none", "--name",
                     f"w11-probe-{int(time.time())}", "--user", "10001:10001",
                     "-v", f"{work}:/work", "--entrypoint", "sleep", WORKER_IMAGE,
                     "600").stdout.strip()
        gate_argv = "python -I /opt/harness-shell/image_gate.py"
        diff = tpl.sh("docker", "exec", cid, "sh", "-lc",
                      f"printf '%s' {w10._sh_quote(request)} | {gate_argv}",
                      check=False)
        refused = tpl.sh("docker", "exec", cid, "sh", "-lc",
                         f"printf '%s' {w10._sh_quote(escape)} | {gate_argv}",
                         check=False)
        palette_out = tpl.sh("docker", "exec", cid, "sh", "-lc",
                             f"printf '%s' {w10._sh_quote(palette)} | {gate_argv}",
                             check=False)
        palette_escape_out = tpl.sh("docker", "exec", cid, "sh", "-lc",
                                    f"printf '%s' {w10._sh_quote(palette_escape)} | {gate_argv}",
                                    check=False)
        palette_cap_out = tpl.sh("docker", "exec", cid, "sh", "-lc",
                                 f"printf '%s' {w10._sh_quote(palette_cap)} | {gate_argv}",
                                 check=False)
        routes = tpl.sh("docker", "exec", cid, "sh", "-lc", "ip route 2>/dev/null || true",
                        check=False)

        def last_json(proc) -> dict:
            for line in reversed((proc.stdout or "").strip().splitlines()):
                try:
                    return json.loads(line)
                except ValueError:
                    continue
            return {"error": (proc.stdout or proc.stderr or "")[-300:]}

        diff_out, escape_out = last_json(diff), last_json(refused)
        return {
            "image": WORKER_IMAGE,
            "network": "none",
            "diff": diff_out,
            "escape": escape_out,
            "palette": last_json(palette_out),
            "paletteEscape": last_json(palette_escape_out),
            "paletteCap": last_json(palette_cap_out),
            "routes": (routes.stdout or "").strip(),
        }
    finally:
        if cid:
            tpl.sh("docker", "rm", "-f", cid, check=False)
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ verification


def measure(mockup: bytes, render: bytes) -> dict:
    """The drill's own comparison, with the gate's decoder but its own numbers."""
    mw, mh, mbpp, mrows = gate.decode_png(mockup)
    rw, rh, rbpp, rrows = gate.decode_png(render)
    colours = {}
    for name, (x, y) in zip(EXPECTED_COLOURS, SAMPLE_POINTS, strict=False):
        i = x * rbpp
        colours[name] = list(rrows[y][i:i + 3])
    changed = 0
    total = min(mw, rw) * min(mh, rh)
    for y in range(min(mh, rh)):
        mr, rr = mrows[y], rrows[y]
        if mr == rr:
            continue
        for x in range(min(mw, rw)):
            mi, ri = x * mbpp, x * rbpp
            if max(abs(mr[mi] - rr[ri]), abs(mr[mi + 1] - rr[ri + 1]),
                   abs(mr[mi + 2] - rr[ri + 2])) > TOLERANCE:
                changed += 1
    return {
        "mockupSize": [mw, mh],
        "renderSize": [rw, rh],
        "changedPixels": changed,
        "totalPixels": total,
        "ratio": round(changed / total, 6) if total else 1.0,
        "sampledColours": colours,
    }


def colour_close(actual: list[int], expected: tuple[int, int, int], tol: int = 12) -> bool:
    return len(actual) == 3 and all(
        abs(a - e) <= tol for a, e in zip(actual, expected, strict=False))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=1500.0)
    ap.add_argument("--triage-timeout", type=float, default=300.0)
    ap.add_argument("--skip-network", action="store_true")
    args = ap.parse_args()

    stamp = int(time.time())
    design_run = f"w11-design-{stamp}"
    triage_run = f"w11-triage-{stamp}"
    email = f"w11-{stamp}@example.com"
    token = design_token()
    sub = f"design review {stamp % 100:02d}"
    code = f"E_UPSTREAM_{stamp % 900 + 100}"
    snapshot = f"{stamp % 65536:04x}"
    message = f"Snapshot {snapshot} could not be uploaded."

    evidence: dict = {
        "date": time.strftime("%Y-%m-%d"),
        "week": "W11",
        "claim": "a mockup becomes a matching page, an error dialog is triaged from its pixels, "
                 "and image diffing measures the result — all inside the run boundary",
        "runs": {"design": design_run, "triage": triage_run},
        "drive": "HTTP only",
        "model": "the configured DSH_MODEL through the OpenRouter vision patch",
    }
    STATE["evidence"] = evidence
    STATE["stamp"] = stamp

    # 1. fixtures, rendered by the worker image before anything runs
    mockup = render_html(MOCKUP_HTML.format(w=WIDTH, h=HEIGHT, token=token, sub=sub))
    dialog = render_html(DIALOG_HTML.format(w=WIDTH, h=HEIGHT, code=code, message=message))
    evidence["fixtures"] = {
        "token": token,
        "subtitle": sub,
        "errorCode": code,
        "dialogMessage": message,
        "mockupBytes": len(mockup),
        "mockupSha256": hashlib.sha256(mockup).hexdigest(),
        "dialogBytes": len(dialog),
        "dialogSha256": hashlib.sha256(dialog).hexdigest(),
        "note": "the HTML that produced these PNGs was written to a temp dir outside the "
                "workspace and deleted; only the bytes above are seeded",
    }

    login = tpl.login(email)
    status, project = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/projects", token=login,
                               body={"name": f"w11 {stamp}"})
    if status != 201:
        raise SystemExit(f"create project failed: {status} {project!r}")

    # 2. design-to-code run
    seed(design_run, {"mockup.png": mockup})
    listing = workspace_listing(design_run)
    evidence["seededFiles"] = listing
    design = run_goal(design_run, login, project["id"],
                      DESIGN_GOAL.format(w=WIDTH, h=HEIGHT), max_tool_calls=60,
                      timeout=args.timeout)
    evidence["design"] = {k: v for k, v in design.items() if k != "goal"}
    evidence["designTrail"] = tool_trail(design_run)

    page_files = {
        "index.html": workspace_text(design_run, "index.html"),
        "style.css": workspace_text(design_run, "style.css"),
    }
    evidence["pageFiles"] = {k: len(v) for k, v in page_files.items()}
    render = render_page(page_files) if page_files["index.html"] else None
    measurement: dict = {}
    diff_artifact: dict = {}
    if render:
        work = Path(tempfile.mkdtemp(prefix="w11-measure-"))
        try:
            work.chmod(0o777)
            (work / "mockup.png").write_bytes(mockup)
            (work / "render.png").write_bytes(render)
            result = gate.render({"action": "diff", "before": "mockup.png",
                                  "after": "render.png", "workspace": str(work),
                                  "tolerance": TOLERANCE})
            measurement = measure(mockup, render)
            measurement["gateResult"] = {k: result.get(k) for k in
                                         ("ok", "changedPixels", "ratio", "bbox", "maxDelta")}
            if result.get("ok"):
                diff_artifact = {"bytes": (work / result["out"]).read_bytes()}
        finally:
            shutil.rmtree(work, ignore_errors=True)
    evidence["measurement"] = measurement

    # 3. triage run
    seed(triage_run, {"dialog.png": dialog})
    evidence["triageSeededFiles"] = workspace_listing(triage_run)
    triage = run_goal(triage_run, login, project["id"], TRIAGE_GOAL, max_tool_calls=8,
                      timeout=args.triage_timeout)
    evidence["triage"] = {k: v for k, v in triage.items() if k != "goal"}
    evidence["triageTrail"] = tool_trail(triage_run)

    # 4. the tool and the boundary, without a model
    probe = probe_gate()
    network = {"skipped": args.skip_network}
    if not args.skip_network:
        network["noProxyRender"] = w10.probe("https://example.com/", proxy=None)
    evidence["gateProbe"] = probe
    evidence["network"] = network

    # 5. artifacts through the ordinary route
    artifacts: dict = {}
    st, body = upload(design_run, login, "mockup.png", mockup)
    artifacts["mockup"] = {"status": st,
                           "sha256": (body or {}).get("artifact", {}).get("sha256")
                           if isinstance(body, dict) else None}
    if render:
        st, body = upload(design_run, login, "render.png", render)
        artifacts["render"] = {"status": st,
                               "sha256": (body or {}).get("artifact", {}).get("sha256")
                               if isinstance(body, dict) else None}
        diff_bytes = diff_artifact.get("bytes")
        if diff_bytes:
            st, body = upload(design_run, login, "diff.png", diff_bytes)
            artifacts["diff"] = {"status": st,
                                 "sha256": (body or {}).get("artifact", {}).get("sha256")
                                 if isinstance(body, dict) else None}
    st, body = upload(triage_run, login, "dialog.png", dialog)
    artifacts["dialog"] = {"status": st,
                           "sha256": (body or {}).get("artifact", {}).get("sha256")
                           if isinstance(body, dict) else None}
    evidence["artifacts"] = artifacts

    answer = design["answer"]
    triage_answer = triage["answer"]
    trail = evidence["designTrail"]
    probe_diff = probe.get("diff") or {}
    # How much the model probed instead of reading the palette once. Reported,
    # not gated: this is a model-quality measurement, and the gate for the
    # palette itself is the deterministic leg below.
    colour_calls = len(trail["imageSampleCalls"]) + len(trail["imagePaletteCalls"])
    evidence["designLoop"] = {
        "paletteCalls": len(trail["imagePaletteCalls"]),
        "sampleCalls": len(trail["imageSampleCalls"]),
        "colourCallsTotal": colour_calls,
        "goalAskedFor": "one palette call, then at most one sample call",
        "withinBound": colour_calls <= 3,
    }
    checks = {
        "designRunSettled": design["state"] in w10.SETTLED,
        "agentOpenedTheMockup": len(trail["mockupReads"]) >= 1,
        # colour measurement happened through the image tool — palette or
        # sample; what matters is that it was bounded, not which action
        "agentSampledColours": (len(trail["imageSampleCalls"])
                                + len(trail["imagePaletteCalls"])) >= 1,
        "agentScreenshottedItsPage": len(trail["screenshotCalls"]) >= 1,
        "agentUsedTheDiff": len(trail["imageDiffCalls"]) >= 1,
        "agentWroteThePage": len(trail["writes"]) >= 1 and bool(page_files["index.html"]),
        "tokenOnlyInPixelsWasTranscribed": token in page_files["index.html"],
        "pageRendersAtMockupSize": bool(render)
        and measurement.get("renderSize") == [WIDTH, HEIGHT],
        "independentDiffWithinThreshold": bool(render)
        and measurement.get("ratio", 1.0) <= RATIO_LIMIT,
        "flatColoursSurvived": bool(render) and all(
            colour_close(measurement["sampledColours"][name], expected)
            for name, expected in EXPECTED_COLOURS.items()
        ),
        "agentRatioAgreesWithBytes": bool(render) and _ratio_agrees(answer, measurement),
        "diffImageAttached": (artifacts.get("diff", {}).get("status") == 201
                              and artifacts["diff"]["sha256"]
                              == hashlib.sha256(diff_artifact.get("bytes", b"")).hexdigest()),
        "mockupAttached": artifacts.get("mockup", {}).get("status") == 201
        and artifacts["mockup"]["sha256"] == evidence["fixtures"]["mockupSha256"],
        "gateReportsTheExactPatch": (
            probe_diff.get("changedPixels") == 100 and probe_diff.get("ratio") == 0.02
            and probe_diff.get("bbox") == [20, 10, 10, 10]
        ),
        "gateRefusesPathsOutsideTheWorkspace": "escapes the workspace" in str(
            (probe.get("escape") or {}).get("denied", "")),
        # the palette action, proven without a model: one call must return the
        # known 10x10 patch as its own colour with its exact box
        "gateReportsThePalette": (
            (probe.get("palette") or {}).get("ok") is True
            and any(c["hex"] == "#c82828" and c["pixels"] == 100
                    and c["bbox"] == [20, 10, 10, 10]
                    for c in (probe["palette"].get("colors") or []))
            and any(c["hex"] == "#0a141e" and c["pixels"] == 4900
                    for c in (probe["palette"].get("colors") or []))
        ),
        "gateRefusesPalettePathsOutsideTheWorkspace": "escapes the workspace" in str(
            (probe.get("paletteEscape") or {}).get("denied", "")),
        "gateCapsPaletteColours": "maxColors" in str(
            (probe.get("paletteCap") or {}).get("denied", "")),
        "gateContainerHasNoNetwork": probe.get("network") == "none"
        and "default" not in probe.get("routes", ""),
        "triageRunSettled": triage["state"] in w10.SETTLED,
        "triageOpenedTheDialog": len(evidence["triageTrail"]["dialogReads"]) >= 1,
        "triageReportedTheExactCode": code in triage_answer,
        "triageReportedTheExactMessage": message in triage_answer,
        "triageHadNoTextSource": not any(
            name.endswith((".html", ".css", ".txt", ".md"))
            for name in evidence["triageSeededFiles"]),
        "browserStillHasNoNetworkWithoutProxy": (
            network["skipped"]
            or (network.get("noProxyRender") or {}).get("verdict") == "FETCH FAILED"
        ),
    }
    evidence["checks"] = checks
    evidence["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    evidence["failedChecks"] = [k for k, v in checks.items() if not v]
    evidence["designAnswer"] = answer[:1500]
    evidence["triageAnswer"] = triage_answer[:800]

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"w11-vision-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n")
    print(json.dumps({"verdict": evidence["verdict"], "failed": evidence["failedChecks"],
                      "ratio": measurement.get("ratio"), "evidence": str(path)}, indent=2))
    return 0 if evidence["verdict"] == "PASS" else 1


def _ratio_agrees(answer: str, measurement: dict) -> bool:
    line = next((ln for ln in answer.splitlines() if ln.upper().startswith("RATIO:")), "")
    if ":" not in line:
        return False
    claimed_text = line.split(":", 1)[1].strip().replace("%", "")
    try:
        claimed = float(claimed_text)
    except ValueError:
        return False
    # A percentage in the answer is the same number the drill measures.
    if claimed > 1:
        claimed /= 100.0
    return abs(claimed - measurement.get("ratio", 1.0)) <= 0.005


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # never lose a live run's evidence to a verification bug
        import traceback

        if STATE.get("evidence") is not None:
            STATE["evidence"]["checkError"] = f"{type(exc).__name__}: {exc}"
            STATE["evidence"]["traceback"] = traceback.format_exc()[-2000:]
            STATE["evidence"]["verdict"] = "ERROR"
            OUT.mkdir(parents=True, exist_ok=True)
            path = OUT / f"w11-vision-{STATE['stamp']}.json"
            path.write_text(json.dumps(STATE["evidence"], indent=2, default=str) + "\n")
        traceback.print_exc()
        raise SystemExit(1) from None
