"""F129 — Chat notifications with approve, reject, and inspect actions.

Slack interactive messages carrying SIGNED, EXPIRING action bindings. The
binding (planId + runId + action + exp) is HMAC-signed and embedded in the
button value: a tampered or transplanted token cannot be used, an expired
token cannot be used, and no action can be replayed into a second gate call
(token-hash ledger). Approval calls the baseline
``POST /api/v1/orchestrator/plans/{planId}/approve`` route (idempotent by
design); rejection has no verified orchestrator route and is therefore only
recorded + reported as such — the run deliberately stays awaiting approval.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
from harness_workflow_connectors.core import IntakeRejected, SignatureScheme, verify_signature
from harness_workflow_connectors.plan_gate import PlanGateError
from harness_workflow_connectors.slack import SLACK_SCHEME_NAME, SLACK_SIGNATURE

from .store import OpsStore

SLACK_SCHEME = SignatureScheme(name=SLACK_SCHEME_NAME, **SLACK_SIGNATURE)

ACTION_IDS = {
    "harness_approve": "approve",
    "harness_reject": "reject",
    "harness_inspect": "inspect",
}
DEFAULT_TTL_S = 900
APPROVER_ROLES = {"approver", "admin", "owner"}
INSPECT_ROLES = {"engineer", "approver", "admin", "owner"}


class ActionTokenError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 403) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(f"{code}: {message}")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_action_token(
    secret: str, *, plan_id: str, run_id: str, action: str, now: datetime,
    ttl_s: int = DEFAULT_TTL_S,
) -> str:
    if action not in set(ACTION_IDS.values()):
        raise ValueError(f"unknown action {action}")
    payload = {
        "planId": plan_id,
        "runId": run_id,
        "action": action,
        "exp": int(now.timestamp()) + ttl_s,
        "v": 1,
    }
    body = _b64(json.dumps(payload, sort_keys=True).encode())
    mac = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def verify_action_token(secret: str, token: str, *, now: datetime) -> dict:
    if "." not in token:
        raise ActionTokenError("TOKEN_MALFORMED", "token shape invalid", 400)
    body, _, mac = token.partition(".")
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        raise ActionTokenError("TOKEN_BAD_SIGNATURE", "token signature invalid")
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ActionTokenError("TOKEN_MALFORMED", "token payload invalid", 400) from exc
    if int(payload.get("exp", 0)) < int(now.timestamp()):
        raise ActionTokenError("TOKEN_EXPIRED", "action binding expired", 409)
    for field in ("planId", "runId", "action"):
        if not payload.get(field):
            raise ActionTokenError("TOKEN_MALFORMED", f"missing {field}", 400)
    return payload


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def build_approval_message(
    *, plan_id: str, run_id: str, goal: str, secret: str, now: datetime,
    requestor: str, ttl_s: int = DEFAULT_TTL_S,
) -> dict:
    """Slack Block Kit message with three signed buttons."""
    tokens = {
        action: sign_action_token(
            secret, plan_id=plan_id, run_id=run_id, action=action, now=now, ttl_s=ttl_s
        )
        for action in ("approve", "reject", "inspect")
    }
    expires_local = datetime.fromtimestamp(int(now.timestamp()) + ttl_s, tz=UTC)
    return {
        "text": f"Plan approval requested by {requestor} for run {run_id}",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text",
                                        "text": "Plan approval requested"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": goal[:500]}},
            {"type": "context", "elements": [
                {"type": "mrkdwn",
                 "text": f"run `{run_id}` · plan `{plan_id}` · requestor {requestor} ·"
                          f" buttons expire {expires_local.isoformat()}"}
            ]},
            {"type": "actions", "block_id": f"harness_plan_{plan_id}", "elements": [
                {"type": "button", "action_id": "harness_approve", "style": "primary",
                 "text": {"type": "plain_text", "text": "Approve"},
                 "value": tokens["approve"]},
                {"type": "button", "action_id": "harness_reject", "style": "danger",
                 "text": {"type": "plain_text", "text": "Reject"},
                 "value": tokens["reject"]},
                {"type": "button", "action_id": "harness_inspect",
                 "text": {"type": "plain_text", "text": "Inspect"},
                 "value": tokens["inspect"]},
            ]},
        ],
        "bindingExpiresAt": expires_local.isoformat(),
    }


class ApprovePlanGate:
    """The single approve capability in ops; mirrors the verified baseline route
    (``POST /api/v1/orchestrator/plans/{planId}/approve`` -> {runId}, idempotent).
    Deliberately no start/execute methods exist — see module docstring."""

    def __init__(
        self, base_url: str, *, bearer_token: str, timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {bearer_token}"}
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def approve(self, plan_id: str) -> str:
        try:
            res = await self._client.post(
                f"{self._base}/api/v1/orchestrator/plans/{plan_id}/approve",
                headers=self._headers,
            )
        except httpx.HTTPError as exc:
            raise PlanGateError(
                "PLAN_GATE_UNAVAILABLE", f"plan gate unreachable: {exc}", 502
            ) from exc
        if res.status_code >= 400:
            raise PlanGateError(
                "PLAN_GATE_REJECTED",
                f"approve returned HTTP {res.status_code}: {res.text[:200]}",
                502,
            )
        run_id = (res.json() or {}).get("runId")
        if not run_id:
            raise PlanGateError("PLAN_GATE_BAD_RESPONSE", "no runId in response", 502)
        return str(run_id)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class SlackActionHandler:
    def __init__(
        self,
        *,
        signing_secret: str,
        action_secret: str,
        store: OpsStore,
        approve_gate: ApprovePlanGate | None,
        inspector=None,
    ) -> None:
        self._signing_secret = signing_secret
        self._action_secret = action_secret
        self._store = store
        self._gate = approve_gate
        self._inspector = inspector

    async def handle(
        self,
        raw_body: bytes,
        headers: dict[str, str],
        *,
        actor: str,
        actor_roles: list[str],
        now: datetime,
    ) -> dict:
        # 1. Slack request authenticity (v0 scheme, replay window).
        verify_signature(
            SLACK_SCHEME, self._signing_secret, raw_body, headers,
            now=int(now.timestamp()),
        )
        form = parse_qs(raw_body.decode())
        if "payload" not in form:
            raise IntakeRejected("PAYLOAD_MISSING", "no interactive payload", 400)
        try:
            payload = json.loads(form["payload"][0])
            action_obj = payload["actions"][0]
            clicked = ACTION_IDS[action_obj["action_id"]]
            token = action_obj["value"]
        except (KeyError, IndexError, json.JSONDecodeError, ValueError) as exc:
            raise IntakeRejected("PAYLOAD_INVALID", "unrecognized action payload", 400) from exc

        # 2. Signed binding: intact, unexpired, and BOUND to this action.
        binding = verify_action_token(self._action_secret, token, now=now)
        if binding["action"] != clicked:
            raise ActionTokenError(
                "ACTION_MISMATCH",
                f"token is bound to '{binding['action']}', button says '{clicked}'",
            )

        # 3. Role gate.
        allowed = INSPECT_ROLES if clicked == "inspect" else APPROVER_ROLES
        if not set(actor_roles) & allowed:
            raise ActionTokenError(
                "ROLE_FORBIDDEN", f"roles {actor_roles} may not {clicked}"
            )

        # 4. Replay ledger: one gate call per token, ever.
        th = token_hash(token)
        existing = self._store.approval_action(th)
        if existing is not None:
            return {"outcome": existing["outcome"], "duplicate": True, "actor": actor}

        if clicked == "approve":
            if self._gate is None:
                raise PlanGateError(
                    "PLAN_GATE_UNAVAILABLE", "approve gate not configured", 503
                )
            run_id = await self._gate.approve(binding["planId"])
            outcome = {"decision": "approved", "planId": binding["planId"],
                       "runId": run_id}
        elif clicked == "reject":
            outcome = {
                "decision": "rejected",
                "planId": binding["planId"],
                "recorded": True,
                "note": "rejection recorded; no orchestrator reject route verified —"
                        " the run remains awaiting plan approval",
            }
        else:  # inspect
            outcome = {"decision": "inspected", "planId": binding["planId"],
                       "runId": binding["runId"]}
            if self._inspector is not None:
                outcome["status"] = await self._inspector(binding["runId"])

        self._store.record_approval_action(
            token_hash=th, actor=actor, plan_id=binding["planId"], decision=clicked,
            outcome=outcome, at=now,
        )
        return {"outcome": outcome, "duplicate": False, "actor": actor}
