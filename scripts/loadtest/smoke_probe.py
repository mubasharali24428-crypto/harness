#!/usr/bin/env python3
"""One-journey smoke probe — proves the flow works before any load run.

Boots nothing; point it at a running stack (run_probe.sh up, or the live
compose stack if HARNESS_REQUIRE_OWNERSHIP is off there):

    python3 scripts/loadtest/smoke_probe.py                # defaults below
    python3 scripts/loadtest/smoke_probe.py --vu 4 --email smoke@harness.dev

Prints one JSON line per journey plus a summary block. Exit 0 only if every
journey reached a terminal state (done expected on a dry-engine stack).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

import harness_flow as flow


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--identity", default=flow.IDENTITY_URL)
    ap.add_argument("--orchestrator", default=flow.ORCHESTRATOR_URL)
    ap.add_argument("--email", default=f"smoke-{uuid.uuid4().hex[:6]}@loadtest.local")
    ap.add_argument("--vu", type=int, default=1, help="parallel journeys (each its own team)")
    ap.add_argument("--iterations", type=int, default=None,
                    help="journeys per VU (default: 1 each)")
    args = ap.parse_args()

    flow.IDENTITY_URL = args.identity.rstrip("/")
    flow.ORCHESTRATOR_URL = args.orchestrator.rstrip("/")

    def one(vu: int, it: int) -> dict[str, object]:
        client = flow.HarnessFlowClient()
        try:
            email = f"{args.email.replace('@', f'.{vu}-{it}@', 1)}"
            result = flow.run_flow(client, email, "Smoke goal: echo a haiku about load testing.")
            return {"vu": vu, "it": it, **result}  # type: ignore[dict-item]
        finally:
            client.close()

    jobs = [(v, i) for v in range(1, args.vu + 1) for i in range(1, (args.iterations or 1) + 1)]
    results: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(one, v, i) for v, i in jobs]
        for fut in futures:
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001 — report, don't crash the batch
                print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)

    ok = [r for r in results if r.get("final_state") == "done"]
    for r in results:
        t = r["timings"]
        print(
            json.dumps({
                "vu": r["vu"], "iteration": r["it"],
                "runId": r["run_id"], "state": r["final_state"],
                "login_s": round(t["login"], 3),          # type: ignore[index]
                "project_s": round(t["project"], 3),      # type: ignore[index]
                "plan_s": round(t["plan"], 3),            # type: ignore[index]
                "approve_s": round(t["approve"], 3),      # type: ignore[index]
                "start_s": round(t["start"], 3),          # type: ignore[index]
                "engine_wait_s": round(t["wait_terminal"], 3),  # type: ignore[index]
                "total_s": round(t["total"], 3),          # type: ignore[index]
            })
        )

    print(f"\n[smoke] {len(ok)}/{len(results)} journeys reached terminal 'done'")
    return 0 if len(ok) == len(jobs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
