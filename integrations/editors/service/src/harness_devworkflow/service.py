"""Developer-workflow service: orchestration over lane workspace + baseline services."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, ClassVar

from harness_devworkflow import diffs
from harness_devworkflow.checks import compute_merge_readiness
from harness_devworkflow.clients import (
    ContextClient,
    IdentityClient,
    OrchestratorClient,
    SandboxClient,
    UpstreamError,
)
from harness_devworkflow.config import Config
from harness_devworkflow.paths import (
    ForbiddenPathError,
    InvalidPathError,
    check_path,
    normalize_rel_path,
)
from harness_devworkflow.store import Store, new_id
from harness_devworkflow.workspace import WorkspaceError, WorkspaceManager

_PRINCIPAL_CACHE_TTL_S = 30.0


class LaneError(RuntimeError):
    """A lane-level failure with an HTTP status and machine code."""

    def __init__(self, code: str, message: str, status: int = 400, detail: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail


# Mirrors harness_contracts.roles.READ_ONLY (cross-section imports are limited to contracts
# and this lane stays dependency-light); reads and review comments stay open to every role.
_READ_ONLY_ROLES = {"viewer", "guest"}


def require_writer(principal: dict[str, Any]) -> None:
    if str(principal.get("role", "")).lower() in _READ_ONLY_ROLES or not principal.get("role"):
        raise LaneError("ROLE_DENIED", "your role is read-only for this operation", 403)


def _not_found(code: str, message: str) -> LaneError:
    return LaneError(code, message, status=404)


class DevWorkflowService:
    def __init__(
        self,
        *,
        config: Config,
        store: Store,
        workspaces: WorkspaceManager,
        identity: IdentityClient | None,
        sandbox: SandboxClient | None,
        orchestrator: OrchestratorClient | None,
        context: ContextClient | None,
    ):
        self.config = config
        self.store = store
        self.workspaces = workspaces
        self.identity = identity
        self.sandbox = sandbox
        self.orchestrator = orchestrator
        self.context = context
        self.extras: Any = None  # A4-S5 library/feedback/status; set by build_service
        self._principal_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
        self._projects_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    # -- identity ------------------------------------------------------------

    @property
    def identity_configured(self) -> bool:
        return self.identity is not None

    async def resolve_principal(self, token: str) -> dict[str, Any] | None:
        if not token or self.identity is None:
            return None
        cached = self._principal_cache.get(token)
        now = time.monotonic()
        if cached and cached[0] > now:
            return cached[1]
        principal = await self.identity.me(token)
        self._principal_cache[token] = (now + _PRINCIPAL_CACHE_TTL_S, principal)
        return principal

    async def _projects_for(self, token: str) -> list[dict[str, Any]]:
        assert self.identity is not None
        cached = self._projects_cache.get(token)
        now = time.monotonic()
        if cached and cached[0] > now:
            return cached[1]
        projects = await self.identity.list_projects(token)
        self._projects_cache[token] = (now + _PRINCIPAL_CACHE_TTL_S, projects)
        return projects

    async def require_project(self, token: str, team_id: str, project_id: str) -> dict[str, Any]:
        """Team-scoped project ownership check; unknown and foreign look identical."""
        if self.identity is None:
            raise LaneError("CAPABILITY_UNAVAILABLE", "identity is not configured", 503)
        projects = await self._projects_for(token)
        for project in projects:
            if project.get("id") == project_id and project.get("teamId", team_id) == team_id:
                return project
        raise _not_found("PROJECT_NOT_FOUND", f"unknown project '{project_id}'")

    async def require_run(self, token: str, run_id: str) -> dict[str, Any]:
        if self.orchestrator is None:
            raise LaneError(
                "CAPABILITY_UNAVAILABLE",
                "orchestrator is not configured; run binding unavailable",
                503,
            )
        try:
            return await self.orchestrator.run_status(token, run_id)
        except UpstreamError as exc:
            if exc.status in (403, 404):
                raise _not_found("RUN_NOT_FOUND", f"unknown run '{run_id}'") from exc
            raise

    # -- sessions ------------------------------------------------------------

    def _session_scoped(self, session_id: str, team_id: str) -> dict[str, Any]:
        session = self.store.get_session(session_id)
        if session is None or session["team_id"] != team_id:
            raise _not_found("SESSION_NOT_FOUND", f"unknown code session '{session_id}'")
        return session

    def _active_session(self, session_id: str, principal: dict[str, Any]) -> dict[str, Any]:
        session = self._session_scoped(session_id, principal["teamId"])
        if session.get("status") != "active":
            raise LaneError("SESSION_CLOSED", "this code session is closed", 409)
        return session

    async def create_session(
        self, token: str, principal: dict[str, Any], body: dict[str, Any]
    ) -> dict[str, Any]:
        require_writer(principal)
        project_id = (body.get("projectId") or "").strip()
        if not project_id:
            raise LaneError("INVALID_REQUEST", "projectId is required", 422)
        await self.require_project(token, principal["teamId"], project_id)

        run_id = (body.get("runId") or "").strip() or None
        if run_id:
            await self.require_run(token, run_id)

        forbidden: list[str] = []
        for raw in body.get("forbiddenPaths") or []:
            try:
                forbidden.append(normalize_rel_path(str(raw)))
            except InvalidPathError as exc:
                raise LaneError("INVALID_PATH", str(exc), 422) from exc

        branch = (body.get("branch") or "main").strip() or "main"
        repo_ref = (body.get("repoRef") or "").strip() or None

        session_id = new_id("dw")
        try:
            checkout_info = self.workspaces.ensure_checkout(session_id, repo_ref, branch)
        except WorkspaceError as exc:
            raise LaneError("WORKSPACE_ERROR", str(exc), 422) from exc

        task_id: str | None = None
        sandbox_error: str | None = None
        if self.sandbox is not None:
            task_id = f"dw-{session_id}"
            try:
                await self.sandbox.create_task(
                    token=token,
                    task_id=task_id,
                    repo_ref=repo_ref or "(local)",
                    branch=branch,
                    forbidden_paths=forbidden,
                    project_id=project_id,
                )
            except UpstreamError as exc:
                task_id = None
                sandbox_error = f"{exc.code}: {exc.message}"

        session = self.store.create_session(
            session_id=session_id,
            project_id=project_id,
            team_id=principal["teamId"],
            run_id=run_id,
            task_id=task_id,
            repo_ref=repo_ref,
            branch=branch,
            forbidden_paths=forbidden,
            created_by=principal["userId"],
        )
        session["headSha"] = checkout_info.get("headSha")
        session["sandboxError"] = sandbox_error
        return session

    def list_sessions(
        self, principal: dict[str, Any], project_id: str | None
    ) -> list[dict[str, Any]]:
        return self.store.list_sessions(principal["teamId"], project_id)

    def close_session(self, session_id: str, principal: dict[str, Any]) -> None:
        session = self._session_scoped(session_id, principal["teamId"])
        self.store.close_session(session["session_id"])

    # -- files ---------------------------------------------------------------

    def _checked_path(self, session: dict[str, Any], path: str) -> str:
        try:
            return check_path(path, session["forbiddenPaths"])
        except ForbiddenPathError as exc:
            raise LaneError("FORBIDDEN_PATH", str(exc), 403) from exc
        except InvalidPathError as exc:
            raise LaneError("INVALID_PATH", str(exc), 422) from exc

    def list_tree(
        self, session_id: str, principal: dict[str, Any], dir_path: str
    ) -> dict[str, Any]:
        session = self._session_scoped(session_id, principal["teamId"])
        checked = self._checked_path(session, dir_path) if dir_path else ""
        try:
            listing = self.workspaces.list_dir(session_id, checked)
        except WorkspaceError as exc:
            raise _not_found("PATH_NOT_FOUND", str(exc)) from exc
        return listing

    def read_file(self, session_id: str, principal: dict[str, Any], path: str) -> dict[str, Any]:
        session = self._session_scoped(session_id, principal["teamId"])
        checked = self._checked_path(session, path)
        try:
            content = self.workspaces.read_file(session_id, checked)
        except WorkspaceError as exc:
            raise _not_found("FILE_NOT_FOUND", str(exc)) from exc
        return {"path": checked, "content": content, "sha256": diffs.sha256_text(content)}

    def write_file(
        self,
        session_id: str,
        principal: dict[str, Any],
        path: str,
        content: str,
        base_sha256: str | None,
    ) -> dict[str, Any]:
        require_writer(principal)
        session = self._active_session(session_id, principal)
        checked = self._checked_path(session, path)
        existed = self.workspaces.file_exists(session_id, checked)
        if base_sha256 is not None:
            if not existed:
                raise LaneError(
                    "STALE_BASE",
                    "the file no longer exists; you based this edit on an older state",
                    409,
                )
            current = self.workspaces.read_file(session_id, checked)
            current_sha = diffs.sha256_text(current)
            if current_sha != base_sha256:
                raise LaneError(
                    "STALE_BASE",
                    "the file changed since it was loaded; reload before saving",
                    409,
                    detail={"path": checked, "currentSha256": current_sha},
                )
        try:
            new_sha = self.workspaces.write_file(session_id, checked, content)
        except diffs.DiffError as exc:
            raise LaneError("INVALID_CONTENT", str(exc), 422) from exc
        return {"path": checked, "sha256": new_sha, "created": not existed}

    # -- change sets ---------------------------------------------------------

    def _hunks_payload(self, hunks: list[diffs.Hunk]) -> list[dict[str, Any]]:
        return [
            {
                "id": f"h{i}",
                "oldStart": h.old_start,
                "oldCount": h.old_count,
                "newStart": h.new_start,
                "newCount": h.new_count,
                "lines": [{"kind": kind, "text": text} for kind, text in h.lines],
            }
            for i, h in enumerate(hunks)
        ]

    def get_change_set(self, cs_id: str, principal: dict[str, Any]) -> dict[str, Any]:
        cs = self.store.get_change_set(cs_id)
        if cs is None:
            raise _not_found("CHANGE_SET_NOT_FOUND", f"unknown change set '{cs_id}'")
        session = self._session_scoped(cs["session_id"], principal["teamId"])
        for proposal in cs["files"]:
            self._annotate_drift(session, proposal)
        return cs

    def _annotate_drift(self, session: dict[str, Any], proposal: dict[str, Any]) -> None:
        path = proposal["path"]
        exists = self.workspaces.file_exists(session["session_id"], path)
        if proposal["base_sha256"] is None:  # creation proposal: the file must still be absent
            proposal["drift"] = "stale" if exists else "current"
            return
        if not exists:
            proposal["drift"] = "file-missing"
            return
        current = self.workspaces.read_file(session["session_id"], path)
        current_sha = diffs.sha256_text(current)
        proposal["currentSha256"] = current_sha
        proposal["drift"] = "current" if current_sha == proposal["base_sha256"] else "stale"

    def create_change_set(
        self, session_id: str, principal: dict[str, Any], body: dict[str, Any]
    ) -> dict[str, Any]:
        require_writer(principal)
        session = self._active_session(session_id, principal)
        title = (body.get("title") or "").strip()
        if not title:
            raise LaneError("INVALID_REQUEST", "title is required", 422)
        edits = body.get("edits")
        if not isinstance(edits, list) or not edits:
            raise LaneError("INVALID_REQUEST", "edits must be a non-empty list", 422)

        merged: dict[str, dict[str, Any]] = {}
        unchanged: list[str] = []
        for raw in edits:
            if not isinstance(raw, dict):
                raise LaneError("INVALID_REQUEST", "each edit must be an object", 422)
            checked = self._checked_path(session, str(raw.get("path") or ""))
            try:
                new_text = diffs.normalize_text(str(raw.get("newContent") or ""))
            except diffs.DiffError as exc:
                raise LaneError("INVALID_CONTENT", f"{checked}: {exc}", 422) from exc
            base_sha = raw.get("baseSha256")
            existed = self.workspaces.file_exists(session_id, checked)
            base_text: str | None = None
            if existed:
                base_text = self.workspaces.read_file(session_id, checked)
                if base_sha is not None and diffs.sha256_text(base_text) != base_sha:
                    raise LaneError(
                        "STALE_BASE",
                        f"{checked} changed since this change set was composed",
                        409,
                        detail={"path": checked},
                    )
            if base_text is None and new_text == "":
                raise LaneError("EMPTY_FILE", f"{checked}: cannot create an empty file", 422)
            if base_text is not None and base_text == new_text:
                if checked not in unchanged:
                    unchanged.append(checked)
                continue
            try:
                new_hunks = diffs.make_hunks_with_creation(base_text, new_text)
            except diffs.DiffError as exc:
                raise LaneError("INVALID_CONTENT", f"{checked}: {exc}", 422) from exc

            if checked in merged:
                prior = merged[checked]
                try:
                    combined = diffs.merge_disjoint_edits(prior["base"], prior["target"], new_text)
                    hunks = diffs.make_hunks_with_creation(prior["base"], combined)
                except diffs.OverlappingEditsError as exc:
                    raise LaneError(
                        "OVERLAPPING_EDITS",
                        f"{checked}: proposed edits overlap; split them into separate change sets",
                        422,
                    ) from exc
                except diffs.DiffError as exc:
                    raise LaneError("INVALID_CONTENT", f"{checked}: {exc}", 422) from exc
                merged[checked] = {**prior, "target": combined, "hunks": hunks}
            else:
                merged[checked] = {
                    "base": base_text,
                    "base_sha256": diffs.sha256_text(base_text) if base_text is not None else None,
                    "target": new_text,
                    "hunks": new_hunks,
                }

        if not merged:
            raise LaneError("NO_CHANGES", "every edit is identical to the current file", 422)

        cs_id = self.store.create_change_set(
            session_id=session_id,
            title=title,
            note=(body.get("note") or None),
            created_by=principal["userId"],
        )
        for path, data in merged.items():
            self.store.add_file_proposal(
                cs_id,
                path,
                data["base_sha256"],
                data["target"],
                self._hunks_payload(data["hunks"]),
            )
        cs = self.store.get_change_set(cs_id)
        assert cs is not None
        cs["unchanged"] = unchanged
        return cs

    def list_change_sets(self, session_id: str, principal: dict[str, Any]) -> list[dict[str, Any]]:
        self._session_scoped(session_id, principal["teamId"])
        return self.store.list_change_sets(session_id)

    def decide_change_set(
        self, cs_id: str, principal: dict[str, Any], decisions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        require_writer(principal)
        cs = self.get_change_set(cs_id, principal)
        if cs["status"] != "open":
            raise LaneError(
                "INVALID_STATE", f"change set is {cs['status']}; decisions are closed", 409
            )
        known: dict[str, set[str]] = {f["path"]: {h["id"] for h in f["hunks"]} for f in cs["files"]}
        for raw in decisions:
            path = str(raw.get("path") or "")
            hunk_id = str(raw.get("hunkId") or "")
            decision = str(raw.get("decision") or "")
            if path not in known:
                raise LaneError("INVALID_REQUEST", f"unknown path '{path}'", 422)
            if hunk_id not in known[path]:
                raise LaneError("INVALID_REQUEST", f"unknown hunk '{hunk_id}' for {path}", 422)
            if decision not in ("accept", "reject", "pending"):
                raise LaneError("INVALID_REQUEST", "decision must be accept|reject|pending", 422)
            comment = raw.get("comment")
            self.store.set_decision(
                cs_id,
                path,
                hunk_id,
                decision,
                str(comment) if comment else None,
                principal["userId"],
            )
        return self.get_change_set(cs_id, principal)

    def apply_change_set(
        self,
        cs_id: str,
        principal: dict[str, Any],
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        require_writer(principal)
        if idempotency_key:
            stored = self.store.get_idempotent(f"apply:{cs_id}", idempotency_key)
            if stored is not None:
                return stored
        cs = self.get_change_set(cs_id, principal)
        if cs["status"] == "cancelled":
            raise LaneError("INVALID_STATE", "change set was cancelled", 409)
        if cs["status"] == "applied":
            raise LaneError("INVALID_STATE", "change set was already applied", 409)
        session = self._session_scoped(cs["session_id"], principal["teamId"])

        stale: list[str] = []
        total_accepted = 0
        for proposal in cs["files"]:
            if proposal["drift"] != "current":
                stale.append(proposal["path"])
                continue
            accepted = {
                i
                for i, hunk in enumerate(proposal["hunks"])
                if proposal["decisions"].get(hunk["id"], {}).get("decision") == "accept"
            }
            total_accepted += len(accepted)
        if stale:
            raise LaneError(
                "STALE_BASE",
                "the workspace changed since this change set was composed",
                409,
                detail={"staleFiles": stale},
            )
        if total_accepted == 0:
            raise LaneError(
                "NO_ACCEPTED_HUNKS",
                "no hunks are accepted; accept the hunks to apply first",
                400,
            )

        results: list[dict[str, Any]] = []
        originals: dict[str, str | None] = {}  # for rollback: all-or-nothing across files
        sid = session["session_id"]
        try:
            for proposal in cs["files"]:
                hunks = [
                    diffs.Hunk(
                        h["oldStart"],
                        h["oldCount"],
                        h["newStart"],
                        h["newCount"],
                        [(line["kind"], line["text"]) for line in h["lines"]],
                    )
                    for h in proposal["hunks"]
                ]
                accepted = {
                    i
                    for i, h in enumerate(proposal["hunks"])
                    if proposal["decisions"].get(h["id"], {}).get("decision") == "accept"
                }
                if not accepted:
                    results.append(
                        {
                            "path": proposal["path"],
                            "status": "unchanged",
                            "acceptedHunks": 0,
                            "rejectedHunks": len(hunks),
                        }
                    )
                    continue
                path = proposal["path"]
                existed = self.workspaces.file_exists(sid, path)
                base_text = self.workspaces.read_file(sid, path) if existed else ""
                originals[path] = base_text if existed else None
                target = diffs.apply_accepted_hunks(base_text, hunks, accepted)
                self.workspaces.write_file(sid, path, target)
                readback = self.workspaces.read_file(sid, path)
                if diffs.normalize_text(readback) != target:
                    raise LaneError(
                        "INTERNAL_READBACK_MISMATCH",
                        f"{path}: applied content does not match the accepted target",
                        500,
                    )
                results.append(
                    {
                        "path": path,
                        "status": "written",
                        "acceptedHunks": len(accepted),
                        "rejectedHunks": len(hunks) - len(accepted),
                    }
                )
        except Exception as exc:
            for path, original in originals.items():
                try:
                    if original is None:
                        self.workspaces.delete_file(sid, path)
                    else:
                        self.workspaces.write_file(sid, path, original)
                except Exception:  # noqa: BLE001,S110 best effort; caller sees the original failure
                    pass
            if isinstance(exc, LaneError):
                raise
            raise LaneError(
                "APPLY_FAILED", "applying the change set failed; no files were changed", 500
            ) from exc

        fingerprint = self.workspaces.fingerprint(session["session_id"])
        self.store.mark_change_set_applied(cs_id, fingerprint)
        response = {
            "changeSetId": cs_id,
            "status": "applied",
            "files": results,
            "fingerprint": fingerprint,
        }
        if idempotency_key:
            self.store.put_idempotent(f"apply:{cs_id}", idempotency_key, response)
        return response

    def cancel_change_set(self, cs_id: str, principal: dict[str, Any]) -> dict[str, Any]:
        require_writer(principal)
        cs = self.get_change_set(cs_id, principal)
        if cs["status"] == "applied":
            raise LaneError("INVALID_STATE", "applied change sets cannot be cancelled", 409)
        self.store.cancel_change_set(cs_id)
        return self.get_change_set(cs_id, principal)

    # -- sandbox sync + verify ----------------------------------------------

    def _require_task(self, session: dict[str, Any]) -> str:
        if self.sandbox is None:
            raise LaneError("CAPABILITY_UNAVAILABLE", "sandbox is not configured", 503)
        task_id = session.get("task_id")
        if not task_id:
            raise LaneError(
                "SANDBOX_TASK_MISSING",
                "this session has no sandbox task (creation failed or denied)",
                409,
            )
        return task_id

    def _changed_worktree_files(self, session_id: str) -> list[str]:
        return self.workspaces.changed_paths(session_id)

    async def sync_session(
        self, session_id: str, token: str, principal: dict[str, Any]
    ) -> dict[str, Any]:
        require_writer(principal)
        session = self._active_session(session_id, principal)
        task_id = self._require_task(session)
        assert self.sandbox is not None

        synced: list[str] = []
        skipped: list[str] = []
        blocked: list[str] = []
        failed: dict[str, Any] | None = None

        for path in self._changed_worktree_files(session_id):
            try:
                checked = check_path(path, session["forbiddenPaths"])
            except (ForbiddenPathError, InvalidPathError):
                blocked.append(path)
                continue
            if not self.workspaces.file_exists(session_id, checked):
                # Deletions are not synced by this adapter (honest limit).
                blocked.append(checked)
                continue
            content = self.workspaces.read_file(session_id, checked)
            content_sha = diffs.sha256_text(content)
            if self.store.get_synced(session_id, checked) == content_sha:
                skipped.append(checked)
                continue
            synced_copy = self.workspaces.synced_dir(session_id) / checked
            base_text = synced_copy.read_text(encoding="utf-8") if synced_copy.is_file() else None
            diff_text = diffs.diff_base_to_target(checked, base_text, content)
            try:
                await self.sandbox.apply_edit(token, task_id, checked, diff_text)
            except UpstreamError as exc:
                failed = {"path": checked, "code": exc.code, "message": exc.message}
                break
            synced_copy.parent.mkdir(parents=True, exist_ok=True)
            synced_copy.write_text(content, encoding="utf-8")
            self.store.set_synced(session_id, checked, content_sha)
            synced.append(checked)

        return {
            "sessionId": session_id,
            "synced": synced,
            "skipped": skipped,
            "blocked": blocked,
            "failed": failed,
        }

    async def verify(
        self,
        session_id: str,
        token: str,
        principal: dict[str, Any],
        command: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        if idempotency_key:
            stored = self.store.get_idempotent(f"verify:{session_id}", idempotency_key)
            if stored is not None:
                return stored
        require_writer(principal)
        session = self._active_session(session_id, principal)
        command = (command or "").strip()
        if not command:
            raise LaneError("INVALID_REQUEST", "command is required", 422)
        task_id = self._require_task(session)
        assert self.sandbox is not None

        sync_report = await self.sync_session(session_id, token, principal)
        if sync_report["failed"] is not None:
            raise LaneError(
                "SYNC_FAILED",
                f"could not sync {sync_report['failed']['path']} to the sandbox",
                502,
                detail=sync_report,
            )
        fingerprint = self.workspaces.fingerprint(session_id)
        result = await self.sandbox.exec_command(token, task_id, command)
        evidence = self.store.add_evidence(
            session_id=session_id,
            command=command,
            status=str(result.get("status", "error")),
            summary=str(result.get("summary", "")),
            exit_code=int(result.get("exit_code", -1)),
            diagnostics=[str(d) for d in (result.get("diagnostics") or [])],
            tree_fingerprint=fingerprint,
            created_by=principal["userId"],
        )
        response = {
            "sessionId": session_id,
            "evidenceId": evidence["evidence_id"],
            "status": evidence["status"],
            "summary": evidence["summary"],
            "exitCode": evidence["exit_code"],
            "diagnostics": evidence["diagnostics"],
            "treeFingerprint": fingerprint,
            "sync": {
                "synced": sync_report["synced"],
                "skipped": sync_report["skipped"],
                "blocked": sync_report["blocked"],
            },
        }
        if idempotency_key:
            self.store.put_idempotent(f"verify:{session_id}", idempotency_key, response)
        return response

    def list_evidence(self, session_id: str, principal: dict[str, Any]) -> list[dict[str, Any]]:
        self._session_scoped(session_id, principal["teamId"])
        out = []
        for item in self.store.list_evidence(session_id):
            out.append(
                {
                    "evidenceId": item["evidence_id"],
                    "command": item["command"],
                    "status": item["status"],
                    "summary": item["summary"],
                    "exitCode": item["exit_code"],
                    "diagnostics": item["diagnostics"],
                    "treeFingerprint": item["tree_fingerprint"],
                    "createdBy": item["created_by"],
                    "createdAt": item["created_at"],
                }
            )
        return out

    # -- comments ------------------------------------------------------------

    def add_comment(
        self, session_id: str, principal: dict[str, Any], body: dict[str, Any]
    ) -> dict[str, Any]:
        session = self._session_scoped(session_id, principal["teamId"])
        checked = self._checked_path(session, str(body.get("path") or ""))
        text = str(body.get("body") or "").strip()
        if not text:
            raise LaneError("INVALID_REQUEST", "comment body is required", 422)
        if len(text) > 4000:
            raise LaneError("INVALID_REQUEST", "comment body exceeds 4000 characters", 422)
        try:
            start_line = int(body.get("startLine") or 0)
            end_line = int(body.get("endLine") or start_line)
        except (TypeError, ValueError) as exc:
            raise LaneError("INVALID_REQUEST", "line numbers must be integers", 422) from exc
        if start_line < 1 or end_line < start_line:
            raise LaneError("INVALID_REQUEST", "invalid line range", 422)
        side = str(body.get("side") or "new")
        if side not in ("base", "new"):
            raise LaneError("INVALID_REQUEST", "side must be base|new", 422)
        if not self.workspaces.file_exists(session_id, checked):
            raise _not_found("FILE_NOT_FOUND", f"cannot anchor a comment to missing file {checked}")
        content = self.workspaces.read_file(session_id, checked)
        anchor = str(body.get("anchorSha256") or diffs.sha256_text(content))
        comment = self.store.add_comment(
            session_id=session_id,
            path=checked,
            start_line=start_line,
            end_line=end_line,
            side=side,
            body=text,
            anchor_sha256=anchor,
            author=principal["userId"],
        )
        return self._comment_payload(session, comment)

    def _comment_payload(self, session: dict[str, Any], comment: dict[str, Any]) -> dict[str, Any]:
        path = comment["path"]
        anchor_status = "orphaned"
        if self.workspaces.file_exists(session["session_id"], path):
            current = self.workspaces.read_file(session["session_id"], path)
            anchor_status = (
                "anchored" if diffs.sha256_text(current) == comment["anchor_sha256"] else "drifted"
            )
        return {
            "commentId": comment["comment_id"],
            "sessionId": comment["session_id"],
            "path": path,
            "startLine": comment["start_line"],
            "endLine": comment["end_line"],
            "side": comment["side"],
            "body": comment["body"],
            "anchorSha256": comment["anchor_sha256"],
            "anchorStatus": anchor_status,
            "author": comment["author"],
            "createdAt": comment["created_at"],
        }

    def list_comments(
        self, session_id: str, principal: dict[str, Any], path: str | None
    ) -> list[dict[str, Any]]:
        session = self._session_scoped(session_id, principal["teamId"])
        checked = self._checked_path(session, path) if path else None
        return [
            self._comment_payload(session, c) for c in self.store.list_comments(session_id, checked)
        ]

    def delete_comment(self, comment_id: str, principal: dict[str, Any]) -> None:
        comment = self.store.get_comment(comment_id)
        if comment is None:
            raise _not_found("COMMENT_NOT_FOUND", f"unknown comment '{comment_id}'")
        session = self._session_scoped(comment["session_id"], principal["teamId"])
        is_author = comment["author"] == principal["userId"]
        is_admin = principal.get("role") == "admin"
        if not (is_author or is_admin):
            raise LaneError("ROLE_DENIED", "only the author or a team admin can delete", 403)
        assert session is not None
        self.store.delete_comment(comment_id)

    # -- code actions --------------------------------------------------------

    _UNSUPPORTED_ACTIONS: ClassVar[dict[str, str]] = {
        "explain": "AI explanation requires the orchestrator code-action endpoint",
        "refactor": "AI refactor proposals require the orchestrator code-action endpoint",
    }

    async def run_action(
        self,
        session_id: str,
        token: str,
        principal: dict[str, Any],
        operation: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        session = self._session_scoped(session_id, principal["teamId"])
        if operation == "add-tests":
            require_writer(principal)
        checked = self._checked_path(session, str(arguments.get("path") or ""))
        try:
            start = int(arguments.get("startLine") or 1)
            end = int(arguments.get("endLine") or start)
        except (TypeError, ValueError) as exc:
            raise LaneError("INVALID_REQUEST", "line numbers must be integers", 422) from exc
        if start < 1 or end < start:
            raise LaneError("INVALID_REQUEST", "invalid line range", 422)

        if operation in self._UNSUPPORTED_ACTIONS:
            selection = str(arguments.get("selection") or "")[:2000]
            return {
                "outcome": "unsupported",
                "error": {
                    "code": "NEEDS_INTEGRATION",
                    "message": self._UNSUPPORTED_ACTIONS[operation],
                },
                "result": {
                    "proposedIntegration": (
                        "A2 orchestrator: POST /api/v1/orchestrator/runs/{runId}/code-actions "
                        "accepting {action, path, startLine, endLine, selection, projectId} "
                        "and returning a suggestion artifact"
                    ),
                    "promptPayload": {
                        "action": operation,
                        "path": checked,
                        "startLine": start,
                        "endLine": end,
                        "selection": selection,
                    },
                },
            }

        if operation == "references":
            if self.context is None:
                return {
                    "outcome": "unsupported",
                    "error": {
                        "code": "CAPABILITY_UNAVAILABLE",
                        "message": "context index not configured",
                    },
                }
            content = self.workspaces.read_file(session_id, checked)
            lines = diffs.split_lines(content)
            if start < 1 or end > max(len(lines), 1) or end < start:
                raise LaneError("INVALID_REQUEST", "line range is outside the file", 422)
            selection = str(arguments.get("selection") or "").strip()
            if not selection:
                selection = "\n".join(lines[start - 1 : end])
            query = str(arguments.get("query") or "").strip() or _first_symbol(selection)
            if not query:
                raise LaneError("INVALID_REQUEST", "provide a selection or query", 422)
            hits = await self.context.search(token, session["project_id"], query[:200])
            return {"outcome": "succeeded", "result": {"query": query, "hits": hits}}

        if operation == "blame":
            if not self.workspaces.file_exists(session_id, checked):
                raise _not_found("FILE_NOT_FOUND", f"{checked} does not exist")
            try:
                log = self.workspaces.log_line_range(session_id, checked, start, end)
            except WorkspaceError as exc:
                raise LaneError("INVALID_REQUEST", str(exc), 422) from exc
            return {"outcome": "succeeded", "result": {"log": log[-8000:]}}

        if operation == "add-tests":
            return await self._add_tests_action(session, principal, checked)

        raise LaneError("UNSUPPORTED_OPERATION", f"unknown action '{operation}'", 422)

    async def _add_tests_action(
        self, session: dict[str, Any], principal: dict[str, Any], path: str
    ) -> dict[str, Any]:
        session_id = session["session_id"]
        if path.endswith(".py"):
            stem = Path(path).stem
            test_path = f"tests/test_{_safe_ident(stem)}.py"
            module = path[: -len(".py")].replace("/", ".").replace(".__init__", "")
            content = (
                f"# Scaffolded by the dev-workflow add-tests action for {path}.\n"
                f"# Replace the import check with real behavioral tests.\n\n\n"
                f"def test_imports():\n"
                f"    import {module}  # noqa: F401\n"
            )
        elif path.endswith((".ts", ".tsx", ".js", ".jsx")):
            stem = Path(path).name
            test_path = f"tests/{stem}.test.ts"
            content = (
                f"// Scaffolded by the dev-workflow add-tests action for {path}.\n"
                f"// Replace each todo with real behavioral assertions.\n\n"
                f'import {{ describe, it }} from "vitest";\n\n'
                f'describe("{stem}", () => {{\n'
                f'  it.todo("describe the behavior of {path}");\n'
                f"}});\n"
            )
        else:
            return {
                "outcome": "unsupported",
                "error": {
                    "code": "UNSUPPORTED_OPERATION",
                    "message": f"add-tests has no scaffold for {path}",
                },
            }
        if self.workspaces.file_exists(session_id, test_path):
            return {
                "outcome": "failed",
                "error": {"code": "TEST_FILE_EXISTS", "message": f"{test_path} already exists"},
            }
        cs = self.create_change_set(
            session_id,
            principal,
            {
                "title": f"Scaffold tests for {path}",
                "note": f"Generated by the add-tests action for {path} (lines {path}).",
                "edits": [{"path": test_path, "newContent": content}],
            },
        )
        return {
            "outcome": "succeeded",
            "result": {"changeSetId": cs["cs_id"], "testPath": test_path},
        }

    # -- merge checks --------------------------------------------------------

    async def merge_checks(
        self, session_id: str, token: str, principal: dict[str, Any]
    ) -> dict[str, Any]:
        session = self._session_scoped(session_id, principal["teamId"])
        latest = self.store.latest_change_set(session_id)
        if latest is not None:
            latest = self.get_change_set(latest["cs_id"], principal)
        evidence = self.list_evidence(session_id, principal)
        fingerprint = self.workspaces.fingerprint(session_id)
        comments = self.list_comments(session_id, principal, None)

        checkpoint_sha: str | None = None
        sandbox_configured = self.sandbox is not None and bool(session.get("task_id"))
        if sandbox_configured:
            try:
                listing = await self.sandbox.list_checkpoints(token, session["project_id"])
                items = listing.get("items") if isinstance(listing, dict) else None
                if items:
                    checkpoint_sha = str(items[0].get("sha"))
            except UpstreamError:
                checkpoint_sha = None

        report = compute_merge_readiness(
            latest_change_set=latest,
            evidence=evidence,
            current_fingerprint=fingerprint,
            comments_count=len(comments),
            sandbox_configured=sandbox_configured,
            checkpoint_sha=checkpoint_sha,
        )
        report["sessionId"] = session_id
        report["changeSetId"] = latest["cs_id"] if latest else None
        return report

    # -- capabilities --------------------------------------------------------

    async def capabilities(
        self, token: str, principal: dict[str, Any], project_id: str | None
    ) -> dict[str, Any]:
        caps: dict[str, Any] = {
            "identity": {"configured": self.identity is not None, "verified": True},
            "sandbox": {"configured": self.sandbox is not None, "probed": False},
            "orchestrator": {"configured": self.orchestrator is not None},
            "context": {"configured": self.context is not None},
        }
        if self.sandbox is not None and project_id:
            await self.require_project(token, principal["teamId"], project_id)
            try:
                await self.sandbox.list_checkpoints(token, project_id)
            except UpstreamError as exc:
                caps["sandbox"].update({"probed": True, "reachable": False, "code": exc.code})
            else:
                caps["sandbox"].update({"probed": True, "reachable": True})
        return caps


def _first_symbol(text: str) -> str:
    for token in text.replace("(", " ").replace(")", " ").split():
        cleaned = token.strip(".,;:!?\"'`")
        if cleaned and (cleaned[0].isalpha() or cleaned[0] == "_"):
            return cleaned[:120]
    return ""


def _safe_ident(stem: str) -> str:
    return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in stem) or "module"
