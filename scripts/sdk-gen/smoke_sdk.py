#!/usr/bin/env python3
"""Smoke-test harness_sdk against the LIVE compose stack.

Runs the full scripter journey from the commander's intent (auth -> one sync
call per section -> event streaming) and prints what the API actually
returned. Exits non-zero on any failure.

    cd clients/python && ./.venv/bin/python ../../scripts/sdk-gen/smoke_sdk.py

Requires: docker compose stack up (identity 8001 … review 8008). Dev OTP
codes are read from the identity container logs (grep HARNESS-OTP).
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import uuid

from harness_sdk import ApiError, Harness

IDENTITY_LOG_LINES = 400


def otp_from_logs(email: str) -> str:
    """Dev stacks email nothing — the OTP is printed to identity's logs."""
    ps = subprocess.run(
        ["docker", "ps", "--filter", "publish=8001", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=30,
    )
    container = ps.stdout.strip().splitlines()[0] if ps.stdout.strip() else None
    if not container:
        raise SystemExit("no identity container found listening on :8001")
    out = subprocess.run(["docker", "logs", "--tail", str(IDENTITY_LOG_LINES),
                          container], capture_output=True, text=True,
                         timeout=60)
    combined = out.stdout + out.stderr  # the OTP line lands on stderr
    marker = f"HARNESS-OTP login code for {email}: "
    for line in combined.splitlines():
        if marker in line:
            return line.split(marker, 1)[1].strip()[:6]
    raise SystemExit(f"no HARNESS-OTP code for {email} in {container} logs")


def step(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 62 - len(title)))


def show(label: str, value) -> None:
    text = json.dumps(value, indent=2, default=str)
    if len(text) > 700:
        text = text[:700] + "\n  … (truncated)"
    print(f"--- {label}\n{text}")


def main() -> int:
    email = f"sdk-smoke-{uuid.uuid4().hex[:8]}@example.com"
    sdk = Harness()
    ok = True

    try:
        # -- AUTH ------------------------------------------------------------
        step("AUTH: legacy login (expect OWNERSHIP_REQUIRED envelope)")
        try:
            Harness.login(email)
            print("UNEXPECTED: legacy login succeeded")
        except ApiError as err:
            show("ApiError raised by Harness.login()", {
                "status": err.status, "code": err.code, "message": str(err)})
            assert err.code == "OWNERSHIP_REQUIRED", err

        step(f"AUTH: OTP flow for {email}")
        ack = sdk.request_otp(email)
        show("POST /auth/request-login", ack)
        code = otp_from_logs(email)
        session = sdk.verify_otp(email, code)
        show("POST /auth/verify-otp", {
            "token": session["token"][:14] + "…",
            "principal": session.get("principal")})
        team_id = session["principal"]["teamId"]
        token = sdk.token

        # -- ONE SYNC CALL PER SECTION ----------------------------------------
        step("IDENTITY: list_projects()")
        projects = sdk.identity.list_projects(5)
        items = projects.get("items") if isinstance(projects, dict) else projects
        project_id = items[0]["id"] if items else None
        show("GET /api/v1/projects?limit=5", {"count": len(items),
                                              "first": items[0] if items else None})
        if not project_id:
            created = sdk.identity.create_project(
                {"name": f"sdk-smoke-{uuid.uuid4().hex[:6]}"})
            project_id = created["id"]
            show("POST /api/v1/projects (none existed)", created)

        step("GITHUB: connection_status()")
        show("GET /api/v1/github/connection", sdk.github.connection_status())

        step("ROUTER: list_models() / spend(teamId=…)")
        models = sdk.router.list_models()
        show("GET /api/v1/models", models)
        try:
            show("GET /api/v1/models/spend?teamId=…",
                 sdk.router.model_spend(team_id))
        except ApiError as err:
            show("spend -> ApiError", {"code": err.code, "message": str(err)})

        step("ORCHESTRATOR: list_runs(projectId=…)")
        runs = sdk.orchestrator.list_runs(project_id=project_id, limit=3)
        run_items = runs.get("items", []) if isinstance(runs, dict) else []
        show("GET /api/v1/orchestrator/runs?…", {"count": len(run_items),
                                                 "states": sorted({
                                                     r.get("state") or r.get("status")
                                                     for r in run_items})})

        step("SANDBOX: list_checkpoints(projectId)")
        try:
            cps = sdk.sandbox.list_checkpoints(project_id)
            cp_items = cps.get("items", []) if isinstance(cps, dict) else cps
            show("GET /api/v1/sandbox/{projectId}/checkpoints",
                 {"count": len(cp_items)})
        except ApiError as err:
            show("list_checkpoints -> ApiError", {"code": err.code,
                                                  "message": str(err)})

        step("MEETING: create_meeting() + close (no side effects left)")
        meeting = sdk.meeting.create_meeting(
            {"projectId": project_id,
             "title": "SDK smoke meeting (safe to ignore)"})
        show("POST /api/v1/meetings", meeting)
        closed = sdk.meeting.close_meeting(meeting["id"])
        show("POST /api/v1/meetings/{{id}}/close", closed)

        step("REVIEW: get_reviewer_threshold_settings()")
        show("GET /api/v1/reviews/settings/thresholds",
             sdk.review.get_reviewer_threshold_settings())

        # -- EVENT STREAMING ---------------------------------------------------
        step("WS: ticket mint + fingerprint + live stream")
        ticket = sdk.identity.issue_ws_ticket({"projectId": project_id})
        show("POST /api/v1/ws-ticket (M18-C5 fingerprint echo present?)",
             {"ticket": str(ticket.get("ticket"))[:10] + "…",
              "expiresInSeconds": ticket.get("expiresInSeconds"),
              "fingerprint": ticket.get("fingerprint"),
              "<- echoed server binding": bool(ticket.get("fingerprint"))})

        replay_rest = sdk.replay_events(project_id, after_seq=0)
        existing_seqs = [e["seq"] for e in replay_rest.get("items", [])]
        print(f"REST replay: {len(existing_seqs)} historical events "
              f"(max seq {max(existing_seqs) if existing_seqs else 0})")

        stop = threading.Event()
        received: list[dict] = []

        def consume():
            try:
                for env in sdk.events.stream(project_id, token):
                    received.append(env)
                    if stop.is_set():
                        break
            except ApiError as err:
                print(f"stream ended: ApiError {err.code}: {err}")

        thread = threading.Thread(target=consume, daemon=True)
        thread.start()
        time.sleep(2.0)  # let the socket attach

        # Trigger a real bus publish on THIS project: proposing a run plan
        # bound to projectId emits plan_proposed (tutorial §4 flow). We reject
        # it right after, so no run is started.
        run_id = f"run-sdksmoke{uuid.uuid4().hex[:8]}"[:20]
        trigger = sdk.orchestrator.propose_plan_2(
            run_id, {"goal": "SDK smoke — safe to ignore", "projectId": project_id})
        show("POST /api/v1/orchestrator/runs/{runId}/plan (stream trigger)",
             trigger)
        try:
            sdk.orchestrator.reject_plan_2(
                run_id, {"planId": trigger.get("planId")})
        except ApiError as err:
            print(f"(cleanup reject: {err.code})")
        deadline = time.time() + 15
        while time.time() < deadline and not received:
            time.sleep(0.2)
        stop.set(); time.sleep(0.3)

        seqs = [e.get("seq") for e in received]
        dedup_ok = len(seqs) == len(set(seqs))
        ascending = all(a < b for a, b in zip(seqs, seqs[1:]))
        show("streamed envelopes", [
            {k: e.get(k) for k in ("seq", "type", "projectId")} for e in received[:4]])
        print(f"received={len(received)} unique={dedup_ok} "
              f"ascending(seq-dedupe+order)= {ascending}")
        ok = ok and bool(received) and dedupe_ok_check(dedup_ok, ascending)

        step("RESULT")
        print("SMOKE PASS" if ok else "SMOKE FAIL")
        return 0 if ok else 1
    except AssertionError as err:
        print(f"SMOKE FAIL (assertion): {err}")
        return 1
    except ApiError as err:
        print(f"SMOKE FAIL (ApiError {err.code} status={err.status}): {err}")
        return 1
    finally:
        sdk.close()


def dedupe_ok_check(dedup_ok: bool, ascending: bool) -> bool:
    return dedup_ok and ascending


if __name__ == "__main__":
    sys.exit(main())
