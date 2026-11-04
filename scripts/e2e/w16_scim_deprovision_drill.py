#!/usr/bin/env python3
"""W16 slice 3 — prove a SCIM deprovisioning reaches the live stack's doors.

The unit tests pin the SCIM module. This drill answers the question an
enterprise buyer puts to it, against the deployed containers and a real
Postgres:

    "When Okta says this person left, what actually stops them here?"

It mints a SCIM token through the admin API, reads the directory view, PATCHes
`active=false`, and then shows that the SAME user's already-issued session
token is refused by Section 1 AND by a service using the shared contract
dependency — with no admin touching Harness in between. Then it reactivates,
proves the last admin cannot be deprovisioned, and proves a revoked SCIM token
cannot write anything.

    python3 scripts/e2e/w16_scim_deprovision_drill.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
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

MISSING_RUN = "w16-scim-probe-does-not-exist"
SCIM = f"{IDENTITY}/scim/v2"


def call(
    method: str,
    url: str,
    token: str | None = None,
    body: dict | None = None,
    headers: dict | None = None,
) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    else:
        status = resp.status
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {"raw": raw[:200].decode(errors="replace")}


def status_only(method: str, url: str, token: str | None = None) -> int:
    return call(method, url, token)[0]


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
    checks: list[dict] = []

    def expect(name: str, got: object, want: object) -> None:
        ok = got == want
        checks.append({"check": name, "got": got, "want": want, "ok": ok})
        print(
            f"[{'PASS' if ok else 'FAIL'}] {name}: {got}"
            + ("" if ok else f" (want {want})"),
            flush=True,
        )
        if not ok:
            raise SystemExit(json.dumps({"verdict": "FAIL", "failed": name, "checks": checks}))

    admin_token, admin = cj.authenticate("w16 scim admin", f"w16-scim-admin-{stamp}@example.com")
    team_id = admin["teamId"]
    expect("admin login is admin", admin["role"], "admin")
    st, _ = call("PUT", f"{IDENTITY}/api/v1/billing/plan", admin_token, {"plan": "team"})
    expect("team tier raised for the drill", st, 200)

    member_email = f"w16-scim-member-{stamp}@example.com"
    st, invite = call(
        "POST",
        f"{IDENTITY}/api/v1/teams/{team_id}/invites",
        admin_token,
        {"email": member_email, "role": "engineer"},
    )
    expect("member invited", st, 201)
    member_token, member = cj.authenticate("w16 scim member", member_email)
    st, _ = call(
        "POST",
        f"{IDENTITY}/api/v1/invites/redeem",
        member_token,
        {"token": invite["token"], "email": member_email},
    )
    expect("invite redeemed", st, 200)
    member_id = member["userId"]

    # ---------------------------------------------------------------- credential
    st, minted = call(
        "POST", f"{IDENTITY}/api/v1/teams/{team_id}/scim/tokens", admin_token, {"label": "drill"}
    )
    expect("SCIM token minted", st, 201)
    scim_token = minted["token"]
    expect("the raw token is shown once", scim_token.startswith("hsc_"), True)
    st, listed = call("GET", f"{IDENTITY}/api/v1/teams/{team_id}/scim/tokens", admin_token)
    expect(
        "and the list carries no secret",
        any("token" in json.dumps(item) for item in listed["items"]),
        False,
    )
    st, _ = call("GET", f"{SCIM}/Users", member_token)
    expect("a user session token does not open the SCIM door", st, 401)

    # ------------------------------------------------------------------- read
    st, listing = call("GET", f"{SCIM}/Users", scim_token)
    expect("SCIM list answers", st, 200)
    expect("both seats are visible", listing["totalResults"], 2)
    st, one = call("GET", f"{SCIM}/Users/{member_id}", scim_token)
    expect("the member is active before the drill writes", one["active"], True)
    expect(
        "meta.location points at this surface",
        one["meta"]["location"],
        f"{SCIM}/Users/{member_id}",
    )
    etag = one["meta"]["etag"]
    quoted = urllib.parse.quote(f'userName eq "{member_email}"')
    st, filtered = call("GET", f"{SCIM}/Users?filter={quoted}", scim_token)
    expect("the userName filter finds one seat", filtered["totalResults"], 1)

    # ------------------------------------------------------------- deprovision
    st, _ = call(
        "PATCH",
        f"{SCIM}/Users/{member_id}",
        scim_token,
        {"Operations": [{"op": "replace", "path": "active", "value": False}]},
        headers={"If-Match": 'W/"stale-value"'},
    )
    expect("a stale If-Match is refused with 412", st, 412)
    expect(
        "and nothing changed behind it",
        psql(f"SELECT is_active FROM users WHERE id = '{member_id}';"),
        "t",
    )

    st, after = call(
        "PATCH",
        f"{SCIM}/Users/{member_id}",
        scim_token,
        {"Operations": [{"op": "replace", "path": "active", "value": False}]},
    )
    expect("deprovision accepted", st, 200)
    expect("and the resource says so", after["active"], False)
    expect("the etag moved", etag == after["meta"]["etag"], False)

    # The seat itself, at both live doors, with the token issued BEFORE the write.
    expect("identity /me refuses the deprovisioned member", 
           status_only("GET", f"{IDENTITY}/api/v1/me", member_token), 401)
    expect(
        "the shared dependency refuses it too (401, not 404)",
        status_only("GET", f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{MISSING_RUN}", member_token),
        401,
    )
    expect(
        "the store shows the seat disabled",
        psql(f"SELECT is_active FROM users WHERE id = '{member_id}';"),
        "f",
    )
    st, denied_login = call(
        "POST",
        f"{IDENTITY}/api/v1/auth/login",
        body={"email": member_email, "teamId": team_id},
    )
    expect("a fresh login is refused too", st, 403)
    expect(
        "and the refusal names the seat, not the password",
        ((denied_login.get("detail") or {}).get("error") or {}).get("code"),
        "ACCOUNT_DISABLED",
    )
    # Deletion is scoped to THIS team on purpose: the account flag is what
    # reaches every door, the row cleanup follows the seat the IdP manages.
    expect(
        "and this team's session rows are gone (teams_api convention)",
        psql(
            "SELECT count(*) FROM auth_sessions WHERE user_id = '"
            f"{member_id}' AND team_id = '{team_id}';"
        ),
        "0",
    )
    expect(
        "the mutation is audited as a deprovision by the token, not a person",
        psql(
            "SELECT detail->>'kind' FROM audit_log WHERE action = 'scim.user_changed' "
            "ORDER BY seq DESC LIMIT 1;"
        ),
        "deprovision",
    )
    expect(
        "and the audit row names the credential that wrote it",
        psql(
            "SELECT (detail->>'refId') IS NOT NULL FROM audit_log "
            "WHERE action = 'scim.user_changed' ORDER BY seq DESC LIMIT 1;"
        ),
        "t",
    )

    # --------------------------------------------------------------- reactivate
    st, revived = call(
        "PATCH",
        f"{SCIM}/Users/{member_id}",
        scim_token,
        {"Operations": [{"op": "replace", "path": "active", "value": True}]},
    )
    expect("reactivation accepted", st, 200)
    expect("and the seat is live again", revived["active"], True)
    # The member belongs to two teams, so the login names the seat. A 403 here
    # would mean the deprovision was never undone.
    st, session = call(
        "POST",
        f"{IDENTITY}/api/v1/auth/login",
        body={"email": member_email, "teamId": team_id},
    )
    expect("a new session can be minted again", st, 200)
    new_token = (session or {}).get("token", "")
    expect("and it works at the shared door",
           status_only("GET", f"{ORCHESTRATOR}/api/v1/orchestrator/runs/{MISSING_RUN}", new_token),
           404)

    # ----------------------------------------------------------- refusals bound
    st, denied = call(
        "PATCH",
        f"{SCIM}/Users/{admin['userId']}",
        scim_token,
        {"Operations": [{"op": "replace", "path": "active", "value": False}]},
    )
    expect("the last admin cannot be deprovisioned", st, 403)
    expect(
        "and the refusal is a SCIM error object, not our envelope",
        denied.get("schemas"),
        ["urn:ietf:params:scim:api:messages:2.0:Error"],
    )
    expect(
        "the admin seat is untouched",
        psql(f"SELECT is_active FROM users WHERE id = '{admin['userId']}';"),
        "t",
    )

    st, _ = call(
        "PATCH",
        f"{SCIM}/Users/{member_id}",
        scim_token,
        {"Operations": [{"op": "replace", "path": "name.givenName", "value": "Z"}]},
    )
    expect("an attribute this service does not own is refused, not ignored", st, 400)

    st, _ = call("DELETE", f"{IDENTITY}/api/v1/teams/{team_id}/scim/tokens/{minted['id']}", admin_token)
    expect("the SCIM token is revoked", st, 204)
    expect(
        "and a revoked credential writes nothing",
        status_only(
            "PATCH",
            f"{SCIM}/Users/{member_id}",
            scim_token,
        ),
        401,
    )

    evidence = {
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "milestone": "W16 slice 3 — a SCIM deprovision reaches every door",
        "teamId": team_id,
        "memberEmail": member_email,
        "checks": checks,
        "verdict": "PASS",
        "notes": [
            "no secret appears in this file: the SCIM token is shown once in the mint",
            "response and never stored, only its SHA-256 is.",
            "the member's session token was issued BEFORE the PATCH; nothing else",
            "was changed by hand between acceptance and the 401s.",
        ],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE / f"w16-scim-deprovision-{stamp}.json"
    out.write_text(json.dumps(evidence, indent=2) + "\n")
    print(
        json.dumps(
            {"verdict": "PASS", "evidence": str(out.relative_to(ROOT)), "checks": len(checks)}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
