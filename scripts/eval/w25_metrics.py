#!/usr/bin/env python3
"""W25/W26 metrics: join eval baseline results to the database and aggregate per task and model.

Reads `docs/briefs/evidence/eval/baseline-*.json` attempts whose start falls in [--since, --until],
then for each attempt reads the run's durable rows (agent_events, cost_ledger) from the live
database through `docker exec`. Nothing here asks the agent what happened.

Per attempt: verdict, wall seconds, start latency (plan_approved -> execution_started), time to the
first tool call (the first useful output the engine produced), engine seconds, tool calls, model
calls with their latencies, tokens, cost. Unknown is `null`, never zero.

Per (task, model): n, passes, and min / p50 / p95 / max for each timing. Percentiles are
nearest-rank over the attempts present; with n below 20 the p95 is just the slowest attempt, and the
output says so. `first` is the first attempt in the window, which is cold only if the caller made it so.

    python3 scripts/eval/w25_metrics.py --since 2026-11-05T14:00:00Z --label deepseek-block
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import math
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EVAL = REPO / "docs" / "briefs" / "evidence" / "eval"


def db_container() -> str:
    """The Postgres container: the one in E2E_COMPOSE_PROJECT when set, else the default stack's."""
    project = os.environ.get("E2E_COMPOSE_PROJECT")
    if project:
        found = subprocess.run(
            ["docker", "ps", "-q", "--filter", "label=com.docker.compose.service=db",
             "--filter", f"label=com.docker.compose.project={project}"],
            capture_output=True, text=True, timeout=30).stdout.split()
        if found:
            return found[0]
    return "harness-db-1"


def psql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", "-i", db_container(), "sh", "-lc",
         'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At'],
        input=sql, capture_output=True, text=True, timeout=60)
    return out.stdout.strip()


def jq(sql: str):
    raw = psql(sql)
    return json.loads(raw) if raw else []


def pct(vals: list[float], p: float):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    return round(v[max(0, math.ceil(p / 100 * len(v)) - 1)], 3)


def stats(vals: list[float]) -> dict:
    v = [x for x in vals if x is not None]
    return {"n": len(v), "min": pct(v, 0) if v else None, "p50": pct(v, 50), "p95": pct(v, 95),
            "max": max(v) if v else None}


def when(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def run_rows(run_id: str) -> tuple[list[dict], list[dict]]:
    ev = jq("select coalesce(json_agg(json_build_object('t', type, 'ts', extract(epoch from ts))"
            " order by seq), '[]') from agent_events where run_id='" + run_id + "' and type in "
            "('plan_approved','execution_started','tool_call','engine_completed','engine_failed')")
    led = jq("select coalesce(json_agg(json_build_object('m', model, 'i', input_tokens, 'o',"
             " output_tokens, 'c', cost_usd, 'l', latency_ms) order by seq), '[]') from cost_ledger"
             " where run_id='" + run_id + "'")
    return ev, led


def window_ledger(start: datetime, seconds: float) -> list[dict]:
    a, b = start.isoformat(), datetime.fromtimestamp(
        start.timestamp() + seconds + 5, timezone.utc).isoformat()
    return jq("select coalesce(json_agg(json_build_object('m', model, 'i', input_tokens, 'o',"
              " output_tokens, 'c', cost_usd, 'l', latency_ms) order by seq), '[]') from cost_ledger"
              f" where created_at >= '{a}' and created_at <= '{b}'")


def attempt_metrics(task: str, a: dict, run_id: str | None) -> dict:
    out = {"task": task, "startedAt": a["startedAt"], "verdict": a["verdict"],
           "wallSeconds": a["durationSec"], "runId": run_id, "startLatencyS": None,
           "firstToolCallS": None, "engineS": None, "toolCalls": None}
    if run_id:
        ev, led = run_rows(run_id)
        ts = {}
        for e in ev:
            ts.setdefault(e["t"], e["ts"])
        calls = [e for e in ev if e["t"] == "tool_call"]
        end = ts.get("engine_completed") or ts.get("engine_failed")
        if "plan_approved" in ts and "execution_started" in ts:
            out["startLatencyS"] = round(ts["execution_started"] - ts["plan_approved"], 3)
        if calls and "execution_started" in ts:
            out["firstToolCallS"] = round(calls[0]["ts"] - ts["execution_started"], 3)
        if end and "execution_started" in ts:
            out["engineS"] = round(end - ts["execution_started"], 3)
        out["toolCalls"] = len(calls)
        out["attribution"] = "run_id"
    if not run_id or not led:  # no run id, or it has no ledger rows: attribute by time window
        led = window_ledger(when(a["startedAt"]), a["durationSec"])  # sequential batches only
        out["attribution"] = "time-window" if led else "none (no ledger rows found)"
    by_model: dict[str, dict] = {}
    for r in led:
        m = by_model.setdefault(r["m"], {"calls": 0, "in": 0, "out": 0, "usd": 0.0, "latMs": []})
        m["calls"] += 1
        m["in"] += r["i"]
        m["out"] += r["o"]
        m["usd"] = round(m["usd"] + float(r["c"]), 6)
        m["latMs"].append(r["l"])
    out["models"] = by_model
    out["primaryModel"] = max(by_model, key=lambda k: by_model[k]["in"] + by_model[k]["out"],
                              default=None)
    out["costUsd"] = round(sum(m["usd"] for m in by_model.values()), 6) if by_model else None
    return out


def evidence_run_id(stdout_tail: str) -> str | None:
    # drivers print either `evidence -> path` or a JSON `"evidence": "/abs/path"`
    m = re.search(r'evidence"?\s*(?:->|:)\s*"?([^"\n]+?\.json)', stdout_tail or "")
    if not m:
        return None
    p = REPO / m.group(1)  # an absolute path replaces REPO in pathlib joins
    if not p.is_file():
        return None
    try:
        rid = json.loads(p.read_text()).get("runId")
    except (ValueError, OSError):
        return None
    return rid if isinstance(rid, str) and psql(
        f"select count(*) from agent_events where run_id='{rid}'") not in ("", "0") else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default="9999-01-01T00:00:00Z")
    ap.add_argument("--label", required=True)
    ap.add_argument("--task", action="append", default=[])
    a = ap.parse_args()
    since, until = when(a.since), when(a.until)

    attempts = []
    for f in sorted(glob.glob(str(EVAL / "baseline-*.json"))):
        d = json.loads(Path(f).read_text())
        for r in d.get("results", []):
            if a.task and r["id"] not in a.task:
                continue
            for at in r.get("attempts", []):
                if since <= when(at["startedAt"]) <= until:
                    m = attempt_metrics(r["id"], at, evidence_run_id(at.get("stdoutTail", "")))
                    m["baseline"] = Path(f).name
                    attempts.append(m)
    attempts.sort(key=lambda x: x["startedAt"])

    groups: dict[tuple[str, str | None], list[dict]] = {}
    for x in attempts:
        groups.setdefault((x["task"], x["primaryModel"]), []).append(x)
    summary = []
    for (task, model), xs in groups.items():
        lat = [ms for x in xs for m in x["models"].values() for ms in m["latMs"]]
        known = [x["costUsd"] for x in xs if x["costUsd"] is not None]
        summary.append({
            "task": task, "model": model, "n": len(xs),
            "passes": sum(x["verdict"] == "PASS" for x in xs),
            "failures": [{"startedAt": x["startedAt"], "verdict": x["verdict"],
                          "baseline": x["baseline"]} for x in xs if x["verdict"] != "PASS"],
            "wallSeconds": stats([x["wallSeconds"] for x in xs]),
            "startLatencyS": stats([x["startLatencyS"] for x in xs]),
            "firstToolCallS": stats([x["firstToolCallS"] for x in xs]),
            "engineS": stats([x["engineS"] for x in xs]),
            "toolCalls": stats([x["toolCalls"] for x in xs]),
            "ledgerRowLatencyMs": stats(lat),  # one ledger row per run, not per model call
            "costUsd": {"knownTotal": round(sum(known), 6) if known else None,
                        "knownPerAttemptMean": round(sum(known) / len(known), 6) if known else None,
                        "unknownAttempts": len(xs) - len(known)},
            "firstAttemptWallS": xs[0]["wallSeconds"],
            "laterAttemptsWallP50": pct([x["wallSeconds"] for x in xs[1:]], 50),
        })
    out = {"schema": 1, "kind": "w25-metrics", "label": a.label, "window": [a.since, a.until],
           "generatedAt": datetime.now(timezone.utc).isoformat(),
           "note": "nearest-rank percentiles over the attempts present; with n < 20 the p95 is the "
                   "slowest attempt, not a stable tail estimate",
           "summary": summary, "attempts": attempts}
    path = EVAL / f"w25-metrics-{a.label}.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    for s in summary:
        print(f"{s['task']:22} {str(s['model']):34} n={s['n']} pass={s['passes']}/{s['n']} "
              f"wall p50={s['wallSeconds']['p50']} max={s['wallSeconds']['max']} "
              f"ttf-tool p50={s['firstToolCallS']['p50']} cost/att={s['costUsd']['knownPerAttemptMean']} (unknown {s['costUsd']['unknownAttempts']})")
    print("->", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
