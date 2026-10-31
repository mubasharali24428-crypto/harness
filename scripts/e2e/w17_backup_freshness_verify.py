#!/usr/bin/env python3
"""W17 Stage-2: verify the backup-freshness signal on the deployed stack.

The alerting runbook already has ``HarnessBackupStale`` / ``HarnessBackupNeverRan``
reading ``harness_backup_last_success_timestamp_seconds`` (exported by the
orchestrator's DB poller from ``backup_runs``). "Verify" here means: prove the
metric is scraped on this deployment and that the alert state agrees with the
metric's age. It does NOT run a real backup: the backup runbook forbids a drill
from archiving into the live prefix. The "a fresh backup clears the alert" leg is
covered by the promtool rule test and by ``test_backup_metrics_pg.py``; this
drill is the live half.

A stale backup is a finding, not a drill failure: the check is that the alert
truthfully reports whatever state the stack is in. A manually inserted
``backup_runs`` row would "fix" the number and prove nothing, so it is not done.

    python3 scripts/e2e/w17_backup_freshness_verify.py
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERIES = "harness_backup_last_success_timestamp_seconds"
STALE_AFTER = 90_000.0  # seconds; must match deploy/observability/alerts.yml


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def scraped_value(prometheus: str, series: str) -> float | None:
    data = get_json(f"{prometheus}/api/v1/query?query={urllib.parse.quote(series)}")
    result = data.get("data", {}).get("result", [])
    if not result:
        return None
    return float(result[0]["value"][1])


def alert_states(prometheus: str) -> dict[str, str]:
    data = get_json(f"{prometheus}/api/v1/alerts")
    return {
        a["labels"].get("alertname", ""): a["state"]
        for a in data.get("data", {}).get("alerts", [])
    }


def expr_holds(prometheus: str, expr: str) -> bool:
    data = get_json(f"{prometheus}/api/v1/query?query={urllib.parse.quote(expr)}")
    return bool(data.get("data", {}).get("result", []))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prometheus", default="http://127.0.0.1:9090")
    args = ap.parse_args()
    evidence: dict = {"checks": {}, "verdict": "FAIL"}
    path = REPO / "docs/briefs/evidence/w17"
    path.mkdir(parents=True, exist_ok=True)
    try:
        value = scraped_value(args.prometheus, SERIES)
        states = alert_states(args.prometheus)
        evidence["seriesValue"] = value
        evidence["alerts"] = states
        if value is None:
            # Absent series: HarnessBackupNeverRan is the designed signal.
            assert states.get("HarnessBackupNeverRan") == "firing", (
                "backup series is absent but HarnessBackupNeverRan is not firing"
            )
            evidence["checks"] = {"seriesScraped": False, "neverRanFires": True}
            evidence["verdict"] = "PASS"
            return 0
        age = time.time() - value
        stale = age > STALE_AFTER
        expr = f"time() - {SERIES} > {int(STALE_AFTER)}"
        holds = expr_holds(args.prometheus, expr)
        state = states.get("HarnessBackupStale", "inactive")
        evidence.update({
            "ageSeconds": round(age, 1),
            "staleThresholdSeconds": STALE_AFTER,
            "expectedStale": stale,
            "exprHolds": holds,
            "backupStaleState": state,
        })
        evidence["checks"] = {
            "seriesScraped": True,
            "neverRanQuiet": states.get("HarnessBackupNeverRan", "inactive") != "firing",
            "exprAgreesWithAge": holds is stale,
            "alertAgreesWithExpr": (state == "firing") is stale,
        }
        assert holds is stale, "the alert expression disagrees with the metric age"
        assert (state == "firing") is stale, (
            f"HarnessBackupStale is {state} but the backup age is {age:.0f}s "
            f"(stale={stale})"
        )
        assert evidence["checks"]["neverRanQuiet"], "HarnessBackupNeverRan fires while a series exists"
        evidence["verdict"] = "PASS"
    except Exception as exc:
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        out = path / f"w17-backup-freshness-{int(time.time())}.json"
        out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"verdict": evidence["verdict"], "evidence": str(out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
