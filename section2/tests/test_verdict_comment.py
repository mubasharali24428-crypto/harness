"""P-15 (I-02): verdict comment posting path — offline, MockTransport, no network.

Covers: the merge guard stays intact (comment endpoints pass it), the write
opt-in gate, create-once/update-in-place idempotency, marker-find, the body
sent verbatim, the token never leaking into a body, and tenant scope.
"""
import pytest
from sqlalchemy import text

from harness_github.client import GITHUB_API, MergeGuardViolation, _guard
from tests.test_service import auth, connect_user, make_service, seed

ROUTE = "/api/v1/projects/{pid}/pulls/{n}/verdict-comment"
MARKER = "<!-- harness-verdict:v1 run=run-1 -->"
BODY = (
    MARKER + "\n\n# Harness verdict — PR #7\n\n"
    "Advisory summary of recorded facts. Not an approval to merge. A human merges.\n"
)
TOKEN = "gho_fake_token_123"


def _payload(body=BODY, marker=MARKER, run_id="run-1"):
    return {"runId": run_id, "bodyMd": body, "marker": marker}


async def link_run(db, run_id="run-1", pr=7, project="p1"):
    """An agent run bound to PR ``pr`` of ``project`` (what pr_open.link_run_to_pr writes)."""
    async with db() as s:
        await s.execute(
            text("INSERT INTO agent_runs (id, project_id, branch, state)"
                 " VALUES (:r, :p, :b, 'executing')"),
            {"r": run_id, "p": project, "b": f"agent/{run_id}"},
        )
        await s.execute(
            text("INSERT INTO pull_requests (id, project_id, run_id, github_pr_number,"
                 " pr_url, summary_md, status) VALUES (:i, :p, :r, :n, 'u', '', 'open')"),
            {"i": f"pr-{run_id}-{pr}", "p": project, "r": run_id, "n": pr},
        )
        await s.commit()


def test_merge_guard_intact_and_comment_endpoints_pass():
    with pytest.raises(MergeGuardViolation):
        _guard("PUT", f"{GITHUB_API}/repos/org/repo/pulls/7/merge", None)
    with pytest.raises(MergeGuardViolation):
        _guard("POST", f"{GITHUB_API}/anything", {"force": True})
    with pytest.raises(MergeGuardViolation):
        _guard("DELETE", f"{GITHUB_API}/repos/org/repo/git/refs/heads/x", None)
    # comment endpoints are POST/PATCH/GET on /issues/... — the guard allows them
    _guard("POST", f"{GITHUB_API}/repos/org/repo/issues/7/comments", {"body": "x"})
    _guard("PATCH", f"{GITHUB_API}/repos/org/repo/issues/comments/1", {"body": "x"})
    _guard("GET", f"{GITHUB_API}/repos/org/repo/issues/7/comments", None)


async def test_writes_off_refused(db, client):
    await seed(db)
    await connect_user(db)
    async with db() as s:
        await s.execute(
            text("INSERT INTO projects (id, team_id, name, repo_full_name)"
                 " VALUES ('p2','t1','fresh','org/fresh')")
        )
        await s.commit()
    r = await client.post(ROUTE.format(pid="p2", n=7), headers=auth(), json=_payload())
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["error"]["code"] == "GITHUB_WRITES_DISABLED"
    assert not any(u.endswith("/comments") for _, u, _ in client.fake.requests)


async def test_create_then_update_one_comment(db, client):
    await seed(db)
    await connect_user(db)
    await link_run(db)
    first = await client.post(ROUTE.format(pid="p1", n=7), headers=auth(), json=_payload())
    assert first.status_code == 200, first.text
    assert first.json()["action"] == "created"
    cid = first.json()["commentId"]
    second = await client.post(ROUTE.format(pid="p1", n=7), headers=auth(), json=_payload())
    assert second.status_code == 200, second.text
    assert second.json()["action"] == "updated"
    assert second.json()["commentId"] == cid  # same comment, updated in place
    assert len(client.fake.comments) == 1  # one comment per PR
    assert any(m == "PATCH" and "/issues/comments/" in u for m, u, _ in client.fake.requests)


async def test_body_sent_verbatim_and_token_never_in_body(db, client):
    await seed(db)
    await connect_user(db)
    await link_run(db)
    r = await client.post(ROUTE.format(pid="p1", n=7), headers=auth(), json=_payload())
    assert r.status_code == 200, r.text
    posts = [b for m, u, b in client.fake.requests if m == "POST" and u.endswith("/comments")]
    assert posts[-1]["body"] == BODY  # the caller's body is sent unchanged
    for _, _, b in client.fake.requests:
        assert TOKEN not in (b or {}).get("body", "")
    assert TOKEN not in r.text


async def test_wrong_team_cannot_comment_http(db, client):
    await seed(db)
    await connect_user(db)
    async with db() as s:
        await s.execute(text("INSERT INTO teams (id, name) VALUES ('t2','other')"))
        await s.execute(
            text("INSERT INTO projects (id, team_id, name, repo_full_name, github_writes_enabled)"
                 " VALUES ('p9','t2','theirs','org/theirs', 1)")
        )
        await s.commit()
    r = await client.post(ROUTE.format(pid="p9", n=7), headers=auth(), json=_payload())
    assert r.status_code == 404, r.text


async def test_wrong_team_cannot_comment_service(db, client):
    await seed(db)
    await connect_user(db)
    async with db() as s:
        await s.execute(text("INSERT INTO teams (id, name) VALUES ('t2','other')"))
        await s.execute(
            text("INSERT INTO projects (id, team_id, name, repo_full_name, github_writes_enabled)"
                 " VALUES ('p9','t2','theirs','org/theirs', 1)")
        )
        await s.commit()
    svc = await make_service(db, client)
    with pytest.raises(LookupError):
        await svc.postVerdictComment("u1", "p9", 7, BODY, run_id="run-1", team_id="t1")


async def test_marker_find_updates_existing_across_keys(db, client):
    await seed(db)
    await connect_user(db)
    await link_run(db)
    svc = await make_service(db, client)
    a = await svc.postVerdictComment(
        "u1", "p1", 7, BODY, run_id="run-1", team_id="t1", marker=MARKER, idempotency_key="k1"
    )
    assert a["action"] == "created"
    # a different idempotency key, same marker: must find and update, not duplicate
    b = await svc.postVerdictComment(
        "u1", "p1", 7, BODY, run_id="run-1", team_id="t1", marker=MARKER, idempotency_key="k2"
    )
    assert b["action"] == "updated"
    assert b["commentId"] == a["commentId"]
    assert len(client.fake.comments) == 1


async def test_service_writes_off_raises(db, client):
    await seed(db)
    await connect_user(db)
    async with db() as s:
        await s.execute(
            text("INSERT INTO projects (id, team_id, name, repo_full_name)"
                 " VALUES ('p2','t1','fresh','org/fresh')")
        )
        await s.commit()
    svc = await make_service(db, client)
    from harness_github.service import GitHubWritesDisabledError

    with pytest.raises(GitHubWritesDisabledError):
        await svc.postVerdictComment("u1", "p2", 7, BODY, run_id="run-1", team_id="t1")

async def test_unlinked_pr_or_run_refused_no_github_call(db, client):
    await seed(db)
    await connect_user(db)
    await link_run(db)  # run-1 is linked to PR 7 only
    other_pr = await client.post(ROUTE.format(pid="p1", n=99999), headers=auth(), json=_payload())
    assert other_pr.status_code == 404, other_pr.text
    ghost = _payload(run_id="ghost", marker="<!-- harness-verdict:v1 run=ghost -->",
                     body="<!-- harness-verdict:v1 run=ghost -->\nx")
    unknown_run = await client.post(ROUTE.format(pid="p1", n=7), headers=auth(), json=ghost)
    assert unknown_run.status_code == 404, unknown_run.text
    assert not any(u.endswith("/comments") for _, u, _ in client.fake.requests)


async def test_foreign_marker_or_markerless_body_refused_and_human_comment_untouched(db, client):
    await seed(db)
    await connect_user(db)
    await link_run(db)
    client.fake.comments[1] = {"id": 1, "html_url": "u", "body": "LGTM, ship it", "pr": 7}
    client.fake._next_comment = 2
    bad_marker = await client.post(ROUTE.format(pid="p1", n=7), headers=auth(),
                                   json=_payload(marker="LGTM"))
    assert bad_marker.status_code == 422, bad_marker.text
    no_marker_in_body = await client.post(ROUTE.format(pid="p1", n=7), headers=auth(),
                                          json=_payload(body="attacker text", marker=""))
    assert no_marker_in_body.status_code == 422, no_marker_in_body.text
    assert client.fake.comments[1]["body"] == "LGTM, ship it"
    assert not any(m == "PATCH" for m, _, _ in client.fake.requests)
    # the right call creates its own comment and still leaves the human one alone
    ok = await client.post(ROUTE.format(pid="p1", n=7), headers=auth(), json=_payload(marker=""))
    assert ok.status_code == 200 and ok.json()["action"] == "created"
    assert client.fake.comments[1]["body"] == "LGTM, ship it"


async def test_comment_deleted_on_github_is_recreated(db, client):
    await seed(db)
    await connect_user(db)
    await link_run(db)
    first = await client.post(ROUTE.format(pid="p1", n=7), headers=auth(), json=_payload())
    assert first.json()["action"] == "created"
    client.fake.comments.clear()  # someone deleted it on GitHub
    again = await client.post(ROUTE.format(pid="p1", n=7), headers=auth(), json=_payload())
    assert again.status_code == 200, again.text
    assert again.json()["action"] == "created"
    assert len(client.fake.comments) == 1


async def test_marker_search_pages_past_100_comments():
    import httpx

    from harness_github.client import GitHubClient

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        if page == 1:
            batch = [{"id": i, "html_url": "u", "body": "noise"} for i in range(100)]
        else:
            batch = [{"id": 5000, "html_url": "u", "body": MARKER}]
        return httpx.Response(200, json=batch)

    c = GitHubClient(TOKEN, transport=httpx.MockTransport(handler))
    comments = await c.list_pr_comments("org/repo", 7)
    assert len(comments) == 101
    assert any(MARKER in x["body"] for x in comments)


def test_marker_format_is_pinned_and_run_id_cannot_break_it():
    from harness_github.service import verdict_marker

    assert verdict_marker("run-1") == MARKER  # same literal the section4 builder emits
    weird = verdict_marker("x --> <img src=//e/p> <!--")
    assert weird.count("-->") == 1 and "<img" not in weird
