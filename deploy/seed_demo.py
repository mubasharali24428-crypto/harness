#!/usr/bin/env python3
"""Idempotent exhibition seed for the Harness demo (wave 5).

Drives ONLY the live REST API — no direct DB access, so it works against any
environment the operator can reach:

    python3 deploy/seed_demo.py                       # defaults below
    python3 deploy/seed_demo.py --identity http://localhost:8001 \
        --github http://localhost:8002 --email demo@harness.dev

Safe to run twice: the demo project is matched by name and the repo connect is
a same-value upsert. A fresh session is minted on every run.

Prints the exact browser localStorage keys the app consumes (see
section7/src/lib/auth.ts) ready to paste into DevTools.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import cast

# S1's passwordless login mints a NEW team on every call, so re-logging-in on
# each run would defeat idempotency (a fresh team never sees the old project).
# Persist the session here and reuse it until the API rejects the token.
STATE_FILE = Path(__file__).resolve().parent / ".seed_session.json"


def _call(base: str, method: str, path: str, token: str | None = None, body: dict | None = None):
    req = urllib.request.Request(
        f"{base.rstrip('/')}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "content-type": "application/json",
            **({"authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        payload = e.read().decode()
        try:
            return e.code, json.loads(payload or "{}")
        except json.JSONDecodeError:
            return e.code, {"raw": payload}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--identity", default="http://localhost:8001")
    ap.add_argument("--github", default="http://localhost:8002")
    ap.add_argument("--email", default="demo@harness.dev")
    ap.add_argument("--project", default="demo-repo")
    ap.add_argument("--repo", default="local/demo-repo",
                    help="repo_full_name to attach (offline-safe label)")
    args = ap.parse_args()

    def _login() -> tuple[str, dict]:
        status, body = _call(args.identity, "POST", "/api/v1/auth/login",
                             body={"email": args.email})
        if status == 403:
            detail = body.get("detail") or {}
            code = (detail.get("error") or {}).get("code") if isinstance(detail, dict) else None
            if code == "OWNERSHIP_REQUIRED":
                print("ERROR: HARNESS_REQUIRE_OWNERSHIP is ON. Run once with it OFF, "
                      "or complete the OTP flow manually and pass that session instead.",
                      file=sys.stderr)
                raise SystemExit(2)
        if status != 200:
            print(f"ERROR: login failed ({status}): {body}", file=sys.stderr)
            raise SystemExit(1)
        token_value: str = body["token"]
        principal_value = cast(dict, body["principal"])
        return token_value, principal_value

    # Idempotency: reuse the persisted demo session instead of logging in again
    # (each passwordless login mints a fresh team). Fall back to a fresh login
    # when no state exists or the stored token was rejected/expired.
    session: tuple[str, dict] | None = None
    if STATE_FILE.exists():
        try:
            saved = json.loads(STATE_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            saved = None
        if isinstance(saved, dict) and saved.get("token"):
            status, _probe = _call(args.identity, "GET", "/api/v1/me",
                                   token=saved["token"])
            if status == 200:
                session = (saved["token"], saved["principal"])
    if session is None:
        session = _login()
        STATE_FILE.write_text(json.dumps({"token": session[0], "principal": session[1]}))
    token, principal = session
    print(f"[seed] session ok for {args.email} (team {principal['teamId'][:8]}…)")

    status, items = _call(args.identity, "GET", "/api/v1/projects?limit=500", token)
    if status != 200:
        print(f"ERROR: list projects failed ({status})", file=sys.stderr)
        return 1
    existing = next((p for p in items.get("items", []) if p["name"] == args.project), None)

    if existing:
        project_id = existing["id"]
        print(f"[seed] project '{args.project}' exists ({project_id[:8]}…)")
    else:
        status, created = _call(args.identity, "POST", "/api/v1/projects", token,
                                body={"name": args.project})
        if status != 201:
            print(f"ERROR: create project failed ({status}): {created}", file=sys.stderr)
            return 1
        project_id = created["id"]
        print(f"[seed] project '{args.project}' created ({project_id[:8]}…)")

    needs_connect = not existing or existing.get("repoFullName") != args.repo
    if needs_connect:
        status, body = _call(
            args.github, "POST",
            f"/api/v1/projects/{project_id}/repo/connect", token,
            body={"projectId": project_id, "repoFullName": args.repo},
        )
        if status != 200:
            print(f"ERROR: repo connect failed ({status}): {body}", file=sys.stderr)
            return 1
        print(f"[seed] repo connected -> {args.repo}")
    else:
        print("[seed] repo already connected")

    session_value = json.dumps({"token": token, "principal": principal})
    print("\n=== Paste into the browser console (DevTools) on localhost:3000 ===")
    print(f"localStorage.setItem('harness.session', JSON.stringify({session_value}));")
    print(f"localStorage.setItem('harness.projectId', '{project_id}');")
    print(f"localStorage.setItem('harness.userId', '{principal['userId']}');")
    print("localStorage.removeItem('harness.runId'); // set by starting a run in the UI")
    print("\n[seed] done — open http://localhost:3000 and sign-in state is live.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
