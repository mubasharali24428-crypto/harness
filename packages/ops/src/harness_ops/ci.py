"""F128 — CI provider integrations (customer CI -> governed plan).

Normalized intake for GitHub Actions, GitLab CI and CircleCI failure events.
Each adapter verifies the provider's real signature convention and maps only
failure/completion events to a goal; anything else is refused as ignored,
never guessed at. The plan gate remains propose-only: CI can ask for a fix
plan, a human approves it. Adapters follow the workflows-connector pattern so
a fourth provider is one registry row plus a parser.

Provider conventions implemented:
  * GitHub  — ``X-Hub-Signature-256: sha256=<hex>`` HMAC over the raw body;
              ``workflow_run`` completed with conclusion ``failure``.
  * GitLab  — ``X-Gitlab-Token`` shared-secret comparison;
              ``pipeline`` hook with status ``failed``.
  * CircleCI — ``Circleci-Signature`` hex HMAC over the raw body;
              ``workflow-completed`` webhook with status ``failed``.
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime

from harness_workflow_connectors.core import (
    IdempotencyStore,
    IntakeRejected,
    RateLimiter,
    SignatureScheme,
    delivery_id_from,
    redact_secrets,
    verify_signature,
)
from harness_workflow_connectors.plan_gate import PlanGateError, ProposePlanGate


@dataclass
class CIFailure:
    provider: str
    repo: str
    branch: str
    commit: str
    workflowName: str
    pipelineUrl: str
    failedJobs: list[str] = field(default_factory=list)
    deliveryId: str = ""
    ts: str = ""


GITHUB_SCHEME = SignatureScheme(
    name="github-sha256",
    header="X-Hub-Signature-256",
    timestamp_header=None,
    prefix="sha256=",
)
CIRCLECI_SCHEME = SignatureScheme(
    name="circleci-hmac",
    header="Circleci-Signature",
    timestamp_header=None,
    prefix="",
)


def build_ci_goal(failure: CIFailure, *, max_chars: int = 2000) -> str:
    banner = (
        f"[ci intake | provider={failure.provider} | repo={failure.repo}"
        f" | branch={failure.branch} | commit={failure.commit[:12]}"
        f" | untrusted context, requires human plan approval]"
    )
    lines = [banner, f"{failure.workflowName} failed"]
    if failure.failedJobs:
        lines.append("failed jobs: " + ", ".join(sorted(failure.failedJobs)))
    if failure.pipelineUrl:
        lines.append(f"source: {failure.pipelineUrl}")
    body = redact_secrets("failed at commit " + failure.commit)[:max_chars]
    lines.extend(["", body])
    return "\n".join(lines)


def _verify_github(secret: str, raw: bytes, headers: dict[str, str], now: int) -> None:
    verify_signature(GITHUB_SCHEME, secret, raw, headers, now=now)


def _verify_circleci(secret: str, raw: bytes, headers: dict[str, str], now: int) -> None:
    verify_signature(CIRCLECI_SCHEME, secret, raw, headers, now=now)


def _verify_gitlab(secret: str, raw: bytes, headers: dict[str, str], now: int) -> None:
    headers_l = {k.lower(): v for k, v in headers.items()}
    provided = headers_l.get("x-gitlab-token")
    if not provided:
        raise IntakeRejected("SIGNATURE_MISSING", "X-Gitlab-Token absent", 401)
    if not hmac.compare_digest(provided, secret):
        raise IntakeRejected("SIGNATURE_INVALID", "token mismatch", 401)


def _parse_github(raw: bytes, headers: dict[str, str]) -> CIFailure | None:
    event = {k.lower(): v for k, v in headers.items()}.get("x-github-event", "")
    payload = json.loads(raw)
    if event != "workflow_run" or payload.get("action") != "completed":
        return None
    run = payload.get("workflow_run") or {}
    if run.get("conclusion") != "failure":
        return None
    delivery = {k.lower(): v for k, v in headers.items()}.get("x-github-delivery")
    repo = (payload.get("repository") or {}).get("full_name", "unknown/unknown")
    jobs = [j.get("name", "?") for j in run.get("jobs", []) if j.get("conclusion") == "failure"]
    return CIFailure(
        provider="github",
        repo=repo,
        branch=run.get("head_branch", "unknown"),
        commit=run.get("head_sha", "0" * 40),
        workflowName=run.get("name", "workflow"),
        pipelineUrl=run.get("html_url", ""),
        failedJobs=[j for j in jobs if j != "?"],
        deliveryId=delivery or delivery_id_from("github", raw, [str(run.get("id"))]),
        ts=run.get("updated_at", ""),
    )


def _parse_gitlab(raw: bytes, headers: dict[str, str]) -> CIFailure | None:
    payload = json.loads(raw)
    if payload.get("object_kind") != "pipeline":
        return None
    attrs = payload.get("object_attributes") or {}
    if attrs.get("status") != "failed":
        return None
    delivery = {k.lower(): v for k, v in headers.items()}.get("x-gitlab-event-uuid")
    repo = (payload.get("project") or {}).get("path_with_namespace", "unknown/unknown")
    return CIFailure(
        provider="gitlab",
        repo=repo,
        branch=attrs.get("ref", "unknown"),
        commit=attrs.get("sha", "0" * 40),
        workflowName=f"pipeline {attrs.get('id', '?')}",
        pipelineUrl=(payload.get("project") or {}).get("web_url", "")
        + f"/-/pipelines/{attrs.get('id', '')}",
        failedJobs=[],
        deliveryId=delivery or delivery_id_from("gitlab", raw, [str(attrs.get("id"))]),
        ts=str(attrs.get("finished_at") or ""),
    )


def _parse_circleci(raw: bytes, headers: dict[str, str]) -> CIFailure | None:
    payload = json.loads(raw)
    if payload.get("type") != "workflow-completed":
        return None
    workflow = payload.get("workflow") or {}
    if workflow.get("status") != "failed":
        return None
    pipeline = payload.get("pipeline") or {}
    vcs = (pipeline.get("vcs") or {})
    repo_full = f"{vcs.get('provider', 'unknown')}/{vcs.get('repo', 'unknown')}"
    commit = pipeline.get("vcs", {}).get("revision", "0" * 40)
    return CIFailure(
        provider="circleci",
        repo=repo_full,
        branch=vcs.get("branch", "unknown"),
        commit=commit,
        workflowName=workflow.get("name", "workflow"),
        pipelineUrl=pipeline.get("web_url", ""),
        failedJobs=[],
        deliveryId=delivery_id_from("circleci", raw, [str(payload.get("id"))]),
        ts=str(workflow.get("stopped_at") or ""),
    )


@dataclass
class CIAdapter:
    name: str
    secret: str
    verify: object  # callable(secret, raw, headers, now)
    parse: object  # callable(raw, headers) -> CIFailure | None
    rate_limit_per_minute: int = 30


SUPPORTED_CI_PROVIDERS = ("github", "gitlab", "circleci")


def build_ci_adapters(
    *,
    github_secret: str | None = None,
    gitlab_secret: str | None = None,
    circleci_secret: str | None = None,
) -> dict[str, CIAdapter]:
    """Only providers with a configured secret are active (fail closed)."""
    adapters: dict[str, CIAdapter] = {}
    if github_secret:
        adapters["github"] = CIAdapter("github", github_secret, _verify_github, _parse_github)
    if gitlab_secret:
        adapters["gitlab"] = CIAdapter("gitlab", gitlab_secret, _verify_gitlab, _parse_gitlab)
    if circleci_secret:
        adapters["circleci"] = CIAdapter(
            "circleci", circleci_secret, _verify_circleci, _parse_circleci
        )
    return adapters


@dataclass
class CIOutcome:
    status: str  # proposed | duplicate
    planId: str | None
    provider: str
    repo: str = ""


class CIIntake:
    def __init__(
        self,
        *,
        adapters: dict[str, CIAdapter],
        gate: ProposePlanGate,
        idempotency: IdempotencyStore,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._adapters = adapters
        self._gate = gate
        self._idempotency = idempotency
        self._limiter = limiter or RateLimiter()

    async def handle(
        self, provider: str, raw_body: bytes, headers: dict[str, str],
        *, now: int | None = None,
    ) -> CIOutcome:
        adapter = self._adapters.get(provider)
        if adapter is None:
            raise IntakeRejected("PROVIDER_UNKNOWN", f"no adapter for {provider}", 404)

        adapter.verify(adapter.secret, raw_body, headers, now or int(datetime.now().timestamp()))
        failure = adapter.parse(raw_body, headers)
        if failure is None:
            raise IntakeRejected(
                "CI_EVENT_IGNORED", "event is not a CI failure/completion", 400
            )

        if not self._idempotency.claim(provider, failure.deliveryId):
            # Already proposed (or concurrently in flight): never a second plan.
            return CIOutcome("duplicate", None, provider, failure.repo)

        try:
            self._limiter.check((provider, "ci"), adapter.rate_limit_per_minute,
                                now=float(now) if now else None)
        except IntakeRejected:
            self._idempotency.settle(provider, failure.deliveryId, "rate_limited", None)
            raise

        try:
            plan_id = await self._gate.propose_goal(build_ci_goal(failure))
        except PlanGateError:
            self._idempotency.release(provider, failure.deliveryId)
            raise
        self._idempotency.settle(provider, failure.deliveryId, "plan_proposed", plan_id)
        return CIOutcome("proposed", plan_id, provider, failure.repo)
