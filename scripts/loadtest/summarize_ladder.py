"""Summarise a run_ladder.sh evidence directory as a markdown table (stdlib only).

usage: summarize_ladder.py <evidence-dir> <vus> [<vus> ...]
"""

import json
import re
import sys
from pathlib import Path

ROW = re.compile(r"^FLOW\s+(\S+)\s+(.*)$")


def percentiles(console: str) -> dict[str, dict[str, int]]:
    """name -> {p50,p95,p99,max,reqs} from locust's 'Response time percentiles' table."""
    out: dict[str, dict[str, int]] = {}
    section = console.split("Response time percentiles", 1)[-1]
    for line in section.splitlines():
        m = ROW.match(line)
        if m and (nums := re.findall(r"\d+", m.group(2))) and len(nums) >= 11:
            out[m.group(1)] = {"p50": int(nums[0]), "p95": int(nums[5]), "p99": int(nums[7]),
                               "max": int(nums[10]), "reqs": int(nums[-1])}
    return out


def failures(console: str) -> int:
    """Failed requests/journeys over ALL stats (the Aggregated row), not just journey-total."""
    m = re.search(r"Aggregated\s+\d+\s+(\d+)\(", console.split("Response time percentiles")[0])
    return int(m.group(1)) if m else -1


def peak_queue(samples: Path) -> str:
    queued = running = 0
    for line in samples.read_text().splitlines() if samples.exists() else []:
        for state, n in re.findall(r"(queued|running|orphaned)=(\d+)", line):
            if state == "queued":
                queued = max(queued, int(n))
            elif state == "running":
                running = max(running, int(n))
    return f"{queued}/{running}"


def main() -> None:
    out, rungs = Path(sys.argv[1]), sys.argv[2:]
    print("| Users | Journeys | Failures | Control p50/p95 (ms) | Engine-wait p50/p95/p99 (s) "
          "| Journey max (s) | Peak queued/running claims | Drain (s) | Audit (cumulative) |")
    print("|---:|---:|---:|---|---|---:|---|---:|---|")
    for vus in rungs:
        console = (out / f"console_{vus}.log").read_text() if (out / f"console_{vus}.log").exists() else ""
        pct = percentiles(console)
        audit = json.loads((out / f"audit_{vus}.json").read_text() or "{}") if (out / f"audit_{vus}.json").exists() else {}
        drain = json.loads((out / f"drain_{vus}.json").read_text()) if (out / f"drain_{vus}.json").exists() else {}
        jt, cp, ew = pct.get("journey-total", {}), pct.get("control-plane", {}), pct.get("engine-wait", {})
        bad = {k: v for k, v in audit.items()
               if k in ("lost", "duplicateStart", "duplicateEngineTerminal", "duplicateReviewTerminal",
                        "conflictingEngineTerminal", "terminalWithoutStart", "leftoverClaims", "duplicateSeq") and v}
        verdict = f"runs={audit.get('runs', '?')} anomalies={bad or 'none'}"
        if drain.get("drained") == "no":
            verdict += " (queue did NOT drain)"
        print(f"| {vus} | {jt.get('reqs', '?')} | {failures(console)} | {cp.get('p50', '?')}/{cp.get('p95', '?')} "
              f"| {ew.get('p50', 0) / 1000:.1f}/{ew.get('p95', 0) / 1000:.1f}/{ew.get('p99', 0) / 1000:.1f} "
              f"| {jt.get('max', 0) / 1000:.1f} | {peak_queue(out / f'samples_{vus}.log')} "
              f"| {drain.get('drainSeconds', '?')} | {verdict} |")


main()
