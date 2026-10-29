# ruff: noqa: F811 — `env` is a pytest fixture imported for discovery; test args shadow it.
"""W9: a deployment approval names the reviewed revision, and revocation is final.

The gate text is "deployment approval binds reviewed revision; revocation works", so
these tests are about the binding failing at the right moment: a head that moved, a
patch that arrived, a plan version that changed, or a client that echoes a different
digest than the one the request was bound to. None of those may produce an approval.
"""

from .test_artifacts_support import SHA_B, SHA_C, env, err, git_diff  # noqa: F401

P = "/api/v1/orchestrator"


def _request(env, user="lead", run="r1", environment="staging", **extra):
    return env.as_(user).post(
        env.url(run, "/deployments"), json={"environment": environment, **extra}
    )


def _approved_ready_run(env):
    data = env.ready()  # diff recorded, every change approved, base fresh, head recorded
    return data


def test_a_request_binds_the_reviewed_revision(env):
    data = _approved_ready_run(env)
    r = _request(env)
    assert r.status_code == 201, r.text
    dep = r.json()["deployment"]
    assert dep["environment"] == "staging" and dep["state"] == "requested"
    assert dep["binding"]["changesDigest"] == data["changesDigest"]
    assert dep["binding"]["runHeadSha"] == SHA_C
    assert dep["binding"]["baseSha"] == "a" * 40
    assert dep["stale"] is False and dep["staleFields"] == []


def test_approval_binds_the_digest_the_client_echoes_and_the_revision(env):
    data = _approved_ready_run(env)
    dep = _request(env).json()["deployment"]
    ok = env.as_("lead").post(
        env.url("r1", f"/deployments/{dep['id']}/approve"),
        json={"changesDigest": data["changesDigest"]},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["deployment"]["state"] == "approved"
    assert ok.json()["deployment"]["decidedBy"] == "lead"


def test_a_head_that_moved_makes_the_request_stale(env):
    data = _approved_ready_run(env)
    dep = _request(env).json()["deployment"]
    env.git("lead", "r1", baseSha="a" * 40, observedHeadSha="a" * 40,
            headBranch="agent/r1", runHeadSha=SHA_B)  # the run head moved
    listed = env.as_("lead").get(env.url("r1", "/deployments")).json()["items"][0]
    assert listed["stale"] is True and listed["staleFields"] == ["runHeadSha"]
    stale = err(
        env.as_("lead").post(
            env.url("r1", f"/deployments/{dep['id']}/approve"),
            json={"changesDigest": data["changesDigest"]},
        ),
        409,
        "DEPLOYMENT_STALE",
    )
    assert stale["staleFields"] == ["runHeadSha"]
    # Still requested: a refusal is not a decision.
    assert env.as_("lead").get(env.url("r1", "/deployments")).json()["items"][0]["state"] == (
        "requested"
    )


def test_a_new_patch_makes_the_request_stale(env):
    data = _approved_ready_run(env)
    dep = _request(env).json()["deployment"]
    env.set_diff("r1", git_diff(("src/a.py", ["print(3)"])))
    env.changes("lead", "r1")  # records the new patch, superseding the old one
    stale = err(
        env.as_("lead").post(
            env.url("r1", f"/deployments/{dep['id']}/approve"),
            json={"changesDigest": data["changesDigest"]},
        ),
        409,
        "DEPLOYMENT_STALE",
    )
    assert "changesDigest" in stale["staleFields"]


def test_a_client_echoing_another_digest_cannot_approve(env):
    _approved_ready_run(env)
    dep = _request(env).json()["deployment"]
    stale = err(
        env.as_("lead").post(
            env.url("r1", f"/deployments/{dep['id']}/approve"),
            json={"changesDigest": "0" * 64},
        ),
        409,
        "DEPLOYMENT_STALE",
    )
    assert stale["staleFields"] == ["clientDigest"]


def test_revocation_is_final_and_idempotent(env):
    data = _approved_ready_run(env)
    dep = _request(env).json()["deployment"]
    approved = env.as_("lead").post(
        env.url("r1", f"/deployments/{dep['id']}/approve"),
        json={"changesDigest": data["changesDigest"]},
    )
    assert approved.status_code == 200
    first = env.as_("lead").post(env.url("r1", f"/deployments/{dep['id']}/revoke"), json={})
    assert first.status_code == 200 and first.json()["deployment"]["state"] == "revoked"
    assert first.json()["deployment"]["revokedAt"]

    again = env.as_("lead").post(env.url("r1", f"/deployments/{dep['id']}/revoke"), json={})
    assert again.status_code == 200
    assert again.json()["deployment"]["revokedAt"] == first.json()["deployment"]["revokedAt"]

    err(
        env.as_("lead").post(
            env.url("r1", f"/deployments/{dep['id']}/approve"),
            json={"changesDigest": data["changesDigest"]},
        ),
        409,
        "DEPLOYMENT_REVOKED",
    )


def test_a_decided_request_cannot_be_decided_again(env):
    data = _approved_ready_run(env)
    dep = _request(env).json()["deployment"]
    body = {"changesDigest": data["changesDigest"]}
    approve = env.as_("lead").post(
        env.url("r1", f"/deployments/{dep['id']}/approve"), json=body
    )
    assert approve.status_code == 200
    err(
        env.as_("lead").post(env.url("r1", f"/deployments/{dep['id']}/approve"), json=body),
        409,
        "DEPLOYMENT_DECIDED",
    )


def test_a_run_without_a_recorded_revision_cannot_be_deployed(env):
    # No done-signal diff at all.
    err(_request(env), 409, "NOTHING_TO_DEPLOY")
    # Changes exist but the head was never recorded.
    env.set_diff("r2", git_diff(("src/a.py", ["print(1)"])))
    env.changes("lead", "r2")
    err(_request(env, run="r2"), 409, "REVISION_UNKNOWN")


def test_a_bad_environment_name_is_refused(env):
    _approved_ready_run(env)
    err(_request(env, environment="Prod "), 409, "ENVIRONMENT_INVALID")
    assert _request(env, environment="prod").status_code == 201


def test_a_foreign_team_cannot_see_or_decide(env):
    data = _approved_ready_run(env)
    dep = _request(env).json()["deployment"]
    err(_request(env, user="evil"), 404, "NOT_FOUND")
    err(env.as_("evil").get(env.url("r1", "/deployments")), 404, "NOT_FOUND")
    err(
        env.as_("evil").post(
            env.url("r1", f"/deployments/{dep['id']}/approve"),
            json={"changesDigest": data["changesDigest"]},
        ),
        404,
        "NOT_FOUND",
    )
    err(
        env.as_("evil").post(env.url("r1", f"/deployments/{dep['id']}/revoke"), json={}),
        404,
        "NOT_FOUND",
    )


def test_roles_are_enforced(env):
    data = _approved_ready_run(env)
    dep = _request(env).json()["deployment"]
    # A viewer may read, not request or decide.
    assert env.as_("view").get(env.url("r1", "/deployments")).status_code == 200
    err(_request(env, user="view"), 403, "ROLE_DENIED")
    err(
        env.as_("view").post(
            env.url("r1", f"/deployments/{dep['id']}/approve"),
            json={"changesDigest": data["changesDigest"]},
        ),
        403,
        "ROLE_DENIED",
    )


def test_a_retried_request_with_the_same_id_is_one_deployment(env):
    _approved_ready_run(env)
    first = _request(env, requestId="req-1")
    assert first.status_code == 201 and first.json()["replayed"] is False
    second = _request(env, requestId="req-1")
    assert second.status_code == 200 and second.json()["replayed"] is True
    assert second.json()["deployment"]["id"] == first.json()["deployment"]["id"]
    assert len(env.as_("lead").get(env.url("r1", "/deployments")).json()["items"]) == 1


def test_every_step_is_audited_with_the_binding(env):
    data = _approved_ready_run(env)
    dep = _request(env).json()["deployment"]
    env.as_("lead").post(
        env.url("r1", f"/deployments/{dep['id']}/approve"),
        json={"changesDigest": data["changesDigest"]},
    )
    env.as_("lead").post(env.url("r1", f"/deployments/{dep['id']}/revoke"), json={})
    requested = env.audit("deployment.requested")[0]
    assert data["changesDigest"] in requested.detail and SHA_C in requested.detail
    approved = env.audit("deployment.approved")[0]
    assert data["changesDigest"] in approved.detail
    assert env.audit("deployment.revoked")
