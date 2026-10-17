"""Webhook pipeline + management operations. Untrusted input in, pending intake out."""

from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass

from . import signing, triggers
from .gitlab import GitLabClient, validate_api_base
from .store import IntakeStore, public

MAX_BODY = 1024 * 1024
RATE_LIMIT = 60
RATE_WINDOW_S = 60.0
PROVIDERS = {"github", "gitlab"}


class IntakeError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass
class Outcome:
    status: int
    body: dict


class IntakeService:
    def __init__(
        self, store: IntakeStore, vault, notifier=None, github_transport=None, gitlab_transport=None
    ):
        self.store = store
        self.vault = vault
        self.notifier = notifier
        self.github_transport = github_transport
        self.gitlab_transport = gitlab_transport

    # -- management (principal-scoped by the route layer) ---------------------

    def register(
        self,
        *,
        team_id: str,
        project_id: str,
        user_id: str,
        provider: str,
        external_project: str,
        label: str = "harness",
        api_token: str | None = None,
        api_base: str | None = None,
        signing_mode: bool = False,
    ) -> dict:
        if provider not in PROVIDERS:
            raise IntakeError(422, "INVALID_REQUEST", "provider must be github or gitlab")
        ext = (external_project or "").strip()
        if not ext or len(ext) > 200 or ".." in ext or ext.startswith("/") or ext.count("/") < 1:
            raise IntakeError(422, "INVALID_REQUEST", "external project must look like owner/repo")
        if api_base:
            try:
                api_base = validate_api_base(api_base)
            except ValueError as exc:
                raise IntakeError(422, "INVALID_REQUEST", str(exc)) from exc
        if provider == "gitlab" and signing_mode:
            secret = "whsec_" + base64.b64encode(secrets.token_bytes(32)).decode()
        else:
            secret = secrets.token_hex(32)
        rid = self.store.add_registration(
            team_id=team_id,
            project_id=project_id,
            provider=provider,
            external_project=ext,
            secret_enc=self.vault.encrypt(secret),
            api_token_enc=self.vault.encrypt(api_token) if api_token else None,
            api_base=api_base,
            label=(label or "harness")[:50],
            created_by=user_id,
        )
        row = self.store.registration(rid)
        assert row is not None
        # The secret is returned exactly once; only a hint is ever shown again.
        return {
            **public(row),
            "secret": secret,
            "secretHint": self.vault.hint(secret),
            "webhookPath": f"/api/v1/developer-workflow/intake/webhooks/{provider}/{rid}",
        }

    def decide(self, intake_id: str, team_id: str, user_id: str, approve: bool) -> dict:
        row = self.store.intake(intake_id)
        if row is None or row["team_id"] != team_id:
            raise IntakeError(404, "INTAKE_NOT_FOUND", "unknown intake")
        if not self.store.decide(intake_id, "approved" if approve else "rejected", user_id):
            raise IntakeError(409, "INVALID_STATE", f"intake is already {row['status']}")
        out = self.store.intake(intake_id)
        assert out is not None
        # Approval records the human decision only. Creating the run/plan is a separate,
        # explicitly integrated step (A2 orchestrator); nothing is dispatched from here.
        return {**public(out), "dispatch": "needs-integration" if approve else "none"}

    # -- webhook ---------------------------------------------------------------

    async def handle_webhook(
        self, provider: str, registration_id: str, headers: dict[str, str], body: bytes
    ) -> Outcome:
        h = {k.lower(): v for k, v in headers.items()}
        if len(body) > MAX_BODY:
            return Outcome(
                413, {"error": {"code": "PAYLOAD_TOO_LARGE", "message": "body exceeds 1 MiB"}}
            )
        reg = self.store.registration(registration_id)
        # Unknown, disabled and wrong-provider registrations are indistinguishable (no enumeration).
        if reg is None or not reg["enabled"] or reg["provider"] != provider:
            return Outcome(404, {"error": {"code": "NOT_FOUND", "message": "unknown webhook"}})
        secret = self.vault.decrypt(reg["secret_enc"])
        try:
            if provider == "github":
                signing.verify_github(secret, body, h.get("x-hub-signature-256"))
                delivery, event = h.get("x-github-delivery"), h.get("x-github-event", "")
            elif secret.startswith("whsec_"):
                signing.verify_standard_webhooks(
                    secret,
                    h.get("webhook-id"),
                    h.get("webhook-timestamp"),
                    body,
                    h.get("webhook-signature"),
                )
                delivery, event = h.get("webhook-id"), h.get("x-gitlab-event", "")
            else:
                signing.verify_gitlab_token(secret, h.get("x-gitlab-token"))
                delivery, event = (
                    h.get("x-gitlab-event-uuid") or h.get("idempotency-key"),
                    h.get("x-gitlab-event", ""),
                )
        except signing.SignatureError:
            return Outcome(
                401,
                {"error": {"code": "BAD_SIGNATURE", "message": "signature verification failed"}},
            )
        if not delivery or len(delivery) > 128:
            return Outcome(
                400,
                {
                    "error": {
                        "code": "MISSING_DELIVERY_ID",
                        "message": "delivery id header required",
                    }
                },
            )
        if not self.store.rate_hit(reg["id"], RATE_LIMIT, RATE_WINDOW_S):
            return Outcome(
                429, {"error": {"code": "RATE_LIMITED", "message": "too many deliveries"}}
            )
        if not self.store.claim_delivery(reg["id"], delivery):
            return Outcome(200, {"status": "duplicate"})
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError
        except ValueError:
            self.store.set_delivery_outcome(reg["id"], delivery, "bad-json")
            return Outcome(
                400, {"error": {"code": "INVALID_JSON", "message": "body is not a JSON object"}}
            )
        cand = (
            triggers.github(event, payload, reg["label"])
            if provider == "github"
            else triggers.gitlab(event, payload, reg["label"])
        )
        if cand is None:
            self.store.set_delivery_outcome(reg["id"], delivery, "ignored")
            return Outcome(200, {"status": "ignored"})
        if cand.external_project.lower() != reg["external_project"].lower():
            # Signed by this registration's secret but naming another project: never cross tenants.
            self.store.set_delivery_outcome(reg["id"], delivery, "project-mismatch")
            return Outcome(
                403,
                {
                    "error": {
                        "code": "PROJECT_MISMATCH",
                        "message": "payload project does not match this registration",
                    }
                },
            )
        intake_id, created = self.store.add_intake(reg, cand)
        self.store.set_delivery_outcome(
            reg["id"], delivery, "queued" if created else "already-queued"
        )
        if created and self.notifier is not None:
            try:
                await self.notifier.comment(
                    reg,
                    cand,
                    f"Harness queued this for human approval (intake {intake_id[:8]}). "
                    "Nothing will run until a person approves it.",
                )
            except Exception:  # noqa: BLE001 - write-back is best effort, never fails the webhook
                pass
        return Outcome(
            202, {"status": "queued" if created else "already-queued", "intakeId": intake_id}
        )


class ProviderNotifier:
    """Status comment via the registration's own API token; no token = no write-back."""

    def __init__(self, vault, github_transport=None, gitlab_transport=None):
        self.vault, self.gh, self.gl = vault, github_transport, gitlab_transport

    async def comment(self, reg: dict, cand, text: str) -> None:
        if not reg.get("api_token_enc"):
            return
        token = self.vault.decrypt(reg["api_token_enc"])
        if reg["provider"] == "github":
            from ..client import GitHubClient

            await GitHubClient(token, self.gh).create_issue_comment(
                reg["external_project"], int(cand.external_id), text
            )
        else:
            kind = "merge_requests" if cand.kind == "review-request" else "issues"
            await GitLabClient(
                reg.get("api_base") or "https://gitlab.com", token, self.gl
            ).post_note(reg["external_project"], kind, int(cand.external_id), text)
