import httpx
from harness_contracts import KeyVault
from sqlalchemy.ext.asyncio import AsyncSession

from .client import GitHubClient
from .store import connect_repo, get_repo_full_name, get_user_token


class GitHubService:
    """The seven contract functions. Merge is ALWAYS a manual human action on GitHub."""

    def __init__(
        self,
        db: AsyncSession,
        vault: KeyVault,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._db = db
        self._vault = vault
        self._transport = transport

    async def _client_for(self, user_id: str) -> tuple[GitHubClient, str]:
        token = await get_user_token(self._db, self._vault, user_id)
        return GitHubClient(token, self._transport), token

    async def connectRepo(self, project_id: str, team_id: str, repo_full_name: str) -> dict:
        await connect_repo(self._db, project_id, team_id, repo_full_name)
        return {"projectId": project_id, "repoFullName": repo_full_name}

    async def readRepoTree(self, user_id: str, project_id: str, ref: str) -> list[dict]:
        client, _ = await self._client_for(user_id)
        repo = await get_repo_full_name(self._db, project_id)
        return await client.repo_tree(repo, ref)

    async def readFile(self, user_id: str, project_id: str, ref: str, path: str) -> dict:
        client, _ = await self._client_for(user_id)
        repo = await get_repo_full_name(self._db, project_id)
        return await client.read_file(repo, ref, path)

    async def createBranch(
        self, user_id: str, project_id: str, base_ref: str, new_branch: str
    ) -> dict:
        client, _ = await self._client_for(user_id)
        repo = await get_repo_full_name(self._db, project_id)
        sha = await client.create_branch(repo, base_ref, new_branch)
        return {"branch": new_branch, "baseRef": base_ref, "sha": sha}

    async def commitFiles(
        self, user_id: str, project_id: str, branch: str, message: str, files: list[dict]
    ) -> dict:
        client, _ = await self._client_for(user_id)
        repo = await get_repo_full_name(self._db, project_id)
        parent_sha = await client.get_ref_sha(repo, branch)
        entries = [
            {"path": f["path"], "blobSha": await client.create_blob(repo, f["content"])}
            for f in files
        ]
        tree_sha = await client.create_tree(repo, parent_sha, entries)
        commit_sha = await client.create_commit(repo, message, tree_sha, parent_sha)
        await client.update_ref_fast_forward(repo, branch, commit_sha)
        return {"commitSha": commit_sha, "branch": branch, "files": [f["path"] for f in files]}

    async def openDraftPR(
        self, user_id: str, project_id: str, branch: str, base_branch: str, title: str, body_md: str
    ) -> dict:
        client, _ = await self._client_for(user_id)
        repo = await get_repo_full_name(self._db, project_id)
        pr = await client.open_draft_pr(repo, branch, base_branch, title, body_md)
        if not pr["draft"]:
            raise RuntimeError("GitHub returned a non-draft PR; refusing to accept")
        return pr

    async def getPRStatus(self, user_id: str, project_id: str, pr_number: int) -> dict:
        client, _ = await self._client_for(user_id)
        repo = await get_repo_full_name(self._db, project_id)
        status = await client.get_pr(repo, pr_number)
        if status["merged"]:
            raise RuntimeError("PR already merged; merge is a manual GitHub action")
        return status

    async def getPRDiff(self, user_id: str, project_id: str, pr_number: int) -> dict:
        client, _ = await self._client_for(user_id)
        repo = await get_repo_full_name(self._db, project_id)
        diff_text = await client.get_pr_diff(repo, pr_number)
        return {"projectId": project_id, "prNumber": pr_number, "diff": diff_text}
