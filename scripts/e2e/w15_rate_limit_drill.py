#!/usr/bin/env python3
"""W15 slice 2 — prove the per-team model-call budget on the live stack.

The deployment must be booted with a small ``HARNESS_TEAM_MODEL_CALL_LIMIT``
(a dev stack normally runs the shipped default of 300, which this drill cannot
afford to exhaust). Each run authenticates two FRESH teams, so the bucket starts
empty and the block point is exact, not a guess.

What it proves:

* the N+1th call for one team is refused 429 RATE_LIMITED with Retry-After,
  after exactly N allowed calls;
* a second team's call is not throttled by the first team's exhaustion.

The provider path may itself answer 503 in a keyless dev stack; that is
orthogonal — the limiter decision is the status transition under test.

    W15_TEAM_CALL_LIMIT=3 python3 scripts/e2e/w15_rate_limit_drill.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "briefs" / "evidence" / "eval"
MODELS = os.environ.get("E2E_MODELS_URL", "http://127.0.0.1:8003").rstrip("/")
LIMIT = int(os.environ.get("W15_TEAM_CALL_LIMIT", "3"))

spec = importlib.util.spec_from_file_location(
    "compose_journey", ROOT / "scripts" / "e2e" / "compose_journey.py"
)
cj = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(cj)

BODY = {
    "agentId": "w15-limits",
    "role": "primary",
    "messages": [{"role": "user", "content": "w15 team budget probe"}],
}


def _call(token: str) -> tuple[int, dict | str, dict[str, str]]:
    data = json.dumps(BODY).encode()
    req = urllib.request.Request(f"{MODELS}/api/v1/models/call", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw, status, headers = resp.read(), resp.status, _headers(resp.headers)
    except urllib.error.HTTPError as exc:
        raw, status, headers = exc.read(), exc.code, _headers(exc.headers)
    try:
        parsed = json.loads(raw) if raw else {}
    except ValueError:
        parsed = raw.decode("utf-8", "replace")
    return status, parsed, headers


def _headers(message) -> dict[str, str]:  # noqa: ANN001 — http.client message
    return {key.lower(): value for key, value in message.items()}


def _code(body: dict | str) -> str | None:
    if isinstance(body, dict):
        return (body.get("error") or {}).get("code")
    return None


def main() -> int:
    stamp = int(time.time())
    token_a, _ = cj.authenticate("w15 limits a", f"w15-{stamp}-a@example.com")
    token_b, _ = cj.authenticate("w15 limits b", f"w15-{stamp}-b@example.com")

    allowed = 0
    blocked = None
    for _ in range(LIMIT + 2):
        status, body, headers = _call(token_a)
        if status == 429:
            blocked = (status, _code(body), headers.get("retry-after"))
            break
        allowed += 1
    team_b_status, team_b_body, _ = _call(token_b)

    checks = {
        "exactlyLimitCallsAllowed": allowed == LIMIT,
        "nextCallBlocked429": blocked is not None and blocked[0] == 429,
        "errorCodeIsRateLimited": blocked is not None and blocked[1] == "RATE_LIMITED",
        "retryAfterPresent": blocked is not None and str(blocked[2] or "").isdigit(),
        "otherTeamNotThrottled": team_b_status != 429,
    }
    evidence = {
        "schema": 1,
        "kind": "w15-rate-limit",
        "takenAt": datetime.now(UTC).isoformat(),
        "deployedTeamLimit": LIMIT,
        "allowedBeforeBlock": allowed,
        "blocked": {"status": blocked[0], "code": blocked[1], "retryAfter": blocked[2]}
        if blocked
        else None,
        "otherTeam": {"status": team_b_status, "code": _code(team_b_body)},
        "checks": checks,
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "failedChecks": [name for name, ok in checks.items() if not ok],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / f"w15-rate-limit-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": evidence["verdict"], "evidence": str(path)}, indent=2))
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
