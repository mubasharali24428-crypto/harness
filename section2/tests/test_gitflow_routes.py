"""A4-S2 HTTP tests: run branches, governed draft PRs, reviewer routing — fake GitHub only."""

import base64
import hashlib
import json

import httpx
import pytest
from conftest import auth, seed_user_and_project
from sqlalchemy import text

from harness_github.client import GITHUB_API, MergeGuardViolation, _guard
from tests.test_service import connect_user

BASE = "/api/v1/developer-workflow/projects/p1/gitflow"
CODEOWNERS = "* @org/core\n/docs/ @docs-owner\n/src/ @founder-dev @reviewer1\n"


class FakeGH:
    def __init__(self, codeowners=CODEOWNERS):
        self.codeowners = codeowners
        self.refs = {"heads/main": "base111"}
        self.prs: list[dict] = []
        self.requests: list[tuple[str, str, dict | None]] = []
        self.reviewer_requests: list[dict] = []
        self.force_non_draft = False
        self.create_pr_conflict = False

    def transport(self):
        def handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content) if req.content else None
            url = str(req.url)
            path = req.url.path
            self.requests.append((req.method, url, body))
            if path == "/user":
                return httpx.Response(200, json={"login": "founder-dev"})
            if "/git/ref/heads/" in path:
                key = "heads/" + path.split("/git/ref/heads/", 1)[1]
                if key in self.refs:
                    return httpx.Response(
                        200, json={"object": {"type": "commit", "sha": self.refs[key]}}
                    )
                return httpx.Response(404, json={"message": "nf"})
            if path.endswith("/git/refs") and req.method == "POST":
                key = body["ref"].removeprefix("refs/")
                if key in self.refs:
                    return httpx.Response(422, json={"message": "Reference already exists"})
                self.refs[key] = body["sha"]
                return httpx.Response(201, json={"ref": body["ref"]})
            if "/contents/" in path:
                name = path.split("/contents/", 1)[1]
                if name == ".github/CODEOWNERS" and self.codeowners is not None:
                    content = base64.b64encode(self.codeowners.encode()).decode()
                    return httpx.Response(200, json={"path": name, "sha": "s", "content": content})
                return httpx.Response(404, json={"message": "nf"})
            if path.endswith("/requested_reviewers") and req.method == "POST":
                self.reviewer_requests.append(body)
                return httpx.Response(201, json={})
            if path.endswith("/pulls") and req.method == "GET":
                head = req.url.params.get("head", "")
                found = [
                    p
                    for p in self.prs
                    if head.endswith(":" + p["head"]["ref"]) and p["state"] == "open"
                ]
                return httpx.Response(200, json=found)
            if path.endswith("/pulls") and req.method == "POST":
                if self.create_pr_conflict:
                    self.create_pr_conflict = False
                    self.prs.append(self._pr(body, draft=True))
                    return httpx.Response(422, json={"message": "A pull request already exists"})
                pr = self._pr(body, draft=not self.force_non_draft)
                self.prs.append(pr)
                return httpx.Response(201, json=pr)
            return httpx.Response(404, json={"message": "nf"})

        return httpx.MockTransport(handler)

    def _pr(self, body, draft):
        n = len(self.prs) + 1
        return {
            "number": n,
            "html_url": f"https://github.com/org/repo/pull/{n}",
            "draft": draft,
            "state": "open",
            "merged": False,
            "head": {"ref": body["head"]},
            "base": {"ref": body["base"]},
            "title": body["title"],
            "body": body["body"],
        }

    def merge_attempted(self):
        return any(
            "merge" in u.lower().split("/repos/")[-1] and m != "GET" for m, u, _ in self.requests
        )


@pytest.fixture
async def gh(client, db):
    await seed_user_and_project(db)
    await connect_user(db)
    fake = FakeGH()
    client.app.state.transport = fake.transport()
    client.gh = fake
    return client


async def add_user(db, uid, role, token, team="t1"):
    async with db() as s:
        await s.execute(
            text(
                f"INSERT INTO users (id, email, name, is_active)"
                f" VALUES ('{uid}','{uid}@x.com','{uid}',1)"
            )
        )
        await s.execute(
            text(
                "INSERT INTO team_members (id, team_id, user_id, display_name, member_type, role)"
                f" VALUES ('m-{uid}','{team}','{uid}','{uid}','human','{role}')"
            )
        )
        await s.execute(
            text(
                "INSERT INTO auth_sessions (token_hash, user_id, team_id, expires_at)"
                " VALUES (:h,:u,:t,'2100-01-01T00:00:00Z')"
            ),
            {"h": hashlib.sha256(token.encode()).hexdigest(), "u": uid, "t": team},
        )
        await s.commit()


CHANGES = [
    {"path": "src/app.py", "status": "M", "additions": 4, "deletions": 1},
    {"path": "docs/guide.md", "status": "A", "additions": 10},
]


async def test_run_branch_create_and_idempotent_reuse(gh):
    r = await gh.post(
        f"{BASE}/run-branch", json={"runId": "run-1", "baseRef": "main"}, headers=auth()
    )
    assert r.status_code == 200 and r.json() == {
        "branch": "agent/run-1",
        "baseRef": "main",
        "sha": "base111",
        "reused": False,
    }
    again = await gh.post(f"{BASE}/run-branch", json={"runId": "run-1"}, headers=auth())
    assert again.json() == {"branch": "agent/run-1", "reused": True}
    assert sum(1 for m, u, _ in gh.gh.requests if m == "POST" and u.endswith("/git/refs")) == 1


async def test_run_branch_validates_input_and_role_and_tenant(gh, db):
    for body in ({"runId": "../x"}, {"runId": "r", "baseRef": "--evil"}, {}):
        assert (await gh.post(f"{BASE}/run-branch", json=body, headers=auth())).status_code == 422
    await add_user(db, "v1", "viewer", "viewer-tok")
    r = await gh.post(
        f"{BASE}/run-branch", json={"runId": "r1"}, headers={"Authorization": "Bearer viewer-tok"}
    )
    assert r.status_code == 403 and r.json()["detail"]["error"]["code"] == "ROLE_DENIED"
    async with db() as s:
        await s.execute(text("INSERT INTO teams (id, name) VALUES ('t2','other')"))
        await s.commit()
    await add_user(db, "x1", "admin", "other-tok", team="t2")
    r = await gh.post(
        f"{BASE}/run-branch", json={"runId": "r1"}, headers={"Authorization": "Bearer other-tok"}
    )
    assert r.status_code == 404  # project belongs to another team
    assert (await gh.post(f"{BASE}/run-branch", json={"runId": "r1"})).status_code in (401, 403)


async def test_open_pr_end_to_end_draft_with_evidence_and_routed_reviewers(gh):
    await gh.post(f"{BASE}/run-branch", json={"runId": "run-1"}, headers=auth())
    body = {
        "runId": "run-1",
        "goal": "Add retry to the client. fixes #9 cc @someone",
        "baseBranch": "main",
        "changes": [*CHANGES, {"path": "README.md"}],
        "evidence": [{"command": "pytest -q", "status": "passed", "summary": "40 passed"}],
    }
    r = await gh.post(f"{BASE}/pull-requests", json=body, headers=auth())
    assert r.status_code == 200, r.text
    out = r.json()
    pr = gh.gh.prs[0]
    assert pr["draft"] is True and pr["head"]["ref"] == "agent/run-1"
    assert "`pytest -q` — **PASS**: 40 passed" in pr["body"]
    assert "fixes #9" not in pr["body"] and "@someone" not in pr["body"]
    # author (founder-dev) never requested; docs-owner + reviewer1 + team routed
    req = gh.gh.reviewer_requests[0]
    assert "founder-dev" not in req["reviewers"] and "reviewer1" in req["reviewers"]
    assert "docs-owner" in req["reviewers"] and "org/core" in req["team_reviewers"]
    assert out["pullRequest"]["reused"] is False and out["reviewersRequested"]["users"]
    assert not gh.gh.merge_attempted()


async def test_open_pr_is_idempotent_and_never_repings(gh):
    await gh.post(f"{BASE}/run-branch", json={"runId": "run-1"}, headers=auth())
    body = {"runId": "run-1", "goal": "g", "changes": CHANGES}
    a = (await gh.post(f"{BASE}/pull-requests", json=body, headers=auth())).json()
    b = (await gh.post(f"{BASE}/pull-requests", json=body, headers=auth())).json()
    assert (
        a["pullRequest"]["number"] == b["pullRequest"]["number"]
        and b["pullRequest"]["reused"] is True
    )
    assert len(gh.gh.prs) == 1 and len(gh.gh.reviewer_requests) == 1


async def test_open_pr_survives_a_lost_creation_race(gh):
    await gh.post(f"{BASE}/run-branch", json={"runId": "run-1"}, headers=auth())
    gh.gh.create_pr_conflict = True
    r = await gh.post(
        f"{BASE}/pull-requests",
        json={"runId": "run-1", "goal": "g", "changes": CHANGES},
        headers=auth(),
    )
    assert (
        r.status_code == 200 and r.json()["pullRequest"]["reused"] is True and len(gh.gh.prs) == 1
    )


async def test_open_pr_without_evidence_says_so(gh):
    await gh.post(f"{BASE}/run-branch", json={"runId": "run-1"}, headers=auth())
    await gh.post(
        f"{BASE}/pull-requests",
        json={"runId": "run-1", "goal": "g", "changes": CHANGES},
        headers=auth(),
    )
    assert "No verification evidence was recorded" in gh.gh.prs[0]["body"]


async def test_open_pr_requires_existing_run_branch_and_writer_role(gh, db):
    r = await gh.post(
        f"{BASE}/pull-requests",
        json={"runId": "ghost", "goal": "g", "changes": CHANGES},
        headers=auth(),
    )
    assert r.status_code == 404 and r.json()["detail"]["error"]["code"] == "BRANCH_NOT_FOUND"
    await add_user(db, "v1", "viewer", "viewer-tok")
    r = await gh.post(
        f"{BASE}/pull-requests",
        json={"runId": "ghost", "goal": "g", "changes": CHANGES},
        headers={"Authorization": "Bearer viewer-tok"},
    )
    assert r.status_code == 403
    assert gh.gh.prs == []


async def test_non_draft_response_from_provider_is_refused(gh):
    await gh.post(f"{BASE}/run-branch", json={"runId": "run-1"}, headers=auth())
    gh.gh.force_non_draft = True
    r = await gh.post(
        f"{BASE}/pull-requests",
        json={"runId": "run-1", "goal": "g", "changes": CHANGES},
        headers=auth(),
    )
    assert r.status_code == 502 and r.json()["detail"]["error"]["code"] == "PROVIDER_NOT_DRAFT"
    assert gh.gh.reviewer_requests == []  # no reviewers pinged on a non-draft


async def test_bad_changes_payload_is_422(gh):
    for changes in ("x", [{"nopath": 1}], [{"path": "a"}] * 501):
        r = await gh.post(
            f"{BASE}/pull-requests",
            json={"runId": "run-1", "goal": "g", "changes": changes},
            headers=auth(),
        )
        assert r.status_code == 422


async def test_reviewer_routing_endpoint_reports_gaps(gh):
    gh.gh.codeowners = "/src/ @reviewer1\n"
    r = await gh.post(
        f"{BASE}/reviewers/route", json={"changes": CHANGES, "baseBranch": "main"}, headers=auth()
    )
    out = r.json()
    assert (
        out["codeownersFound"]
        and out["reviewers"] == ["reviewer1"]
        and out["unowned"] == ["docs/guide.md"]
    )
    gh.gh.codeowners = None
    out = (
        await gh.post(f"{BASE}/reviewers/route", json={"changes": CHANGES}, headers=auth())
    ).json()
    assert out["codeownersFound"] is False and set(out["unowned"]) == {
        "src/app.py",
        "docs/guide.md",
    }


async def test_commit_draft_endpoint_is_pure_and_tenant_checked(gh):
    n = len(gh.gh.requests)
    r = await gh.post(
        f"{BASE}/commit-draft",
        json={"runId": "r1", "goal": "Do thing", "changes": CHANGES},
        headers=auth(),
    )
    assert r.status_code == 200 and r.json()["subject"].startswith("chore: Do thing")
    assert len(gh.gh.requests) == n  # no provider call
    other = await gh.post(
        "/api/v1/developer-workflow/projects/nope/gitflow/commit-draft",
        json={"runId": "r1", "changes": CHANGES},
        headers=auth(),
    )
    assert other.status_code == 404


async def test_no_merge_route_exists_anywhere(gh):
    paths = set(
        gh.app.openapi()["paths"]
    )  # FastAPI >=0.14x nests routers; OpenAPI is the flat truth
    gitflow = [p for p in paths if "/gitflow/" in p]
    assert len(gitflow) == 4
    assert not [p for p in paths if "merge" in p.lower()]
    for p, ops in gh.app.openapi()["paths"].items():
        if "/gitflow/" in p:
            assert set(ops) == {"post"}  # no PUT/DELETE/PATCH on the Git surface


def test_merge_guard_is_precise_not_a_substring_match():
    _guard("GET", f"{GITHUB_API}/repos/o/r/git/ref/heads/merge-fix", None)  # legitimate branch
    _guard("POST", f"{GITHUB_API}/repos/o/r/pulls/3/requested_reviewers", {})
    for url in (
        "/repos/o/r/pulls/3/merge",
        "/repos/o/r/merges",
        "/repos/o/r/pulls/3/merge?x=1",
        "/repos/o/r/merge-upstream",
    ):
        with pytest.raises(MergeGuardViolation):
            _guard("PUT" if "pulls" in url else "POST", GITHUB_API + url, None)


async def test_client_sends_ref_and_recursive_as_query_params():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if "/contents/" in req.url.path:
            return httpx.Response(
                200, json={"path": "a b.py", "sha": "s", "content": base64.b64encode(b"x").decode()}
            )
        return httpx.Response(200, json={"tree": []})

    from harness_github.client import GitHubClient

    c = GitHubClient("t", httpx.MockTransport(handler))
    await c.read_file("o/r", "feature/x", "dir/a b.py")
    await c.repo_tree("o/r", "feature/x")
    assert "ref=feature%2Fx" in seen[0] and "/contents/dir/a%20b.py" in seen[0]
    assert "recursive=1" in seen[1] and "/git/trees/feature%2Fx" in seen[1]
