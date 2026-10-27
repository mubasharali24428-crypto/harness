"""W9: an artifact share link is a capability that can be revoked.

The gate text is "preview and share URLs enforce access", so these tests are about
what a link CANNOT do: survive revocation, outlive its expiry, reach a foreign
team's artifact, serve a quarantined one, render as HTML, or be discovered from the
run's list. The token never comes back from the API twice, and the row keeps only
its hash.
"""

from .test_artifacts_support import err, env, sha  # noqa: F401 - env is the fixture

P = "/api/v1/orchestrator"


def _mint(env, user="lead", run="r1", artifact=None, **body):
    art = artifact or env.put(user, run, "report.txt", b"hello report\n")
    r = env.as_(user).post(env.url(run, f"/artifacts/{art['id']}/share"), json=body)
    assert r.status_code == 201, r.text
    return art, r.json()["share"]


def _open(env, token):
    """No Authorization header is sent and no principal is consulted."""
    return env.client.get(f"{P}/shares/{token}")


def test_a_link_serves_the_bytes_without_any_auth_header(env):
    art, share = _mint(env)
    assert share["token"] and share["url"] == f"{P}/shares/{share['token']}"
    assert share["artifactId"] == art["id"]

    r = _open(env, share["token"])
    assert r.status_code == 200, r.text
    assert r.content == b"hello report\n"
    # Served as data, never as a document: a capability that can render HTML on
    # the deployment's own origin is a stored-XSS primitive.
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert r.headers["x-artifact-sha256"] == sha(b"hello report\n")
    assert r.headers["cache-control"] == "private, no-store"


def test_active_content_never_gets_a_link_at_all(env):
    """The classifier quarantines markup whatever it is called, and a quarantined
    artifact cannot be minted — so the "download, never render" rule is not the only
    defence: the dangerous case never becomes a URL in the first place."""
    for name, ctype in (("page.html", "text/html"), ("notes.txt", "text/plain")):
        art = env.put("lead", "r1", name, b"<script>alert(1)</script>", ctype=ctype)
        assert art["state"] == "quarantined"
        err(
            env.as_("lead").post(env.url("r1", f"/artifacts/{art['id']}/share"), json={}),
            409,
            "SHARE_REFUSED",
        )


def test_the_token_is_shown_once_and_never_stored_in_the_clear(env):
    _, share = _mint(env)
    listed = env.as_("lead").get(env.url("r1", "/shares"))
    assert listed.status_code == 200
    assert [i["id"] for i in listed.json()["items"]] == [share["id"]]
    assert share["token"] not in listed.text
    row = env.q("SELECT token_sha FROM artifact_shares")[0]
    assert row.token_sha == sha(share["token"].encode())
    assert row.token_sha != share["token"]


def test_revocation_stops_every_copy_and_is_idempotent(env):
    _, share = _mint(env)
    assert _open(env, share["token"]).status_code == 200
    first = env.as_("lead").post(env.url("r1", f"/shares/{share['id']}/revoke"))
    assert first.status_code == 200, first.text
    assert first.json()["share"]["revokedAt"] and first.json()["share"]["active"] is False

    # The revoked URL gets the same answer as a forged one: no existence oracle.
    err(_open(env, share["token"]), 404, "NOT_FOUND")
    second = env.as_("lead").post(env.url("r1", f"/shares/{share['id']}/revoke"))
    assert second.status_code == 200
    assert second.json()["share"]["revokedAt"] == first.json()["share"]["revokedAt"]
    assert _open(env, share["token"]).status_code == 404


def test_an_expired_link_is_gone(env):
    _, share = _mint(env, ttlSeconds=60)
    assert _open(env, share["token"]).status_code == 200
    env.tick(seconds=61)
    err(_open(env, share["token"]), 410, "SHARE_EXPIRED")


def test_a_deleted_artifact_takes_its_links_with_it(env):
    art, share = _mint(env)
    deleted = env.as_("lead").delete(env.url("r1", f"/artifacts/{art['id']}"))
    assert deleted.status_code in (200, 204), deleted.text
    err(_open(env, share["token"]), 410, "ARTIFACT_DELETED")


def test_a_quarantined_artifact_cannot_be_shared_and_an_existing_link_refuses(env):
    art, share = _mint(env)
    env.x("UPDATE artifacts SET state = 'quarantined' WHERE id = :i", i=art["id"])
    err(_open(env, share["token"]), 403, "SHARE_BLOCKED")

    # A fresh upload that the classifier already quarantined cannot be minted at all.
    tool = env.as_("lead").post(
        env.url("r1", "/artifacts"),
        params={"name": "tool.bin"},
        content=b"\x7fELF" + b"\x00" * 32,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert tool.status_code == 201, tool.text
    quarantined = tool.json()["artifact"]
    assert quarantined["state"] == "quarantined"
    err(
        env.as_("lead").post(env.url("r1", f"/artifacts/{quarantined['id']}/share"), json={}),
        409,
        "SHARE_REFUSED",
    )


def test_a_foreign_team_cannot_mint_list_or_revoke(env):
    _, share = _mint(env)
    # `evil` is an admin of team-b: role is not the point, ownership is.
    err(
        env.as_("evil").post(env.url("r1", f"/artifacts/{share['artifactId']}/share"), json={}),
        404,
        "NOT_FOUND",
    )
    err(env.as_("evil").get(env.url("r1", "/shares")), 404, "NOT_FOUND")
    err(env.as_("evil").post(env.url("r1", f"/shares/{share['id']}/revoke")), 404, "NOT_FOUND")
    # Their own run lists nothing, so the link is not even discoverable from outside.
    assert env.as_("evil").get(env.url("r3", "/shares")).json()["items"] == []
    # And the link still works: a foreign attempt changed nothing.
    assert _open(env, share["token"]).status_code == 200


def test_a_forged_token_is_not_a_share(env):
    err(_open(env, "not-a-real-token"), 404, "NOT_FOUND")
    # A bare collection path is not a share and does not enumerate anything.
    assert env.client.get(f"{P}/shares/").status_code == 404


def test_opening_a_link_is_counted_and_every_step_is_audited(env):
    _, share = _mint(env)
    _open(env, share["token"])
    _open(env, share["token"])
    env.as_("lead").post(env.url("r1", f"/shares/{share['id']}/revoke"))

    row = env.q("SELECT uses, last_used_at FROM artifact_shares")[0]
    assert row.uses == 2 and row.last_used_at is not None

    opened = env.audit("share.opened")
    assert len(opened) == 2 and all(r.target_id == share["id"] for r in opened)
    assert share["token"] not in opened[0].detail
    assert env.audit("artifact.shared") and env.audit("artifact.share_revoked")


def test_the_ttl_is_clamped_at_both_ends(env):
    _, short = _mint(env, ttlSeconds=1)
    env.tick(seconds=59)
    assert _open(env, short["token"]).status_code == 200  # clamped UP to the 60s floor
    env.tick(seconds=2)
    err(_open(env, short["token"]), 410, "SHARE_EXPIRED")

    _, long = _mint(env, ttlSeconds=10**9)
    from harness_orchestrator import artifacts as _art

    row = env.q(
        "SELECT created_at, expires_at FROM artifact_shares WHERE id = :i", i=long["id"]
    )[0]
    span = _art._dt(row.expires_at) - _art._dt(row.created_at)
    assert span.days == 90  # clamped down to MAX_TTL_SECONDS


def test_a_share_of_a_run_that_never_had_the_artifact_is_404(env):
    art, _ = _mint(env)
    err(
        env.as_("lead").post(env.url("r2", f"/artifacts/{art['id']}/share"), json={}),
        404,
        "NOT_FOUND",
    )
