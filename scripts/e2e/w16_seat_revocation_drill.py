#!/usr/bin/env python3
"""W16 slice 1 — prove a revoked seat stops working ON THE LIVE STACK.

The unit tests pin each door separately. This drill asks the question an
enterprise buyer actually asks, against the deployed containers:

    "I took the seat away. Show me that their token does nothing now."

It walks the two doors the deployment really exposes — Section 1's own
`get_principal` (identity) and the shared `contracts.make_bearer_dep` that the
other services install — and shows the same token go from accepted to refused on
BOTH, then that re-enabling the seat works again.

A nonexistent run id is used as the non-identity probe on purpose: authorization
happens before the resource lookup, so an active seat gets 404 (not found) and a
revoked seat gets 401 (not allowed). The status transition is the evidence; no
data is written and no model is called.

    python3 scripts/e2e/w16_seat_revocation_drill.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "briefs" / "evidence" / "pilot-prep"
IDENTITY = os.environ.get("E2E_IDENTITY_URL", "http://127.0.0.1:8001").rstrip("/")
ORCHESTRATOR = os.environ.get("E2E_ORCHESTRATOR_URL", "http://127.0.0.1:8004").rstrip("/")

spec = importlib.util.spec_from_file_location(
    "compose_journey", ROOT / "scripts" / "e2e" / "compose_journey.py"
)
cj = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(cj)

MISSING_RUN = "w16-revocation-probe-does-not-exist"


def http(method: str, url: str, token: str | None = None, body: dict | None = None) -> int:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def psql(sql: str) -> str:
    proc = subprocess.run(
        ["docker", "exec", "harness-db-1", "psql", "-U", "harness", "-d", "harness", "-tAc", sql],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def main() -> int:
    stamp = int(time.time())
    email = f"w16-revocation-{stamp}@example.com"
    checks: list[dict] = []

    def expect(name: str, got: object, want: object) -> None:
        ok = got == want
        checks.append({"check": name, "got": got, "want": want, "ok": ok})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {got}" + ("" if ok else f" (want {want})"), flush=True)
        if not ok:
            raise SystemExit(json.dumps({"verdict": "FAIL", "failed": name, "checks": checks}))

    token, principal = cj.authenticate("w16 revocation login", email)
    user_id = principal["userId"]

    # 1. both doors accept the live seat
    expect("identity /me accepts the active seat", http("GET", f"{IDENTITY}/api/v1/me", token), 200)
    expect(
        "the shared dependency accepts it too (404 = authorized, missing run)",
        http("GET", f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{MISSING_RUN}", token),
        404,
    )

    # 2. revoke the seat the way the store does it
    psql(f"UPDATE users SET is_active = false WHERE id = '{user_id}';")
    expect("the store says the seat is revoked", psql(f"SELECT is_active FROM users WHERE id = '{user_id}';"), "f")

    # 3. both doors refuse — and no session row was deleted, so this is the
    #    seat rule working, not `_revoke_access` having cleaned up
    expect("identity /me refuses the revoked seat", http("GET", f"{IDENTITY}/api/v1/me", token), 401)
    expect(
        "the shared dependency refuses it too (401, not 404)",
        http("GET", f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{MISSING_RUN}", token),
        401,
    )

    # 4. a fresh login cannot resurrect it
    http("POST", f"{IDENTITY}/api/v1/auth/request-login", body={"email": email})
    code = cj.fetch_otp(email)
    status, body = cj.http(
        "POST", f"{IDENTITY}/api/v1/auth/verify-otp", body={"email": email, "code": code}
    )
    expect("a new session cannot be minted", status, 403)
    detail = (body or {}).get("detail", {}) if isinstance(body, dict) else {}
    expect("and the refusal names the reason", (detail.get("error") or {}).get("code"), "ACCOUNT_DISABLED")

    # 5. restoring the seat works — revocation is a state, not a deletion
    psql(f"UPDATE users SET is_active = true WHERE id = '{user_id}';")
    http("POST", f"{IDENTITY}/api/v1/auth/request-login", body={"email": email})
    code = cj.fetch_otp(email)
    status, revived = cj.http(
        "POST", f"{IDENTITY}/api/v1/auth/verify-otp", body={"email": email, "code": code}
    )
    expect("re-enabling the seat restores login", status, 200)
    new_token = (revived or {}).get("token", "") if isinstance(revived, dict) else ""
    expect("and the new token works on the shared door", 
           http("GET", f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{MISSING_RUN}", new_token), 404)

    evidence = {
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "milestone": "W16 slice 1 — a revoked seat is revoked everywhere",
        "email": email,
        "userId": user_id,
        "note": (
            "no auth_sessions row was deleted at any point: the token in step 3 is the "
            "same one that worked in step 1, refused only because users.is_active changed"
        ),
        "checks": checks,
        "verdict": "PASS",
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE / f"w16-seat-revocation-{stamp}.json"
    out.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps({"verdict": "PASS", "evidence": str(out.relative_to(ROOT)), "checks": len(checks)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
