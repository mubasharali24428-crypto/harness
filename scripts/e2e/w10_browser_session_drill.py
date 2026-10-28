#!/usr/bin/env python3
"""W10 live gate: a persistent browser is driven action by action, inside the boundary.

Marathon 2.0 W10 is not the static-fixture repro (that gate exists); it is a
browser that *stays open* while the caller navigates, inspects, clicks, types,
scrolls and screenshots — with every action policy-checked, audited and cleaned
up. This drill runs that adapter the way a worker would:

  1. one Chromium, launched by ``browser_session.py`` inside a detached worker
     container (``--network none``, read-only root, cap-drop ALL, uid 10001);
  2. six actions on a fixture with an accessible button, a labelled canvas, a
     labelled textbox, a tall page and a download link;
  3. the accessibility tree compared with visual grounding: the button is found
     by role+name, clicked at its DOM box centre with real mouse events, and the
     effect is checked in three places — ``aria-pressed``, the rendered pixel and
     the page text;
  4. the refusals: a file outside the workspace, https with no egress proxy,
     plain http, a page-script redirect out of the workspace (refused *and*
     quarantined to about:blank), a screenshot path escaping the workspace, and a
     download (denied at the browser, state ``canceled``, no file written);
  5. cleanup: no chromium process survives the session, the profile is gone, and
     the browser's environment held only PATH/HOME/LANG (a canary variable set on
     the container never reached it);
  6. evidence over claims: the two screenshots are attached to a real run as a
     W9 visual pair, with the run's recorded revision in their provenance.

    python3 scripts/e2e/w10_browser_session_drill.py [--timeout 300]

No model is called: this is the adapter's own proof.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
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
CANARY = "w10-canary-do-not-leak"
DIFF = "\n".join(
    [
        "diff --git a/web/settings.js b/web/settings.js",
        "index 111..222 100644",
        "--- a/web/settings.js",
        "+++ b/web/settings.js",
        "@@ -1,1 +1,2 @@",
        "-old",
        "+toggle.setAttribute('aria-pressed', on ? 'true' : 'false')",
        "+paint(on)",
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

REDIRECT_HTML = (
    '<!doctype html><meta charset="utf-8"><title>redirect</title>'
    '<script>location.replace("file:///etc/hostname")</script>\n'
)


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


def err_code(status: int, body) -> str | None:
    if isinstance(body, dict):
        detail = body.get("detail") or body
        if isinstance(detail, dict):
            return (detail.get("error") or {}).get("code")
    return None


def sh(*argv: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), capture_output=True, text=True, check=check)


def start_container(work: Path, name: str) -> str:
    """A detached worker container: the session runs here, then we inspect the wreckage."""
    proc = sh("docker", "run", "-d", "--name", name, "--network", "none",
              "--user", "10001", "--read-only", "--tmpfs", "/tmp",
              "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
              "-e", f"HARNESS_SECRET_CANARY={CANARY}",
              "-v", f"{work}:/ws", "--entrypoint", "sleep", WORKER_IMAGE, "1800")
    if proc.returncode != 0:
        raise SystemExit(f"container start failed: {proc.stderr[-400:]}")
    return proc.stdout.strip()


def run_session(cid: str, request: dict) -> tuple[list[dict], str]:
    proc = subprocess.run(
        ["docker", "exec", "-i", cid, "python3", "-I", SESSION_CLI],
        input=json.dumps(request), capture_output=True, text=True, timeout=240, check=False,
    )
    lines = [json.loads(ln) for ln in proc.stdout.splitlines() if ln.startswith("{")]
    return lines, proc.stderr[-800:]


def chromium_processes(cid: str) -> str:
    """Processes whose command line names the browser (built at runtime, not in ours)."""
    code = ("import glob, os\n"
            "needle = b'chro' + b'mium'\n"
            "me = str(os.getpid())\n"
            "print([p for p in glob.glob('/proc/[0-9]*/cmdline')"
            " if p.split('/')[2] != me and needle in open(p, 'rb').read()])")
    return sh("docker", "exec", cid, "python3", "-c", code).stdout.strip()


def by_action(lines: list[dict], action: str) -> list[dict]:
    return [ln for ln in lines if ln.get("action") == action]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()

    stamp = int(time.time())
    run_id = f"w10sess-{stamp}"
    name = f"w10-session-{stamp}"
    email = f"w10-session-{stamp}@example.com"
    work = Path(tempfile.mkdtemp(prefix="w10-session-"))
    (work / "shots").mkdir()
    shutil.copy2(FIXTURES / "w10_session.html", work / "w10_session.html")
    (work / "redirect.html").write_text(REDIRECT_HTML)
    work.chmod(0o777)
    (work / "shots").chmod(0o777)
    evidence: dict = {
        "date": time.strftime("%Y-%m-%d"),
        "week": "W10",
        "claim": "a persistent browser drives bounded, audited actions inside the boundary; "
                 "AX data and pixels agree; refused destinations and downloads stay out",
        "drive": "one detached worker container (--network none, read-only, cap-drop ALL); "
                 "the adapter speaks CDP over its own stdlib WebSocket client",
        "runId": run_id,
    }
    cid = ""
    checks: dict[str, bool] = {}
    try:
        cid = start_container(work, name)
        request = {
            "workspace": "/ws", "width": 640, "height": 400,
            "steps": [
                {"action": "navigate", "url": "w10_session.html"},
                {"action": "inspect"},
                {"action": "screenshot", "path": "shots/before.png"},
                {"action": "clickAx", "role": "button", "name": "Notifications off"},
                {"action": "screenshot", "path": "shots/after.png"},
                {"action": "type", "text": "Mubashar", "selector": "#name"},
                {"action": "scroll", "dy": 600},
                {"action": "clipboard"},
                {"action": "requests"},
                {"action": "navigate", "url": "file:///etc/passwd"},
                {"action": "navigate", "url": "https://api-docs.deepseek.com/"},
                {"action": "navigate", "url": "http://example.com/"},
                {"action": "navigate", "url": "redirect.html"},
                {"action": "inspect"},
                {"action": "screenshot", "path": "../escape.png"},
                {"action": "navigate", "url": "w10_session.html"},
                {"action": "clickAx", "role": "link", "name": "Download report"},
                {"action": "wait", "ms": 800},
                {"action": "requests"},
            ],
        }
        lines, stderr = run_session(cid, request)
        if not lines:
            raise SystemExit(f"session printed nothing: {stderr}")
        evidence["steps"] = lines
        evidence["stderr"] = stderr
        start = lines[0]
        checks["sessionStarted"] = (
            start.get("action") == "start" and start.get("ok") is True
            and start.get("width") == 640 and start.get("height") == 400)
        checks["browserEnvScrubbed"] = start.get("envKeys") == ["HOME", "LANG", "PATH"]

        nav = by_action(lines, "navigate")[0]
        checks["navigated"] = nav.get("ok") is True and nav.get("url", "").endswith(
            "/w10_session.html")

        inspect_one = by_action(lines, "inspect")[0]
        ax = {node.get("role"): node.get("name") for node in inspect_one.get("ax", [])
              if node.get("name")}
        checks["accessibilityTreeNamesControls"] = (
            ax.get("button") == "Notifications off" and ax.get("textbox") == "Your name"
            and ax.get("image") == "Weekly signups chart")
        checks["pageTextCaptured"] = "CLIP: DENIED" in (inspect_one.get("text") or "")

        before_png = (work / "shots/before.png").read_bytes()
        after_png = (work / "shots/after.png").read_bytes()
        before_px = w9.pixel(before_png, *w9.CANVAS_PIXEL)
        after_px = w9.pixel(after_png, *w9.CANVAS_PIXEL)
        click = by_action(lines, "clickAx")[0]
        evidence["axVsPixels"] = {
            "axRole": "button", "axName": "Notifications off",
            "clickedAt": [click.get("x"), click.get("y")],
            "backendNodeId": click.get("backendNodeId"),
            "ariaPressedAfter": click.get("ariaPressed"),
            "beforePixel": before_px, "afterPixel": after_px,
        }
        checks["clickUsedTheBoxCentre"] = (
            isinstance(click.get("x"), int) and isinstance(click.get("y"), int)
            and click.get("backendNodeId") is not None)
        checks["clickEffectVisibleInAr"] = click.get("ariaPressed") == "true"
        checks["pixelsProveTheSameChange"] = (
            before_px == w9.BEFORE_RGB and after_px == w9.AFTER_RGB)
        checks["screenshotsBounded"] = (
            by_action(lines, "screenshot")[0].get("width") == 640
            and by_action(lines, "screenshot")[0].get("height") == 400)

        typed = by_action(lines, "type")[0]
        checks["typedIntoTheFocusedField"] = typed.get("ok") is True and typed.get(
            "value") == "Mubashar"
        scrolled = by_action(lines, "scroll")[0]
        checks["scrolled"] = (scrolled.get("scrollY") or 0) > 0

        clip = by_action(lines, "clipboard")[0]
        evidence["clipboard"] = {"outcome": clip.get("outcome"),
                                 "permission": clip.get("permission")}
        checks["clipboardDenied"] = str(clip.get("outcome", "")).startswith("DENIED")

        first_requests = by_action(lines, "requests")[0]
        urls = first_requests.get("urls") or []
        checks["onlyTheWorkspaceWasRequested"] = bool(urls) and all(
            u.startswith("file:///ws/") for u in urls)

        denials = {ln.get("action", "") + str(i): ln.get("denied")
                   for i, ln in enumerate(lines) if ln.get("ok") is False}
        denial_text = list(denials.values())
        evidence["denials"] = denial_text
        checks["fileOutsideRefused"] = any(
            "outside the workspace" in str(d) and "passwd" in str(d) for d in denial_text)
        checks["httpsWithoutProxyRefused"] = any(
            "egress proxy" in str(d) for d in denial_text)
        checks["httpSchemeRefused"] = any("only https://" in str(d) for d in denial_text)
        checks["redirectRefused"] = any(
            "redirect reached" in str(d) and "hostname" in str(d) for d in denial_text)
        checks["screenshotEscapeRefused"] = any(
            "escapes the workspace" in str(d) for d in denial_text)

        # Quarantine: the refused document must not stay readable.
        inspects = by_action(lines, "inspect")
        quarantined = inspects[1] if len(inspects) > 1 else {}
        evidence["quarantine"] = {"url": quarantined.get("url"),
                                  "text": (quarantined.get("text") or "")[:50]}
        checks["redirectQuarantinedToBlank"] = (
            quarantined.get("url") == "about:blank" and not quarantined.get("text"))

        last_requests = by_action(lines, "requests")[-1]
        downloads = last_requests.get("downloads") or []
        evidence["downloads"] = downloads
        checks["downloadCanceled"] = any(d.get("state") == "canceled" for d in downloads)
        checks["noDownloadFileWritten"] = not (work / "w10-report.txt").exists()

        cleanup = by_action(lines, "cleanup")[0] if by_action(lines, "cleanup") else {}
        checks["profileRemoved"] = cleanup.get("profileRemoved") is True
        checks["chromiumGone"] = chromium_processes(cid) == "[]"
        checks["everyStepHasAResult"] = (
            len([ln for ln in lines if "action" in ln]) == len(request["steps"]) + 2)
        checks["everyStepAudited"] = all(
            isinstance(ln.get("ms"), (int, float)) for ln in lines if ln.get("action") not in
            ("start", "cleanup"))
    finally:
        if cid:
            sh("docker", "rm", "-f", cid)

    try:
        return finish(stamp, run_id, email, work, evidence, checks)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def finish(stamp: int, run_id: str, email: str, work: Path, evidence: dict,
           checks: dict) -> int:
    """Attach the before/after pair to a real run and write the evidence file."""
    token = tpl.login(email)
    status, project = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"w10 session {stamp}"})
    if status != 201:
        raise SystemExit(f"create project failed: {status} {project!r}")
    base = f"/runs/{run_id}"
    status, plan = api("POST", f"{base}/plan", token=token,
                       body={"goal": "Reproduce and re-verify the settings toggle", "projectId":
                             project["id"]})
    if status != 201 or not plan.get("planId"):
        raise SystemExit(f"propose plan failed: {status} {plan!r}")
    api("POST", f"{base}/plan/approve", token=token, body={"planId": plan["planId"]})
    status, versions = api("GET", f"{base}/plan/versions", token=token)
    latest = int((versions or {}).get("latestVersion") or 0) if isinstance(versions, dict) else 0
    if latest == 0:
        _, made = api("POST", f"{base}/plan/versions", token=token, body={
            "content": {"goal": "Reproduce and re-verify the settings toggle"},
            "expectedVersion": 0})
        latest = int((made or {}).get("version") or 1)
    api("POST", f"{base}/plan/versions/{latest}/approve", token=token)
    api("POST", f"{base}/done-signal", token=token, body={"diffText": DIFF})
    status, changes = api("GET", f"{base}/changes", token=token)
    digest = (changes or {}).get("changesDigest")
    api("PUT", f"{base}/git-state", token=token, body={
        "baseBranch": "main", "baseSha": SHA_BASE, "observedHeadSha": SHA_BASE,
        "headBranch": f"agent/{run_id}", "runHeadSha": SHA_HEAD})

    # Screenshots are regenerated by the session above only inside the container's
    # mounted dir, which is still present until here.
    before_png = (work / "shots/before.png").read_bytes()
    after_png = (work / "shots/after.png").read_bytes()

    def upload(data: bytes, *, name: str, role: str, pair: str | None = None):
        query = f"name={name}&role={role}" + (f"&pairOf={pair}" if pair else "")
        status, _, payload = raw(
            "POST", f"{tpl.ORCHESTRATOR}{P}{base}/visuals?{query}", data=data, token=token,
            content_type="application/octet-stream")
        try:
            return status, json.loads(payload or b"{}")
        except ValueError:
            return status, {}

    status_before, made_before = upload(before_png, name="before.png", role="before")
    before = (made_before or {}).get("visual") or {}
    status_after, made_after = upload(after_png, name="after.png", role="after",
                                      pair=before.get("id", ""))
    after = (made_after or {}).get("visual") or {}
    status, listed = api("GET", f"{base}/visuals", token=token)
    pairs = (listed or {}).get("pairs", [])
    evidence["visualPair"] = {
        "before": {"status": status_before, "id": before.get("id"),
                   "provenance": before.get("provenance")},
        "after": {"status": status_after, "id": after.get("id"),
                  "pairId": after.get("pairId"), "provenance": after.get("provenance")},
        "pairs": len(pairs),
        "runHeadSha": SHA_HEAD, "changesDigest": digest, "planVersion": latest,
        "screenshotSha256": hashlib.sha256(after_png).hexdigest(),
    }
    prov = before.get("provenance") or {}
    checks["visualPairAttached"] = (
        status_before == 201 and status_after == 201 and after.get("pairId") == before.get("id")
        and len(pairs) == 1)
    checks["pairCarriesTheReviewedRevision"] = (
        prov.get("runHeadSha") == SHA_HEAD and prov.get("changesDigest") == digest
        and prov.get("planVersion") == latest)
    evidence["checks"] = checks
    evidence["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"w10-browser-session-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"checks": checks, "verdict": evidence["verdict"]}, indent=1))
    print(f"{evidence['verdict']}  evidence -> {path.relative_to(REPO)}")
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
