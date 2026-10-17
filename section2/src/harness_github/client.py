import re
from urllib.parse import quote

import httpx

GITHUB_API = "https://api.github.com"
GITHUB_WEB = "https://github.com"

# Precise on purpose: a bare "/merge" substring also blocked legitimate refs/paths such as
# a branch named "merge-fix" while the real merge endpoints stay blocked.
_MERGE_PATH = re.compile(r"(/pulls/\d+/merge|/merges|/merge-upstream|/auto-merge)/?$")
FORBIDDEN_URL_PARTS = ("force-push",)
MERGE_METHODS = {"PUT", "DELETE"}


class MergeGuardViolation(RuntimeError):
    """Raised when any call would merge or force-push. Hard rule, enforced in code."""


def _guard(method: str, url: str, json_body: dict | None) -> None:
    path = url.replace(GITHUB_API, "").replace(GITHUB_WEB, "").lower().split("?", 1)[0]
    if _MERGE_PATH.search(path) or any(p in path for p in FORBIDDEN_URL_PARTS):
        raise MergeGuardViolation(f"forbidden GitHub operation attempted: {method} {path}")
    if json_body and ("force" in json_body or json_body.get("force") is True):
        raise MergeGuardViolation("force flag is forbidden in this service")
    if method == "DELETE" and "/git/refs/" in path:
        raise MergeGuardViolation("branch deletion is not allowed from this service")
    if method in MERGE_METHODS and "/pulls/" in path:
        raise MergeGuardViolation("PRs are never merged by this service")


class GitHubClient:
    """Thin REST wrapper. Holds the token; never logs it, never returns it."""

    def __init__(self, token: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._transport = transport

    async def _request(
        self,
        method: str,
        url: str,
        json_body: dict | None = None,
        accept: str | None = None,
        params: dict | None = None,
    ) -> httpx.Response:
        _guard(method, url, json_body)
        headers = dict(self._headers)
        if accept:
            headers["Accept"] = accept
        async with httpx.AsyncClient(
            base_url=GITHUB_API, headers=headers, transport=self._transport, timeout=30
        ) as client:
            resp = await client.request(method, url, json=json_body, params=params)
        return resp

    async def get_pr_diff(self, repo: str, number: int) -> str:
        resp = await self._request(
            "GET", f"/repos/{repo}/pulls/{number}", accept="application/vnd.github.v3.diff"
        )
        resp.raise_for_status()
        return resp.text

    async def get_user(self) -> dict:
        resp = await self._request("GET", "/user")
        resp.raise_for_status()
        return {"login": resp.json()["login"]}

    async def repo_tree(self, repo: str, ref: str) -> list[dict]:
        resp = await self._request(
            "GET", f"/repos/{repo}/git/trees/{quote(ref, safe='')}", params={"recursive": "1"}
        )
        resp.raise_for_status()
        data = resp.json()
        items = [
            {"path": i["path"], "type": i["type"], "sha": i["sha"], "size": i.get("size")}
            for i in data.get("tree", [])
            if i["type"] == "blob"
        ]
        return items

    async def read_file(self, repo: str, ref: str, path: str) -> dict:
        import base64

        resp = await self._request(
            "GET", f"/repos/{repo}/contents/{quote(path, safe='/')}", params={"ref": ref}
        )
        resp.raise_for_status()
        data = resp.json()
        content = base64.b64decode(data["content"]).decode() if data.get("content") else ""
        return {"path": data["path"], "sha": data["sha"], "content": content}

    async def get_ref_sha(self, repo: str, branch: str) -> str:
        resp = await self._request("GET", f"/repos/{repo}/git/ref/heads/{branch}")
        resp.raise_for_status()
        obj = resp.json()["object"]
        if obj["type"] != "commit":
            raise LookupError(f"ref {branch} does not point at a commit")
        return obj["sha"]

    async def create_branch(self, repo: str, base_ref: str, new_branch: str) -> str:
        sha = await self.get_ref_sha(repo, base_ref)
        resp = await self._request(
            "POST",
            f"/repos/{repo}/git/refs",
            {"ref": f"refs/heads/{new_branch}", "sha": sha},
        )
        resp.raise_for_status()
        return sha

    async def create_blob(self, repo: str, content: str) -> str:
        resp = await self._request(
            "POST", f"/repos/{repo}/git/blobs", {"content": content, "encoding": "utf-8"}
        )
        resp.raise_for_status()
        return resp.json()["sha"]

    async def create_tree(self, repo: str, base_tree: str, entries: list[dict]) -> str:
        tree = [
            {"path": e["path"], "mode": "100644", "type": "blob", "sha": e["blobSha"]}
            for e in entries
        ]
        resp = await self._request(
            "POST", f"/repos/{repo}/git/trees", {"base_tree": base_tree, "tree": tree}
        )
        resp.raise_for_status()
        return resp.json()["sha"]

    async def create_commit(self, repo: str, message: str, tree_sha: str, parent_sha: str) -> str:
        resp = await self._request(
            "POST",
            f"/repos/{repo}/git/commits",
            {"message": message, "tree": tree_sha, "parents": [parent_sha]},
        )
        resp.raise_for_status()
        return resp.json()["sha"]

    async def update_ref_fast_forward(self, repo: str, branch: str, new_sha: str) -> None:
        path = f"/repos/{repo}/git/refs/heads/{branch}"
        url = GITHUB_API + path
        _guard("PATCH", url, None)
        async with httpx.AsyncClient(
            base_url=GITHUB_API, headers=self._headers, transport=self._transport, timeout=30
        ) as client:
            resp = await client.patch(url, json={"sha": new_sha})
        resp.raise_for_status()

    async def open_draft_pr(
        self, repo: str, branch: str, base: str, title: str, body_md: str
    ) -> dict:
        resp = await self._request(
            "POST",
            f"/repos/{repo}/pulls",
            {"title": title, "head": branch, "base": base, "body": body_md, "draft": True},
        )
        resp.raise_for_status()
        pr = resp.json()
        return {
            "number": pr["number"],
            "url": pr["html_url"],
            "draft": pr["draft"],
            "state": pr["state"],
        }

    async def get_pr(self, repo: str, number: int) -> dict:
        resp = await self._request("GET", f"/repos/{repo}/pulls/{number}")
        resp.raise_for_status()
        pr = resp.json()
        return {
            "number": pr["number"],
            "state": pr["state"],
            "draft": pr["draft"],
            "merged": pr["merged"],
            "mergeable": pr.get("mergeable"),
            "url": pr["html_url"],
            "headBranch": pr["head"]["ref"],
            "baseBranch": pr["base"]["ref"],
        }

    async def find_open_pr(self, repo: str, branch: str) -> dict | None:
        owner = repo.split("/", 1)[0]
        resp = await self._request(
            "GET",
            f"/repos/{repo}/pulls",
            params={"head": f"{owner}:{branch}", "state": "open", "per_page": "10"},
        )
        resp.raise_for_status()
        for pr in resp.json():
            if pr.get("head", {}).get("ref") == branch and pr.get("state") == "open":
                return {
                    "number": pr["number"],
                    "url": pr["html_url"],
                    "draft": pr.get("draft", False),
                    "state": pr["state"],
                }
        return None

    async def request_reviewers(
        self, repo: str, number: int, reviewers: list[str], team_reviewers: list[str]
    ) -> dict:
        resp = await self._request(
            "POST",
            f"/repos/{repo}/pulls/{number}/requested_reviewers",
            {"reviewers": reviewers, "team_reviewers": team_reviewers},
        )
        resp.raise_for_status()
        return {"requested": {"users": reviewers, "teams": team_reviewers}}
