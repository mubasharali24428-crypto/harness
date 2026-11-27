#!/usr/bin/env python3
"""Bulk shell-gate drill (G-B exit check): >=100k generated hostile commands.

Stdlib-only and deterministic (seeded RNG), so it runs in CI without Hypothesis
and can run inside the worker image against the DEPLOYED gate, not just HEAD.
The invariant, same as test_shell_gate_fuzz.py: a command that escapes the
intent scope must come back denied, or executed-without-canary. Writes a JSON
evidence file (counts, per-code histogram, any violations).

Usage: python3 shell_gate_fuzz_drill.py [--cases 100000] [--gate PATH]
       [--evidence PATH]  (default gate: repo HEAD shell_gate.py, real run())
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ESCAPES = [
    "../../etc/passwd", "../../etc/hosts", "../out.tar", "~/x", "~/", "~root/x",
    "$HOME/.ssh/id_rsa", "${HOME}/.aws/credentials", "$PWD/../../x", "$HOME$",
    "/etc/hostname", "/etc/shadow", "/etc/passwd", "/proc/1/environ",
    "/proc/self/environ", "/root/.bashrc", "..%2f..%2fetc%2fpasswd",
    "....//....//etc/passwd", "a/../../b", "./" * 8 + "etc/passwd",
    "/var/run/docker.sock", "/run/secrets/db_password", "~+/x", "~-/x",
    "$UNSET_VAR/x", "/etc/kubernetes/admin.conf", "~/Library/Keychains/login.keychain",
]
WRAPPERS = ["{}", "--flag={}", "-o{}", "--output={}", '"{}"', "'{}'", "$({})",
            "$(echo {})", "a=b {}", "$(echo a) {}", "{} 2>&1", "{}/", "{}.",
            "{}..", "-e {}", "--={}", "--long={}", "{};", "{} && cat x", "{}``"]
PROGRAMS = ["cat", "ls", "head", "tail", "python3", "python", "node", "grep", "find"]
SCOPED = ["docs", "docs/y.txt", "src", "src/app.py", "notes.md"]
UNICODE_BITS = ["\u200b", "\u200d", "\ufeff", "\uff0f", "\u2215", "\t"]


def generate(rng: random.Random) -> str:
    program = rng.choice(PROGRAMS)
    roll = rng.random()
    if roll < 0.55:
        arg = rng.choice(ESCAPES)
    elif roll < 0.75:
        arg = rng.choice(SCOPED)
    else:
        arg = "".join(rng.choice(ESCAPES + SCOPED) for _ in range(rng.randint(2, 3)))
        arg = rng.choice(UNICODE_BITS) + arg if rng.random() < 0.3 else arg
    return f"{program} {rng.choice(WRAPPERS).format(arg)}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=int, default=100_000)
    parser.add_argument("--gate", default=str(REPO_ROOT / "section5/src/harness_sandbox/shell_gate.py"))
    parser.add_argument("--evidence", default=str(
        REPO_ROOT / "docs/briefs/evidence/security/shell-gate-fuzz-drill.json"))
    parser.add_argument("--seed", type=int, default=20261121)
    args = parser.parse_args()

    import importlib.util

    spec = importlib.util.spec_from_file_location("gate", args.gate)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)

    rng = random.Random(args.seed)
    ws = Path(tempfile.mkdtemp(prefix="gate-fuzz-"))
    (ws / "docs").mkdir()
    (ws / "docs" / "y.txt").write_text("in-scope-ok")
    # .env is deliberately ABSENT: a planted forbidden file would trip the
    # gate's FORBIDDEN_PATH_EXPOSED environment guard for every command and
    # starve the decision layer of coverage. forbiddenPaths still names it.
    canary_file = Path(tempfile.mkdtemp(prefix="gate-canary-")) / "canary.txt"
    canary_file.write_text("F-UZZ-CANARY-DO-NOT-LEAK")

    codes: collections.Counter = collections.Counter()
    allowed = 0
    violations: list[dict] = []
    for i in range(args.cases):
        command = generate(rng)
        try:
            result = gate.run({
                "command": command,
                "cwd": str(ws),
                "forbiddenPaths": [".env"],
                "scopePaths": [str(ws)],
            })
        except Exception as exc:  # noqa: BLE001 - a crash IS a finding here
            violations.append({"i": i, "command": command, "error": repr(exc)})
            codes["CRASH"] += 1
            continue
        code = result.get("code") or ("EXECUTED" if "exitCode" in result else "?")
        codes[code] += 1
        executed = "exitCode" in result and not result.get("denied")
        if executed:
            allowed += 1
            out = result.get("output") or ""
            if "F-UZZ-CANARY-DO-NOT-LEAK" in out:
                violations.append({"i": i, "command": command, "output": out[:200]})

    evidence = {
        "drill": "shell-gate bulk fuzz (G-B, P2)",
        "executedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gate": args.gate,
        "cases": args.cases,
        "seed": args.seed,
        "host": sys.platform,
        "allowed": allowed,
        "verdictCodes": dict(codes),
        "violations": violations[:20],
        "violationCount": len(violations),
        "invariant": "an executed command's output never contains the out-of-scope canary or the forbidden .env content",
    }
    Path(args.evidence).write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps({"cases": args.cases, "allowed": allowed,
                      "violations": len(violations), "codes": dict(codes)}, indent=1))
    shutil.rmtree(ws, ignore_errors=True)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
