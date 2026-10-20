"""F128 acceptance: provider signature verification is real per-provider,
only failure events become goals, duplicates never double-propose, and an
upstream outage releases the claim so the provider's retry re-enters."""

from __future__ import annotations

import json

import pytest
from harness_workflow_connectors.core import IdempotencyStore, IntakeRejected, sign
from harness_workflow_connectors.plan_gate import PlanGateError, ProposePlanGate
from support import fake_orchestrator

from harness_ops.ci import (
    CIRCLECI_SCHEME,
    GITHUB_SCHEME,
    SUPPORTED_CI_PROVIDERS,
    CIIntake,
    build_ci_adapters,
    build_ci_goal,
)

GH_SECRET = "gh-secret"
GL_SECRET = "gl-secret"
CC_SECRET = "cc-secret"


def github_payload(*, conclusion="failure", action="completed"):
    return {
        "action": action,
        "repository": {"full_name": "acme/app"},
        "workflow_run": {
            "id": 7, "name": "CI", "conclusion": conclusion, "head_branch": "main",
            "head_sha": "a" * 40, "html_url": "https://gh.example/runs/7",
            "updated_at": "2026-10-17T11:00:00Z",
            "jobs": [{"name": "unit", "conclusion": "failure"},
                     {"name": "lint", "conclusion": "success"}],
        },
    }


def github_headers(raw: bytes, delivery="del-1"):
    return {
        "X-GitHub-Event": "workflow_run",
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": sign(GITHUB_SCHEME, GH_SECRET, raw, ""),
    }


def gitlab_payload(status="failed"):
    return {
        "object_kind": "pipeline",
        "object_attributes": {"status": status, "ref": "main", "sha": "b" * 40,
                              "id": 42, "finished_at": "2026-10-17T11:00:00Z"},
        "project": {"path_with_namespace": "acme/app",
                    "web_url": "https://gl.example/acme/app"},
    }


def gitlab_headers(token=GL_SECRET, delivery="gl-1"):
    return {"X-Gitlab-Token": token, "X-Gitlab-Event-UUID": delivery}


def circleci_payload(status="failed"):
    return {
        "type": "workflow-completed",
        "id": "wf-1",
        "workflow": {"name": "deploy", "status": status,
                     "stopped_at": "2026-10-17T11:00:00Z"},
        "pipeline": {
            "web_url": "https://circle.example/pipelines/1",
            "vcs": {"provider": "github", "repo": "acme/app", "branch": "main",
                    "revision": "c" * 40},
        },
    }


def circleci_headers(raw: bytes):
    return {"Circleci-Signature": sign(CIRCLECI_SCHEME, CC_SECRET, raw, "")}


@pytest.fixture()
async def intake(tmp_path):
    async with fake_orchestrator() as (base, state):
        adapters = build_ci_adapters(github_secret=GH_SECRET, gitlab_secret=GL_SECRET,
                                     circleci_secret=CC_SECRET)
        gate = ProposePlanGate(base, bearer_token="ci-token")
        store = IdempotencyStore(str(tmp_path / "idem.sqlite"))
        yield CIIntake(adapters=adapters, gate=gate, idempotency=store), state
        await gate.aclose()


def test_provider_registry_is_explicit():
    assert SUPPORTED_CI_PROVIDERS == ("github", "gitlab", "circleci")
    assert build_ci_adapters() == {}
    assert set(build_ci_adapters(github_secret="x")) == {"github"}


async def test_github_failure_proposes_governed_plan(intake):
    ci, state = intake
    raw = json.dumps(github_payload()).encode()
    outcome = await ci.handle("github", raw, github_headers(raw), now=1_700_000_000)
    assert outcome.status == "proposed"
    assert outcome.planId and outcome.repo == "acme/app"
    plan = state["plans"][outcome.planId]
    assert "[ci intake" in plan["goal"]
    assert "requires human plan approval" in plan["goal"]
    assert "unit" in plan["goal"] and "lint" not in plan["goal"]
    assert state["approve_calls"] == []


async def test_github_duplicate_never_double_proposes(intake):
    ci, state = intake
    raw = json.dumps(github_payload()).encode()
    first = await ci.handle("github", raw, github_headers(raw), now=1_700_000_000)
    second = await ci.handle("github", raw, github_headers(raw), now=1_700_000_010)
    assert first.status == "proposed"
    assert second.status == "duplicate" and second.planId is None
    assert len(state["plans"]) == 1


async def test_github_bad_signature_rejected(intake):
    ci, _ = intake
    raw = json.dumps(github_payload()).encode()
    headers = github_headers(raw)
    headers["X-Hub-Signature-256"] = sign(GITHUB_SCHEME, "wrong", raw, "")
    with pytest.raises(IntakeRejected) as exc:
        await ci.handle("github", raw, headers, now=1_700_000_000)
    assert exc.value.code == "SIGNATURE_INVALID"
    assert exc.value.status_code == 401


async def test_non_failure_events_are_ignored(intake):
    ci, state = intake
    raw = json.dumps(github_payload(conclusion="success")).encode()
    with pytest.raises(IntakeRejected) as exc:
        await ci.handle("github", raw, github_headers(raw), now=1_700_000_000)
    assert exc.value.code == "CI_EVENT_IGNORED"
    push_raw = json.dumps(github_payload()).encode()
    headers = github_headers(push_raw)
    headers["X-GitHub-Event"] = "push"
    with pytest.raises(IntakeRejected):
        await ci.handle("github", push_raw, headers, now=1_700_000_000)
    assert state["plans"] == {}


async def test_gitlab_token_and_pipeline(intake):
    ci, state = intake
    raw = json.dumps(gitlab_payload()).encode()
    outcome = await ci.handle("gitlab", raw, gitlab_headers(), now=1_700_000_000)
    assert outcome.status == "proposed"
    assert "acme/app" in state["plans"][outcome.planId]["goal"]
    with pytest.raises(IntakeRejected) as exc:
        await ci.handle("gitlab", raw, gitlab_headers(token="nope"), now=1_700_000_001)
    assert exc.value.code == "SIGNATURE_INVALID"


async def test_circleci_hmac_and_workflow(intake):
    ci, state = intake
    raw = json.dumps(circleci_payload()).encode()
    outcome = await ci.handle("circleci", raw, circleci_headers(raw), now=1_700_000_000)
    assert outcome.status == "proposed"
    bad = {"Circleci-Signature": sign(CIRCLECI_SCHEME, "wrong", raw, "")}
    with pytest.raises(IntakeRejected) as exc:
        await ci.handle("circleci", raw, bad, now=1_700_000_001)
    assert exc.value.code == "SIGNATURE_INVALID"


async def test_unknown_provider_fails_closed(intake):
    ci, _ = intake
    with pytest.raises(IntakeRejected) as exc:
        await ci.handle("jenkins", b"{}", {}, now=1_700_000_000)
    assert exc.value.code == "PROVIDER_UNKNOWN"
    assert exc.value.status_code == 404


async def test_rate_limited_deliveries_do_not_queue_plans(intake):
    ci, state = intake
    ci._limiter._hits[("github", "ci")] = [1_700_000_000.0] * 30
    raw = json.dumps(github_payload()).encode()
    with pytest.raises(IntakeRejected) as exc:
        await ci.handle("github", raw, github_headers(raw), now=1_700_000_000)
    assert exc.value.code == "RATE_LIMITED"
    assert state["plans"] == {}


async def test_gate_outage_releases_claim_for_provider_retry(tmp_path):
    class FlakyGate:
        def __init__(self, inner):
            self.inner = inner
            self.fail_next = True

        async def propose_goal(self, goal: str) -> str:
            if self.fail_next:
                self.fail_next = False
                raise PlanGateError("PLAN_GATE_UNAVAILABLE", "simulated outage", 502)
            return await self.inner.propose_goal(goal)

    async with fake_orchestrator() as (base, state):
        inner = ProposePlanGate(base, bearer_token="ci-token")
        adapters = build_ci_adapters(github_secret=GH_SECRET)
        ci = CIIntake(adapters=adapters, gate=FlakyGate(inner),
                      idempotency=IdempotencyStore(str(tmp_path / "idem.sqlite")))
        raw = json.dumps(github_payload()).encode()
        with pytest.raises(PlanGateError):
            await ci.handle("github", raw, github_headers(raw), now=1_700_000_000)
        outcome = await ci.handle("github", raw, github_headers(raw), now=1_700_000_010)
        assert outcome.status == "proposed"
        assert len(state["plans"]) == 1
        await inner.aclose()


def test_goal_banner_marks_content_untrusted():
    from harness_ops.ci import CIFailure

    goal = build_ci_goal(CIFailure(
        provider="github", repo="acme/app", branch="main", commit="d" * 40,
        workflowName="CI", pipelineUrl="https://x", failedJobs=["unit"],
    ))
    assert "[ci intake" in goal
    assert "untrusted context, requires human plan approval" in goal
    assert "failed jobs: unit" in goal
