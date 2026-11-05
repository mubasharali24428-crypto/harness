#!/usr/bin/env python3
"""W25/W26: run an eval task's goal on ANOTHER harness (opencode), graded by the SAME verifier.

The roadmap allows a harness comparison only under the same task, model, budget and permissions.
This script holds what it can equal and says what it cannot:

* same task: the goal text and generated fixture come from `tasks/code_change_task.py`;
* same grader: the verifier block of that file is executed here, unchanged, on the result;
* same model: `anthropic/claude-haiku-5.5` through the same OpenRouter key;
* same price: tokens are priced at the rate this stack's ledger charged for that model
  (see RATE_*), because opencode has no price for a model its catalogue does not list;
* NOT the same: tool set, system prompt, and the permission layer. The harness runs commands
  through its gate (allow-list, scope mounts, container); opencode here runs in a scratch
  directory with an allow-list in its own permission config, no container, no tool-call cap.

opencode is isolated from the user's own config (that file is not read or changed): scratch XDG
directories, a one-model config, and the key passed by environment only.

    OPENROUTER_API_KEY=... python3 scripts/eval/compare_harness.py --task refactor
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "scripts" / "eval" / "tasks" / "code_change_task.py"
OUT = REPO / "docs" / "briefs" / "evidence" / "w33-compare"
MODEL = "anthropic/claude-haiku-5.5"
RATE_IN, RATE_OUT = 0.10 / 1e6, 0.50 / 1e6  # USD per token, as this stack's cost_ledger charged it

CONFIG = {
    "$schema": "https://opencode.ai/config.json",
    "provider": {"openrouter": {"models": {MODEL: {"name": "Claude Haiku 5.5"}}}},
    "permission": {
        "edit": "allow",
        "webfetch": "deny",
        "bash": {"python3 *": "allow", "python *": "allow", "ls *": "allow", "cat *": "allow",
                 "grep *": "allow", "rg *": "allow", "find *": "allow", "pwd": "allow",
                 "*": "deny"},
    },
}


def load_task(name: str):
    text = SRC.read_text(encoding="utf-8")
    block = text[text.index("# --- verifier block begin"):text.index("# --- verifier block end")]
    ns: dict = {"__name__": "verifier"}
    exec(compile(block, str(SRC), "exec"), ns)  # noqa: S102 - stdlib-only block, shipped as is
    goals = {"refactor": ("REFACTOR_GOAL", "SHOP", "verify_refactor"),
             "tests-written": ("TESTS_GOAL", "PAGER", "verify_tests")}
    # the goals and the table sit below the block in the driver; read them without running main
    tail = text[text.index("REFACTOR_GOAL ="):text.index("TASKS = {")]
    exec(compile(tail, str(SRC), "exec"), ns)  # noqa: S102 - two string constants
    goal, files, verify = goals[name]
    return ns[goal], ns[files], ns[verify]


def parse_events(stdout: str) -> dict:
    tokens_in = tokens_out = tools = 0
    answer = ""
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        part = ev.get("part") or {}
        if ev.get("type") == "tool_use":
            tools += 1
        if ev.get("type") == "step_finish":
            t = part.get("tokens") or {}
            cache = t.get("cache") or {}
            tokens_in += int(t.get("input") or 0) + int(cache.get("read") or 0) \
                + int(cache.get("write") or 0)
            tokens_out += int(t.get("output") or 0) + int(t.get("reasoning") or 0)
        if ev.get("type") == "text":
            answer = part.get("text") or answer
    return {"toolCalls": tools, "inputTokens": tokens_in, "outputTokens": tokens_out,
            "pricedUsd": round(tokens_in * RATE_IN + tokens_out * RATE_OUT, 6),
            "answer": answer[:400]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=("refactor", "tests-written"), required=True)
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--scratch", required=True, help="directory for opencode's private config")
    args = ap.parse_args()
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set")

    goal, files, verify = load_task(args.task)
    scratch = Path(args.scratch)
    cfg = scratch / "config" / "opencode"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "opencode.json").write_text(json.dumps(CONFIG, indent=2))
    env = {"PATH": os.environ["PATH"], "HOME": str(scratch / "home"), "OPENROUTER_API_KEY": key,
           "XDG_CONFIG_HOME": str(scratch / "config"), "XDG_DATA_HOME": str(scratch / "data"),
           "XDG_CACHE_HOME": str(scratch / "cache"), "XDG_STATE_HOME": str(scratch / "state")}
    (scratch / "home").mkdir(exist_ok=True)

    ws = Path(tempfile.mkdtemp(prefix=f"cmp-{args.task}-"))
    try:
        for rel, text in files.items():
            (ws / rel).parent.mkdir(parents=True, exist_ok=True)
            (ws / rel).write_text(text, encoding="utf-8")
        started = time.monotonic()
        try:
            proc = subprocess.run(
                ["opencode", "run", "--format", "json", "-m", f"openrouter/{MODEL}",
                 "--dir", str(ws), goal],
                cwd=ws, env=env, capture_output=True, text=True, timeout=args.timeout, check=False)
            stdout, rc = proc.stdout, proc.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, rc = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) \
                else (exc.stdout or ""), 124
        seconds = round(time.monotonic() - started, 2)
        measured = parse_events(stdout)
        verified = verify(str(ws))
        checks = verified.pop("checks")
        evidence = {
            "schema": 1, "harness": "opencode " + subprocess.run(
                ["opencode", "--version"], capture_output=True, text=True, env=env,
                check=False).stdout.strip(),
            "task": args.task, "model": MODEL, "exitCode": rc, "durationSeconds": seconds,
            **measured, "checks": checks, "failedChecks": [k for k, v in checks.items() if not v],
            "verdict": "PASS" if rc == 0 and all(checks.values()) else "FAIL",
            "changed": verified.get("changed"), "diff": verified.get("diff"),
            "notSameAsHarness": [
                "tool set", "system prompt",
                "permission layer (own allow-list, no container, no tool-call cap)"],
        }
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"opencode-{args.task}-{int(time.time())}.json"
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in evidence.items() if k != "diff"}, indent=1))
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
