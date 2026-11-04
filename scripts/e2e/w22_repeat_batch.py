#!/usr/bin/env python3
"""Stage 1: repeat each W22 gate's drill on the deployed stack, keep every outcome.

Roadmap 3.0 Stage 1 asks to "repeat where reliability is claimed". Each drill
runs ``--repeat`` times in sequence (they share one stack and must not overlap);
the exit code and the newest evidence file it wrote are recorded, FAILs
included. The summary is the rate per gate. Spend comes from cost_ledger
afterwards (stopped runs are metered since W23 F4).

    section4/.venv/bin/python scripts/e2e/w22_repeat_batch.py --repeat 3
"""
import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
EVIDENCE = REPO / "docs/briefs/evidence/w22"
# Each drill is an argv after the interpreter; "{evidence}" becomes a per-attempt path.
GATES = {
    "worker and child cancellation": [["w22_stop_neighbour.py"]],
    "partial-write reconciliation": [["w22_partial_write_drill.py", "--evidence", "{evidence}"]],
    "crash cleanup": [["w22_crash_cleanup_drill.py"], ["w22_daemon_kill_drill.py"],
                      ["w22_daemon_kill_drill.py", "--orchestrator-restart"]],
    "event replay": [["w22_replay_overflow_drill.py"], ["w22_restart_replay_drill.py"]],
    "provider outage and recovery": [["w22_response_cut_drill.py"]],
    "lease/heartbeat": [["w22_suspend_reap_drill.py"]],
}


def newest_evidence(since: float, batch_record: Path) -> list[str]:
    return sorted(str(p.relative_to(REPO)) for p in EVIDENCE.glob("*.json")
                  if p.stat().st_mtime >= since and p != batch_record)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--only", action="append", choices=GATES, help="run this gate (repeatable)")
    args = ap.parse_args()
    batch_id = f"stage1-repeat-{uuid.uuid4().hex[:8]}"
    out = EVIDENCE / f"{batch_id}.json"
    record: dict = {"batchId": batch_id, "repeat": args.repeat, "runs": [], "rates": {}}
    git = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True,
                         text=True, check=False)
    image = subprocess.run(["docker", "inspect", "-f", "{{.Config.Image}}",
                            "harness-orchestrator-1"], capture_output=True, text=True, check=False)
    record["git"], record["image"] = git.stdout.strip(), image.stdout.strip()
    gates = {g: GATES[g] for g in args.only} if args.only else GATES
    for gate, drills in gates.items():
        for argv in drills:
            drill = " ".join(argv)
            for i in range(args.repeat):
                started = time.time()
                evidence = EVIDENCE / f"{batch_id}-{argv[0][:-3]}-{i + 1}.json"
                cmd = [a.replace("{evidence}", str(evidence)) for a in argv]
                proc = subprocess.run([sys.executable, *cmd], cwd=HERE, capture_output=True,
                                      text=True, check=False, timeout=1800)
                run = {"gate": gate, "drill": drill, "attempt": i + 1,
                       "exitCode": proc.returncode, "seconds": round(time.time() - started, 1),
                       "evidence": newest_evidence(started, out), "tail": proc.stdout[-600:]}
                record["runs"].append(run)
                out.write_text(json.dumps(record, indent=2) + "\n")  # survive an abort
                print(f"{gate} / {drill} #{i + 1}: exit {proc.returncode} "
                      f"({run['seconds']} s)", flush=True)
    for gate in gates:
        runs = [r for r in record["runs"] if r["gate"] == gate]
        record["rates"][gate] = f"{sum(r['exitCode'] == 0 for r in runs)}/{len(runs)}"
    out.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"batchId": batch_id, "rates": record["rates"], "evidence": str(out)},
                     indent=1))
    return 0 if all(r["exitCode"] == 0 for r in record["runs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
