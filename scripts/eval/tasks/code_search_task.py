#!/usr/bin/env python3
"""W2 live gate: find the right module in a repo and change one value.

The agent is given a repository with a wrong constant — ``RETRY_BACKOFF_SECONDS``
is 5 and must be 7 — and no path. It must locate the defining module and change
only that value. The verdict is the disk, not the answer text:

* the target file contains ``RETRY_BACKOFF_SECONDS = 7``;
* every other seeded file is byte-identical to what was seeded;
* the answer names the target path (supplementary, not the verdict).

    python3 scripts/eval/tasks/code_search_task.py [--timeout 900]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "docs" / "briefs" / "evidence" / "eval"
TARGET = "pkg/billing/invoice_retry.py"
SEEDED_FILES = 100

spec = importlib.util.spec_from_file_location(
    "w11", REPO / "scripts" / "e2e" / "w11_vision_drill.py")
w11 = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(w11)
w10 = w11.w10

GOAL = """This repository has a wrong value: RETRY_BACKOFF_SECONDS is set to 5 and
must be 7. Find the module that defines it and change only that value. Do not
modify any other file. Reply with exactly one line:
FILE: <the path of the file you changed>
"""

TARGET_BODY = '''"""Invoice retry policy."""

RETRY_BACKOFF_SECONDS = 5
MAX_RETRIES = 4


def next_attempt_delay(attempt: int) -> int:
    """Retry backoff for an unpaid invoice."""
    return RETRY_BACKOFF_SECONDS * attempt


def should_retry_invoice(attempt: int) -> bool:
    # invoice retry backoff schedule
    return attempt < MAX_RETRIES
'''

DECOYS = {
    "pkg/billing/invoice_report.py": (
        '"""Invoice report."""\n\ndef report():\n'
        "    # retry data is read elsewhere\n    return {}\n"
    ),
    "pkg/jobs/retry_queue.py": (
        '"""Retry queue."""\n\nRETRY_LIMIT = 3\n\n'
        "def enqueue(job):\n    return job\n"
    ),
    "pkg/net/retry_policy.py": '"""Network retry policy."""\n\nNETWORK_RETRY = 2\n',
    "pkg/auth/session_rotation.py": '"""Session rotation."""\n\nROTATE_AFTER = 3600\n',
    "pkg/telemetry/flush.py": '"""Telemetry flush."""\n\nFLUSH_INTERVAL = 30\n',
}


def build_fixture() -> dict[str, bytes]:
    files = {TARGET: TARGET_BODY.encode()}
    for rel, body in DECOYS.items():
        files[rel] = body.encode()
    for index in range(SEEDED_FILES - len(files)):
        rel = f"pkg/modules/module_{index:03d}.py"
        files[rel] = (
            f'"""Module {index}."""\n\nTOPIC_{index:03d} = "routine_{index:03d}"\n\n'
            f"def routine_{index:03d}(value: int) -> int:\n    return value + {index}\n"
        ).encode()
    return files


def seed_tree(run_id: str, files: dict[str, bytes]) -> None:
    payload = {rel: base64.b64encode(blob).decode() for rel, blob in files.items()}
    code = (
        "import base64, json, os\n"
        f"root = {w11.WORKSPACE_ROOT + '/' + run_id!r}\n"
        f"files = json.loads({json.dumps(payload)!r})\n"
        "for rel, b64 in files.items():\n"
        "    p = os.path.join(root, rel)\n"
        "    os.makedirs(os.path.dirname(p), exist_ok=True)\n"
        "    open(p, 'wb').write(base64.b64decode(b64))\n"
        "print(len(files))\n"
    )
    w10.in_orchestrator(code)


def tree_hashes(run_id: str) -> dict[str, str]:
    code = (
        "import hashlib, json, os\n"
        f"root = {w11.WORKSPACE_ROOT + '/' + run_id!r}\n"
        "rows = {}\n"
        "for r, _, fs in os.walk(root):\n"
        "    for f in fs:\n"
        "        p = os.path.join(r, f)\n"
        "        rel = os.path.relpath(p, root).replace(os.sep, '/')\n"
        "        rows[rel] = hashlib.sha256(open(p, 'rb').read()).hexdigest()\n"
        "print(json.dumps(rows))\n"
    )
    return json.loads(w10.in_orchestrator(code).strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=900.0)
    args = ap.parse_args()
    stamp = int(time.time())
    run_id = f"w2-code-search-{stamp}"
    email = f"w2-{stamp}@example.com"
    files = build_fixture()
    seeded = {rel: hashlib.sha256(blob).hexdigest() for rel, blob in files.items()}

    login = w11.tpl.login(email)
    status, project = w11.tpl.http("POST", f"{w11.tpl.IDENTITY}/api/v1/projects",
                                   token=login, body={"name": f"w2 cs {stamp}"})
    if status != 201:
        raise SystemExit(f"create project failed: {status} {project!r}")

    seed_tree(run_id, files)
    started = time.monotonic()
    result = w11.run_goal(run_id, login, project["id"], GOAL, max_tool_calls=20,
                          timeout=args.timeout)
    duration = round(time.monotonic() - started, 2)
    calls = w11.tpl.audit_tool_events(run_id)
    tool_calls = [c for c in calls if c.get("phase") == "call"] or [
        c for c in calls if c.get("type") == "tool_call"]
    _, engine_body = w11.api("GET", f"/runs/{run_id}/engine", token=login)
    engine = engine_body if isinstance(engine_body, dict) else {}
    after = tree_hashes(run_id)
    target_text = w10.workspace_file(run_id, TARGET) or ""
    changed = sorted(rel for rel, digest in seeded.items()
                     if after.get(rel) != digest)
    added = sorted(rel for rel in after if rel not in seeded)
    answer = result.get("answer") or ""

    checks = {
        "runSettled": result.get("state") in w10.SETTLED,
        "targetHasNewValue": "RETRY_BACKOFF_SECONDS = 7" in target_text,
        "onlyTargetChanged": changed == [TARGET],
        "answerNamesTheFile": "invoice_retry.py" in answer,
        "noFilesDeleted": all(rel in after for rel in seeded),
    }
    evidence = {
        "schema": 1,
        "kind": "w2-code-search",
        "takenAt": datetime.now(UTC).isoformat(),
        "runId": run_id,
        "goal": GOAL,
        "model": "the stack DSH_MODEL",
        "seededFiles": len(seeded),
        "changedFiles": changed,
        "addedFiles": added,
        "state": result.get("state"),
        "states": result.get("states"),
        "durationSeconds": duration,
        "toolCalls": len(tool_calls),
        "toolsUsed": sorted({c.get("tool") or c.get("name") for c in tool_calls}),
        "usage": engine.get("usage"),
        "usageNote": ("null on this transport per the M07 finding; wall clock and "
                      "tool calls are recorded instead"),
        "answer": answer[:1200],
        "checks": checks,
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "failedChecks": [k for k, v in checks.items() if not v],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"code-search-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": evidence["verdict"],
                      "failed": evidence["failedChecks"],
                      "changed": changed, "added": added[:5],
                      "evidence": str(path)}, indent=2))
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
