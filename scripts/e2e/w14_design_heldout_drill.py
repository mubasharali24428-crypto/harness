#!/usr/bin/env python3
"""W14 held-out design set: a fresh mockup, pre-registered before any run.

The W11 vision task is disclosed as no longer purely held out: its goal was
re-worded at W12 after the first run. This drill adds a second, never-tuned
design that differs in size (960x640), palette and layout so neither the W11
goal text nor its geometry carries over.

Two legs, deliberately separable:

* ``--fixture-only`` — deterministic, no model. Renders the mockup with the
  worker image's own Chromium, then proves the fixture is what the live leg
  will assume: exact size, exact flat colours at fixed sample points, a token
  present in the title box, a zero self-diff, and a byte-stable re-render.
  This runs today and is the recorded evidence while the live leg is owed.
* live (default) — seeds only ``mockup.png``, runs the configured model, then
  re-renders the agent's page with the same Chromium and measures it itself.
  The pass rule is the task's own verifier with the thresholds below; it is
  never a relative win against another model.

PRE-REGISTERED THRESHOLDS (fixed 2026-10-29, before any run of this task):
recorded in the evidence file and in docs/m2-w14-plan.md. They must not be
relaxed after seeing a result; a failure is evidence and stays in the record.

    python3 scripts/e2e/w14_design_heldout_drill.py --fixture-only
    python3 scripts/e2e/w14_design_heldout_drill.py            # live, owed
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "briefs" / "evidence" / "eval"
WORKER_IMAGE = "harness-engine-worker:vdev-local"
GATE_PATH = REPO / "section5" / "src" / "harness_sandbox" / "image_gate.py"
WIDTH, HEIGHT = 960, 640

# Pre-registered pass rule. Nothing below may be loosened after a run; a run
# that misses it is reported as FAIL with the measurement attached.
PRE_REGISTERED = {
    "ratioMax": 0.03,  # <= 3% of pixels beyond the colour tolerance
    "colourTolerance": 12,  # per-channel absolute difference
    "answerRatioTolerance": 0.005,  # agent's reported RATIO vs measured ratio
    "fixtureColourTolerance": 0,  # the fixture itself must render exactly
}

# Flat regions, each sampled well inside its rectangle and away from text.
SAMPLE_POINTS = [
    ("canvas", (900, 600)),
    ("sidebar", (60, 300)),
    ("header", (400, 30)),
    ("cardA", (300, 150)),
    ("cardB", (700, 150)),
    ("pill", (300, 390)),
    ("accent", (700, 375)),
]
EXPECTED_COLOURS = {
    "canvas": (11, 13, 18),
    "sidebar": (20, 24, 33),
    "header": (27, 32, 48),
    "cardA": (31, 36, 51),
    "cardB": (31, 36, 51),
    "pill": (47, 191, 113),
    "accent": (240, 164, 60),
}
TOKEN_BOX = (600, 420, 300, 60)  # left, top, width, height

spec = importlib.util.spec_from_file_location(
    "w4_template_w14", REPO / "scripts" / "e2e" / "w4_web_fetch_engine_drill.py"
)
tpl = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(tpl)

w11_spec = importlib.util.spec_from_file_location(
    "w11_drill_w14", REPO / "scripts" / "e2e" / "w11_vision_drill.py"
)
w11 = importlib.util.module_from_spec(w11_spec)
assert w11_spec and w11_spec.loader
w11_spec.loader.exec_module(w11)

w10_spec = importlib.util.spec_from_file_location(
    "w10_drill_w14", REPO / "scripts" / "e2e" / "w10_browser_drill.py"
)
w10 = importlib.util.module_from_spec(w10_spec)
assert w10_spec and w10_spec.loader
w10_spec.loader.exec_module(w10)

gate_spec = importlib.util.spec_from_file_location("image_gate_w14", GATE_PATH)
gate = importlib.util.module_from_spec(gate_spec)
assert gate_spec and gate_spec.loader
gate_spec.loader.exec_module(gate)

# A design unlike W11's: a sidebar-led dashboard, no rounded corners, straight
# axis-aligned rectangles, one small title line carrying the run token.
MOCKUP_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;padding:0;width:{w}px;height:{h}px;background:#0b0d12;
  overflow:hidden;}}
.sidebar{{position:absolute;left:0;top:0;width:220px;height:{h}px;background:#141821;}}
.header{{position:absolute;left:220px;top:0;width:{w_minus_sidebar}px;height:72px;
  background:#1b2030;}}
.card-a{{position:absolute;left:260px;top:110px;width:300px;height:220px;
  background:#1f2433;}}
.card-b{{position:absolute;left:600px;top:110px;width:300px;height:220px;
  background:#1f2433;}}
.pill{{position:absolute;left:260px;top:370px;width:220px;height:44px;
  background:#2fbf71;}}
.accent{{position:absolute;left:600px;top:370px;width:300px;height:14px;
  background:#f0a43c;}}
.title{{position:absolute;left:600px;top:420px;width:300px;
  font-family:"DejaVu Sans",sans-serif;font-size:30px;font-weight:700;
  color:#e8edf6;}}
</style></head><body>
<div class="sidebar"></div><div class="header"></div>
<div class="card-a"></div><div class="card-b"></div>
<div class="pill"></div><div class="accent"></div>
<div class="title">{token}</div>
</body></html>
"""

DESIGN_GOAL = """The spec for this task is an image: mockup.png in your workspace. It is a
{w}x{h} design. No HTML, CSS or text file describes it — the design exists only
as pixels in that PNG.

It is drawn ONLY with axis-aligned rectangles at integer coordinates plus one
line of text. There are no rounded corners, borders, shadows or gradients.

Work in this order and stay inside the call budget:
1. Open mockup.png with the read tool and note the background, the sidebar, the
   header strip, the two cards, the filled pill, the thin accent bar and the
   exact title text.
2. Call the image tool ONCE with action "palette" (path "mockup.png"). It returns
   dominant colours with bounding boxes. If and only if one edge is ambiguous,
   make AT MOST ONE action "sample" call (up to 64 points in that single call).
   Never probe an edge point by point.
3. Write index.html and style.css at the workspace root. The page must render at
   exactly {w}x{h}, with no margin and no scrollbar, on the same background.
   Place position:absolute rectangles at the measured coordinates with the exact
   sampled colours. Use "DejaVu Sans" for the title, roughly 28-34px, and copy
   the title text EXACTLY.
4. Screenshot your page: browser action "screenshot", url "index.html",
   path "shots/render.png", width {w}, height {h}.
5. Compare with image action "diff" (before "mockup.png", after
   "shots/render.png"); the bbox names the region to fix.
6. Repeat steps 4-5 at most 3 times while the ratio is above 0.03. Never write
   an unchanged file and never repeat an identical call.
7. Reply with exactly three lines and nothing else:
TOKEN: <the title text you saw in mockup.png>
RATIO: <the last diff ratio the image tool reported, as a decimal>
FILES: index.html, style.css
"""


def design_token() -> str:
    import secrets

    stems = ["HALCYON", "VESPER", "TUNDRA", "ARTIFACT", "NOVA", "CIRRUS"]
    return f"{secrets.choice(stems)}-{secrets.randbelow(90) + 10}"


# ------------------------------------------------------------------ rendering


def render_html(html: str, *, width: int, height: int, name: str = "design.html") -> bytes:
    """Render HTML with the worker image's own Chromium (fixture renderer)."""
    work = Path(tempfile.mkdtemp(prefix="w14-fixture-"))
    try:
        work.chmod(0o777)
        (work / name).write_text(html, encoding="utf-8")
        tpl.sh("docker", "run", "--rm", "--network", "none", "--user", "10001:10001",
               "-e", "HOME=/tmp", "-v", f"{work}:/work", "-w", "/work",
               "--entrypoint", "chromium", WORKER_IMAGE,
               "--headless=new", "--no-sandbox", "--disable-gpu",
               "--disable-dev-shm-usage", "--hide-scrollbars",
               "--user-data-dir=/tmp/profile", f"--window-size={width},{height}",
               "--screenshot=/work/out.png", "--virtual-time-budget=4000",
               f"file:///work/{name}", check=False)
        out = work / "out.png"
        if not out.is_file():
            raise SystemExit(f"fixture render produced no PNG in {work}")
        return out.read_bytes()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def render_page(files: dict[str, str], *, width: int, height: int) -> bytes | None:
    """Render what the agent wrote, with the same Chromium the agent had."""
    if not files.get("index.html"):
        return None
    work = Path(tempfile.mkdtemp(prefix="w14-page-"))
    try:
        work.chmod(0o777)
        for name, body in files.items():
            (work / name).write_text(body, encoding="utf-8")
        tpl.sh("docker", "run", "--rm", "--network", "none", "--user", "10001:10001",
               "-e", "HOME=/tmp", "-v", f"{work}:/work", "-w", "/work",
               "--entrypoint", "chromium", WORKER_IMAGE,
               "--headless=new", "--no-sandbox", "--disable-gpu",
               "--disable-dev-shm-usage", "--hide-scrollbars",
               "--user-data-dir=/tmp/profile", f"--window-size={width},{height}",
               "--screenshot=/work/out.png", "--virtual-time-budget=4000",
               "file:///work/index.html", check=False)
        out = work / "out.png"
        return out.read_bytes() if out.is_file() else None
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ measuring


def _pixels(png: bytes) -> tuple[int, int, int, list[bytearray]]:
    width, height, bpp, rows = gate.decode_png(png)
    return width, height, bpp, rows


def measure(mockup: bytes, render: bytes) -> dict:
    """The drill's own comparison: pixels decoded here, numbers computed here."""
    mw, mh, mbpp, mrows = _pixels(mockup)
    rw, rh, rbpp, rrows = _pixels(render)
    colours = {}
    for name, (x, y) in SAMPLE_POINTS:
        if y >= rh or x >= rw:
            colours[name] = None
            continue
        index = x * rbpp
        colours[name] = list(rrows[y][index:index + 3])
    tol = PRE_REGISTERED["colourTolerance"]
    changed = 0
    total = min(mw, rw) * min(mh, rh)
    for y in range(min(mh, rh)):
        mine, theirs = mrows[y], rrows[y]
        if mine == theirs:
            continue
        for x in range(min(mw, rw)):
            mi, ri = x * mbpp, x * rbpp
            if max(abs(mine[mi] - theirs[ri]), abs(mine[mi + 1] - theirs[ri + 1]),
                   abs(mine[mi + 2] - theirs[ri + 2])) > tol:
                changed += 1
    return {
        "mockupSize": [mw, mh],
        "renderSize": [rw, rh],
        "changedPixels": changed,
        "totalPixels": total,
        "ratio": round(changed / total, 6) if total else 1.0,
        "sampledColours": colours,
    }


def colour_close(actual, expected: tuple[int, int, int], tol: int) -> bool:
    return isinstance(actual, list) and len(actual) == 3 and all(
        abs(a - e) <= tol for a, e in zip(actual, expected, strict=False)
    )


def token_pixels_present(rows: list[bytearray], bpp: int) -> bool:
    """True iff the title box has more than one colour (text was drawn)."""
    left, top, width, height = TOKEN_BOX
    seen: set[bytes] = set()
    for y in range(top, min(top + height, len(rows))):
        row = rows[y]
        for x in range(left, min(left + width, len(row) // bpp)):
            index = x * bpp
            seen.add(bytes(row[index:index + 3]))
            if len(seen) > 1:
                return True
    return False


# ------------------------------------------------------------------ fixture leg


def fixture_checks() -> tuple[bytes, dict, dict]:
    """Render the mockup and prove the properties the live leg assumes."""
    token = design_token()
    html = MOCKUP_HTML.format(
        w=WIDTH, h=HEIGHT, w_minus_sidebar=WIDTH - 220, token=token
    )
    first = render_html(html, width=WIDTH, height=HEIGHT)
    second = render_html(html, width=WIDTH, height=HEIGHT)
    width, height, bpp, rows = _pixels(first)
    _, _, _, rows2 = _pixels(second)

    sampled = {}
    for name, (x, y) in SAMPLE_POINTS:
        sampled[name] = list(rows[y][x * bpp:x * bpp + 3])
    tol = PRE_REGISTERED["fixtureColourTolerance"]
    self_diff = measure(first, first)
    checks = {
        "fixtureRendersAtDeclaredSize": [width, height] == [WIDTH, HEIGHT],
        "flatColoursExactAtSamplePoints": all(
            colour_close(sampled[name], expected, tol)
            for name, expected in EXPECTED_COLOURS.items()
        ),
        "tokenTextIsDrawn": token_pixels_present(rows, bpp),
        "selfDiffIsZero": self_diff["ratio"] == 0.0,
        "renderIsDeterministic": rows == rows2,
    }
    fixture = {
        "token": token,
        "bytes": len(first),
        "sha256": hashlib.sha256(first).hexdigest(),
        "sampledColours": sampled,
        "expectedColours": {k: list(v) for k, v in EXPECTED_COLOURS.items()},
    }
    return first, checks, fixture


def fixture_only() -> int:
    stamp = int(time.time())
    mockup, checks, fixture = fixture_checks()
    evidence = {
        "date": time.strftime("%Y-%m-%d"),
        "week": "W14",
        "task": "w14-design-heldout",
        "leg": "fixture",
        "claim": "the fresh held-out mockup is exactly what the live leg assumes",
        "preRegistered": PRE_REGISTERED,
        "fixture": fixture,
        "checks": checks,
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "failedChecks": [name for name, ok in checks.items() if not ok],
        "limitations": [
            "This leg proves the fixture, not a model. The live design leg is "
            "not run: no vision provider key is configured and live spend was "
            "not authorized.",
            "Chromium renders this fixture; another renderer may not match it.",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"w14-design-fixture-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": evidence["verdict"],
                      "failed": evidence["failedChecks"],
                      "evidence": str(path)}, indent=2))
    return 0 if evidence["verdict"] == "PASS" else 1


# ------------------------------------------------------------------ live leg


def _ratio_agrees(answer: str, measurement: dict) -> bool:
    line = next((ln for ln in answer.splitlines() if ln.upper().startswith("RATIO:")), "")
    if ":" not in line:
        return False
    text = line.split(":", 1)[1].strip().replace("%", "")
    try:
        claimed = float(text)
    except ValueError:
        return False
    if claimed > 1:
        claimed /= 100.0
    return abs(claimed - measurement.get("ratio", 1.0)) <= PRE_REGISTERED[
        "answerRatioTolerance"
    ]


def live(timeout: float) -> int:
    stamp = int(time.time())
    run_id = f"w14-design-{stamp}"
    email = f"w14-{stamp}@example.com"
    mockup, fixture_leg, fixture = fixture_checks()
    token = fixture["token"]

    evidence: dict = {
        "date": time.strftime("%Y-%m-%d"),
        "week": "W14",
        "task": "w14-design-heldout",
        "leg": "live",
        "run": run_id,
        "preRegistered": PRE_REGISTERED,
        "fixture": fixture,
        "fixtureChecks": fixture_leg,
        "drive": "HTTP only",
    }
    try:
        login = tpl.login(email)
        status, project = tpl.http(
            "POST", f"{tpl.IDENTITY}/api/v1/projects", token=login,
            body={"name": f"w14 {stamp}"},
        )
        if status != 201:
            raise SystemExit(f"create project failed: {status} {project!r}")
        w11.seed(run_id, {"mockup.png": mockup})
        evidence["seededFiles"] = w11.workspace_listing(run_id)
        design = w11.run_goal(
            run_id, login, project["id"],
            DESIGN_GOAL.format(w=WIDTH, h=HEIGHT),
            max_tool_calls=50, timeout=timeout,
        )
        evidence["design"] = {k: v for k, v in design.items() if k != "goal"}
        trail = w11.tool_trail(run_id)
        evidence["designTrail"] = trail

        page_files = {
            "index.html": w11.workspace_text(run_id, "index.html"),
            "style.css": w11.workspace_text(run_id, "style.css"),
        }
        render = render_page(page_files, width=WIDTH, height=HEIGHT)
        measurement = measure(mockup, render) if render else {}
        evidence["measurement"] = measurement

        checks = {
            "fixtureLegPassed": all(fixture_leg.values()),
            "designRunSettled": design["state"] in w10.SETTLED,
            "agentOpenedTheMockup": len(trail["mockupReads"]) >= 1,
            "agentMeasuredColours": (
                len(trail["imagePaletteCalls"]) + len(trail["imageSampleCalls"])
            ) >= 1,
            "agentScreenshottedItsPage": len(trail["screenshotCalls"]) >= 1,
            "agentUsedTheDiff": len(trail["imageDiffCalls"]) >= 1,
            "agentWroteThePage": bool(page_files["index.html"]) and len(trail["writes"]) >= 1,
            "tokenOnlyInPixelsWasTranscribed": token in page_files["index.html"],
            "pageRendersAtMockupSize": bool(render)
            and measurement.get("renderSize") == [WIDTH, HEIGHT],
            "independentDiffWithinThreshold": bool(render)
            and measurement.get("ratio", 1.0) <= PRE_REGISTERED["ratioMax"],
            "flatColoursSurvived": bool(render) and all(
                colour_close(
                    measurement["sampledColours"].get(name),
                    expected,
                    PRE_REGISTERED["colourTolerance"],
                )
                for name, expected in EXPECTED_COLOURS.items()
            ),
            "agentRatioAgreesWithBytes": bool(render)
            and _ratio_agrees(design["answer"], measurement),
        }
        evidence["checks"] = checks
        evidence["verdict"] = "PASS" if all(checks.values()) else "FAIL"
        evidence["failedChecks"] = [name for name, ok in checks.items() if not ok]
        evidence["designAnswer"] = design["answer"][:1500]
    except BaseException as exc:  # never lose a live run's evidence
        import traceback

        evidence["checkError"] = f"{type(exc).__name__}: {exc}"
        evidence["traceback"] = traceback.format_exc()[-2000:]
        evidence["verdict"] = "ERROR"
        evidence["failedChecks"] = ["checkError"]
        last = evidence
    else:
        last = evidence

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"w14-design-{stamp}.json"
    path.write_text(json.dumps(last, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": last["verdict"], "failed": last["failedChecks"],
                      "evidence": str(path)}, indent=2))
    return 0 if last["verdict"] == "PASS" else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture-only", action="store_true",
                    help="render and verify the mockup; no model, no spend")
    ap.add_argument("--timeout", type=float, default=1200.0)
    args = ap.parse_args()
    return fixture_only() if args.fixture_only else live(args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
