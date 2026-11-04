#!/usr/bin/env python3
"""W4 live drill: the web gate's decision, against the real internet.

Runs Section 5's ``web_gate.py`` exactly as the engine plugin does (JSON on
stdin, one JSON line out) for four destinations:

* an allow-listed provider docs page  -> HTTP 200 + readable text
* a host outside the list              -> denied before dialing
* plain HTTP on an allow-listed host   -> denied (https only)
* a non-443 port on an allow-listed host -> denied

Every run writes its own file (``w4-web-fetch-<epoch>.json``): a later run must
never overwrite the sample a claim rests on.

Honest scope: this exercises the *decision and the fetch*. It does not prove
that a confined worker cannot bypass the gate — that is the shell/confinement
boundary, and the gate's own process has ordinary network access here.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "section5" / "src" / "harness_sandbox" / "web_gate.py"
PYTHON = REPO / "section5" / ".venv" / "bin" / "python"
OUT = REPO / "docs" / "briefs" / "evidence" / "pilot-prep"

CASES = [
    # A docs page that answers directly. platform.claude.com is allow-listed too,
    # but its docs reader timed out once from this network; a live drill should
    # not measure a CDN's mood, so the allowed case uses the fast origin.
    ("allowed_docs", "https://api-docs.deepseek.com/"),
    ("denied_host", "https://example.com/"),
    ("denied_scheme", "http://api-docs.deepseek.com/"),
    ("denied_port", "https://api-docs.deepseek.com:8443/"),
]


def run(url: str) -> dict:
    started = time.time()
    proc = subprocess.run(
        [str(PYTHON), "-I", str(GATE)],
        input=json.dumps({"url": url, "timeoutSeconds": 20}),
        capture_output=True,
        text=True,
        timeout=60,
    )
    elapsed = round(time.time() - started, 2)
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        result = {"error": f"gate produced no JSON (exit {proc.returncode}): {proc.stderr[-400:]}"}
    if isinstance(result.get("text"), str):
        result["textChars"] = len(result.pop("text"))
    result["seconds"] = elapsed
    return result


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    evidence = {
        "drill": "w4-web-fetch",
        "takenAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scope": "gate decision + live fetch; not a confinement boundary test",
        "gate": str(GATE.relative_to(REPO)),
        "cases": {name: {"url": url, **run(url)} for name, url in CASES},
    }
    allowed = evidence["cases"]["allowed_docs"]
    evidence["verdict"] = (
        "pass"
        if allowed.get("status") == 200
        and allowed.get("textChars", 0) > 200
        and all("denied" in evidence["cases"][n] for n in ("denied_host", "denied_scheme", "denied_port"))
        else "fail"
    )
    path = OUT / f"w4-web-fetch-{int(time.time())}.json"
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    print(f"\nwrote {path.relative_to(REPO)}")
    return 0 if evidence["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
