"""HAP/1 wire profile and policy simulator (F091-F096, F100).

Implements the predeclared interoperability profile ``hap/1`` for cross-lane
policy evaluations and a stateless/dry-run policy simulator that evaluates
combinations of secret scanning, prompt-injection quarantine, sensitive-file
access, outbound DLP, capability tokens, network egress, RBAC roles, model
allowlists, and data residency without mutating state or consuming capability
token budgets.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .capabilities import UNSET, CapabilitySigner, RequiredBinding
from .decisions import Decision, Outcome, strongest
from .dlp import DEFAULT_DLP_POLICY, DLPPolicy, inspect_outbound
from .egress import EgressPolicy, Resolver, evaluate_redirect_chain, evaluate_url
from .injection import DEFAULT_INJECTION_POLICY, InjectionPolicy
from .injection import inspect as inspect_injection
from .secret_scan import DEFAULT_SECRET_POLICY, SecretScanPolicy, scan_outbound
from .sensitive_files import (
    DEFAULT_SENSITIVE_FILE_POLICY,
    SensitiveFilePolicy,
    evaluate_path,
    evaluate_transmission,
)

HAP_SCHEMA_VERSION = "hap/1"
VALID_HAP_STATUSES = ("succeeded", "denied", "failed", "needsApproval", "unsupported")


@dataclass(frozen=True)
class HapArtifact:
    artifact_id: str
    media_type: str
    sha256: str
    size_bytes: int
    redaction_status: str = "redacted"

    def to_wire(self) -> dict[str, Any]:
        return {
            "artifactId": self.artifact_id,
            "mediaType": self.media_type,
            "sha256": self.sha256,
            "sizeBytes": self.size_bytes,
            "redactionStatus": self.redaction_status,
        }


@dataclass(frozen=True)
class HapRequest:
    request_id: str
    project_id: str
    run_id: str
    operation: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = HAP_SCHEMA_VERSION
    observation_id: str | None = None
    approval_id: str | None = None
    idempotency_key: str | None = None

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> HapRequest:
        if not isinstance(payload, Mapping):
            raise ValueError("HAP/1 request must be a JSON object")
        schema_version = payload.get("schemaVersion")
        if schema_version != HAP_SCHEMA_VERSION:
            raise ValueError(f"unsupported schemaVersion: {schema_version!r}")
        for key in ("requestId", "projectId", "runId", "operation"):
            val = payload.get(key)
            if not isinstance(val, str) or not val.strip() or len(val) > 128:
                raise ValueError(f"invalid or missing {key}")
        args = payload.get("arguments")
        if not isinstance(args, Mapping):
            raise ValueError("arguments must be a JSON object")
        obs_id = payload.get("observationId")
        app_id = payload.get("approvalId")
        idem_key = payload.get("idempotencyKey")
        for opt_name, opt_val in (
            ("observationId", obs_id),
            ("approvalId", app_id),
            ("idempotencyKey", idem_key),
        ):
            if opt_val is not None and (not isinstance(opt_val, str) or not opt_val.strip()):
                raise ValueError(f"{opt_name} must be a non-empty string when present")
        return cls(
            request_id=payload["requestId"].strip(),
            project_id=payload["projectId"].strip(),
            run_id=payload["runId"].strip(),
            operation=payload["operation"].strip(),
            arguments=dict(args),
            schema_version=HAP_SCHEMA_VERSION,
            observation_id=obs_id.strip() if isinstance(obs_id, str) else None,
            approval_id=app_id.strip() if isinstance(app_id, str) else None,
            idempotency_key=idem_key.strip() if isinstance(idem_key, str) else None,
        )


@dataclass(frozen=True)
class HapResponse:
    request_id: str
    status: str
    result: Mapping[str, Any] | None = None
    artifacts: tuple[HapArtifact, ...] = ()
    error: Mapping[str, Any] | None = None
    schema_version: str = HAP_SCHEMA_VERSION

    def to_wire(self) -> dict[str, Any]:
        if self.status not in VALID_HAP_STATUSES:
            raise ValueError(f"invalid HAP/1 status: {self.status}")
        out: dict[str, Any] = {
            "schemaVersion": self.schema_version,
            "requestId": self.request_id,
            "status": self.status,
            "artifacts": [a.to_wire() for a in self.artifacts],
        }
        if self.result is not None:
            out["result"] = dict(self.result)
        if self.error is not None:
            out["error"] = dict(self.error)
        return out


def evaluate_outbound_payload(
    text: str,
    *,
    source: str = "untrusted",
    check_injection: bool = True,
    secret_policy: SecretScanPolicy = DEFAULT_SECRET_POLICY,
    dlp_policy: DLPPolicy = DEFAULT_DLP_POLICY,
    injection_policy: InjectionPolicy = DEFAULT_INJECTION_POLICY,
) -> Decision:
    """Combined outbound gate: F091 secret scan + F094 DLP + F092 injection quarantine.

    Precedence:
    1. Any DENY from secret scanning or DLP immediately returns DENY with ``text=None``.
    2. Otherwise redactions from both F091 and F094 are applied first so no raw
       secret or regulated identifier survives inside a quarantined or allowed payload.
    3. If ``check_injection`` is True and the suspicion score meets the threshold,
       the (already redacted) text is wrapped in ``frame_untrusted`` and the
       combined outcome is ``QUARANTINE`` (or ``REDACT`` if no quarantine was needed).
    """
    secret_dec = scan_outbound(text, policy=secret_policy)
    dlp_dec = inspect_outbound(text, policy=dlp_policy)

    all_findings = list(secret_dec.findings) + list(dlp_dec.findings)
    if secret_dec.outcome is Outcome.DENY or dlp_dec.outcome is Outcome.DENY:
        reasons = [d.reason for d in (secret_dec, dlp_dec) if d.outcome is Outcome.DENY]
        return Decision(
            Outcome.DENY,
            "+".join(reasons),
            findings=tuple(all_findings),
            text=None,
            details={
                "secretOutcome": secret_dec.outcome.value,
                "dlpOutcome": dlp_dec.outcome.value,
                "findingCount": len(all_findings),
            },
        )

    # Apply secret redaction first, then DLP redaction on the working text.
    working_text = secret_dec.text if secret_dec.text is not None else text
    dlp_on_working = inspect_outbound(working_text, policy=dlp_policy)
    if dlp_on_working.outcome is Outcome.REDACT and dlp_on_working.text is not None:
        working_text = dlp_on_working.text

    inj_dec = (
        inspect_injection(working_text, source=source, policy=injection_policy)
        if check_injection
        else Decision(Outcome.ALLOW, "injection.skipped", text=working_text)
    )
    all_findings.extend(inj_dec.findings)

    combined_outcome = strongest((secret_dec.outcome, dlp_dec.outcome, inj_dec.outcome))
    if inj_dec.outcome is Outcome.QUARANTINE and inj_dec.text is not None:
        working_text = inj_dec.text

    reasons = [d.reason for d in (secret_dec, dlp_dec, inj_dec) if d.outcome is not Outcome.ALLOW]
    reason = "+".join(reasons) if reasons else "outbound.clean"
    return Decision(
        combined_outcome,
        reason,
        findings=tuple(all_findings),
        text=working_text,
        details={
            "secretOutcome": secret_dec.outcome.value,
            "dlpOutcome": dlp_dec.outcome.value,
            "injectionOutcome": inj_dec.outcome.value,
            "suspicionScore": inj_dec.details.get("suspicionScore", 0),
            "findingCount": len(all_findings),
        },
    )


def evaluate_hap_policy_request(
    req: HapRequest,
    *,
    team_id: str,
    signer: CapabilitySigner | None = None,
    egress_policy: EgressPolicy | None = None,
    resolver: Resolver | None = None,
    file_policy: SensitiveFilePolicy = DEFAULT_SENSITIVE_FILE_POLICY,
) -> tuple[HapResponse, Decision]:
    """Evaluate a HAP/1 policy operation without consuming state unless asked."""
    op = req.operation
    args = req.arguments

    if op == "policy.scan_outbound":
        raw_text = args.get("text")
        if not isinstance(raw_text, str):
            dec = Decision(Outcome.DENY, "policy.invalid_arguments")
            return (
                HapResponse(
                    req.request_id,
                    "failed",
                    error={"code": "VALIDATION_ERROR", "message": "text string required"},
                ),
                dec,
            )
        source = str(args.get("source") or "untrusted")
        check_inj = bool(args.get("checkInjection", True))
        dec = evaluate_outbound_payload(raw_text, source=source, check_injection=check_inj)
        status = "denied" if dec.outcome is Outcome.DENY else "succeeded"
        return (
            HapResponse(
                req.request_id,
                status,
                result=dec.as_dict(),
                error=(
                    {"code": "POLICY_DENIED", "message": dec.reason}
                    if dec.outcome is Outcome.DENY
                    else None
                ),
            ),
            dec,
        )

    if op == "policy.sensitive_file":
        path = args.get("path")
        file_op = str(args.get("fileOperation") or "read")
        content = args.get("content")
        if not isinstance(path, str):
            dec = Decision(Outcome.DENY, "file.invalid_path")
            return (
                HapResponse(
                    req.request_id,
                    "failed",
                    error={"code": "VALIDATION_ERROR", "message": "path string required"},
                ),
                dec,
            )
        if isinstance(content, str):
            tx = evaluate_transmission(path, content, policy=file_policy)
            dec = tx.decision
        else:
            dec = evaluate_path(path, operation=file_op, policy=file_policy)
        status = "denied" if dec.outcome is Outcome.DENY else "succeeded"
        return (
            HapResponse(
                req.request_id,
                status,
                result=dec.as_dict(),
                error=(
                    {"code": "POLICY_DENIED", "message": dec.reason}
                    if dec.outcome is Outcome.DENY
                    else None
                ),
            ),
            dec,
        )

    if op == "policy.egress":
        eff_policy = egress_policy or EgressPolicy(
            allowed_hosts=tuple(args.get("allowedHosts") or ()),
            denied_hosts=tuple(args.get("deniedHosts") or ()),
            allow_http=bool(args.get("allowHttp", False)),
            allowed_ports=tuple(int(p) for p in (args.get("allowedPorts") or (443,))),
            allow_private=bool(args.get("allowPrivate", False)),
            allow_link_local=bool(args.get("allowLinkLocal", False)),
            allow_ip_literals=bool(args.get("allowIpLiterals", False)),
            require_dns_check=bool(args.get("requireDnsCheck", False)),
        )
        urls = args.get("redirectChain")
        url = args.get("url")
        if isinstance(urls, list) and urls:
            dec = evaluate_redirect_chain(
                [str(u) for u in urls], policy=eff_policy, resolver=resolver
            )
        elif isinstance(url, str):
            dec = evaluate_url(url, policy=eff_policy, resolver=resolver)
        else:
            dec = Decision(Outcome.DENY, "egress.malformed_url")
            return (
                HapResponse(
                    req.request_id,
                    "failed",
                    error={"code": "VALIDATION_ERROR", "message": "url or redirectChain required"},
                ),
                dec,
            )
        status = "denied" if dec.outcome is Outcome.DENY else "succeeded"
        return (
            HapResponse(
                req.request_id,
                status,
                result=dec.as_dict(),
                error=(
                    {"code": "POLICY_DENIED", "message": dec.reason}
                    if dec.outcome is Outcome.DENY
                    else None
                ),
            ),
            dec,
        )

    if op == "policy.capability_verify":
        if signer is None:
            dec = Decision(Outcome.DENY, "capability.signer_unavailable")
            return (
                HapResponse(
                    req.request_id,
                    "failed",
                    error={"code": "SIGNER_UNAVAILABLE", "message": "capability signer missing"},
                ),
                dec,
            )
        token = str(args.get("token") or "")
        target_op = str(args.get("requiredOperation") or "")
        audience = str(args.get("audience") or "")
        bound_args = args["boundArguments"] if "boundArguments" in args else UNSET
        req_binding = RequiredBinding(
            team_id=team_id,
            run_id=req.run_id,
            operation=target_op,
            audience=audience,
            project_id=req.project_id,
            args=bound_args,
        )
        dec = signer.verify(token, required=req_binding)
        status = "denied" if dec.outcome is Outcome.DENY else "succeeded"
        return (
            HapResponse(
                req.request_id,
                status,
                result=dec.as_dict(),
                error=(
                    {"code": "POLICY_DENIED", "message": dec.reason}
                    if dec.outcome is Outcome.DENY
                    else None
                ),
            ),
            dec,
        )

    dec = Decision(Outcome.DENY, "policy.unsupported_operation", details={"operation": op})
    return (
        HapResponse(
            req.request_id,
            "unsupported",
            error={"code": "UNSUPPORTED_OPERATION", "message": f"unsupported operation: {op}"},
        ),
        dec,
    )
