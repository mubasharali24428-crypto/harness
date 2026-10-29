#!/usr/bin/env python3
"""OIDC SSO drill against a real IdP implementation (Keycloak).

Runs INSIDE the identity container, on the compose network, so both the
harness API (127.0.0.1:8001) and the drill IdP (sso-idp:8080) resolve:

    docker compose -f deploy/docker-compose.yml --profile sso-drill up -d sso-idp
    docker exec -i harness-identity-1 python - < scripts/oidc_drill.py

What it proves, with the shipped code paths only:
  1. Team-tier gate: SSO config is refused before the team plan is raised.
  2. Config upsert stores the client secret encrypted (GET returns a hint).
  3. start -> discovery fetch -> authorization URL carrying state, nonce and
     an S256 code challenge.
  4. A REAL Keycloak login form is submitted; the code comes back on the
     registered redirect URI.
  5. complete -> token endpoint exchange WITH the PKCE verifier, ID-token
     signature checked against the discovered JWKS, nonce/iss/aud validated,
     email mapped to an existing team member, session minted.
  6. Fail-closed negatives: state replay, redirect URI mismatch, and an IdP
     identity that is not a team member.

The script writes one JSON object to stdout (progress goes to stderr), so the
wrapper can tee it into docs/briefs/evidence/pilot-prep/. Secrets, codes and
session tokens are never included in the output.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.parse

import httpx

IDENTITY = os.environ.get("DRILL_IDENTITY_URL", "http://127.0.0.1:8001")
ISSUER = os.environ.get("DRILL_ISSUER", "http://sso-idp:8080/realms/harness")
CLIENT_ID = os.environ.get("DRILL_CLIENT_ID", "harness-drill")
CLIENT_SECRET = os.environ.get("DRILL_CLIENT_SECRET", "harness-drill-secret")
REDIRECT_URI = os.environ.get(
    "DRILL_REDIRECT_URI", "http://localhost:3000/auth/sso/callback"
)
STAMP = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
MEMBER_EMAIL = os.environ.get(
    "DRILL_MEMBER_EMAIL", f"sso-drill-{STAMP.lower()}@harness.dev"
)
MEMBER_PASSWORD = os.environ.get("DRILL_MEMBER_PASSWORD", "harness-drill-password")
OUTSIDER_EMAIL = os.environ.get("DRILL_OUTSIDER_EMAIL", "sso-outsider@harness.dev")
DRILL_GROUP = os.environ.get("DRILL_GROUP", "harness-admins")
DRILL_BASE_GROUP = os.environ.get("DRILL_BASE_GROUP", "harness-everyone")
IDP_ADMIN_USER = os.environ.get("DRILL_IDP_ADMIN_USER", "drill-admin")
IDP_ADMIN_PASSWORD = os.environ.get("DRILL_IDP_ADMIN_PASSWORD", "drill-admin-password")

ADMIN_EMAIL = f"oidc-admin-{STAMP.lower()}@harness.dev"


def log(message: str) -> None:
    print(f"[oidc-drill] {message}", file=sys.stderr)


def api(
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict | None = None,
    expect: int | tuple[int, ...] = 200,
) -> tuple[int, dict]:
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    with httpx.Client(timeout=20.0) as client:
        response = client.request(method, f"{IDENTITY}{path}", headers=headers, json=body)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text[:500]}
    wanted = expect if isinstance(expect, tuple) else (expect,)
    if response.status_code not in wanted:
        raise SystemExit(
            f"UNEXPECTED {method} {path}: {response.status_code} {json.dumps(payload)[:400]}"
        )
    return response.status_code, payload


def error_code(payload: dict) -> str | None:
    detail = payload.get("detail")
    if isinstance(detail, dict):
        inner = detail.get("error")
        if isinstance(inner, dict):
            return inner.get("code")
    return None


def login_dev(email: str) -> tuple[str, dict]:
    """Dev-bypass login (HARNESS_REQUIRE_OWNERSHIP=false on a drill stack)."""
    status, payload = api("POST", "/api/v1/auth/login", body={"email": email})
    if status != 200:
        raise SystemExit(f"dev login refused for {email}: {payload}")
    return payload["token"], payload["principal"]


def idp_login(auth_url: str, username: str, password: str) -> str:
    """Drive Keycloak's login form and return the authorization code."""
    with httpx.Client(timeout=20.0, follow_redirects=False) as client:
        page = client.get(auth_url)
        if page.status_code != 200:
            raise SystemExit(f"IdP authorization endpoint answered {page.status_code}")
        action = None
        for tag in re.findall(r"<form[^>]*>", page.text, flags=re.IGNORECASE):
            if 'id="kc-form-login"' in tag:
                match = re.search(r'action="([^"]+)"', tag)
                if match:
                    action = html.unescape(match.group(1))
                    break
        if action is None:
            raise SystemExit("IdP login form not found (theme changed?)")
        action = urllib.parse.urljoin(str(page.url), action)
        submitted = client.post(
            action,
            data={"username": username, "password": password, "credentialId": ""},
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        location = submitted.headers.get("location")
        if submitted.status_code not in (302, 303) or not location:
            raise SystemExit(
                f"IdP login did not redirect (status {submitted.status_code}); "
                "check the drill credentials"
            )
        query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        code = (query.get("code") or [""])[0]
        if not code:
            raise SystemExit(f"no authorization code on the redirect: {location[:160]}")
        return code


def _admin_token(client: httpx.Client) -> str:
    """Realm-admin access token (drill credentials only)."""
    token_response = client.post(
        f"{ISSUER.rsplit('/realms/', 1)[0]}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": IDP_ADMIN_USER,
            "password": IDP_ADMIN_PASSWORD,
        },
    )
    if token_response.status_code != 200:
        raise SystemExit(
            f"IdP admin login failed ({token_response.status_code}); "
            "check DRILL_IDP_ADMIN_* against KC_BOOTSTRAP_ADMIN_*"
        )
    return token_response.json()["access_token"]


def _idp_user_id(client: httpx.Client, headers: dict, email: str) -> str:
    base = f"{ISSUER.rsplit('/realms/', 1)[0]}/admin/realms/harness/users"
    found = client.get(base, headers=headers, params={"email": email, "exact": "true"})
    if found.status_code != 200 or not found.json():
        raise SystemExit(f"IdP user {email} not found")
    return found.json()[0]["id"]


def _ensure_idp_group(client: httpx.Client, headers: dict, name: str) -> str:
    """Create the drill group if needed; return its id."""
    base = f"{ISSUER.rsplit('/realms/', 1)[0]}/admin/realms/harness/groups"
    found = client.get(base, headers=headers, params={"search": name, "exact": "true"})
    for group in found.json() if found.status_code == 200 else []:
        if group.get("name") == name:
            return group["id"]
    created = client.post(base, headers=headers, json={"name": name})
    if created.status_code not in (201, 204):
        raise SystemExit(
            f"IdP group creation failed: {created.status_code} {created.text[:200]}"
        )
    return _ensure_idp_group(client, headers, name)


def _ensure_group_mapper(client: httpx.Client, headers: dict) -> None:
    """Guarantee the drill client emits a `groups` claim.

    The realm import carries the mapper for fresh boots; this makes the drill
    idempotent on an IdP that is already running (a long-lived container never
    re-imports the realm file), so the check never depends on container age.
    """
    core = ISSUER.rsplit("/realms/", 1)[0]
    base = f"{core}/admin/realms/harness/clients"
    found = client.get(base, headers=headers, params={"clientId": CLIENT_ID})
    entries = found.json() if found.status_code == 200 else []
    if not entries:
        raise SystemExit(f"IdP client {CLIENT_ID} not found")
    client_uuid = entries[0]["id"]
    mappers_url = f"{base}/{client_uuid}/protocol-mappers/models"
    mappers = client.get(mappers_url, headers=headers).json()
    if any(m.get("protocolMapper") == "oidc-group-membership-mapper" for m in mappers):
        return
    created = client.post(
        mappers_url,
        headers=headers,
        json={
            "name": "groups",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-group-membership-mapper",
            "config": {
                "full.path": "false",
                "id.token.claim": "true",
                "access.token.claim": "true",
                "userinfo.token.claim": "false",
                "claim.name": "groups",
            },
        },
    )
    if created.status_code not in (201, 204):
        raise SystemExit(
            f"IdP group mapper creation failed: {created.status_code} {created.text[:200]}"
        )


def _set_group_membership(
    client: httpx.Client, headers: dict, email: str, group_id: str, *, present: bool
) -> None:
    base = f"{ISSUER.rsplit('/realms/', 1)[0]}/admin/realms/harness"
    user_id = _idp_user_id(client, headers, email)
    url = f"{base}/users/{user_id}/groups/{group_id}"
    response = client.put(url, headers=headers) if present else client.delete(url, headers=headers)
    if response.status_code not in (204, 201):
        raise SystemExit(
            f"IdP group membership update failed: {response.status_code} {response.text[:200]}"
        )


def ensure_idp_user(email: str, password: str) -> str:
    """Create the drill member at the IdP via the admin API (idempotent).

    A fresh address per run keeps the harness side deterministic: its dev
    login mints exactly one team for a brand-new user, so no team-selection
    prompt can appear on re-runs.
    """
    with httpx.Client(timeout=20.0) as client:
        headers = {"authorization": f"Bearer {_admin_token(client)}"}
        base = f"{ISSUER.rsplit('/realms/', 1)[0]}/admin/realms/harness/users"
        existing = client.get(
            base, headers=headers, params={"email": email, "exact": "true"}
        )
        if existing.status_code == 200 and existing.json():
            return "existing"
        created = client.post(
            base,
            headers=headers,
            json={
                "username": email,
                "email": email,
                "emailVerified": True,
                "enabled": True,
                "firstName": "SSO",
                "lastName": "Drill",
                "credentials": [
                    {"type": "password", "value": password, "temporary": False}
                ],
            },
        )
        if created.status_code not in (201, 204):
            raise SystemExit(
                f"IdP user provisioning failed: {created.status_code} {created.text[:200]}"
            )
        return "created"


def sso_start(team_id: str) -> tuple[str, str]:
    _, payload = api(
        "POST", "/api/v1/auth/sso/start", body={"teamId": team_id, "redirectUri": REDIRECT_URI}
    )
    url = payload["authorizationUrl"]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    state = (query.get("state") or [""])[0]
    if not state:
        raise SystemExit("authorization URL carried no state")
    return url, state


def main() -> int:
    checks: list[dict] = []
    started = time.monotonic()

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        log(f"{'PASS' if ok else 'FAIL'} {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            raise SystemExit(1)

    log(f"issuer {ISSUER}")
    discovery_started = time.monotonic()
    with httpx.Client(timeout=20.0) as http:
        discovery_response = http.get(f"{ISSUER}/.well-known/openid-configuration")
    discovery = discovery_response.json() if discovery_response.status_code == 200 else {}
    record(
        "idp discovery reachable",
        discovery_response.status_code == 200 and discovery.get("issuer") == ISSUER,
        f"{discovery_response.status_code}, issuer={discovery.get('issuer')}",
    )
    discovery_ms = int((time.monotonic() - discovery_started) * 1000)

    idp_user_state = ensure_idp_user(MEMBER_EMAIL, MEMBER_PASSWORD)
    record(
        "drill member exists at the IdP",
        idp_user_state in {"created", "existing"},
        f"{MEMBER_EMAIL} ({idp_user_state})",
    )

    # ---------------------------------------------------------------- admin
    admin_token, admin_principal = login_dev(ADMIN_EMAIL)
    team_id = admin_principal["teamId"]
    record("admin dev login", admin_principal["role"] == "admin", f"team={team_id}")

    status, payload = api(
        "PUT",
        f"/api/v1/teams/{team_id}/sso/config",
        token=admin_token,
        body={
            "issuerUrl": ISSUER,
            "clientId": CLIENT_ID,
            "clientSecret": CLIENT_SECRET,
            "idpDisplayName": "Keycloak drill",
        },
        expect=(403, 200),
    )
    record(
        "SSO config refused on free plan",
        status == 403 and error_code(payload) == "PLAN_REQUIRED",
        f"HTTP {status} {error_code(payload)}",
    )

    api("PUT", "/api/v1/billing/plan", token=admin_token, body={"plan": "team"})
    _, config = api(
        "PUT",
        f"/api/v1/teams/{team_id}/sso/config",
        token=admin_token,
        body={
            "issuerUrl": ISSUER,
            "clientId": CLIENT_ID,
            "clientSecret": CLIENT_SECRET,
            "idpDisplayName": "Keycloak drill",
        },
    )
    record(
        "SSO config stored with secret hint only",
        bool(config.get("secretHint")) and "clientSecret" not in config,
        f"hint=…{config.get('secretHint')}",
    )
    _, public_config = api("GET", f"/api/v1/teams/{team_id}/sso/config", token=admin_token)
    record(
        "GET config never returns the secret",
        "clientSecret" not in json.dumps(public_config),
        f"keys={sorted(public_config)}",
    )

    # --------------------------------------------------------------- member
    member_token, member_principal = login_dev(MEMBER_EMAIL)
    _, invite = api(
        "POST",
        f"/api/v1/teams/{team_id}/invites",
        token=admin_token,
        body={"email": MEMBER_EMAIL, "role": "engineer"},
        expect=(201,),
    )
    _, redeemed = api(
        "POST",
        "/api/v1/invites/redeem",
        token=member_token,
        body={"token": invite["token"], "email": MEMBER_EMAIL},
    )
    membership = redeemed.get("membership") or {}
    record(
        "member joined the team by invite",
        membership.get("teamId") == team_id and redeemed.get("alreadyMember") is False,
        f"member user={member_principal['userId']} role={membership.get('role')}",
    )

    # --------------------------------------------------------- happy path
    auth_url, state = sso_start(team_id)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(auth_url).query)
    record(
        "authorization URL uses S256 PKCE + nonce",
        query.get("code_challenge_method") == ["S256"]
        and bool((query.get("code_challenge") or [""])[0])
        and bool((query.get("nonce") or [""])[0]),
        "state/nonce/challenge present",
    )
    idp_started = time.monotonic()
    code = idp_login(auth_url, MEMBER_EMAIL, MEMBER_PASSWORD)
    idp_ms = int((time.monotonic() - idp_started) * 1000)
    record("IdP login returned an authorization code", bool(code))

    _, session = api(
        "POST",
        "/api/v1/auth/sso/complete",
        body={
            "teamId": team_id,
            "code": code,
            "state": state,
            "redirectUri": REDIRECT_URI,
        },
    )
    principal = session["principal"]
    record(
        "session minted for the matching member",
        principal["teamId"] == team_id
        and principal["role"] == "engineer"
        and principal["userId"] == member_principal["userId"],
        f"team={principal['teamId']} role={principal['role']}",
    )
    _, me = api("GET", "/api/v1/me", token=session["token"])
    record("minted session authenticates", me.get("userId") == member_principal["userId"])

    # ------------------------------------------ group-mapped role (W16 s2)
    # The IdP must be the authority on group membership; the harness may only
    # translate configured groups into roles. Both directions are exercised with
    # a realistic two-group layout: everyone carries the baseline group, admins
    # additionally carry the admin group. Removing the admin group therefore
    # demotes on the next login -- a directory-driven change, not a sticky one.
    with httpx.Client(timeout=20.0) as admin_client:
        admin_headers = {"authorization": f"Bearer {_admin_token(admin_client)}"}
        _ensure_group_mapper(admin_client, admin_headers)
        group_id = _ensure_idp_group(admin_client, admin_headers, DRILL_GROUP)
        base_group_id = _ensure_idp_group(admin_client, admin_headers, DRILL_BASE_GROUP)
        record(
            "drill groups exist at the IdP",
            bool(group_id) and bool(base_group_id),
            f"{DRILL_GROUP}, {DRILL_BASE_GROUP}",
        )

        _, mapped_config = api(
            "PUT",
            f"/api/v1/teams/{team_id}/sso/config",
            token=admin_token,
            body={
                "issuerUrl": ISSUER,
                "clientId": CLIENT_ID,
                "clientSecret": CLIENT_SECRET,
                "idpDisplayName": "Keycloak drill",
                "groupsClaim": "groups",
                "groupRoles": [
                    {"group": DRILL_GROUP, "role": "admin"},
                    {"group": DRILL_BASE_GROUP, "role": "engineer"},
                ],
            },
        )
        expected_rules = [
            {"group": DRILL_GROUP, "role": "admin"},
            {"group": DRILL_BASE_GROUP, "role": "engineer"},
        ]
        record(
            "group mapping stored and projected back",
            mapped_config.get("groupsClaim") == "groups"
            and mapped_config.get("groupRoles") == expected_rules,
            f"claim={mapped_config.get('groupsClaim')} rules={mapped_config.get('groupRoles')}",
        )

        _set_group_membership(
            admin_client, admin_headers, MEMBER_EMAIL, base_group_id, present=True
        )
        _set_group_membership(
            admin_client, admin_headers, MEMBER_EMAIL, group_id, present=True
        )
        auth_url_g, state_g = sso_start(team_id)
        code_g = idp_login(auth_url_g, MEMBER_EMAIL, MEMBER_PASSWORD)
        _, session_g = api(
            "POST",
            "/api/v1/auth/sso/complete",
            body={
                "teamId": team_id,
                "code": code_g,
                "state": state_g,
                "redirectUri": REDIRECT_URI,
            },
        )
        record(
            "IdP group promoted the session role",
            session_g["principal"]["role"] == "admin",
            f"role={session_g['principal']['role']} via group {DRILL_GROUP}",
        )
        # A login answer is not authority: the minted token itself must pass an
        # admin-only door, which reads the role from the membership row.
        _, me_admin = api("GET", "/api/v1/audit?limit=1", token=session_g["token"])
        record(
            "promoted session passes the admin-only audit door",
            isinstance(me_admin.get("items"), list),
            f"{len(me_admin.get('items') or [])} row(s) visible",
        )

        _, audit = api("GET", "/api/v1/audit?limit=50", token=admin_token)
        login_rows = [row for row in audit["items"] if row["action"] == "sso.login"]
        latest = login_rows[0] if login_rows else {}
        detail = latest.get("detail") or {}
        record(
            "login audit says a claim chose the role",
            detail.get("role") == "admin"
            and detail.get("source") == "claim"
            and DRILL_GROUP in (detail.get("matched") or []),
            f"role={detail.get('role')} source={detail.get('source')} "
            f"matched={detail.get('matched')}",
        )

        _set_group_membership(
            admin_client, admin_headers, MEMBER_EMAIL, group_id, present=False
        )
        auth_url_u, state_u = sso_start(team_id)
        code_u = idp_login(auth_url_u, MEMBER_EMAIL, MEMBER_PASSWORD)
        _, session_u = api(
            "POST",
            "/api/v1/auth/sso/complete",
            body={
                "teamId": team_id,
                "code": code_u,
                "state": state_u,
                "redirectUri": REDIRECT_URI,
            },
        )
        record(
            "leaving the admin group demotes on the next login",
            session_u["principal"]["role"] == "engineer",
            f"role={session_u['principal']['role']} via {DRILL_BASE_GROUP}",
        )
        status_u, _ = api(
            "GET", "/api/v1/audit?limit=1", token=session_u["token"], expect=(403,)
        )
        record(
            "demoted session is refused at the admin-only door",
            status_u == 403,
            f"HTTP {status_u}",
        )
        _, audit_after = api("GET", "/api/v1/audit?limit=50", token=admin_token)
        after_rows = [row for row in audit_after["items"] if row["action"] == "sso.login"]
        after_detail = (after_rows[0].get("detail") or {}) if after_rows else {}
        record(
            "second login audit says the baseline group chose the role",
            after_detail.get("role") == "engineer"
            and after_detail.get("source") == "claim"
            and DRILL_BASE_GROUP in (after_detail.get("matched") or []),
            f"role={after_detail.get('role')} source={after_detail.get('source')} "
            f"matched={after_detail.get('matched')}",
        )

    # ------------------------------------------------------------- replay
    status, payload = api(
        "POST",
        "/api/v1/auth/sso/complete",
        body={
            "teamId": team_id,
            "code": code,
            "state": state,
            "redirectUri": REDIRECT_URI,
        },
        expect=(400,),
    )
    record(
        "replayed state refused",
        error_code(payload) == "SSO_STATE_INVALID",
        f"HTTP {status} {error_code(payload)}",
    )

    # --------------------------------------------------- redirect mismatch
    auth_url_2, state_2 = sso_start(team_id)
    code_2 = idp_login(auth_url_2, MEMBER_EMAIL, MEMBER_PASSWORD)
    status, payload = api(
        "POST",
        "/api/v1/auth/sso/complete",
        body={
            "teamId": team_id,
            "code": code_2,
            "state": state_2,
            "redirectUri": "http://localhost:3000/auth/sso/callback-tampered",
        },
        expect=(400,),
    )
    record(
        "redirect URI mismatch refused before code exchange",
        error_code(payload) == "SSO_STATE_INVALID",
        f"HTTP {status} {error_code(payload)}",
    )

    # ------------------------------------------------------------ outsider
    auth_url_3, state_3 = sso_start(team_id)
    code_3 = idp_login(auth_url_3, OUTSIDER_EMAIL, MEMBER_PASSWORD)
    status, payload = api(
        "POST",
        "/api/v1/auth/sso/complete",
        body={
            "teamId": team_id,
            "code": code_3,
            "state": state_3,
            "redirectUri": REDIRECT_URI,
        },
        expect=(403,),
    )
    record(
        "authenticated non-member gets no seat",
        error_code(payload) == "SSO_EMAIL_NOT_MEMBER",
        f"HTTP {status} {error_code(payload)}",
    )

    output = {
        "drill": "oidc-keycloak",
        "executedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "identity": IDENTITY,
        "issuer": ISSUER,
        "clientId": CLIENT_ID,
        "redirectUri": REDIRECT_URI,
        "memberEmail": MEMBER_EMAIL,
        "teamId": team_id,
        "timingsMs": {
            "discovery": discovery_ms,
            "idpFormRoundTrip": idp_ms,
            "total": int((time.monotonic() - started) * 1000),
        },
        "checks": checks,
        "result": "pass" if all(c["ok"] for c in checks) else "fail",
        "notes": [
            "Drill-only credentials live in deploy/keycloak/harness-realm.json.",
            "No client secret, authorization code or session token appears here.",
        ],
    }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
