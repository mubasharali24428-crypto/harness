#!/usr/bin/env python3
"""W9 live gate: share URLs enforce access, and a deployment approval binds the reviewed revision.

Both claims are about what a record CANNOT do, so the drill tries to make them
fail on the running stack:

  shares   mint a link, open it with NO Authorization header, confirm the bytes and
           the "download, never render" headers; confirm the list never carries the
           token; revoke and watch the SAME token answer 404; mint a 60s link and
           watch it answer 410 once it expires; confirm a foreign team cannot see
           the link registry (404) while the token itself is the only authority.
  deploys  request a deployment, approve the digest the client echoes, then move
           the run head: the approved request goes stale, a fresh request refuses
           to approve (409 DEPLOYMENT_STALE) and stays `requested`; revoke the
           approved one and it can never be approved again (DEPLOYMENT_REVOKED);
           every step is read back from audit_log.

The run is not executed by the engine: W9 is about the record, so the diff a
server run would report is supplied through the done-gate (the only writer of a
recorded diff) and the head is recorded through PUT git-state. One planner call is
spent creating the run so it exists for the ownership check.

    python3 scripts/e2e/w9_share_deploy_drill.py [--timeout 300] [--skip-expiry]

Expiry costs one real minute (the product's floor is 60s); --skip-expiry records
that leg as skipped rather than pretending it passed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "briefs" / "evidence" / "pilot-prep"

spec = importlib.util.spec_from_file_location(
    "w4_template", REPO / "scripts" / "e2e" / "w4_web_fetch_engine_drill.py"
)
tpl = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(tpl)

P = "/api/v1/orchestrator"
BASE_SHA = "a" * 40
HEAD_SHA = "c" * 40
MOVED_SHA = "d" * 40
DIFF = "\n".join(
    [
        "diff --git a/src/app.py b/src/app.py",
        "index 111..222 100644",
        "--- a/src/app.py",
        "+++ b/src/app.py",
        "@@ -1,1 +1,2 @@",
        "-old",
        "+print('hello')",
        "+print('world')",
        "diff --git a/README.md b/README.md",
        "index 111..222 100644",
        "--- a/README.md",
        "+++ b/README.md",
        "@@ -1,1 +1,2 @@",
        "-old",
        "+# demo",
        "+released by w9 drill",
    ]
) + "\n"
REPORT = b"w9 drill report\nline two\n"


def raw(method: str, url: str, *, data: bytes | None = None, token: str | None = None,
        content_type: str | None = None, timeout: float = 30.0):
    """(status, headers, body-bytes) with no JSON assumptions on the response."""
    req = urllib.request.Request(url, data=data, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def api(method: str, path: str, *, token: str | None = None, body: dict | None = None):
    return tpl.http(method, f"{tpl.ORCHESTRATOR}{P}{path}", token=token, body=body)


def err_code(status: int, body) -> str | None:
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, dict):
            return (detail.get("error") or {}).get("code")
    return None


def psql(sql: str) -> tuple[bool, str]:
    """Read audit rows from the stack's own database (best effort, never fatal)."""
    cmd = (
        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc ' + json.dumps(sql)
    )
    proc = subprocess.run(
        ["docker", "compose", "-f", "deploy/docker-compose.yml", "--env-file", ".env",
         "exec", "-T", "db", "sh", "-lc", cmd],
        cwd=str(REPO), capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()[:400]
    return True, proc.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--skip-expiry", action="store_true")
    args = ap.parse_args()

    stamp = int(time.time())
    run_id = f"w9-{stamp}"
    owner_email = f"w9-owner-{stamp}@example.com"
    foreign_email = f"w9-foreign-{stamp}@example.com"
    evidence: dict = {
        "date": time.strftime("%Y-%m-%d"),
        "week": "W9",
        "claim": "preview/share URLs enforce access; deployment approval binds the reviewed revision",
        "runId": run_id,
        "drive": "HTTP only (no engine execution; the diff is supplied through the done-gate)",
    }
    steps: list[dict] = []

    token = tpl.login(owner_email)
    steps.append({"step": "login owner", "ok": True})
    status, project = tpl.http("POST", f"{tpl.IDENTITY}/api/v1/projects", token=token,
                               body={"name": f"w9 {run_id}"})
    if status != 201:
        raise SystemExit(f"create project failed: {status} {project!r}")
    status, plan = tpl.http("POST", f"{tpl.ORCHESTRATOR}{P}/runs/{run_id}/plan", token=token,
                            body={"goal": "Ship the demo feature and report it",
                                  "projectId": project["id"]})
    if status != 201 or not isinstance(plan, dict) or not plan.get("planId"):
        raise SystemExit(f"propose plan failed: {status} {plan!r}")
    tpl.http("POST", f"{tpl.ORCHESTRATOR}{P}/runs/{run_id}/plan/approve", token=token,
             body={"planId": plan["planId"]})

    # The binding's third facet is the plan version. The planner's own plan does
    # not become a plan_versions row by itself, so record one through the gate and
    # approve it: that is what the deployment must bind to.
    status, versions = api("GET", f"/runs/{run_id}/plan/versions", token=token)
    latest = int((versions or {}).get("latestVersion") or 0) if isinstance(versions, dict) else 0
    if latest == 0:
        status, made = api("POST", f"/runs/{run_id}/plan/versions", token=token, body={
            "content": {"goal": "Ship the demo feature and report it"},
            "expectedVersion": 0,
        })
        latest = int(((made or {}).get("version") if isinstance(made, dict) else 0) or 1)
        steps.append({"step": "record plan version", "ok": status == 201, "status": status})
    status, approved_plan = api("POST", f"/runs/{run_id}/plan/versions/{latest}/approve",
                                token=token)
    steps.append({"step": "approve plan version", "ok": status in (200, 201),
                  "status": status, "version": latest})
    status, done = api("POST", f"/runs/{run_id}/done-signal", token=token,
                       body={"diffText": DIFF})
    steps.append({"step": "done-signal records the diff", "ok": status == 200, "status": status})
    if status != 200:
        raise SystemExit(f"done-signal failed: {status} {done!r}")

    status, changes = api("GET", f"/runs/{run_id}/changes", token=token)
    items = (changes or {}).get("items", []) if isinstance(changes, dict) else []
    digest = (changes or {}).get("changesDigest") if isinstance(changes, dict) else None
    steps.append({"step": "changes recorded", "ok": status == 200 and len(items) == 2,
                  "count": len(items), "digest": digest})
    if not digest or len(items) != 2:
        raise SystemExit(f"changes not recorded: {status} {changes!r}")

    status, git = api("PUT", f"/runs/{run_id}/git-state", token=token, body={
        "baseBranch": "main", "baseSha": BASE_SHA, "observedHeadSha": BASE_SHA,
        "headBranch": f"agent/{run_id}", "runHeadSha": HEAD_SHA,
    })
    steps.append({"step": "record run head", "ok": status == 200, "status": status})

    # ------------------------------------------------------------------ shares
    art_status, art_headers, art_body = raw(
        "POST", f"{tpl.ORCHESTRATOR}{P}/runs/{run_id}/artifacts?name=report.txt",
        data=REPORT, token=token, content_type="application/octet-stream",
    )
    art = json.loads(art_body).get("artifact") if art_body[:1] == b"{" else None
    if art_status != 201 or not art:
        raise SystemExit(f"artifact upload failed: {art_status} {art_body[:300]!r}")
    art_sha = hashlib.sha256(REPORT).hexdigest()

    status, minted = api("POST", f"/runs/{run_id}/artifacts/{art['id']}/share", token=token,
                         body={})
    mint_status = status
    share = (minted or {}).get("share") or {}
    token_value = share.get("token") or ""
    share_url = f"{tpl.ORCHESTRATOR}{share.get('url', '')}"
    steps.append({"step": "mint share", "ok": mint_status == 201 and bool(token_value),
                  "status": mint_status, "expiresAt": share.get("expiresAt")})

    anon_status, anon_headers, anon_body = raw("GET", share_url)  # no Authorization
    lower = {k.lower(): v for k, v in anon_headers.items()}
    evidence["share"] = {
        "artifactId": art["id"],
        "artifactSha256": art["sha256"],
        "listShowsToken": False,
        "anonymous": {
            "status": anon_status,
            "bytesMatch": anon_body == REPORT,
            "contentType": lower.get("content-type"),
            "contentDisposition": lower.get("content-disposition"),
            "nosniff": lower.get("x-content-type-options"),
            "csp": lower.get("content-security-policy"),
            "cacheControl": lower.get("cache-control"),
            "shaHeader": lower.get("x-artifact-sha256"),
            "expectedSha": art_sha,
        },
    }

    status, listed = api("GET", f"/runs/{run_id}/shares", token=token)
    rows = (listed or {}).get("items", []) if isinstance(listed, dict) else []
    listed_keys = sorted(rows[0].keys()) if rows else []
    share_id = share.get("id")
    after_open = next((r for r in rows if r.get("id") == share_id), {})
    evidence["share"]["list"] = {
        "count": len(rows),
        "keys": listed_keys,
        "carriesToken": any("token" in k.lower() and k.lower() != "tokenSha" for k in listed_keys),
        "uses": after_open.get("uses"),
        "lastUsedAt": after_open.get("lastUsedAt"),
    }

    foreign_token = tpl.login(foreign_email)
    foreign = {}
    for label, call in {
        "listShares": lambda: api("GET", f"/runs/{run_id}/shares", token=foreign_token),
        "mintShare": lambda: api("POST", f"/runs/{run_id}/artifacts/{art['id']}/share",
                                 token=foreign_token, body={}),
        "revokeShare": lambda: api("POST", f"/runs/{run_id}/shares/{share_id}/revoke",
                                   token=foreign_token),
    }.items():
        st, body = call()
        foreign[label] = {"status": st, "code": err_code(st, body)}
    foreign_open = raw("GET", share_url)  # the capability itself is authority, by design
    foreign["tokenAloneOpens"] = foreign_open[0]
    evidence["share"]["foreignTeam"] = foreign

    status, revoked = api("POST", f"/runs/{run_id}/shares/{share_id}/revoke", token=token)
    revoked_at = ((revoked or {}).get("share") or {}).get("revokedAt")
    again_status, again = api("POST", f"/runs/{run_id}/shares/{share_id}/revoke", token=token)
    again_at = ((again or {}).get("share") or {}).get("revokedAt")
    dead_status, _, dead_body = raw("GET", share_url)
    try:
        dead_parsed = json.loads(dead_body or b"{}")
    except ValueError:
        dead_parsed = None
    evidence["share"]["revocation"] = {
        "revokeStatus": status,
        "revokedAt": revoked_at,
        "secondRevokeStatus": again_status,
        "secondRevokeSameTimestamp": again_at == revoked_at,
        "anonymousAfterRevoke": dead_status,
        "anonymousAfterRevokeCode": err_code(dead_status, dead_parsed),
        "foreignAfterRevoke": raw("GET", share_url)[0],
    }

    expiry: dict = {"skipped": args.skip_expiry}
    short_share: dict = {}
    if not args.skip_expiry:
        status, short = api("POST", f"/runs/{run_id}/artifacts/{art['id']}/share", token=token,
                            body={"ttlSeconds": 60})
        short_share = (short or {}).get("share") or {}
        expires = datetime.fromisoformat(str(short_share.get("expiresAt")))
        short_url = f"{tpl.ORCHESTRATOR}{short_share.get('url', '')}"
        expiry = {
            "skipped": False,
            "requestedTtlSeconds": 60,
            "expiresAt": short_share.get("expiresAt"),
            "openedWhileLive": raw("GET", short_url)[0],
        }
        wait = (expires - datetime.now(timezone.utc)).total_seconds() + 2
        if wait > 0:
            time.sleep(min(wait, args.timeout))
        exp_status, _, exp_body = raw("GET", short_url)
        try:
            parsed = json.loads(exp_body or b"{}")
        except ValueError:
            parsed = None
        expiry["expiredStatus"] = exp_status
        expiry["expiredCode"] = err_code(exp_status, parsed)
        expiry["revokeAfterExpiry"] = api(
            "POST", f"/runs/{run_id}/shares/{short_share.get('id')}/revoke", token=token)[0]
    evidence["share"]["expiry"] = expiry

    # -------------------------------------------------------------- deployments
    def request_dep(environment: str):
        return api("POST", f"/runs/{run_id}/deployments", token=token,
                   body={"environment": environment})

    def approve(dep_id: str, digest_value: str):
        return api("POST", f"/runs/{run_id}/deployments/{dep_id}/approve", token=token,
                   body={"changesDigest": digest_value})

    status, first = request_dep("staging")
    dep = (first or {}).get("deployment") or {}
    evidence["deploy"] = {
        "request": {"status": status, "binding": dep.get("binding"),
                    "digestMatches": (dep.get("binding") or {}).get("changesDigest") == digest,
                    "headMatches": (dep.get("binding") or {}).get("runHeadSha") == HEAD_SHA,
                    "planVersion": (dep.get("binding") or {}).get("planVersion"),
                    "expectedPlanVersion": latest},
    }
    # A client that echoes bytes nobody reviewed is refused before anything else.
    wrong_status, wrong = approve(dep.get("id", ""), "0" * 64)
    evidence["deploy"]["wrongClientDigest"] = {
        "status": wrong_status,
        "code": err_code(wrong_status, wrong),
        "staleFields": ((wrong or {}).get("detail") or {}).get("staleFields")
        if isinstance(wrong, dict) else None,
    }
    status, approved = approve(dep.get("id", ""), digest)
    evidence["deploy"]["approval"] = {
        "status": status,
        "state": ((approved or {}).get("deployment") or {}).get("state"),
        "decidedBy": ((approved or {}).get("deployment") or {}).get("decidedBy"),
    }

    # A second request, made while the revision is still the reviewed one: this is
    # the one the head move below must turn down (a request made AFTER the move
    # would legitimately bind the new head, which is the correct behaviour).
    status, second = request_dep("prod")
    dep2_status = status
    dep2 = (second or {}).get("deployment") or {}

    # The reviewed revision moves.
    api("PUT", f"/runs/{run_id}/git-state", token=token, body={"runHeadSha": MOVED_SHA})
    status, listed = api("GET", f"/runs/{run_id}/deployments", token=token)
    rows = (listed or {}).get("items", []) if isinstance(listed, dict) else []
    approved_row = next((r for r in rows if r.get("id") == dep.get("id")), {})
    evidence["deploy"]["afterHeadMoved"] = {
        "approvedRequestStale": approved_row.get("stale"),
        "staleFields": approved_row.get("staleFields"),
    }

    stale_status, stale = approve(dep2.get("id", ""), str(
        (dep2.get("binding") or {}).get("changesDigest")))
    detail = (stale or {}).get("detail") if isinstance(stale, dict) else {}
    evidence["deploy"]["staleRefusal"] = {
        "requestStatus": dep2_status,
        "approveStatus": stale_status,
        "code": err_code(stale_status, stale),
        "staleFields": (detail or {}).get("staleFields"),
        "refusedInList": next(
            (r.get("staleFields") for r in rows if r.get("id") == dep2.get("id")), None),
    }
    status, listed = api("GET", f"/runs/{run_id}/deployments", token=token)
    rows = (listed or {}).get("items", []) if isinstance(listed, dict) else []
    ref = next((r for r in rows if r.get("id") == dep2.get("id")), {})
    evidence["deploy"]["staleRefusal"]["stillRequested"] = ref.get("state")

    status, revoked = api("POST", f"/runs/{run_id}/deployments/{dep.get('id')}/revoke",
                          token=token, body={})
    revoked_at = ((revoked or {}).get("deployment") or {}).get("revokedAt")
    again_status, again = api("POST", f"/runs/{run_id}/deployments/{dep.get('id')}/revoke",
                              token=token, body={})
    again_at = ((again or {}).get("deployment") or {}).get("revokedAt")
    after_status, after = approve(dep.get("id", ""), digest)
    evidence["deploy"]["revocation"] = {
        "revokeStatus": status,
        "state": ((revoked or {}).get("deployment") or {}).get("state"),
        "secondRevokeSameTimestamp": again_at == revoked_at and bool(revoked_at),
        "approveAfterRevokeStatus": after_status,
        "approveAfterRevokeCode": err_code(after_status, after),
    }

    # -------------------------------------------------------------------- audit
    ids = [share_id, short_share.get("id"), dep.get("id"), dep2.get("id")]
    ids = [f"'{i}'" for i in ids if i]
    ok, out = psql(
        "SELECT action, count(*) FROM audit_log WHERE target_id IN ("
        + ", ".join(ids) + ") GROUP BY action ORDER BY action"
    )
    evidence["audit"] = {"ok": ok, "rows": out}

    checks = {
        "changesRecorded": len(items) == 2 and bool(digest),
        "headRecorded": evidence["deploy"]["request"]["headMatches"],
        "shareMinted": mint_status == 201 and bool(token_value),
        "anonymousFetchServesBytes": anon_status == 200 and anon_body == REPORT,
        "servedAsDownloadNotDocument": (
            (lower.get("content-type") or "").startswith("application/octet-stream")
            and (lower.get("content-disposition") or "").startswith("attachment;")
            and lower.get("x-content-type-options") == "nosniff"
            and "sandbox" in (lower.get("content-security-policy") or "")
        ),
        "anonymousHashMatchesArtifact": lower.get("x-artifact-sha256") == art_sha,
        "listNeverCarriesToken": not evidence["share"]["list"]["carriesToken"],
        "anonymousOpenIsCounted": (after_open.get("uses") or 0) >= 1,
        "foreignTeamCannotSeeRegistry": all(
            v.get("status") == 404 for v in foreign.values() if isinstance(v, dict)),
        "tokenAloneIsAuthority": foreign.get("tokenAloneOpens") == 200,
        "revocationKillsTheSameToken": dead_status == 404,
        "revocationIsIdempotent": evidence["share"]["revocation"][
            "secondRevokeSameTimestamp"],
        "expiryEnforced": (
            expiry.get("skipped")
            or (expiry.get("expiredStatus") == 410 and expiry.get("expiredCode") == "SHARE_EXPIRED")
        ),
        "requestBindsReviewedRevision": (
            evidence["deploy"]["request"]["digestMatches"]
            and evidence["deploy"]["request"]["headMatches"]
            and evidence["deploy"]["request"]["planVersion"] == latest
        ),
        "wrongClientDigestRefused": (
            evidence["deploy"]["wrongClientDigest"]["code"] == "DEPLOYMENT_STALE"
            and evidence["deploy"]["wrongClientDigest"]["staleFields"] == ["clientDigest"]
        ),
        "approvalSucceedsOnTheBoundRevision": (
            evidence["deploy"]["approval"]["status"] == 200
            and evidence["deploy"]["approval"]["state"] == "approved"
        ),
        "movedHeadMarksApprovalStale": (
            evidence["deploy"]["afterHeadMoved"]["approvedRequestStale"] is True
            and "runHeadSha" in (evidence["deploy"]["afterHeadMoved"]["staleFields"] or [])
        ),
        "staleApprovalRefused": (
            evidence["deploy"]["staleRefusal"]["code"] == "DEPLOYMENT_STALE"
            and "runHeadSha" in (evidence["deploy"]["staleRefusal"]["staleFields"] or [])
        ),
        "refusalIsNotADecision": evidence["deploy"]["staleRefusal"]["stillRequested"] == "requested",
        "revokeFinal": (
            evidence["deploy"]["revocation"]["approveAfterRevokeCode"] == "DEPLOYMENT_REVOKED"
            and evidence["deploy"]["revocation"]["secondRevokeSameTimestamp"]
        ),
        "everyStepAudited": ok and all(
            action in out for action in
            ("artifact.shared", "share.opened", "artifact.share_revoked",
             "deployment.requested", "deployment.approved", "deployment.revoked")
        ),
    }
    evidence["checks"] = checks
    evidence["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    evidence["failedChecks"] = [k for k, v in checks.items() if not v]
    evidence["steps"] = steps

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"w9-share-deploy-{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str) + "\n")
    print(json.dumps({"verdict": evidence["verdict"], "failed": evidence["failedChecks"],
                      "evidence": str(path)}, indent=2))
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
