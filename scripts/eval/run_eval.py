#!/usr/bin/env python3
"""Marathon 2.0 evaluation runner (W1).

Loads ``docs/eval/corpus.json``, executes the selected tasks' drivers, and writes
one normalized evidence file. The verdict is the driver's independent check plus
its exit code — never the agent's own answer.

    python3 scripts/eval/run_eval.py --list
    python3 scripts/eval/run_eval.py --cost infra          # no model spend
    python3 scripts/eval/run_eval.py --task w11-vision --include-live
    python3 scripts/eval/run_eval.py --include-live         # every implemented task

Every corpus task appears in the output: executed, deferred (live not allowed),
blocked (prerequisite unmet) or fixture-owed. A deferred task is not a pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "docs" / "eval" / "corpus.json"
OUT = REPO / "docs" / "briefs" / "evidence" / "eval"
OUTPUT_CAP = 6000  # chars kept per stream in the evidence file

# Runtime files whose deployed bytes must match the worktree before a result is
# called current-revision. Each entry: (label, host path, backend site-packages
# relative path, worker path).
RUNTIME_FILES = [
    ("engine.py", "section4/src/harness_orchestrator/engine.py",
     "harness_orchestrator/engine.py"),
    ("image_gate.py", "section5/src/harness_sandbox/image_gate.py",
     "harness_sandbox/image_gate.py"),
    ("harness-images.js",
     "section4/src/harness_orchestrator/dsh_plugins/harness-images.js",
     "harness_orchestrator/dsh_plugins/harness-images.js"),
]
WORKER_GATE_PATH = "/opt/harness-shell/image_gate.py"


# --------------------------------------------------------------------- helpers


def run(cmd: list[str], *, timeout: float = 60, cwd: Path | None = None,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd or REPO), env=env, text=True,
                          capture_output=True, timeout=timeout, check=False)


def docker_names(fragment: str) -> list[str]:
    proc = run(["docker", "ps", "--format", "{{.Names}}",
                "--filter", f"name={fragment}"], timeout=20)
    return [n for n in (proc.stdout or "").splitlines() if n.strip()]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def container_python_site(container: str) -> str | None:
    try:
        proc = run(["docker", "exec", container, "python", "-c",
                    ("import harness_orchestrator,os;print(os.path.dirname(os.path.dirname("
                     "harness_orchestrator.__file__)))")], timeout=30)
    except subprocess.TimeoutExpired:
        return None
    return (proc.stdout or "").strip() or None


def container_hash(container: str, path: str) -> str | None:
    try:
        proc = run(["docker", "exec", container, "sha256sum", path], timeout=30)
    except subprocess.TimeoutExpired:
        return None
    out = (proc.stdout or "").strip().split()
    return out[0] if out else None


def image_hash(image: str, path: str) -> str | None:
    try:
        proc = run(["docker", "run", "--rm", "--entrypoint", "sh", image,
                    "-c", f"sha256sum {path}"], timeout=120)
    except subprocess.TimeoutExpired:
        return None
    out = (proc.stdout or "").strip().split()
    return out[0] if out else None


def image_id(image: str) -> str | None:
    try:
        proc = run(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                   timeout=30)
    except subprocess.TimeoutExpired:
        return None
    return (proc.stdout or "").strip() or None


def worker_image() -> str:
    names = docker_names("worker-daemon")
    if names:
        proc = run(["docker", "inspect", names[0], "--format",
                    "{{range .Config.Env}}{{println .}}{{end}}"], timeout=30)
        for line in (proc.stdout or "").splitlines():
            if line.startswith("HARNESS_ENGINE_IMAGE="):
                return line.split("=", 1)[1].strip()
    return "harness-engine-worker:vdev-local"


def stack_model() -> str | None:
    """The model the DEPLOYED stack declares (orchestrator DSH_MODEL).

    The OpenRouter patch configures exactly one model per deployment, so an
    engine-driven task can only run that model. A comparison across other ids
    needs the stack redeployed per model — the runner says so instead of
    spending on runs the engine will refuse.
    """
    names = docker_names("orchestrator")
    if not names:
        return None
    proc = run(["docker", "inspect", names[0], "--format",
                "{{range .Config.Env}}{{println .}}{{end}}"], timeout=30)
    for line in (proc.stdout or "").splitlines():
        if line.startswith("DSH_MODEL="):
            return line.split("=", 1)[1].strip() or None
    return None


def declared_models() -> set[str]:
    """Model ids the deployed engine patches declare.

    The OpenRouter patch lists its models explicitly and the engine rejects
    anything else, so a comparison is only meaningful for ids in this set.
    ``!!js process.env.DSH_MODEL`` resolves to the deployed stack model.
    """
    declared: set[str] = set()
    patches = REPO / "deploy" / "patches"
    if not patches.is_dir():
        return declared
    for path in sorted(patches.glob("*.yml")):
        inside_models = False
        models_indent = -1
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            if stripped == "models:":
                inside_models, models_indent = True, indent
                continue
            if inside_models and indent <= models_indent:
                inside_models = False  # left the models block
            if not inside_models or not stripped.startswith("- id:"):
                continue
            value = stripped.split(":", 1)[1].strip()
            if value.startswith("!!js"):
                if "DSH_MODEL" in value:
                    model = stack_model()
                    if model:
                        declared.add(model)
                continue
            if value:
                declared.add(value.strip("\"'"))
    return declared


def git_revision() -> dict:
    head = run(["git", "rev-parse", "HEAD"], timeout=20).stdout.strip()
    branch = run(["git", "branch", "--show-current"], timeout=20).stdout.strip()
    dirty = bool(run(["git", "status", "--porcelain"], timeout=20).stdout.strip())
    return {"head": head, "branch": branch, "dirty": dirty}


def revision_check() -> dict:
    """Compare deployed runtime bytes with the worktree."""
    orchestrator = docker_names("orchestrator")
    container = orchestrator[0] if orchestrator else None
    site = container_python_site(container) if container else None
    backend: dict[str, dict] = {}
    for label, host_rel, rel in RUNTIME_FILES:
        host_path = REPO / host_rel
        host = sha256_file(host_path) if host_path.is_file() else None
        deployed = container_hash(container, f"{site}/{rel}") if site else None
        backend[label] = {"host": host, "deployed": deployed,
                          "match": bool(host and deployed and host == deployed)}

    image = worker_image()
    worker: dict[str, dict] = {}
    # image_gate.py lives in /opt/harness-shell in the worker; the rest in
    # site-packages, whose Python version differs from the backend's.
    gate_host = sha256_file(REPO / "section5/src/harness_sandbox/image_gate.py")
    gate_deployed = image_hash(image, WORKER_GATE_PATH)
    worker["image_gate.py"] = {"host": gate_host, "deployed": gate_deployed,
                               "match": bool(gate_host and gate_deployed
                                             and gate_host == gate_deployed)}
    site_proc = run(["docker", "run", "--rm", "--entrypoint", "python", image, "-c",
                     ("import harness_orchestrator,os;print(os.path.dirname(os.path.dirname("
                      "harness_orchestrator.__file__)))")], timeout=120)
    wsite = (site_proc.stdout or "").strip() or None
    for label, host_rel, rel in RUNTIME_FILES:
        if label == "image_gate.py":
            continue
        host = sha256_file(REPO / host_rel)
        deployed = image_hash(image, f"{wsite}/{rel}") if wsite else None
        worker[label] = {"host": host, "deployed": deployed,
                         "match": bool(deployed and host == deployed)}

    # The worker daemon imports engine.py and builds every worker itself, so a
    # stale daemon image makes a current worker image a false comfort.
    daemon: dict[str, dict] = {}
    daemons = docker_names("worker-daemon")
    dsite = container_python_site(daemons[0]) if daemons else None
    for label, host_rel, rel in RUNTIME_FILES if daemons else []:
        host = sha256_file(REPO / host_rel)
        path = WORKER_GATE_PATH if label == "image_gate.py" else f"{dsite}/{rel}"
        deployed = container_hash(daemons[0], path) if dsite else None
        daemon[label] = {"host": host, "deployed": deployed,
                         "match": bool(deployed and host == deployed)}
    return {
        "orchestratorContainer": container,
        "workerImage": image,
        "workerDaemonContainer": daemons[0] if daemons else None,
        "backend": backend,
        "worker": worker,
        "daemon": daemon,
        "verified": all(v["match"] for v in backend.values())
                    and all(v["match"] for v in worker.values())
                    and all(v["match"] for v in daemon.values()),
    }


def parse_json_tail(text: str) -> dict | None:
    """The last JSON object in a stream that looks like a verdict.

    Drivers print either one object on the last line or an indented object
    followed by a path; scanning every ``{`` and keeping the last decoded object
    that carries a verdict/checks key handles both without trusting line shape.
    """
    decoder = json.JSONDecoder()
    best: dict | None = None
    for index, char in enumerate(text or ""):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[index:])
        except ValueError:
            continue
        if isinstance(obj, dict) and ("verdict" in obj or "checks" in obj):
            best = obj
    return best


# ------------------------------------------------------------------ execution


def execute(
    task: dict, *, repeats: int, keep_output: bool, model: str | None = None
) -> list[dict]:
    driver = task.get("driver") or {}
    if driver.get("type") != "script" or not driver.get("path"):
        raise ValueError(f"{task['id']}: no executable script driver")
    script = REPO / driver["path"]
    if not script.is_file():
        raise FileNotFoundError(f"{task['id']}: driver missing: {script}")

    timeout = float((task.get("budget") or {}).get("timeoutSeconds") or 900)
    env = os.environ.copy()
    env.update({k: str(v) for k, v in (driver.get("env") or {}).items()})
    if model:
        # W14 comparison: the driver/engine reads DSH_MODEL; the second name is
        # the documented seam for a driver that wants to record it explicitly.
        env["DSH_MODEL"] = model
        if not os.environ.get("HARNESS_EVAL_STACK_MODEL"):
            # set when the model is not in the models catalog (e.g. a TokenLab id): the stack's
            # DSH_MODEL then governs and no per-user model preference is written
            env["HARNESS_EVAL_MODEL"] = model
    results = []
    for attempt in range(1, repeats + 1):
        started = datetime.now(UTC)
        t0 = time.monotonic()
        timed_out = False
        rc = None
        stdout = stderr = ""
        try:
            proc = subprocess.Popen(
                [sys.executable, str(script), *[str(a) for a in driver.get("args") or []]],
                cwd=str(REPO), env=env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True,
            )
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    stdout, stderr = proc.communicate(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    stdout, stderr = proc.communicate()
                rc = proc.returncode
        except Exception as exc:  # noqa: BLE001 - recorded, not hidden
            stderr = f"{type(exc).__name__}: {exc}"
        duration = round(time.monotonic() - t0, 2)
        tail = parse_json_tail(stdout)
        verdict_tag = str((tail or {}).get("verdict") or "").upper()
        if timed_out:
            verdict = "TIMEOUT"
        elif rc == 0 and verdict_tag in ("", "PASS"):
            verdict = "PASS"
        else:
            verdict = "FAIL"
        results.append({
            "attempt": attempt,
            "startedAt": started.isoformat(),
            "durationSec": duration,
            "exitCode": rc,
            "timedOut": timed_out,
            "verdict": verdict,
            "driverVerdict": verdict_tag or None,
            "failedChecks": (tail or {}).get("failedChecks")
                            or (tail or {}).get("failed") or [],
            "stdoutTail": (stdout or "")[-OUTPUT_CAP:] if keep_output else None,
            "stderrTail": (stderr or "")[-OUTPUT_CAP:] if keep_output else None,
        })
        if verdict == "PASS":
            break
    return results


# ---------------------------------------------------------------------- main



# ---------------------------------------------------------------- corpus gate

_CATEGORIES = {
    "agent_run", "artifact_authz", "bug_fix", "cancellation_recovery", "cli_parity",
    "code_search", "concurrency", "confinement", "denied_network", "mcp", "refactor",
    "skills", "stack_health", "stale_edit", "streaming", "tenant_isolation", "tests",
    "ui_screenshot", "visual_interaction",
}
_KINDS = {"agent", "boundary", "infra"}
_COSTS = {"none", "infra", "live"}
_STATUSES = {"implemented", "fixture-owed", "blocked"}


def check_corpus(doc: dict) -> list[str]:
    """W14 gate: the corpus is a contract, so malformed entries fail CI.

    Fixed expected outputs only mean something if every task declares one, has a
    real driver, and cannot silently lose its held-out flag. This validates
    structure, not results — running the tasks is the eval harness's job.
    """
    problems: list[str] = []
    tasks = doc.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return ["corpus has no tasks list"]
    seen: set[str] = set()
    for index, task in enumerate(tasks):
        where = f"tasks[{index}]"
        if not isinstance(task, dict):
            problems.append(f"{where}: not an object")
            continue
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id:
            problems.append(f"{where}: missing id")
            continue
        where = task_id
        if task_id in seen:
            problems.append(f"{where}: duplicate id")
        seen.add(task_id)
        if task.get("category") not in _CATEGORIES:
            problems.append(f"{where}: unknown category {task.get('category')!r}")
        if task.get("kind") not in _KINDS:
            problems.append(f"{where}: unknown kind {task.get('kind')!r}")
        if task.get("cost") not in _COSTS:
            problems.append(f"{where}: unknown cost {task.get('cost')!r}")
        if task.get("status") not in _STATUSES:
            problems.append(f"{where}: unknown status {task.get('status')!r}")
        if not isinstance(task.get("heldOut"), bool):
            problems.append(f"{where}: heldOut must be a boolean")
        for field in ("title", "verifier", "expected", "evidence"):
            value = task.get(field)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{where}: {field} must be non-empty text")
        driver = task.get("driver") or {}
        driver_path = driver.get("path")
        if driver.get("type") not in ("script", "http", "builtin", "none"):
            problems.append(f"{where}: driver.type {driver.get('type')!r} unsupported")
        if driver.get("type") == "script":
            if not isinstance(driver_path, str) or not driver_path:
                problems.append(f"{where}: script driver has no path")
            elif not (REPO / driver_path).is_file():
                problems.append(f"{where}: driver path {driver_path!r} does not exist")
        budget = task.get("budget") or {}
        timeout = budget.get("timeoutSeconds")
        if not isinstance(timeout, int) or timeout <= 0:
            problems.append(f"{where}: budget.timeoutSeconds must be a positive int")
        if task.get("cost") == "live" and not (task.get("model") or "").strip():
            problems.append(f"{where}: a live task must name its model")
        if task.get("cost") == "live" and task.get("heldOut") and "tuning" in (
            task.get("notes") or ""
        ).lower() and "not used for tuning" not in (task.get("notes") or "").lower():
            problems.append(f"{where}: a held-out task must not record tuning use")
    held_out = sum(1 for task in tasks if task.get("heldOut") is True)
    if held_out < 5:
        problems.append(f"only {held_out} held-out task(s); the corpus needs a real hold-out split")
    return problems


def select(corpus: list[dict], args) -> tuple[list[dict], list[tuple[dict, str]]]:
    selected, skipped = [], []
    for task in corpus:
        # Classify the corpus gaps first: a fixture-owed or blocked task is not
        # merely "not selected", and the baseline must say which it is.
        if task["status"] == "fixture-owed":
            note = str(task.get("notes") or "")
            skipped.append((task, note if note.startswith(("owed", "fixture-owed"))
                            else "fixture-owed: " + note))
            continue
        if task["status"] == "blocked":
            note = str(task.get("notes") or "")
            skipped.append((task, note if note.startswith("blocked")
                            else "blocked: " + note))
            continue
        if args.task and task["id"] not in args.task:
            skipped.append((task, "deferred: not selected (--task)"))
            continue
        if args.category and task["category"] not in args.category:
            skipped.append((task, "deferred: not selected (--category)"))
            continue
        if args.cost and task["cost"] not in args.cost:
            skipped.append((task, "deferred: not selected (--cost)"))
            continue
        if task["cost"] == "live" and not args.include_live:
            skipped.append((task, "deferred: live spend (pass --include-live)"))
            continue
        selected.append(task)
    return selected, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description="Marathon 2.0 evaluation runner")
    ap.add_argument("--corpus", default=str(CORPUS))
    ap.add_argument("--task", action="append", default=[])
    ap.add_argument("--category", action="append", default=[])
    ap.add_argument("--cost", action="append", choices=["none", "infra", "live"],
                    default=[])
    ap.add_argument("--include-live", action="store_true",
                    help="allow tasks whose cost is live (model spend)")
    ap.add_argument("--repeats", type=int, default=1,
                    help="retry a failing task up to N attempts; stops at the first PASS, so this is not N independent runs")
    ap.add_argument("--gap", type=float, default=0.0,
                    help="seconds to wait between tasks; the credential edge allows about 5 logins a minute per IP, so a login-heavy batch needs ~65")
    ap.add_argument("--timeout-scale", type=float, default=1.0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-only", action="store_true",
                    help="print the deployed-vs-worktree revision check and exit")
    ap.add_argument("--check", action="store_true",
                    help="validate the corpus contract (fixed outputs, drivers, hold-out) and exit")
    ap.add_argument("--no-output", action="store_true",
                    help="do not keep driver stdout/stderr tails in evidence")
    ap.add_argument("--compare-model", action="append", default=[], metavar="MODEL",
                    help="run the selection once per model and write a comparison "
                         "evidence file (requires --include-live; repeat the flag)")
    args = ap.parse_args()

    doc = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    corpus = doc["tasks"]
    if args.check:
        problems = check_corpus(doc)
        if problems:
            print(json.dumps({"ok": False, "problems": problems}, indent=2))
            return 1
        held_out = sum(1 for task in corpus if task.get("heldOut") is True)
        print(json.dumps({"ok": True, "tasks": len(corpus), "heldOut": held_out,
                          "implemented": sum(
                              1 for task in corpus if task.get("status") == "implemented")}))
        return 0
    if args.list:
        for t in corpus:
            print(f"{t['id']:28s} {t['category']:20s} {t['kind']:8s} "
                  f"{t['cost']:6s} {t['status']:12s} heldOut={t['heldOut']}")
        return 0
    if args.verify_only:
        print(json.dumps({"git": git_revision(), "revisionCheck": revision_check()},
                         indent=2))
        return 0

    selected, skipped = select(corpus, args)
    if args.compare_model and args.dry_run:
        print(json.dumps({
            "comparison": list(dict.fromkeys(args.compare_model)),
            "declared": sorted(declared_models()),
            "stackModel": stack_model(),
            "tasks": [t["id"] for t in selected],
            "wouldRun": len(selected) * len(set(args.compare_model)),
        }, indent=2))
        return 0
    if args.compare_model:
        models = list(dict.fromkeys(args.compare_model))
        if not args.include_live:
            ap.error("--compare-model requires --include-live (model spend is opt-in)")
        if len(models) < 2:
            ap.error("--compare-model needs at least two distinct models")
        if not selected:
            ap.error("--compare-model selected no tasks")
        deployed_model = stack_model()
        declared = declared_models()
        if declared:
            missing = [m for m in models if m not in declared]
            if missing:
                print(json.dumps({
                    "ok": False,
                    "error": "UNDECLARED_MODEL",
                    "stackModel": deployed_model,
                    "declared": sorted(declared),
                    "requested": models,
                    "missing": missing,
                    "hint": (
                        "add the model to a patch in deploy/patches/ (it declares "
                        "exactly the models the engine may bill) and rebuild the "
                        "worker image; a run under an undeclared id fails before "
                        "the model is called."
                    ),
                }, indent=2))
                return 2
        elif deployed_model and set(models) != {deployed_model}:
            # No readable patch: fall back to the single-model check so an
            # undeclared comparison cannot report two identical fake failures.
            print(json.dumps({
                "ok": False,
                "error": "SINGLE_MODEL_STACK",
                "stackModel": deployed_model,
                "requested": models,
                "hint": (
                    "no patch declares these models; the engine refuses every "
                    "undeclared id. Declare them in deploy/patches/ and rebuild "
                    "the worker image."
                ),
            }, indent=2))
            return 2
        started = datetime.now(UTC)
        comparison: dict = {
            "kind": "comparison",
            "startedAt": started.isoformat(),
            "git": git_revision(),
            "revisionCheck": revision_check(),
            "models": models,
            "passRule": "each task's own verifier, judged per model; never a relative win",
            "budget": {
                "repeats": args.repeats,
                "timeoutScale": args.timeout_scale,
            },
            "tasks": {},
        }
        failures = 0
        for task in selected:
            per_model: dict = {}
            for model in models:
                try:
                    runs = execute(
                        task,
                        repeats=args.repeats,
                        keep_output=not args.no_output,
                        model=model,
                    )
                    last = runs[-1]
                    per_model[model] = {
                        "verdict": last["verdict"],
                        "driverVerdict": last.get("driverVerdict"),
                        "durationSec": last.get("durationSec"),
                        "exitCode": last.get("exitCode"),
                        "attempts": len(runs),
                        "failedChecks": last.get("failedChecks") or [],
                    }
                    if last["verdict"] != "PASS":
                        failures += 1
                except Exception as exc:  # noqa: BLE001 - recorded per model
                    per_model[model] = {"verdict": "ERROR", "error": str(exc)}
                    failures += 1
            comparison["tasks"][task["id"]] = per_model
        OUT.mkdir(parents=True, exist_ok=True)
        stamp = int(started.timestamp())
        path = OUT / f"comparison-{stamp}.json"
        comparison["finishedAt"] = datetime.now(UTC).isoformat()
        path.write_text(json.dumps(comparison, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps({
            "models": models,
            "tasks": len(selected),
            "failures": failures,
            "evidence": str(path),
        }))
        return 0 if failures == 0 else 1
    if args.dry_run:
        print(json.dumps({"selected": [t["id"] for t in selected],
                          "skipped": [{"id": t["id"], "reason": r} for t, r in skipped]},
                         indent=2))
        return 0

    started = datetime.now(UTC)
    env = {"git": git_revision()}
    try:
        env["revisionCheck"] = revision_check()
    except Exception as exc:  # noqa: BLE001 - evidence must still be written
        env["revisionCheck"] = {"error": f"{type(exc).__name__}: {exc}", "verified": False}
    env["images"] = {
        "backend": image_id("harness-backend:" + os.environ.get("HARNESS_IMAGE_TAG", "vdev-local")),
        "engineWorker": image_id(worker_image()),
    }
    env["model"] = os.environ.get("DSH_MODEL") or None
    env["repeats"] = args.repeats
    env["gapSeconds"] = args.gap

    results = []
    for index, task in enumerate(selected):
        if index and args.gap:
            time.sleep(args.gap)
        scale = args.timeout_scale
        if scale != 1.0:
            task = json.loads(json.dumps(task))
            task["budget"]["timeoutSeconds"] = (
                float(task["budget"].get("timeoutSeconds") or 900) * scale)
        print(f"[eval] {task['id']} ({task['cost']}) ...", flush=True)
        try:
            attempts = execute(task, repeats=args.repeats, keep_output=not args.no_output)
            results.append({"id": task["id"], "category": task["category"],
                            "kind": task["kind"], "cost": task["cost"],
                            "heldOut": task["heldOut"], "status": "executed",
                            "verdict": attempts[-1]["verdict"], "attempts": attempts,
                            "durationSec": attempts[-1]["durationSec"],
                            "budget": task.get("budget"),
                            "model": task.get("model"),
                            "fixture": task.get("fixture"),
                            "expected": task.get("expected"),
                            "driver": (task.get("driver") or {}).get("path"),
                            "verifier": task["verifier"]})
            print(f"[eval] {task['id']}: {attempts[-1]['verdict']} "
                  f"({attempts[-1]['durationSec']}s)", flush=True)
        except Exception as exc:  # noqa: BLE001
            results.append({"id": task["id"], "category": task["category"],
                            "kind": task["kind"], "cost": task["cost"],
                            "heldOut": task["heldOut"], "status": "error",
                            "verdict": "ERROR", "error": f"{type(exc).__name__}: {exc}",
                            "budget": task.get("budget"), "model": task.get("model"),
                            "fixture": task.get("fixture"),
                            "driver": (task.get("driver") or {}).get("path"),
                            "verifier": task["verifier"]})
            print(f"[eval] {task['id']}: ERROR {exc}", flush=True)

    deferred = [{"id": t["id"], "category": t["category"], "status": t["status"],
                 "reason": r} for t, r in skipped]
    executed = [r for r in results if r["status"] == "executed"]
    passed = [r for r in executed if r["verdict"] == "PASS"]
    summary = {
        "executed": len(executed),
        "passed": len(passed),
        "failed": len([r for r in executed if r["verdict"] != "PASS"]),
        "errors": len([r for r in results if r["status"] == "error"]),
        "deferred": len(deferred),
        "corpus": len(corpus),
        "revisionVerified": bool((env.get("revisionCheck") or {}).get("verified")),
    }
    evidence = {
        "schema": 1,
        "kind": "marathon2-baseline",
        "corpusRevision": doc.get("revision"),
        "startedAt": started.isoformat(),
        "finishedAt": datetime.now(UTC).isoformat(),
        "environment": env,
        "selection": {"includeLive": args.include_live, "costs": args.cost,
                      "tasks": args.task, "categories": args.category},
        "summary": summary,
        "results": results,
        "deferred": deferred,
        "limitations": [
            "n=1 unless --repeats was raised; a single pass is an existence proof, not a rate.",
            "A task is deferred, blocked or fixture-owed is not a pass.",
            ("Revision verification compares runtime files inside the deployed "
             "containers/images with this worktree; a mismatch makes every result "
             "stale for HEAD."),
        ],
    }

    out_path = Path(args.out) if args.out else (
        OUT / f"baseline-{started.strftime('%Y%m%dT%H%M%SZ')}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(evidence, indent=2, default=str) + "\n",
                        encoding="utf-8")
    print(json.dumps({"summary": summary, "evidence": str(out_path)}, indent=2))
    if not summary["revisionVerified"]:
        print("[eval] WARNING: deployed runtime files do not match this worktree; "
              "results are stale for HEAD", file=sys.stderr)
    return 0 if summary["failed"] == 0 and summary["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
