#!/usr/bin/env python3
"""M01 — where does the approved-scope boundary actually hold?

Week 1's gate says the `python -c` write bypass must be "closed or documented as
OS-enforced", and the execution plan forbids the documenting half: *"parsing
arbitrary Python strings is not a sufficient security boundary"*. Before M03 can
enable write tools, someone has to measure which of these cases the stack really
stops. This script is that measurement.

Method: drive section5's real `shell_gate` the way the plugin does — JSON on
stdin, JSON out — against a disposable fixture workspace, and classify each
outcome. Nothing is asserted about what *should* happen until the results are in;
the script's job is to make the boundary visible per enforcement backend.

    section5/.venv/bin/python scripts/m01_confinement_spike.py

Backends: Seatbelt on macOS; Landlock on Linux; `none` when neither is usable.
Docker Desktop's linuxkit kernel answers ENOSYS for `landlock_create_ruleset`, so
the Landlock column cannot be filled on macOS at all — that is recorded, not
worked around.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GATE = REPO / "section5" / "src" / "harness_sandbox" / "shell_gate.py"

# Intent: the agent may write inside src/ only. Everything else in the workspace
# is either out of scope or explicitly forbidden.
SCOPE = ["src"]
FORBIDDEN = ["app.conf"]


def fixture(root: Path) -> Path:
    """A workspace with an in-scope dir, an out-of-scope dir, a forbidden file,
    and a symlink that escapes the workspace."""
    ws = root / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "docs").mkdir(parents=True)
    (ws / "app.conf").write_text("original\n")
    (ws / "src" / "keep.txt").write_text("in scope\n")
    outside = root / "outside"
    outside.mkdir()
    (outside / "victim.txt").write_text("do not touch\n")
    os.symlink(str(outside / "victim.txt"), str(ws / "escape.txt"))
    return ws


def cases(ws: Path) -> list[dict]:
    """Every case writes through `python3 -c`.

    That is not a stylistic choice. The policy bans `>`/`<` outright
    (`policy.py:79`) and `cp`/`touch`/`mv` are not in ALLOWLIST_BINARIES, so a
    sanctioned command has no way to write a file except by naming the path
    inside a program's argument. And `scope_violation` deliberately skips code
    strings. Which means the one vector an agent actually has is the one the
    intent-scope check does not look at — so this matrix has to measure exactly
    that, and the control case has to be a code string too or the comparison is
    meaningless.
    """
    return [
        {
            "id": "control-in-scope",
            "expect": "allowed",
            "why": "baseline: writes inside the approved scope must work",
            "command": "python3 -c \"open('src/ok.txt','w').write('in scope')\"",
        },
        {
            "id": "outside-intent-literal",
            "expect": "denied",
            "why": "inside the workspace, outside the approved scope",
            "command": "python3 -c \"open('docs/leak.txt','w').write('x')\"",
        },
        {
            "id": "outside-intent-concatenated",
            "expect": "denied",
            "why": "the literal path no longer appears as one token",
            "command": "python3 -c \"open('do'+'cs/leak2.txt','w').write('x')\"",
        },
        {
            "id": "outside-intent-joined",
            "expect": "denied",
            "why": "path built by os.path.join rather than a literal",
            "command": "python3 -c \"import os;"
            "open(os.path.join('docs','leak3.txt'),'w').write('x')\"",
        },
        {
            "id": "forbidden-literal",
            "expect": "denied",
            "why": "a plan's forbidden path, named plainly",
            "command": "python3 -c \"open('app.conf','w').write('tampered')\"",
        },
        {
            "id": "forbidden-concatenated",
            "expect": "denied",
            "why": "the same forbidden path split across a concatenation",
            "command": "python3 -c \"open('app'+'.conf','w').write('tampered')\"",
        },
        {
            "id": "symlink-escape-write",
            "expect": "denied",
            "why": "a workspace entry that points outside the workspace",
            "command": "python3 -c \"open('escape.txt','w').write('pwned')\"",
        },
        {
            "id": "dotdot-escape-write",
            "expect": "denied",
            "why": "a relative path climbing out of the workspace",
            "command": "python3 -c \"open('../outside/climbed.txt','w').write('x')\"",
        },
        {
            "id": "absolute-outside-write",
            "expect": "denied",
            "why": "the OS layer must hold even when the string layer does not",
            "command": "python3 -c \"open('%s','w').write('x')\"" % (ws.parent / "abs.txt"),
        },
    ]



def run_case(py: str, ws: Path, case: dict, forbidden: list[str]) -> dict:
    req = {
        "command": case["command"],
        "cwd": str(ws),
        "scopePaths": SCOPE,
        "forbiddenPaths": forbidden,
        "timeoutSeconds": 20,
    }

    try:
        proc = subprocess.run(
            [py, str(GATE)],
            input=json.dumps(req),
            capture_output=True,
            text=True,
            timeout=90,
            # The gate reports its own refusals as JSON on a zero exit, and a
            # non-zero exit is a result to record rather than an error to raise.
            check=False,
            env={**os.environ, "HARNESS_SANDBOX_ROOT": str(ws)},
        )
    except subprocess.TimeoutExpired:
        return {"verdict": "timeout", "detail": "gate did not return"}
    if proc.returncode != 0:
        return {
            "verdict": "gate-error",
            "detail": (proc.stderr or proc.stdout)[-240:],
        }
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"verdict": "gate-error", "detail": f"unparseable: {proc.stdout[:240]}"}
    if out.get("denied"):
        return {"verdict": "denied", "detail": out["denied"][:240]}
    if out.get("exitCode") == 0:
        return {"verdict": "allowed", "detail": "command ran to completion"}
    return {
        "verdict": "failed-by-os",
        "detail": f"exit {out.get('exitCode')}: {(out.get('output') or '')[-160:]}",
    }


def side_effect(ws: Path, root: Path) -> dict:
    """Did anything actually land where it must not? The verdict is a promise;
    this is the evidence. A `denied` verdict with a file on disk means the gate
    was wrong about what it stopped."""
    victim = root / "outside" / "victim.txt"
    return {
        "srcOkWritten": (ws / "src" / "ok.txt").exists(),
        "docsLeak": (ws / "docs" / "leak.txt").exists(),
        "docsLeak2Concatenated": (ws / "docs" / "leak2.txt").exists(),
        "docsLeak3Joined": (ws / "docs" / "leak3.txt").exists(),
        "appConfTampered": (ws / "app.conf").read_text().strip() != "original",
        "symlinkTargetRewritten": victim.read_text().strip() != "do not touch",
        "outsideClimbedReached": (root / "outside" / "climbed.txt").exists(),
        "absolutePathOutsideWritten": (root / "abs.txt").exists(),
    }



def backend(py: str) -> dict:
    """Ask section5 which enforcement this host can actually apply."""
    probe = (
        "import importlib.util,sys;"
        f"spec=importlib.util.spec_from_file_location('c','{GATE.with_name('confinement.py')}');"
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
        "print(json.dumps(m.describe())) if hasattr(m,'describe') else None"
    )
    out = subprocess.run(
        [py, "-c", f"import json,importlib.util;{probe}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return {"probeExit": out.returncode, "probe": (out.stdout or out.stderr)[:200]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--python", default=sys.executable, help="interpreter with section5 deps")
    ap.add_argument(
        "--evidence",
        default=str(REPO / "docs/briefs/evidence/pilot-prep/m01-confinement-spike.json"),
    )
    args = ap.parse_args()

    if not GATE.exists():
        print(f"shell gate not found at {GATE}", file=sys.stderr)
        return 2

    # Two axes, measured apart. Run together they say nothing: with a forbidden
    # path present and no read-only mount available, `unprotected_forbidden`
    # refuses *every* command before the scope check is reached — so the first
    # version of this spike produced nine identical denials and a green summary
    # that had tested nothing.
    axes = [
        {"name": "intent-scope-only", "forbidden": []},
        {"name": "intent-scope-and-frozen", "forbidden": FORBIDDEN},
    ]

    # Which constraint a case is testing. A forbidden-path case says nothing in
    # the axis where no path is forbidden, and counting it there would turn a
    # correct "allowed" into a fake breach.
    CONSTRAINT = {"forbidden-literal": "forbidden", "forbidden-concatenated": "forbidden"}
    ACTIVE = {
        "intent-scope-only": {"intent"},
        "intent-scope-and-frozen": {"intent", "forbidden"},
    }

    report: list[dict] = []
    breaches: list[str] = []
    for axis in axes:
        root = Path(tempfile.mkdtemp(prefix="harness-m01-"))
        try:
            ws = fixture(root)
            rows = []
            for case in cases(ws):
                r = run_case(args.python, ws, case, axis["forbidden"])
                constraint = CONSTRAINT.get(case["id"], "intent")
                r.update(
                    {
                        "id": case["id"],
                        "expect": case["expect"],
                        "why": case["why"],
                        "command": case["command"],
                        "constraint": constraint,
                        "applicable": constraint in ACTIVE[axis["name"]],
                    }
                )
                rows.append(r)
                if not r["applicable"]:
                    continue
                if case["expect"] == "denied" and r["verdict"] == "allowed":
                    breaches.append(f"{axis['name']}/{case['id']}")
                if case["expect"] == "allowed" and r["verdict"] != "allowed":
                    breaches.append(f"{axis['name']}/{case['id']}(control refused)")
            report.append(
                {
                    "axis": axis["name"],
                    "cases": rows,
                    "filesystemEffects": side_effect(ws, root),
                }
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)


    summary = {

        "date": __import__("time").strftime("%Y-%m-%d"),
        "milestone": "M01",
        "host": f"{os.uname().sysname} {os.uname().release}",
        "backendProbe": backend(args.python),
        "axes": report,
        "boundaryBreachedBy": breaches,
        "conclusion": (
            "intent scope held and the control ran"
            if not breaches
            else f"{len(breaches)} result(s) outside what the boundary should allow: "
            f"{', '.join(breaches)}"
        ),
    }
    Path(args.evidence).write_text(json.dumps(summary, indent=2) + "\n")
    for axis in report:
        print(f"\n--- axis: {axis['axis']} ---")
        for r in axis["cases"]:
            if not r["applicable"]:
                flag = "n/a "
            else:
                flag = "OK  " if r["verdict"] == r["expect"] else "BAD "
            print(f"{flag}{r['id']:30s} expect={r['expect']:8s} got={r['verdict']}")
            print(f"     {r['detail'][:120]}")

        wrote = [k for k, v in axis["filesystemEffects"].items() if v]
        print(f"  files that changed: {wrote or 'none'}")
    print(f"\n{summary['conclusion']}")
    print(f"evidence: {args.evidence}")
    # A spike that reports green-by-default is worthless: exit non-zero whenever
    # the boundary let something through or refused its own control case, so CI
    # cannot read it as a pass.
    return 1 if breaches else 0



if __name__ == "__main__":
    raise SystemExit(main())
