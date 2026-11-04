#!/usr/bin/env python3
"""W9 live gate, claims 1 and 2: visual evidence is bounded, bound and shareable — safely.

The roadmap asks for "sanitized visual artifact provenance, retention, tenant isolation
and before/after comparisons" and "screenshot artifacts respect limits and access"; the
share/deploy half of W9 has its own drill (``w9_share_deploy_drill.py``). This one drives
the visual path end to end on the running stack:

  render   a fixture page with an accessible button (``aria-pressed``) and a labelled
           ``<canvas>``, twice (``?state=before|after``), through the product's own
           browser gate inside a ``--network none``, cap-dropped, read-only container.
           The PNGs are decoded here and the canvas pixel is sampled: the drill never
           trusts the page's word for what it looks like.
  attach   before, then after paired to it. Both carry a provenance snapshot (run head,
           base sha, approved plan version, changes digest) taken from the run itself.
  refuse   a run with no recorded head, SVG/GIF/PDF, 9000-px-wide images, an oversized
           body, an unpaired after, a second after, and an after whose before belongs to
           a different revision — each with its stable code.
  isolate  a foreign team gets 404 for the list, the item and the attach.
  share    the PNG share link downloads (octet-stream, attachment, nosniff, sandbox CSP,
           sha header) without an Authorization header, never renders, and dies on revoke.
  retain   a backdated retention makes the visual expire (410 on its share and content),
           and the product's own purge turns it into a tombstone and removes its blob.

    python3 scripts/e2e/w9_visual_evidence_drill.py [--timeout 300] [--skip-purge]

``--skip-purge`` records the tombstone leg as skipped instead of pretending it ran.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from urllib.parse import urlencode

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "briefs" / "evidence" / "pilot-prep"
FIXTURE = REPO / "scripts" / "e2e" / "fixtures" / "w9_app.html"
WORKER_IMAGE = "harness-engine-worker:vdev-local"
GATE = "/opt/harness-shell/browser_gate.py"
P = "/api/v1/orchestrator"
SHA_BASE = "a" * 40
SHA_HEAD = "c" * 40
SHA_MOVED = "d" * 40
CANVAS_PIXEL = (160, 176)  # inside the fixture's canvas (left 40, top 96, 240x160)
BEFORE_RGB = (0xB3, 0x26, 0x1E)
AFTER_RGB = (0x1A, 0x7F, 0x37)
DIFF = "\n".join(
    [
        "diff --git a/src/app.py b/src/app.py",
        "index 111..222 100644",
        "--- a/src/app.py",
        "+++ b/src/app.py",
        "@@ -1,1 +1,2 @@",
        "-old",
        "+print('hello')",
        "+print('world')",
        "diff --git a/web/settings.js b/web/settings.js",
        "index 111..222 100644",
        "--- a/web/settings.js",
        "+++ b/web/settings.js",
        "@@ -1,1 +1,2 @@",
        "-old",
        "+toggle.setAttribute('aria-pressed', 'true')",
        "+draw(canvas, 'after')",
    ]
) + "\n"

spec = importlib.util.spec_from_file_location(
    "w4_template", REPO / "scripts" / "e2e" / "w4_web_fetch_engine_drill.py"
)
tpl = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(tpl)


def raw(method: str, url: str, *, data: bytes | None = None, token: str | None = None,
        content_type: str | None = None, timeout: float = 60.0):
    """(status, headers, body) with no JSON assumptions on the response."""
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


def api(method: str, path: str, *, token: str | None = None, body: dict | None = None):
    return tpl.http(method, f"{tpl.ORCHESTRATOR}{P}{path}", token=token, body=body)


def err_code(status: int, body) -> str | None:
    if isinstance(body, dict):
        detail = body.get("detail") or body
        if isinstance(detail, dict):
            return (detail.get("error") or {}).get("code")
    return None


def declared_length_probe(url: str, token: str, declared: int) -> tuple[int, bytes]:
    """POST headers only: the cap is refused before the body is read, so nothing is sent."""
    from http.client import HTTPConnection
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    conn = HTTPConnection(parts.hostname, parts.port, timeout=30)
    conn.putrequest("POST", parts.path + (f"?{parts.query}" if parts.query else ""))
    conn.putheader("Authorization", f"Bearer {token}")
    conn.putheader("Content-Type", "application/octet-stream")
    conn.putheader("Content-Length", str(declared))
    conn.putheader("Expect", "100-continue")
    conn.endheaders()
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response.status, body


def psql(sql: str) -> str:
    cmd = 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc ' + json.dumps(sql)
    proc = subprocess.run(
        ["docker", "compose", "-f", "deploy/docker-compose.yml", "--env-file", ".env",
         "exec", "-T", "db", "sh", "-lc", cmd],
        cwd=str(REPO), capture_output=True, text=True, check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else f"ERROR: {proc.stderr.strip()[:300]}"


# ------------------------------------------------------------------ rendering


def render(action: str, state: str, width: int = 640, height: int = 400) -> tuple[dict, bytes]:
    """Run the product's browser gate in the worker image with no network at all."""
    work = Path(tempfile.mkdtemp(prefix="w9-visual-"))
    (work / "shots").mkdir()
    shutil.copy2(FIXTURE, work / "w9_app.html")
    work.chmod(0o755)
    (work / "shots").chmod(0o777)
    (work / "w9_app.html").chmod(0o644)
    request: dict = {"action": action, "url": f"file:///ws/w9_app.html?state={state}",
                     "workspace": "/ws", "width": width, "height": height,
                     "timeoutSeconds": 60}
    if action == "screenshot":
        request["path"] = f"shots/{state}.png"
    proc = subprocess.run(
        ["docker", "run", "--rm", "-i", "--network", "none", "--user", "10001",
         "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL",
         "--security-opt", "no-new-privileges", "-v", f"{work}:/ws",
         "--entrypoint", "python3", WORKER_IMAGE, "-I", GATE],
        input=json.dumps(request), capture_output=True, text=True, timeout=180, check=False,
    )
    line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith("{")), None)
    result = json.loads(line) if line else {
        "error": f"gate printed nothing: {proc.stdout[:200]} {proc.stderr[:200]}"}
    png = work / "shots" / f"{state}.png"
    data = png.read_bytes() if action == "screenshot" and result.get("ok") and png.is_file() else b""
    shutil.rmtree(work, ignore_errors=True)
    return result, data


def png_header(width: int, height: int) -> bytes:
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(
            ">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


def pixel(data: bytes, x: int, y: int) -> tuple[int, int, int] | None:
    """Decode a non-interlaced 8-bit PNG and return one RGB pixel (None if not PNG)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    index, idat, width = 8, b"", None
    while index + 8 <= len(data):
        length = int.from_bytes(data[index:index + 4], "big")
        kind = data[index + 4:index + 8]
        payload = data[index + 8:index + 8 + length]
        index += 12 + length
        if kind == b"IHDR":
            width, height, depth, colour = struct.unpack(">IIBB", payload[:10])
            if depth != 8 or colour not in (2, 6):
                return None
        elif kind == b"IDAT":
            idat += payload
    if not width or not idat:
        return None
    channels = 3 if colour == 2 else 4
    stride = width * channels
    rows, previous, pos = [], bytearray(stride), 0
    raw_bytes = zlib.decompress(idat)
    for _ in range(height):
        filt = raw_bytes[pos]
        line = bytearray(raw_bytes[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = previous[i]
            corner = previous[i - channels] if i >= channels else 0
            if filt == 1:
                line[i] = (line[i] + left) & 0xFF
            elif filt == 2:
                line[i] = (line[i] + up) & 0xFF
            elif filt == 3:
                line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
            elif filt == 4:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                pred = left if (pa <= pb and pa <= pc) else (up if pb <= pc else corner)
                line[i] = (line[i] + pred) & 0xFF
        rows.append(bytes(line))
        previous = line
    if y >= len(rows) or x * channels + 3 > len(rows[y]):
        return None
    return tuple(rows[y][x * channels:x * channels + 3])  # type: ignore[return-value]


# ------------------------------------------------------------------ main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--skip-purge", action="store_true")
    args = ap.parse_args()

    stamp = int(time.time())
    run_id = f"w9vis-{stamp}"
    owner_email = f"w9vis-owner-{stamp}@example.com"
    foreign_email = f"w9vis-foreign-{stamp}@example.com"
    base = f"/runs/{run_id}"
    evidence: dict = {
        "date": time.strftime("%Y-%m-%d"),
        "week": "W9",
        "claim": "visual evidence is bounded, revision-bound, pairable, tenant-isolated and "
                 "download-only when shared; retention expires it",
        "runId": run_id,
        "drive": "HTTP + one confined render per image (--network none, cap-drop ALL, read-only)",
    }
    checks: dict[str, bool] = {}

    token = tpl.login(owner_email)
    status, project = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"w9 visuals {stamp}"})
    if status != 201:
        raise SystemExit(f"create project failed: {status} {project!r}")
    status, plan = api("POST", f"{base}/plan", token=token,
                       body={"goal": "Fix the settings toggle and evidence it with screenshots",
                             "projectId": project["id"]})
    if status != 201 or not plan.get("planId"):
        raise SystemExit(f"propose plan failed: {status} {plan!r}")
    api("POST", f"{base}/plan/approve", token=token, body={"planId": plan["planId"]})

    status, versions = api("GET", f"{base}/plan/versions", token=token)
    latest = int((versions or {}).get("latestVersion") or 0) if isinstance(versions, dict) else 0
    if latest == 0:
        _, made = api("POST", f"{base}/plan/versions", token=token, body={
            "content": {"goal": "Fix the settings toggle and evidence it with screenshots"},
            "expectedVersion": 0})
        latest = int((made or {}).get("version") or 1)
    api("POST", f"{base}/plan/versions/{latest}/approve", token=token)

    # 1. render both states, and read the DOM for the accessibility claims.
    shot_before, before_png = render("screenshot", "before")
    shot_after, after_png = render("screenshot", "after")
    dump_before, _ = render("dump", "before")
    dump_after, _ = render("dump", "after")
    evidence["render"] = {
        "before": {k: shot_before.get(k) for k in ("ok", "width", "height", "bytes")},
        "after": {k: shot_after.get(k) for k in ("ok", "width", "height", "bytes")},
        "beforePixel": pixel(before_png, *CANVAS_PIXEL),
        "afterPixel": pixel(after_png, *CANVAS_PIXEL),
        "beforePressed": 'aria-pressed="false"' in (dump_before.get("text") or ""),
        "afterPressed": 'aria-pressed="true"' in (dump_after.get("text") or ""),
        "canvasLabelled": 'aria-label="Weekly signups chart"' in (dump_before.get("text") or ""),
    }
    checks["renderedBothStates"] = bool(
        shot_before.get("ok") and shot_after.get("ok") and before_png and after_png)
    checks["pixelsProveTheChange"] = (
        evidence["render"]["beforePixel"] == BEFORE_RGB
        and evidence["render"]["afterPixel"] == AFTER_RGB)
    checks["controlsAreAccessible"] = bool(
        evidence["render"]["beforePressed"] and evidence["render"]["afterPressed"]
        and evidence["render"]["canvasLabelled"]
        and "<canvas" in (dump_after.get("text") or ""))

    # 2. no recorded head: visual evidence has no revision to be evidence about.
    def upload(data: bytes, *, name: str, role: str, pair: str | None = None,
               retention: int | None = None, captured: str | None = None):
        params = {"name": name, "role": role}
        if pair:
            params["pairOf"] = pair
        if retention:
            params["retentionDays"] = str(retention)
        if captured:
            params["capturedFrom"] = captured
        status, _headers, payload = raw(
            "POST", f"{tpl.ORCHESTRATOR}{P}{base}/visuals?{urlencode(params)}",
            data=data, token=token, content_type="application/octet-stream")
        try:
            parsed = json.loads(payload or b"{}")
        except ValueError:
            parsed = None
        return status, parsed

    status, unbound = upload(before_png, name="before.png", role="before")
    evidence["unbound"] = {"status": status, "code": err_code(status, unbound)}
    checks["unboundRunRefused"] = status == 409 and err_code(status, unbound) == "VISUAL_REVISION_UNBOUND"

    # 3. record the reviewed revision, then attach both halves of the pair.
    status, done = api("POST", f"{base}/done-signal", token=token, body={"diffText": DIFF})
    if status != 200:
        raise SystemExit(f"done-signal failed: {status} {done!r}")
    status, changes = api("GET", f"{base}/changes", token=token)
    digest = (changes or {}).get("changesDigest")
    status, git = api("PUT", f"{base}/git-state", token=token, body={
        "baseBranch": "main", "baseSha": SHA_BASE, "observedHeadSha": SHA_BASE,
        "headBranch": f"agent/{run_id}", "runHeadSha": SHA_HEAD})
    if status != 200:
        raise SystemExit(f"git-state failed: {status} {git!r}")

    status, made_before = upload(
        before_png, name="before.png", role="before",
        captured="file:///ws/w9_app.html?state=before")
    before = (made_before or {}).get("visual") or {}
    status_after, made_after = upload(after_png, name="after.png", role="after",
                                      pair=before.get("id", ""), captured=(
                                          "file:///ws/w9_app.html?state=after"))
    after = (made_after or {}).get("visual") or {}
    evidence["attach"] = {
        "before": {"status": status, "id": before.get("id"), "width": before.get("width"),
                    "height": before.get("height"), "mediaType": before.get("mediaType"),
                    "sha256": before.get("sha256"), "provenance": before.get("provenance")},
        "after": {"status": status_after, "id": after.get("id"), "pairId": after.get("pairId"),
                   "provenance": after.get("provenance")},
        "planVersion": latest,
        "changesDigest": digest,
    }
    prov = before.get("provenance") or {}
    checks["bothAttached"] = status == 201 and status_after == 201
    checks["dimensionsReadFromHeader"] = (before.get("width"), before.get("height")) == (640, 400)
    checks["provenanceNamesReviewedRevision"] = (
        prov.get("runHeadSha") == SHA_HEAD and prov.get("baseSha") == SHA_BASE
        and prov.get("planVersion") == latest and prov.get("changesDigest") == digest)
    checks["pairSharesTheSnapshot"] = (
        (after.get("provenance") or {}).get("runHeadSha") == prov.get("runHeadSha")
        and (after.get("provenance") or {}).get("changesDigest") == prov.get("changesDigest"))
    checks["afterNamesItsBefore"] = after.get("pairId") == before.get("id")
    checks["artifactIdShaMatchesBytes"] = before.get("sha256") == hashlib.sha256(
        before_png).hexdigest()

    status, listed = api("GET", f"{base}/visuals", token=token)
    items = (listed or {}).get("items", [])
    pairs = (listed or {}).get("pairs", [])
    evidence["list"] = {"items": len(items), "pairs": len(pairs),
                        "pair": pairs[0] if pairs else None}
    checks["listResolvesThePair"] = (
        len(items) == 2 and len(pairs) == 1
        and (pairs[0].get("before") or {}).get("id") == before.get("id")
        and (pairs[0].get("after") or {}).get("id") == after.get("id"))

    # 4. the refusals: what the pipeline must not store.
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="48"></svg>'
    refusals = {}
    for label, data, name, code, expected in (
        ("svg", svg, "shot.svg", "VISUAL_TYPE_NOT_ALLOWED", 415),
        ("gif", b"GIF89a" + b"\x00" * 32, "shot.gif", "VISUAL_TYPE_NOT_ALLOWED", 415),
        ("pdf", b"%PDF-1.7\n" + b"x" * 32, "shot.pdf", "VISUAL_TYPE_NOT_ALLOWED", 415),
        ("tooWide", png_header(9000, 20), "wide.png", "VISUAL_DIMENSIONS", 422),
    ):
        st, body = upload(data, name=name, role="before")
        refusals[label] = {"status": st, "code": err_code(st, body)}
        checks[f"refused_{label}"] = st == expected and err_code(st, body) == code
    # The byte cap is checked on the declared length before the body is read, so the
    # server answers and hangs up: declare only, do not push 5 MiB at a closed socket.
    over_status, over_body = declared_length_probe(
        f"{tpl.ORCHESTRATOR}{P}{base}/visuals?name=huge.png&role=before", token,
        declared=5 * 1024 * 1024)
    refusals["oversized"] = {"status": over_status,
                             "code": err_code(over_status, json.loads(over_body or b"{}"))}
    checks["refused_oversized"] = (
        over_status == 413 and refusals["oversized"]["code"] == "ARTIFACT_TOO_LARGE")
    st, body = upload(after_png, name="lonely.png", role="after")
    refusals["afterWithoutBefore"] = {"status": st, "code": err_code(st, body)}
    checks["refused_afterWithoutBefore"] = err_code(st, body) == "VISUAL_PAIR_INVALID"
    st, body = upload(after_png, name="second.png", role="after", pair=before.get("id", ""))
    refusals["secondAfter"] = {"status": st, "code": err_code(st, body)}
    checks["refused_secondAfter"] = st == 409 and err_code(st, body) == "VISUAL_PAIR_TAKEN"

    # Moving the reviewed revision makes the earlier half a different image's evidence.
    api("PUT", f"{base}/git-state", token=token, body={"runHeadSha": SHA_MOVED})
    st, body = upload(after_png, name="moved.png", role="after", pair=before.get("id", ""))
    refusals["crossRevisionPair"] = {"status": st, "code": err_code(st, body),
                                      "staleHead": ((body or {}).get("detail") or {})
                                      .get("after", {}).get("runHeadSha")}
    checks["refused_crossRevisionPair"] = (
        st == 409 and err_code(st, body) == "VISUAL_PAIR_REVISION_MISMATCH")
    api("PUT", f"{base}/git-state", token=token, body={"runHeadSha": SHA_HEAD})
    evidence["refusals"] = refusals

    items = (api("GET", f"{base}/visuals", token=token)[1] or {}).get("items", [])
    checks["refusedUploadsStoredNothing"] = len(items) == 2

    # 5. tenant isolation: same answers as an unknown run.
    foreign_token = tpl.login(foreign_email)
    foreign = {}
    for label, call in {
        "list": lambda: api("GET", f"{base}/visuals", token=foreign_token),
        "item": lambda: api("GET", f"{base}/visuals/{before.get('id')}", token=foreign_token),
        "attach": lambda: raw("POST", f"{tpl.ORCHESTRATOR}{P}{base}/visuals?name=x.png&role=before",
                              data=before_png, token=foreign_token,
                              content_type="application/octet-stream"),
    }.items():
        foreign[label] = call()[0]
    evidence["foreignTeam"] = foreign
    checks["foreignTeamGets404"] = all(st == 404 for st in foreign.values())

    # 6. sharing a visual: a download, never a rendered page.
    status, minted = api("POST", f"{base}/artifacts/{before.get('id')}/share", token=token,
                         body={})
    share = (minted or {}).get("share") or {}
    share_url = f"{tpl.ORCHESTRATOR}{share.get('url', '')}"
    anon_status, anon_headers, anon_body = raw("GET", share_url)
    lower = {k.lower(): v for k, v in anon_headers.items()}
    checks["shareMinted"] = status == 201 and bool(share.get("token"))
    checks["anonymousDownloadServesTheBytes"] = anon_status == 200 and anon_body == before_png
    checks["servedAsDownloadNotDocument"] = (
        (lower.get("content-type") or "").startswith("application/octet-stream")
        and (lower.get("content-disposition") or "").startswith("attachment;")
        and lower.get("x-content-type-options") == "nosniff"
        and "sandbox" in (lower.get("content-security-policy") or ""))
    checks["shaHeaderMatchesTheImage"] = lower.get("x-artifact-sha256") == before.get("sha256")
    evidence["share"] = {
        "headers": {k: lower.get(k) for k in
                    ("content-type", "content-disposition", "x-content-type-options",
                     "content-security-policy", "x-artifact-sha256")},
        "revokeStatus": api("POST", f"{base}/shares/{share.get('id')}/revoke",
                            token=token)[0],
        "afterRevoke": raw("GET", share_url)[0],
    }
    checks["revokedShareDies"] = evidence["share"]["afterRevoke"] == 404

    # 7. retention: expiry is enforced on read, and the purge makes it durable.
    # Distinct bytes on purpose: the store is content-addressed, and a second copy of
    # the same image would share the first one's blob (so the blob would rightly stay).
    retiring_png = png_header(320, 240)
    status, made_old = upload(retiring_png, name="retiring.png", role="before", retention=1)
    retiring = (made_old or {}).get("visual") or {}
    status, old_share = api("POST", f"{base}/artifacts/{retiring.get('id')}/share",
                            token=token, body={})
    old_token = ((old_share or {}).get("share") or {}).get("url", "")
    old_url = f"{tpl.ORCHESTRATOR}{old_token}"
    psql(f"UPDATE artifacts SET retention_until = '2000-01-01T00:00:00+00:00'"
         f" WHERE id = '{retiring.get('id')}'")
    items = (api("GET", f"{base}/visuals", token=token)[1] or {}).get("items", [])
    expired_state = next((i.get("state") for i in items if i.get("id") == retiring.get("id")),
                         None)
    expired_content = raw("GET", f"{tpl.ORCHESTRATOR}{P}{base}/artifacts/"
                                 f"{retiring.get('id')}/content", token=token)
    expired_share = raw("GET", old_url)
    evidence["retention"] = {
        "stateAfterBackdate": expired_state,
        "contentStatus": expired_content[0],
        "contentCode": err_code(expired_content[0], json.loads(expired_content[2] or b"{}")),
        "shareStatus": expired_share[0],
        "shareCode": err_code(expired_share[0], json.loads(expired_share[2] or b"{}")),
    }
    checks["expiredIsGoneOnRead"] = expired_state == "expired"
    checks["expiredContentIs410"] = expired_content[0] == 410
    checks["expiredShareIs410"] = expired_share[0] == 410

    if args.skip_purge:
        evidence["retention"]["purge"] = {"skipped": True}
    else:
        ref = psql(f"SELECT storage_ref FROM artifacts WHERE id = '{retiring.get('id')}'")
        root = subprocess.run(
            ["docker", "exec", "harness-orchestrator-1", "printenv", "HARNESS_ARTIFACT_ROOT"],
            capture_output=True, text=True, check=False).stdout.strip() or "/tmp/harness_artifacts"
        script = (
            "import asyncio, json, sys\n"
            "from types import SimpleNamespace\n"
            "sys.path.insert(0, '/opt/harness/deploy/runners')\n"
            "from harness_orchestrator import artifacts\n"
            "from services import get_factory\n"
            # The sweep drops blobs through the module's own factory (normally the running
            # app's service), so a one-off process must wire it, not just pass the factory.
            "artifacts.configure_artifacts(SimpleNamespace(session_factory=get_factory()))\n"
            "print(json.dumps({'purged': asyncio.run("
            "artifacts.purge_expired(get_factory()))}))\n"
        )
        proc = subprocess.run(
            ["docker", "exec", "-i", "harness-orchestrator-1", "python3", "-c", script],
            capture_output=True, text=True, check=False)
        purged = None
        for line in reversed(proc.stdout.splitlines()):
            if line.startswith("{"):
                purged = json.loads(line)
                break
        after_purge = raw("GET", f"{tpl.ORCHESTRATOR}{P}{base}/artifacts/"
                                 f"{retiring.get('id')}/content", token=token)
        blob_gone = subprocess.run(
            ["docker", "exec", "harness-orchestrator-1", "test", "-f", f"{root}/{ref}"],
            capture_output=True, text=True, check=False).returncode != 0
        items = (api("GET", f"{base}/visuals", token=token)[1] or {}).get("items", [])
        state_after = next((i.get("state") for i in items if i.get("id") == retiring.get("id")),
                           None)
        evidence["retention"]["purge"] = {
            "skipped": False, "result": purged, "storageRef": ref,
            "stateAfterPurge": state_after,
            "contentStatusAfterPurge": after_purge[0],
            "contentCodeAfterPurge": err_code(
                after_purge[0], json.loads(after_purge[2] or b"{}")),
            "blobGone": blob_gone, "stderr": proc.stderr.strip()[:200],
        }
        checks["purgeTombstonesTheRow"] = (
            isinstance(purged, dict) and purged.get("purged", 0) >= 1
            and state_after in ("deleted", "expired"))
        checks["purgeRemovesTheBlob"] = blob_gone
        checks["purgedContentIsGone"] = after_purge[0] in (410, 404)

    ids = [before.get("id"), after.get("id"), retiring.get("id")]
    listing = ", ".join(f"'{i}'" for i in ids if i)
    evidence["audit"] = {"rows": psql(
        "SELECT action, count(*) FROM audit_log WHERE target_id IN ("
        f"{listing}) GROUP BY action ORDER BY action")}
    checks["auditRecordsTheAttach"] = "visual.attached" in evidence["audit"]["rows"]

    evidence["checks"] = checks
    evidence["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"w9-visual-evidence-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"checks": checks, "verdict": evidence["verdict"]}, indent=1))
    print(f"{evidence['verdict']}  evidence -> {path.relative_to(REPO)}")
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
