import json
import re

import httpx
from harness_contracts import KeyVault
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .client import GitHubClient
from .pr_open import record_pr_opened
from .store import (
    connect_repo,
    get_repo_full_name,
    get_user_token,
    read_write_key,
    release_write_key,
    reserve_write_key,
    set_writes_enabled,
    store_write_result,
    writes_enabled,
)


class StagedChangesPendingError(RuntimeError):
    pass


class StagingGateUnavailableError(RuntimeError):
    pass


class GitHubWritesDisabledError(RuntimeError):
    pass


class WriteInProgressError(RuntimeError):
    pass


_RUN_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")
_VERDICT_MARKER_PREFIX = "<!-- harness-verdict:v1 run="


def verdict_marker(run_id: str) -> str:
    """Server-derived marker for a run's verdict comment. Same literal format as the
    orchestrator's builder (section4 verdict_comment.verdict_marker); the two sections
    cannot import each other, so tests on both sides pin the format."""
    safe = _RUN_ID_UNSAFE.sub("_", str(run_id or "unknown"))[:200] or "unknown"
    return f"{_VERDICT_MARKER_PREFIX}{safe} -->"


async def _post_or_update(client, repo, pr_number, body_md, known_id, marker):
    """Update the run's comment (stored id, else found by exact marker), else create it.
    A comment deleted on GitHub (404 on update) is recreated."""
    if known_id is None:
        for c in await client.list_pr_comments(repo, pr_number):
            if marker in (c.get("body") or ""):
                known_id = c["id"]
                break
    if known_id is not None:
        try:
            return await client.update_comment(repo, known_id, body_md), "updated"
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
    return await client.comment_on_pr(repo, pr_number, body_md), "created"


class GitHubService:
    """The seven contract functions. Merge is ALWAYS a manual human action on GitHub."""

    def __init__(
        self,
        db: AsyncSession,
        vault: KeyVault,
        transport: httpx.AsyncBaseTransport | None = None,
        bus=None,
    ):
        self._db = db
        self._vault = vault
        self._transport = transport
        # Duck-typed S1 EventBus (M12-W2 E4a): when wired, opening a PR for a
        # run persists prId + emits pr_opened. None keeps the legacy behavior.
        self._bus = bus

    async def _client_for(self, user_id: str) -> tuple[GitHubClient, str]:
        token = await get_user_token(self._db, self._vault, user_id)
        return GitHubClient(token, self._transport), token

    async def connectRepo(self, project_id: str, team_id: str, repo_full_name: str) -> dict:
        await connect_repo(self._db, project_id, team_id, repo_full_name)
        return {"projectId": project_id, "repoFullName": repo_full_name}

    async def writesEnabled(self, project_id: str, *, team_id: str) -> dict:
        enabled = await writes_enabled(self._db, project_id, team_id)
        return {"projectId": project_id, "githubWritesEnabled": enabled}

    async def setWritesEnabled(self, project_id: str, enabled: bool, *, team_id: str) -> dict:
        await set_writes_enabled(self._db, project_id, team_id, enabled)
        return {"projectId": project_id, "githubWritesEnabled": enabled}

    async def _require_writes(self, project_id: str, team_id: str) -> None:
        if not await writes_enabled(self._db, project_id, team_id):
            raise GitHubWritesDisabledError(
                "GitHub writes are off for this project; a team admin must turn them on"
            )

    async def readRepoTree(
        self, user_id: str, project_id: str, ref: str, *, team_id: str
    ) -> list[dict]:
        client, _ = await self._client_for(user_id)
        repo = await get_repo_full_name(self._db, project_id, team_id)
        return await client.repo_tree(repo, ref)

    async def readFile(
        self, user_id: str, project_id: str, ref: str, path: str, *, team_id: str
    ) -> dict:
        client, _ = await self._client_for(user_id)
        repo = await get_repo_full_name(self._db, project_id, team_id)
        return await client.read_file(repo, ref, path)

    async def createBranch(
        self, user_id: str, project_id: str, base_ref: str, new_branch: str, *, team_id: str
    ) -> dict:
        await self._require_writes(project_id, team_id)
        client, _ = await self._client_for(user_id)
        repo = await get_repo_full_name(self._db, project_id, team_id)
        sha = await client.create_branch(repo, base_ref, new_branch)
        return {"branch": new_branch, "baseRef": base_ref, "sha": sha}

    async def commitFiles(
        self,
        user_id: str,
        project_id: str,
        branch: str,
        message: str,
        files: list[dict],
        *,
        team_id: str,
    ) -> dict:
        await self._require_writes(project_id, team_id)
        client, _ = await self._client_for(user_id)
        repo = await get_repo_full_name(self._db, project_id, team_id)
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
        self,
        user_id: str,
        project_id: str,
        branch: str,
        base_branch: str,
        title: str,
        body_md: str,
        *,
        team_id: str,
    ) -> dict:
        await self._require_writes(project_id, team_id)
        from .pr_open import run_id_from_branch

        repo = await get_repo_full_name(self._db, project_id, team_id)
        run_id = run_id_from_branch(branch)
        if run_id is not None:
            try:
                pending = (
                    await self._db.execute(
                        text(
                            "SELECT a.id FROM high_impact_approvals a "
                            "JOIN agent_runs r ON r.id = a.run_id "
                            "WHERE r.id = :r AND r.project_id = :p "
                            "AND a.requester_user_id = 'system:staging' "
                            "AND a.state IN ('pending','approved','executing') LIMIT 1"
                        ),
                        {"r": run_id, "p": project_id},
                    )
                ).first()
            except Exception as exc:
                raise StagingGateUnavailableError("Staged-change gate is unavailable") from exc
            if pending:
                raise StagedChangesPendingError("Apply staged changes before opening a PR")
        client, _ = await self._client_for(user_id)
        pr = await client.open_draft_pr(repo, branch, base_branch, title, body_md)
        if not pr["draft"]:
            raise RuntimeError("GitHub returned a non-draft PR; refusing to accept")
        # M12-W2 E4a (W2-C2): persist the PR against its run + announce it.
        # The runId arrives via the branch name (agent/<runId> convention);
        # absent one there is no run to link and the response keeps its
        # legacy shape (plus prId when bound). Never fails the PR open.
        emitted = None
        try:
            from .pr_open import run_id_from_branch

            run_id = run_id_from_branch(branch)
            if run_id is not None:
                emitted = await record_pr_opened(self._db, self._bus, project_id, run_id, pr)
        except Exception:  # noqa: BLE001 — producer must never fail the open
            pass
        if emitted is not None:
            return {**pr, "prId": emitted["prId"]}
        return pr

    async def listIssues(self, user_id: str, project_id: str, state: str, *, team_id: str) -> dict:
        client, _ = await self._client_for(user_id)
        repo = await get_repo_full_name(self._db, project_id, team_id)
        return {"projectId": project_id, "items": await client.list_issues(repo, state)}

    async def createIssue(
        self,
        user_id: str,
        project_id: str,
        title: str,
        body_md: str,
        idempotency_key: str,
        *,
        team_id: str,
    ) -> dict:
        await self._require_writes(project_id, team_id)
        if not await reserve_write_key(self._db, project_id, idempotency_key, "create_issue"):
            stored = await read_write_key(self._db, project_id, idempotency_key)
            if stored is None:
                raise WriteInProgressError(
                    "an earlier request with this Idempotency-Key is still running"
                )
            return json.loads(stored)
        try:
            client, _ = await self._client_for(user_id)
            repo = await get_repo_full_name(self._db, project_id, team_id)
            issue = await client.create_issue(repo, title, body_md)
        except BaseException:
            # Free the key so the caller can retry; nothing was created on GitHub.
            await release_write_key(self._db, project_id, idempotency_key)
            raise
        await store_write_result(self._db, project_id, idempotency_key, json.dumps(issue))
        return issue

    async def getPRStatus(
        self, user_id: str, project_id: str, pr_number: int, *, team_id: str
    ) -> dict:
        client, _ = await self._client_for(user_id)
        repo = await get_repo_full_name(self._db, project_id, team_id)
        status = await client.get_pr(repo, pr_number)
        if status["merged"]:
            raise RuntimeError("PR already merged; merge is a manual GitHub action")
        return status

    async def getPRDiff(
        self, user_id: str, project_id: str, pr_number: int, *, team_id: str
    ) -> dict:
        client, _ = await self._client_for(user_id)
        repo = await get_repo_full_name(self._db, project_id, team_id)
        diff_text = await client.get_pr_diff(repo, pr_number)
        return {"projectId": project_id, "prNumber": pr_number, "diff": diff_text}

    async def postVerdictComment(
        self,
        user_id: str,
        project_id: str,
        pr_number: int,
        body_md: str,
        *,
        run_id: str,
        team_id: str,
        marker: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        """P-15 (I-02): post the advisory verdict comment for a run's PR.

        Gated by the team-admin write opt-in and tenant-scoped via the project lookup.
        The run must be linked to this PR in this project (pull_requests), the marker is
        derived here from the run id (a caller-supplied marker must equal it), and the body
        must carry it, so only that run's own comment is ever created or updated.
        Idempotent through github_write_keys. The body is built by the orchestrator side;
        this service never imports section4. The bot only comments; a human merges.
        """
        await self._require_writes(project_id, team_id)
        repo = await get_repo_full_name(self._db, project_id, team_id)
        expected = verdict_marker(run_id)
        if marker and marker != expected:
            raise ValueError("marker does not match the run's verdict marker")
        if expected not in body_md:
            raise ValueError("comment body must carry the run's verdict marker")
        linked = (
            await self._db.execute(
                text(
                    "SELECT 1 FROM pull_requests WHERE project_id = :p AND run_id = :r"
                    " AND github_pr_number = :n LIMIT 1"
                ),
                {"p": project_id, "r": run_id, "n": int(pr_number)},
            )
        ).first()
        if linked is None:
            raise LookupError("no run with this id is linked to that PR in this project")
        idem = idempotency_key or f"verdict:{run_id}:{pr_number}"
        client, _ = await self._client_for(user_id)
        if not await reserve_write_key(self._db, project_id, idem, "verdict_comment"):
            stored = await read_write_key(self._db, project_id, idem)
            if stored is None:
                raise WriteInProgressError(
                    "an earlier verdict-comment request with this key is still running"
                )
            prior = json.loads(stored)
            comment, action = await _post_or_update(
                client, repo, pr_number, body_md, prior["commentId"], expected
            )
            result = {
                "commentId": comment["id"], "url": comment["url"], "action": action,
                "prNumber": pr_number, "runId": run_id,
            }
            await store_write_result(self._db, project_id, idem, json.dumps(result))
            return result
        try:
            comment, action = await _post_or_update(
                client, repo, pr_number, body_md, None, expected
            )
        except BaseException:
            # Free the key so the caller can retry; nothing durable was created.
            await release_write_key(self._db, project_id, idem)
            raise
        result = {
            "commentId": comment["id"], "url": comment["url"], "action": action,
            "prNumber": pr_number, "runId": run_id,
        }
        await store_write_result(self._db, project_id, idem, json.dumps(result))
        return result
