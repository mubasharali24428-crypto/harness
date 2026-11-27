#!/usr/bin/env python3
"""P-13 (W7): N independent benchmark trials per task per model, one raw JSON each.

run_eval.py's ``--repeats`` is retry-until-first-pass (it stops at the first
PASS), so it cannot produce a rate. This wrapper runs N INDEPENDENT trials
(default 5, minimum 5) and keeps every attempt — passes, failures, timeouts and
denials — as its own raw JSON with run id, model, timestamps, exit status and the
cost_ledger total for that run.

    # zero-spend pipeline exercise (fake model, no Docker, no network):
    python3 scripts/eval/bench_trials.py --task stale-edit --trials 5 --dry

    # live (owner-gated spend; needs the deployed stack + Docker):
    python3 scripts/eval/bench_trials.py --task multi-file-refactor --trials 5 \
        --model anthropic/claude-haiku-5.5 --include-live

Dry output is labelled mode="dry" / model="dry-fake-model" on every file and is
never a capability claim. Live cost/interventions come from cost_ledger /
run_collab_events by run id (reusing w25_metrics); when the ledger is unreachable
they are null, never zero. The frozen held-out manifest is verified before any
run. Execution reuses run_eval.execute (one independent attempt per trial).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import run_eval
import w25_metrics
from freeze_heldout import MANIFEST, check_manifest, corpus_changed

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "briefs" / "evidence" / "w7-bench"
MIN_TRIALS = 5
DRY_MODEL = "dry-fake-model"
# Deterministic dry outcome cycle so the default 5-trial batch exercises pass/fail/timeout/denial.
_DRY_CYCLE = ("PASS", "FAIL", "TIMEOUT", "DENIED", "PASS")
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


def _slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", model)


def _manifest_sha() -> str | None:
    if not MANIFEST.is_file():
        return None
    return hashlib.sha256(MANIFEST.read_bytes()).hexdigest()


def fake_attempt(task: dict, trial: int) -> dict:
    """A deterministic simulated attempt — dry pipeline exercise, NOT a measurement."""
    outcome = _DRY_CYCLE[(trial - 1) % len(_DRY_CYCLE)]
    duration = round(1.0 + 0.25 * trial + (sum(map(ord, task["id"])) % 7) * 0.1, 3)
    timed_out = outcome == "TIMEOUT"
    denied = outcome == "DENIED"
    verdict = "TIMEOUT" if timed_out else ("PASS" if outcome == "PASS" else "FAIL")
    exit_code = None if timed_out else (0 if outcome == "PASS" else 1)
    started = datetime.now(UTC)
    return {
        "schema": 1, "kind": "w7-bench-attempt", "mode": "dry", "simulation": True,
        "taskId": task["id"], "heldOut": task.get("heldOut"), "model": DRY_MODEL,
        "trial": trial, "runId": f"dry-{task['id']}-{trial}",
        "startedAt": started.isoformat(),
        "finishedAt": (started + timedelta(seconds=duration)).isoformat(),
        "durationSec": duration, "exitCode": exit_code, "timedOut": timed_out,
        "verdict": verdict, "driverVerdict": "DENIED" if denied else verdict,
        "failedChecks": (["dry: simulated policy denial"] if denied else
                         ["dry: simulated check failure"] if verdict == "FAIL" else []),
        "firstOutputS": round(0.2 + 0.05 * trial, 3),
        "toolCalls": 3 + (trial % 4),
        "interventions": 1 if denied else 0,
        "costUsd": 0.0,
        "costSource": "dry-fake: no ledger, zero spend (not a measurement)",
        "budget": task.get("budget"),
        "stdoutTail": None, "stderrTail": None,
    }


def classify_verdict(attempt: dict) -> str:
    """run_eval says FAIL for any non-zero exit. A driver that died before producing any verdict
    or failed check (stack unreachable, OTP not found, ...) is an environment problem, not a
    product result: report it as ERROR so it never counts toward a success rate."""
    verdict = attempt["verdict"]
    if verdict == "FAIL" and not attempt.get("driverVerdict") and not (attempt.get("failedChecks") or []):
        return "ERROR"
    return verdict


_THROTTLE = ("rate_limit", "rate limit", " 429")


def provider_throttled(stdout_tail: str) -> bool:
    """True when the driver's evidence file shows the model provider answered 429: that is the
    provider throttling us (shared concurrency limit), not a product or model result."""
    m = re.search(r'"evidence":\s*"([^"]+)"', stdout_tail or "")
    try:
        answer = str(json.loads(Path(m.group(1)).read_text(encoding="utf-8")).get("answer") or "")
    except (AttributeError, OSError, ValueError):
        return False
    return any(t in answer.lower() for t in _THROTTLE)


def ambiguous_stack() -> str | None:
    """Several compose projects running and no E2E_COMPOSE_PROJECT: the eval drivers would talk to
    the default ports but look up containers by service name only. Returns a message, else None."""
    if os.environ.get("E2E_COMPOSE_PROJECT"):
        return None
    try:
        out = subprocess.run(["docker", "ps", "--format", '{{.Label "com.docker.compose.project"}}'],
                             capture_output=True, text=True, timeout=30).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None  # no docker: nothing to disambiguate
    projects = sorted({p for p in out if p})
    if len(projects) > 1:
        return ("several compose stacks are running (" + ", ".join(projects) + "); set "
                "E2E_COMPOSE_PROJECT plus E2E_IDENTITY_URL / E2E_ORCHESTRATOR_URL for the one to test")
    return None


def _interventions(run_id: str) -> int | None:
    """Human interventions for a run: redirect.* collab events (pilot_metrics.py:171)."""
    if not _SAFE_RUN_ID.match(run_id):
        return None
    raw = w25_metrics.psql(
        "select count(*) from run_collab_events where run_id='" + run_id
        + "' and kind like 'redirect.%'")
    return int(raw) if raw.strip().isdigit() else None


def engine_cache_fields(stdout_tail: str) -> dict:
    """S-01/S-02: the engine result's ``usage`` and ``promptPrefix`` digest, when the
    driver's evidence carries them (its ``"evidence": <path>`` file first, else a
    JSON object printed on stdout). Only those two fields are kept — never prompt
    text. Absent stays absent: bench_stats reports unknown, never zero."""
    doc = None
    m = re.search(r'"evidence":\s*"([^"]+)"', stdout_tail or "")
    if m:
        try:
            doc = json.loads(Path(m.group(1)).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = None
    if not isinstance(doc, dict):
        try:
            start = (stdout_tail or "").index("{")
            doc = json.loads((stdout_tail or "")[start:])
        except ValueError:
            doc = None
    if not isinstance(doc, dict):
        return {}
    out: dict = {}
    usage = doc.get("usage")
    if isinstance(usage, dict):
        out["usage"] = {k: usage[k] for k in ("inputTokens", "outputTokens", "cacheReadTokens")
                        if isinstance(usage.get(k), (int, float))}
    prefix = doc.get("promptPrefix")
    if isinstance(prefix, dict) and isinstance(prefix.get("sha256"), str):
        out["promptPrefix"] = {"sha256": prefix["sha256"], "bytes": prefix.get("bytes")}
    return out


def live_attempt(task: dict, trial: int, model: str) -> dict:
    """One independent live trial via run_eval.execute, joined to the durable ledger."""
    runs = run_eval.execute(task, repeats=1, keep_output=True, model=model)
    a = runs[-1]
    run_id = w25_metrics.evidence_run_id(a.get("stdoutTail") or "")
    try:
        joined = w25_metrics.attempt_metrics(task["id"], a, run_id)
        interventions = _interventions(run_id) if run_id else None
        cost_source = joined.get("attribution")
        ledger_model = joined.get("primaryModel")
    except Exception as exc:  # noqa: BLE001 - ledger unreachable must not fabricate a cost
        joined = {"costUsd": None, "firstToolCallS": None, "toolCalls": None}
        interventions, ledger_model = None, None
        cost_source = f"unavailable: {type(exc).__name__}"
    return {
        "schema": 1, "kind": "w7-bench-attempt", "mode": "live", "simulation": False,
        "taskId": task["id"], "heldOut": task.get("heldOut"), "model": model,
        "ledgerModel": ledger_model, "trial": trial, "runId": run_id,
        "startedAt": a["startedAt"], "finishedAt": datetime.now(UTC).isoformat(),
        "durationSec": a["durationSec"], "exitCode": a["exitCode"], "timedOut": a["timedOut"],
        "verdict": "ERROR" if a["verdict"] == "FAIL" and provider_throttled(a.get("stdoutTail"))
        else classify_verdict(a),
        "errorReason": "provider 429 (rate limited)" if a["verdict"] == "FAIL"
        and provider_throttled(a.get("stdoutTail")) else None, "driverVerdict": a.get("driverVerdict"),
        "failedChecks": a.get("failedChecks") or [],
        "firstOutputS": joined.get("firstToolCallS"),
        "toolCalls": joined.get("toolCalls"),
        "interventions": interventions,
        "costUsd": joined.get("costUsd"),
        "costSource": cost_source,
        "budget": task.get("budget"),
        "stdoutTail": a.get("stdoutTail"), "stderrTail": a.get("stderrTail"),
        **engine_cache_fields(a.get("stdoutTail") or ""),
    }


def _spent(attempts: list[dict]) -> float:
    """Recorded cost; a trial whose cost is unknown or 0 is charged its task's full budget."""
    return sum(a["costUsd"] if a.get("costUsd") else (a.get("budget") or {}).get("maxCostUsd") or 0
               for a in attempts)


def run_batch(tasks: list[dict], trials: int, model: str, dry: bool,
              out_dir: Path, max_total_usd: float | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    batch_start = datetime.now(UTC)
    stamp = batch_start.strftime("%Y%m%dT%H%M%SZ")
    mode = "dry" if dry else "live"
    manifest_sha = _manifest_sha()
    git_rev = run_eval.git_revision()
    files: list[str] = []
    attempts: list[dict] = []
    stopped = None
    for task in tasks:
        for trial in range(1, trials + 1):
            nxt = (task.get("budget") or {}).get("maxCostUsd") or 0
            if max_total_usd is not None and _spent(attempts) + nxt > max_total_usd:
                stopped = f"spend cap ${max_total_usd} would be exceeded before {task['id']} trial {trial}"
                break
            a = fake_attempt(task, trial) if dry else live_attempt(task, trial, model)
            a["trials"] = trials
            a["manifestSha256"] = manifest_sha
            a["git"] = git_rev
            name = f"{mode}-{task['id']}-{_slug(a['model'])}-trial{trial}-{stamp}.json"
            p = out_dir / name
            p.write_text(json.dumps(a, indent=2, default=str) + "\n", encoding="utf-8")
            files.append(str(p))
            attempts.append(a)
    summary = {
        "schema": 1, "kind": "w7-bench-batch", "mode": mode,
        "model": DRY_MODEL if dry else model,
        "trialsPerTask": trials, "tasks": [t["id"] for t in tasks],
        "startedAt": batch_start.isoformat(), "finishedAt": datetime.now(UTC).isoformat(),
        "manifestSha256": manifest_sha, "git": git_rev, "attemptFiles": files,
        "counts": {
            "attempts": len(attempts),
            "pass": sum(a["verdict"] == "PASS" for a in attempts),
            "fail": sum(a["verdict"] == "FAIL" for a in attempts),
            "timeout": sum(a["verdict"] == "TIMEOUT" for a in attempts),
            "error": sum(a["verdict"] == "ERROR" for a in attempts),
            "denied": sum(a.get("driverVerdict") == "DENIED" for a in attempts),
        },
        "stoppedByCap": stopped, "maxTotalUsd": max_total_usd, "spentUsdConservative": _spent(attempts),
        "claimable": False if dry else None,
        "note": ("DRY pipeline exercise: fake model, zero spend, no Docker/network; "
                 "not a capability claim." if dry else
                 "Live trials; cost/interventions from cost_ledger/run_collab_events by run id; "
                 "null when unreachable. Rates require N>=5 per cell (bench_stats)."),
    }
    sp = out_dir / f"{mode}-batch-{stamp}.json"
    sp.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    summary["batchFile"] = str(sp)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="P-13 N-trial benchmark runner (dry or live)")
    ap.add_argument("--task", action="append", required=True, default=[])
    ap.add_argument("--trials", type=int, default=MIN_TRIALS)
    ap.add_argument("--model", default=None)
    ap.add_argument("--dry", action="store_true", help="fake model, zero spend, no Docker/network")
    ap.add_argument("--include-live", action="store_true", help="allow live-spend tasks (owner-gated)")
    ap.add_argument("--max-total-usd", type=float, default=None,
                    help="stop launching trials once recorded (or budgeted, if unknown) cost would exceed this")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--corpus", default=str(run_eval.CORPUS))
    a = ap.parse_args()

    if a.trials < MIN_TRIALS:
        ap.error(f"P-13 requires >= {MIN_TRIALS} independent trials per task per model")
    corpus_bytes = Path(a.corpus).read_bytes()
    doc = json.loads(corpus_bytes)
    by_id = {t["id"]: t for t in doc["tasks"]}
    tasks = []
    for tid in a.task:
        if tid not in by_id:
            ap.error(f"unknown task {tid}")
        tasks.append(by_id[tid])

    if MANIFEST.is_file():
        man = json.loads(MANIFEST.read_text(encoding="utf-8"))
        problems = check_manifest(man, doc, corpus_bytes)
        if problems:
            print(json.dumps({"ok": False, "error": "held-out manifest drift",
                              "problems": problems}, indent=2))
            return 2
        if corpus_changed(man, corpus_bytes):
            print("[bench] note: corpus.json changed since freeze (corpusChanged=true); "
                  "held-out split unchanged, proceeding", file=sys.stderr)
    else:
        print("[bench] WARNING: no frozen held-out manifest; run freeze_heldout.py --write first",
              file=sys.stderr)

    if not a.dry:
        for t in tasks:
            if t["cost"] == "live" and not a.include_live:
                ap.error(f"{t['id']} is live spend; pass --include-live (owner-gated)")
        if not a.model:
            ap.error("live mode needs --model")
        clash = ambiguous_stack()
        if clash:
            ap.error(clash)

    summary = run_batch(tasks, a.trials, a.model or DRY_MODEL, a.dry, Path(a.out), a.max_total_usd)
    print(json.dumps({k: summary[k] for k in
                      ("mode", "model", "trialsPerTask", "tasks", "counts", "claimable", "stoppedByCap",
                       "spentUsdConservative", "note")},
                     indent=2))
    print(f"-> {len(summary['attemptFiles'])} attempt files in {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())