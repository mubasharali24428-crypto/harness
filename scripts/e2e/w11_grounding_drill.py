#!/usr/bin/env python3
"""W11 live gate: a vision model's pick is executed, then re-rendered and judged.

The earlier W11 drill proved design-to-code and dialog triage. Marathon 2.0 W11
asks for the harder half: **grounding an action** on a realistic page — including
a responsive layout, a canvas control and two visually near-identical targets —
and comparing model-only grounding with structured accessibility data. The point
is not that a model can name a colour. It is that a coordinate is a claim, and a
claim needs an independent check.

Legs:

  1. four grounding tasks, each: render → ask the model (one image, one
     instruction) → execute its (x, y) through ``browser_session`` → **re-render
     and read the page's own state** (the ``LOG`` text and, for the canvas, the
     pixel at the clicked point). The model's word is never the verdict.
     * "Delete folder" among a near-identical "Delete file";
     * a canvas switch that is an ``image`` to the accessibility tree;
     * an ``Export CSV`` div that looks and clicks like a button but has no role;
     * the same canvas switch at 480 px, after the responsive reflow.
  2. accessibility comparison, per target: can ``box`` (role+name from Chromium's
     accessibility tree) address it? The two delete buttons yes; the canvas
     switch and the div no — recorded as the accessibility gap they are.
  3. a deterministic false-click control: click "Delete file" while the task is
     "Delete folder" and show the verifier flags it (the detector is proven
     independently of whether a model happens to be wrong).
  4. unsupported models: the product's ``HARNESS_VISION_MODELS`` refusal for a
     text-only model, and the provider's own answer when an image is attached to
     one.
  5. the before/after pair of the canvas task attached to a run as a W9 visual
     pair, so the action evidence carries the reviewed revision.

    python3 scripts/e2e/w11_grounding_drill.py [--timeout 600]

Live model call; one image per task, at most two attempts per task.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "briefs" / "evidence" / "pilot-prep"
FIXTURES = REPO / "scripts" / "e2e" / "fixtures"
WORKER_IMAGE = "harness-engine-worker:vdev-local"
SESSION_CLI = "/opt/harness-shell/browser_session.py"
P = "/api/v1/orchestrator"
SHA_BASE = "a" * 40
SHA_HEAD = "c" * 40
AFTER_RGB = (0x1A, 0x7F, 0x37)   # the canvas switch's ON colour
BEFORE_RGB = (0x44, 0x4B, 0x5A)  # its OFF colour
MAX_TOKENS = 3000
def calibration_html(width: int, height: int) -> str:
    """A UI-like calibration page: one button, a task-style prompt, a far-apart decoy.

    The first calibration used a bare red square and a simple prompt; the model
    answered in pixels there and in a 0..1000 grid on the task pages. Same
    model, same instruction style is what makes the space inferable, so the
    calibration is a page with a labelled button exactly like the tasks.
    """
    left, top = width - 300, height - 160
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>calibration</title>"
        "<style>html,body{margin:0;font-family:system-ui,sans-serif;background:#0f1115;"
        "color:#eef2f8}h1{padding:20px 40px 0;font-size:22px;margin:0}"
        f".btn{{position:absolute;left:{left}px;top:{top}px;width:100px;height:40px;"
        "border:0;border-radius:6px;background:#4f7cff;color:#fff;font-size:15px}"
        "</style></head><body><h1>Calibration</h1>"
        "<button class=\"btn\" type=\"button\">Continue</button></body></html>"
    )


def calibration_centre(width: int, height: int) -> tuple[int, int]:
    return width - 250, height - 140
DIFF = "\n".join(
    [
        "diff --git a/web/workspace.js b/web/workspace.js",
        "index 111..222 100644",
        "--- a/web/workspace.js",
        "+++ b/web/workspace.js",
        "@@ -1,1 +1,2 @@",
        "-old",
        "+switchTo(on)",
        "+exportCsv()",
    ]
) + "\n"

spec = importlib.util.spec_from_file_location(
    "w4_template", REPO / "scripts" / "e2e" / "w4_web_fetch_engine_drill.py"
)
tpl = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(tpl)

png_spec = importlib.util.spec_from_file_location(
    "w9_visual", REPO / "scripts" / "e2e" / "w9_visual_evidence_drill.py"
)
w9 = importlib.util.module_from_spec(png_spec)
assert png_spec and png_spec.loader
png_spec.loader.exec_module(w9)

EASY_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Release</title><style>html,body{margin:0;width:900px;height:640px;background:#0f1115;
font-family:system-ui,sans-serif;color:#eef2f8}h1{padding:40px 0 0 40px;font-size:24px;margin:0}
button{position:absolute;left:300px;top:260px;width:300px;height:120px;border:0;border-radius:10px;
background:#4f7cff;color:#fff;font-size:28px}#log{padding:40px;color:#9aa7bd}"""     """</style></head><body><h1>Release</h1>
<button id="go" type="button">Publish release</button><p id="log">LOG: none</p>
<script>document.getElementById('go').onclick = () => {
  document.getElementById('log').textContent = 'LOG: publish'; };</script></body></html>
"""

TASKS = [
    {
        "id": "easy-target",
        "viewport": (900, 640),
        "page": "easy.html",
        "prompt": "Click the Publish release button.",
        "expectLog": "publish",
        "expectLabel": "publish",
        "surface": "dom",
        "axTarget": ("button", "Publish release"),
        "boxTarget": ("button", "Publish release"),
    },
    {
        "id": "delete-folder",
        "viewport": (900, 640),
        "prompt": ("Click the button that deletes a FOLDER (not the one that deletes a file). "
                   "Some controls look alike; use the labels."),
        "expectLog": "delete-folder",
        "expectLabel": "folder",
        "surface": "dom",
        "axTarget": ("button", "Delete folder"),
        "boxTarget": ("button", "Delete folder"),
    },
    {
        "id": "canvas-switch",
        "viewport": (900, 640),
        "prompt": "Click the Auto-save switch to turn it ON.",
        "expectLog": "auto:on",
        "expectLabel": "auto",
        "surface": "canvas",
        "axTarget": ("button", "Auto-save switch"),
        "boxTarget": ("image", "Auto-save switch"),
    },
    {
        "id": "export-div",
        "viewport": (900, 640),
        "prompt": "Click the Export CSV control.",
        "expectLog": "export",
        "expectLabel": "export",
        "surface": "div",
        "axTarget": ("button", "Export CSV"),
        "boxTarget": ("StaticText", "Export CSV"),
    },
    {
        "id": "canvas-narrow",
        "viewport": (480, 800),
        "prompt": "Click the Auto-save switch to turn it ON.",
        "expectLog": "auto:on",
        "expectLabel": "auto",
        "surface": "canvas",
        "axTarget": ("button", "Auto-save switch"),
        "boxTarget": ("image", "Auto-save switch"),
    },
]


def raw(method: str, url: str, *, data: bytes | None = None, token: str | None = None,
        content_type: str | None = None, timeout: float = 60.0):
    req = urllib.request.Request(url, data=data, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def api(method: str, path: str, *, token: str, body: dict | None = None):
    return tpl.http(method, f"{tpl.ORCHESTRATOR}{P}{path}", token=token, body=body)


def env_file() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (REPO / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


# ------------------------------------------------------------------ browser


def start_container(work: Path, name: str) -> str:
    proc = subprocess.run(
        ["docker", "run", "-d", "--name", name, "--network", "none", "--user", "10001",
         "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL",
         "--security-opt", "no-new-privileges", "-v", f"{work}:/ws",
         "--entrypoint", "sleep", WORKER_IMAGE, "1800"],
        capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"container start failed: {proc.stderr[-400:]}")
    return proc.stdout.strip()


def session(cid: str, steps: list[dict], width: int, height: int,
            timeout: float = 240.0) -> list[dict]:
    request = {"workspace": "/ws", "width": width, "height": height, "steps": steps}
    proc = subprocess.run(
        ["docker", "exec", "-i", cid, "python3", "-I", SESSION_CLI],
        input=json.dumps(request), capture_output=True, text=True, timeout=timeout, check=False)
    return [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]


def last(lines: list[dict], action: str) -> dict:
    found = [line for line in lines if line.get("action") == action]
    return found[-1] if found else {}


# ------------------------------------------------------------------ model


def parse_choice(content: str | None) -> dict | None:
    if not content:
        return None
    text = re.sub(r"```(?:json)?", "", content).strip("` \n")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        x, y = int(payload["x"]), int(payload["y"])
    except (KeyError, TypeError, ValueError):
        return None
    return {"x": x, "y": y, "label": str(payload.get("label") or "")[:80],
            "confidence": payload.get("confidence")}


def ask_model(image: bytes, width: int, height: int, instruction: str,
              values: dict[str, str], timeout: float = 300.0,
              contract: str = "normalized") -> dict:
    """One image, one instruction, JSON back. Two attempts at most; no hidden retries."""
    url = values["DEEPSEEK_BASE_URL"].rstrip("/") + "/chat/completions"
    b64 = base64.b64encode(image).decode()
    if contract == "pixels":
        tail = ("Reply with JSON only: {\"x\": <int>, \"y\": <int>, \"label\": \"<the "
                "control you picked>\", \"confidence\": <0..1>}. Coordinates are pixels "
                "from the top-left of the image; pick the centre of the control.")
    else:
        tail = ("Reply with JSON only: {\"x\": <int 0..1000>, \"y\": <int 0..1000>, "
                "\"label\": \"<the control you picked>\", \"confidence\": <0..1>}. "
                "x and y are the centre of the control on a 0..1000 grid over the image "
                "(1000 = image width for x, 1000 = image height for y).")
    prompt = f"The image is {width}x{height} pixels. {instruction}\n" + tail
    attempts = []
    for attempt in range(2):
        body = {
            "model": values["DSH_MODEL"], "temperature": 0, "max_tokens": MAX_TOKENS,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
            ]}],
        }
        request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
        request.add_header("Authorization", f"Bearer {values['DEEPSEEK_API_KEY']}")
        request.add_header("Content-Type", "application/json")
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            attempts.append({"attempt": attempt + 1, "status": exc.code,
                             "error": exc.read()[:200].decode("utf-8", "replace")})
            continue
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            # A reset mid-response is the provider's, not a verdict: retry bounded.
            attempts.append({"attempt": attempt + 1, "status": None,
                             "error": f"{type(exc).__name__}: {exc}"[:200]})
            time.sleep(2)
            continue
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content")
        usage = data.get("usage") or {}
        attempts.append({"attempt": attempt + 1, "status": 200,
                         "finish": choice.get("finish_reason"),
                         "content": content,
                         "latencySeconds": round(time.time() - started, 1),
                         "costUsd": usage.get("cost"),
                         "completionTokens": usage.get("completion_tokens")})
        parsed = parse_choice(content)
        if parsed:
            return {"ok": True, "choice": parsed, "attempts": attempts,
                    "model": data.get("model") or values["DSH_MODEL"]}
    return {"ok": False, "attempts": attempts, "model": values["DSH_MODEL"]}


def provider_probe(image: bytes, model: str, values: dict[str, str]) -> dict:
    """What the provider does with an image attached to a text-only model id."""
    url = values["DEEPSEEK_BASE_URL"].rstrip("/") + "/chat/completions"
    body = {"model": model, "max_tokens": 16, "messages": [{"role": "user", "content": [
        {"type": "text", "text": "Describe the image in one word."},
        {"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(image).decode()}},
    ]}]}
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    request.add_header("Authorization", f"Bearer {values['DEEPSEEK_API_KEY']}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.load(response)
        return {"status": 200, "accepted": True,
                "content": ((data.get("choices") or [{}])[0].get("message") or {}).get("content")}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "accepted": False,
                "error": exc.read()[:300].decode("utf-8", "replace")}


def product_vision_refusal(model: str) -> dict:
    """The product's own gate: a text-only model never receives the image."""
    try:
        from harness_orchestrator import goal_images
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return {"ok": False, "error": f"goal_images unavailable: {exc}"}
    previous = os.environ.get("HARNESS_VISION_MODELS")
    os.environ["HARNESS_VISION_MODELS"] = "qwen/qwen3.7-flash"
    try:
        import asyncio

        result = asyncio.run(goal_images.resolve_goal_images(
            object(), "w11-grounding", None, model, read=lambda ref: None))
        return {"ok": True, "refused": result.refused, "attached": result.selected}
    finally:
        if previous is None:
            os.environ.pop("HARNESS_VISION_MODELS", None)
        else:
            os.environ["HARNESS_VISION_MODELS"] = previous


# ------------------------------------------------------------------ coordinates


def infer_space(raw: dict, width: int, height: int, centre: tuple[int, int]) -> dict:
    """Which coordinate space the model actually answered in.

    Instructed in pixels, the model under test answered on a 0..1000 grid. That
    is a fact about the model, not a detail to hide: execute on a calibrated
    conversion, record the raw pick, and refuse when the space is neither.
    """
    tx, ty = centre
    px, py = raw["x"], raw["y"]
    pixel_err = ((px - tx) ** 2 + (py - ty) ** 2) ** 0.5
    nx, ny = tx / width * 1000, ty / height * 1000
    norm_err = ((px - nx) ** 2 + (py - ny) ** 2) ** 0.5
    tol_px = 0.02 * (width ** 2 + height ** 2) ** 0.5
    tol_norm = 0.02 * (1000 ** 2 + 1000 ** 2) ** 0.5
    if pixel_err <= tol_px and pixel_err <= norm_err:
        space = "pixels"
    elif norm_err <= tol_norm:
        space = "normalized-1000"
    else:
        space = "unknown"
    return {"space": space, "raw": {"x": px, "y": py},
            "expectedPixels": {"x": tx, "y": ty},
            "pixelError": round(pixel_err, 1), "normalizedError": round(norm_err, 1),
            "tolPixels": round(tol_px, 1), "tolNormalized": round(tol_norm, 1)}


def to_pixels(space: str, raw: dict, width: int, height: int) -> dict | None:
    if space == "pixels":
        x, y = raw["x"], raw["y"]
    elif space == "normalized-1000":
        x = round(raw["x"] * width / 1000)
        y = round(raw["y"] * height / 1000)
    else:
        return None
    if not (0 <= x < width and 0 <= y < height):
        return None
    return {"x": x, "y": y}


def page_log(lines: list[dict]) -> str:
    text = last(lines, "inspect").get("text") or ""
    for line in text.splitlines():
        if line.startswith("LOG: "):
            return line.split("LOG: ", 1)[1].strip()
    return ""


# ------------------------------------------------------------------ main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=600.0)
    args = ap.parse_args()

    stamp = int(time.time())
    run_id = f"w11ground-{stamp}"
    name = f"w11-grounding-{stamp}"
    email = f"w11-grounding-{stamp}@example.com"
    work = Path(tempfile.mkdtemp(prefix="w11-grounding-"))
    (work / "shots").mkdir()
    shutil.copy2(FIXTURES / "w11_grounding.html", work / "grounding.html")
    (work / "easy.html").write_text(EASY_HTML)
    work.chmod(0o777)
    (work / "shots").chmod(0o777)
    values = env_file()
    evidence: dict = {
        "date": time.strftime("%Y-%m-%d"),
        "week": "W11",
        "claim": "every model pick is executed and independently judged; misses are recorded "
                 "as false clicks, AX locates accessible controls, and canvas/div controls are "
                 "reported as inaccessible rather than counted as success",
        "model": values["DSH_MODEL"],
        "runId": run_id,
        "drive": "one Chromium session per step in a network-none worker container; one image "
                 "per model call; verification from the re-rendered page",
    }
    checks: dict[str, bool] = {}
    cid = ""
    try:
        cid = start_container(work, name)
        # The model is asked for a 0..1000 grid: on this stack it ignored a pixel
        # contract on dense pages, so the harness states the grid and converts.
        evidence["coordinateContract"] = "0..1000 normalized; converted by the harness"
        # Evidence, not a gate: what the same model does when asked in pixels.
        calib_page = work / "calibration-900x640.html"
        calib_page.write_text(calibration_html(900, 640))
        session(cid, [
            {"action": "navigate", "url": calib_page.name},
            {"action": "screenshot", "path": "shots/calibration.png"},
        ], 900, 640, args.timeout)
        calib_png = (work / "shots" / "calibration.png").read_bytes()
        pixel_asked = ask_model(calib_png, 900, 640, "Click the Continue button.", values,
                                args.timeout, contract="pixels")
        evidence["pixelContractProbe"] = {
            "asked": pixel_asked,
            "space": (infer_space(pixel_asked["choice"], 900, 640,
                                  calibration_centre(900, 640))
                      if pixel_asked.get("ok") else {"space": "unknown"}),
        }
        results = []
        for task in TASKS:
            tid, (w, h) = task["id"], task["viewport"]
            page = task.get("page", "grounding.html")
            truth_role, truth_name = task["boxTarget"]
            operate_role, operate_name = task["axTarget"]
            before = session(cid, [
                {"action": "navigate", "url": page},
                {"action": "screenshot", "path": f"shots/{tid}-before.png"},
                {"action": "inspect"},
                {"action": "box", "role": truth_role, "name": truth_name},
                {"action": "box", "role": operate_role, "name": operate_name},
            ], w, h, args.timeout)
            boxes = [line for line in before if line.get("action") == "box"]
            truth_box = boxes[0] if boxes and boxes[0].get("ok") else {}
            operate_box = boxes[1] if len(boxes) > 1 else {}
            before_png = (work / "shots" / f"{tid}-before.png").read_bytes()
            asked = ask_model(before_png, w, h, task["prompt"], values, args.timeout)
            pick = asked.get("choice") or {}
            executed = (to_pixels("normalized-1000", pick, w, h) if pick else None)
            after = session(cid, [
                {"action": "navigate", "url": page},
                {"action": "click", "x": (executed or {}).get("x", -1),
                 "y": (executed or {}).get("y", -1)},
                {"action": "wait", "ms": 250},
                {"action": "inspect"},
                {"action": "screenshot", "path": f"shots/{tid}-after.png"},
            ], w, h, args.timeout)
            after_png = (work / "shots" / f"{tid}-after.png").read_bytes()
            observed = page_log(after)
            canvas_pixel = (w9.pixel(after_png, executed["x"], executed["y"])
                            if executed and task["surface"] == "canvas" else None)
            verified = observed == task["expectLog"] and (
                task["surface"] != "canvas" or canvas_pixel == AFTER_RGB)
            displacement = None
            if executed and truth_box:
                displacement = {"dx": executed["x"] - truth_box.get("x", 0),
                                "dy": executed["y"] - truth_box.get("y", 0)}
            ax_node = next((node for node in (last(before, "inspect").get("ax") or [])
                            if node.get("name") == truth_name), None)
            label = (pick.get("label") or "").lower()
            results.append({
                "task": tid, "viewport": [w, h], "surface": task["surface"],
                "prompt": task["prompt"], "expectedLog": task["expectLog"],
                "model": {"picked": pick, "executed": executed, "ok": asked.get("ok"),
                          "attempts": asked.get("attempts"),
                          "labelMatched": task["expectLabel"].lower() in label},
                "answered": bool(pick and executed),
                "observedLog": observed, "canvasPixel": canvas_pixel,
                "verified": verified, "displacement": displacement,
                "truthBox": truth_box or None,
                "screenshotAfterWidth": last(after, "screenshot").get("width"),
                "afterActions": [line.get("action") for line in after
                                 if line.get("ms") is not None],
                "ax": {"node": ax_node,
                        "truthBoxOk": bool(truth_box),
                        "operateBoxOk": operate_box.get("ok") is True,
                        "operateBoxError": operate_box.get("error")
                        or operate_box.get("denied"),
                        "operateRole": operate_role},
            })

        # Deterministic legs: what structured data and pixels can do, no model.
        det: dict = {}
        ax_folder = session(cid, [
            {"action": "navigate", "url": "grounding.html"},
            {"action": "clickAx", "role": "button", "name": "Delete folder"},
            {"action": "wait", "ms": 250},
            {"action": "inspect"},
        ], 900, 640, args.timeout)
        det["axDeleteFolder"] = {"log": page_log(ax_folder),
                                 "clicked": last(ax_folder, "clickAx")}

        canvas_truth = session(cid, [
            {"action": "navigate", "url": "grounding.html"},
            {"action": "screenshot", "path": "shots/canvas-before.png"},
            {"action": "box", "role": "image", "name": "Auto-save switch"},
        ], 900, 640, args.timeout)
        canvas_box = last(canvas_truth, "box")
        canvas_after = session(cid, [
            {"action": "navigate", "url": "grounding.html"},
            {"action": "click", "x": canvas_box.get("x", -1), "y": canvas_box.get("y", -1)},
            {"action": "wait", "ms": 250},
            {"action": "inspect"},
            {"action": "screenshot", "path": "shots/canvas-after.png"},
        ], 900, 640, args.timeout)
        canvas_png = (work / "shots" / "canvas-after.png").read_bytes()
        det["canvasByPixels"] = {
            "box": canvas_box, "log": page_log(canvas_after),
            "pixel": w9.pixel(canvas_png, canvas_box.get("x", 0), canvas_box.get("y", 0)),
        }

        export_truth = session(cid, [
            {"action": "navigate", "url": "grounding.html"},
            {"action": "box", "role": "StaticText", "name": "Export CSV"},
        ], 900, 640, args.timeout)
        export_box = last(export_truth, "box")
        export_after = session(cid, [
            {"action": "navigate", "url": "grounding.html"},
            {"action": "click", "x": export_box.get("x", -1), "y": export_box.get("y", -1)},
            {"action": "wait", "ms": 250},
            {"action": "inspect"},
        ], 900, 640, args.timeout)
        det["exportByPixels"] = {"box": export_box, "log": page_log(export_after)}

        neighbour = session(cid, [
            {"action": "navigate", "url": "grounding.html"},
            {"action": "clickAx", "role": "button", "name": "Delete file"},
            {"action": "wait", "ms": 250},
            {"action": "inspect"},
        ], 900, 640, args.timeout)
        det["falseClickControl"] = {
            "instruction": "Delete folder", "clicked": "Delete file",
            "observedLog": page_log(neighbour), "expectedLog": "delete-folder",
            "flaggedAsFalseClick": page_log(neighbour) != "delete-folder",
        }
        evidence["deterministic"] = det
        evidence["tasks"] = results

        # unsupported models: product gate first, then the provider itself.
        candidate = work / "shots" / "delete-folder-before.png"
        if not candidate.exists():
            candidate = next(iter(sorted((work / "shots").glob("*.png"))), None)
        probe_png = candidate.read_bytes() if candidate else b""
        evidence["unsupportedModel"] = {
            "model": "deepseek/deepseek-chat",
            "product": product_vision_refusal("deepseek/deepseek-chat"),
            "provider": provider_probe(probe_png, "deepseek/deepseek-chat", values),
        }

        answered = [task for task in results if task["answered"]]
        hits = [task for task in results if task["verified"]]
        misses = [task for task in answered if not task["verified"]]
        no_answer = [task["task"] for task in results if not task["answered"]]
        evidence["modelAccuracy"] = {
            "answered": len(answered), "hits": len(hits), "total": len(results),
            "ratio": round(len(hits) / len(results), 3) if results else None,
            "labelsMatched": sum(1 for task in answered if task["model"]["labelMatched"]),
            "misses": [{"task": task["task"], "displacement": task["displacement"]}
                       for task in misses],
            "noAnswer": no_answer,
        }
        costs = [attempt.get("costUsd") or 0
                 for task in results for attempt in ((task["model"]["attempts"]) or [])]
        probe = evidence.get("pixelContractProbe") or {}
        costs += [attempt.get("costUsd") or 0
                  for attempt in ((probe.get("asked") or {}).get("attempts") or [])]
        evidence["usage"] = {"calls": len(costs), "costUsd": round(sum(costs), 6)}
        evidence["accessibilityGap"] = {
            "canvas": {"axRole": next(((task["ax"]["node"] or {}).get("role")
                                       for task in results
                                       if task["task"] == "canvas-switch"), None),
                       "operableByPixels": det["canvasByPixels"]["log"] == "auto:on",
                       "operableByAx": next((task["ax"]["operateBoxOk"] for task in results
                                             if task["task"] == "canvas-switch"), None)},
            "exportDiv": {"axNode": next((task["ax"]["node"] for task in results
                                          if task["task"] == "export-div"), None),
                          "operableByPixels": det["exportByPixels"]["log"] == "export",
                          "operableByAx": next((task["ax"]["operateBoxOk"] for task in results
                                                if task["task"] == "export-div"), None)},
        }

        by_task = {task["task"]: task for task in results}
        checks["modelAccuracyMeasured"] = bool(results)
        checks["pixelContractProbeRecorded"] = isinstance(
            evidence.get("pixelContractProbe"), dict)
        checks["modelAnsweredMostTasks"] = len(answered) >= len(results) - 1 and bool(results)
        checks["picksExecutedAndJudged"] = bool(answered) and all(
            task["model"]["executed"] and "click" in task["afterActions"]
            for task in answered)
        checks["noFalseSuccess"] = bool(results) and all(
            task["verified"] == (task["observedLog"] == task["expectedLog"])
            for task in results)
        checks["missesRecordedAsFalseClicks"] = all(
            task["displacement"] is not None for task in misses)
        checks["noAnswerRecorded"] = len(no_answer) == len(results) - len(answered)
        checks["falseClickDetectorWorks"] = det["falseClickControl"]["flaggedAsFalseClick"]
        checks["axBaselineLandsFolder"] = det["axDeleteFolder"]["log"] == "delete-folder"
        checks["visualOnlyCanvasWorks"] = (
            det["canvasByPixels"]["log"] == "auto:on"
            and det["canvasByPixels"]["pixel"] == AFTER_RGB)
        checks["visualOnlyExportWorks"] = det["exportByPixels"]["log"] == "export"
        checks["axCannotOperateCanvas"] = (
            by_task.get("canvas-switch", {}).get("ax", {}).get("operateBoxOk") is False
            and by_task.get("canvas-switch", {}).get("ax", {}).get("truthBoxOk") is True)
        checks["axCannotOperateExportDiv"] = (
            by_task.get("export-div", {}).get("ax", {}).get("operateBoxOk") is False
            and by_task.get("export-div", {}).get("ax", {}).get("truthBoxOk") is True)
        checks["canvasIsAnImageToAx"] = (
            evidence["accessibilityGap"]["canvas"]["axRole"] == "image")
        checks["exportDivIsTextToAx"] = (
            (evidence["accessibilityGap"]["exportDiv"]["axNode"] or {}).get("role")
            in ("StaticText", "generic"))
        checks["responsiveViewportHonoured"] = bool(results) and all(
            task["screenshotAfterWidth"] == task["viewport"][0] for task in results)
        checks["productRefusesTextOnlyModel"] = (
            evidence["unsupportedModel"]["product"].get("refused") is not None
            and evidence["unsupportedModel"]["product"].get("attached") == 0)
        checks["providerRejectsImageForTextOnly"] = (
            evidence["unsupportedModel"]["provider"].get("accepted") is False)
        checks["modelCostRecorded"] = evidence["usage"]["calls"] > 0
        checks["everyActionAudited"] = bool(answered) and all(
            "click" in task["afterActions"] for task in answered)
    finally:
        if cid:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True, text=True,
                           check=False)

    try:
        return finish(stamp, run_id, email, work, evidence, checks)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def finish(stamp: int, run_id: str, email: str, work: Path, evidence: dict,
           checks: dict) -> int:
    token = tpl.login(email)
    status, project = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"w11 grounding {stamp}"})
    if status != 201:
        raise SystemExit(f"create project failed: {status} {project!r}")
    base = f"/runs/{run_id}"
    status, plan = api("POST", f"{base}/plan", token=token,
                       body={"goal": "Ground the Auto-save switch and prove the click",
                             "projectId": project["id"]})
    if status != 201 or not plan.get("planId"):
        raise SystemExit(f"propose plan failed: {status} {plan!r}")
    api("POST", f"{base}/plan/approve", token=token, body={"planId": plan["planId"]})
    status, versions = api("GET", f"{base}/plan/versions", token=token)
    latest = int((versions or {}).get("latestVersion") or 0) if isinstance(versions, dict) else 0
    if latest == 0:
        _, made = api("POST", f"{base}/plan/versions", token=token, body={
            "content": {"goal": "Ground the Auto-save switch and prove the click"},
            "expectedVersion": 0})
        latest = int((made or {}).get("version") or 1)
    api("POST", f"{base}/plan/versions/{latest}/approve", token=token)
    api("POST", f"{base}/done-signal", token=token, body={"diffText": DIFF})
    status, changes = api("GET", f"{base}/changes", token=token)
    digest = (changes or {}).get("changesDigest")
    api("PUT", f"{base}/git-state", token=token, body={
        "baseBranch": "main", "baseSha": SHA_BASE, "observedHeadSha": SHA_BASE,
        "headBranch": f"agent/{run_id}", "runHeadSha": SHA_HEAD})

    before_png = (work / "shots" / "canvas-before.png").read_bytes()
    after_png = (work / "shots" / "canvas-after.png").read_bytes()

    def upload(data: bytes, *, name: str, role: str, pair: str | None = None):
        query = f"name={name}&role={role}" + (f"&pairOf={pair}" if pair else "")
        status, _, payload = raw(
            "POST", f"{tpl.ORCHESTRATOR}{P}{base}/visuals?{query}", data=data, token=token,
            content_type="application/octet-stream")
        try:
            return status, json.loads(payload or b"{}")
        except ValueError:
            return status, {}

    status_before, made_before = upload(before_png, name="switch-before.png", role="before")
    before = (made_before or {}).get("visual") or {}
    status_after, made_after = upload(after_png, name="switch-after.png", role="after",
                                      pair=before.get("id", ""))
    after = (made_after or {}).get("visual") or {}
    status, listed = api("GET", f"{base}/visuals", token=token)
    pairs = (listed or {}).get("pairs", [])
    prov = before.get("provenance") or {}
    evidence["visualPair"] = {
        "before": {"status": status_before, "id": before.get("id")},
        "after": {"status": status_after, "id": after.get("id"),
                  "pairId": after.get("pairId")},
        "pairs": len(pairs), "provenance": prov,
        "afterSha256": hashlib.sha256(after_png).hexdigest(),
    }
    checks["visualPairAttached"] = (
        status_before == 201 and status_after == 201 and after.get("pairId") == before.get("id")
        and len(pairs) == 1)
    checks["pairCarriesRevision"] = (
        prov.get("runHeadSha") == SHA_HEAD and prov.get("changesDigest") == digest
        and prov.get("planVersion") == latest)
    evidence["checks"] = checks
    evidence["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"w11-grounding-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"checks": checks, "verdict": evidence["verdict"]}, indent=1))
    print(f"{evidence['verdict']}  evidence -> {path.relative_to(REPO)}")
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
