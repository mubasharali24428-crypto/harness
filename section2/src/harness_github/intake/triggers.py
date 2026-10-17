"""Turn provider webhook payloads into normalized intake candidates (or None = ignore)."""

from __future__ import annotations

from dataclasses import dataclass

from ..gitflow.text import clean_block, clean_line, neutralize

COMMAND = "/harness plan"
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


@dataclass
class Candidate:
    provider: str
    kind: str  # issue | review-request
    external_project: str
    external_id: str
    url: str
    title: str
    body: str
    actor: str
    trigger: str


def _norm(c: Candidate) -> Candidate:
    c.title = neutralize(clean_line(c.title, 200))
    c.body = neutralize(clean_block(c.body or "", 4000))
    c.actor = clean_line(c.actor, 80)
    return c


def github(event: str, p: dict, label: str) -> Candidate | None:
    repo = (p.get("repository") or {}).get("full_name")
    sender = p.get("sender") or {}
    if not repo or sender.get("type") == "Bot":
        return None  # loop prevention: never react to bots (including ourselves)
    issue = p.get("issue") or {}
    if (
        event == "issues"
        and p.get("action") == "labeled"
        and (p.get("label") or {}).get("name") == label
    ):
        return _norm(
            Candidate(
                "github",
                "issue",
                repo,
                str(issue.get("number")),
                issue.get("html_url", ""),
                issue.get("title", ""),
                issue.get("body") or "",
                sender.get("login", ""),
                f"label:{label}",
            )
        )
    if event == "issue_comment" and p.get("action") == "created":
        comment = p.get("comment") or {}
        if str(comment.get("body", "")).strip().lower().startswith(COMMAND) and (
            comment.get("author_association") in TRUSTED_ASSOCIATIONS
        ):
            return _norm(
                Candidate(
                    "github",
                    "issue",
                    repo,
                    str(issue.get("number")),
                    issue.get("html_url", ""),
                    issue.get("title", ""),
                    issue.get("body") or "",
                    sender.get("login", ""),
                    "comment:/harness plan",
                )
            )
    return None


def gitlab(event: str, p: dict, label: str) -> Candidate | None:
    project = (p.get("project") or {}).get("path_with_namespace")
    user = p.get("user") or {}
    if not project or user.get("bot") or str(user.get("username", "")).endswith("_bot"):
        return None
    attrs = p.get("object_attributes") or {}
    if event == "Issue Hook":
        added = {x.get("title") for x in (p.get("labels") or [])}
        prev = {
            x.get("title")
            for x in ((p.get("changes") or {}).get("labels") or {}).get("previous", [])
        }
        if label in added and label not in prev:
            return _norm(
                Candidate(
                    "gitlab",
                    "issue",
                    project,
                    str(attrs.get("iid")),
                    attrs.get("url", ""),
                    attrs.get("title", ""),
                    attrs.get("description") or "",
                    user.get("username", ""),
                    f"label:{label}",
                )
            )
    if event == "Note Hook" and attrs.get("noteable_type") in ("Issue", "MergeRequest"):
        if str(attrs.get("note", "")).strip().lower().startswith(COMMAND):
            target = p.get("issue") or p.get("merge_request") or {}
            kind = "review-request" if attrs.get("noteable_type") == "MergeRequest" else "issue"
            return _norm(
                Candidate(
                    "gitlab",
                    kind,
                    project,
                    str(target.get("iid")),
                    attrs.get("url", ""),
                    target.get("title", ""),
                    target.get("description") or "",
                    user.get("username", ""),
                    "comment:/harness plan",
                )
            )
    if event == "Merge Request Hook" and attrs.get("action") == "open":
        return _norm(
            Candidate(
                "gitlab",
                "review-request",
                project,
                str(attrs.get("iid")),
                attrs.get("url", ""),
                attrs.get("title", ""),
                attrs.get("description") or "",
                user.get("username", ""),
                "merge-request:opened",
            )
        )
    return None


def bitbucket(event: str, p: dict, label: str) -> Candidate | None:
    repo = (p.get("repository") or {}).get("full_name")
    actor = p.get("actor") or {}
    if not repo or actor.get("type") not in (None, "user"):
        return None  # apps/teams/bots never trigger work (loop prevention)
    who = actor.get("nickname") or actor.get("display_name") or ""
    comment = str(((p.get("comment") or {}).get("content") or {}).get("raw", ""))
    if event == "pullrequest:created":
        pr = p.get("pullrequest") or {}
        return _norm(
            Candidate(
                "bitbucket",
                "review-request",
                repo,
                str(pr.get("id")),
                ((pr.get("links") or {}).get("html") or {}).get("href", ""),
                pr.get("title", ""),
                pr.get("description") or "",
                who,
                "pull-request:created",
            )
        )
    if event in ("issue:comment_created", "pullrequest:comment_created") and (
        comment.strip().lower().startswith(COMMAND)
    ):
        is_pr = event.startswith("pullrequest")
        obj = p.get("pullrequest") if is_pr else p.get("issue")
        obj = obj or {}
        body = obj.get("description") if is_pr else (obj.get("content") or {}).get("raw")
        return _norm(
            Candidate(
                "bitbucket",
                "review-request" if is_pr else "issue",
                repo,
                str(obj.get("id")),
                ((obj.get("links") or {}).get("html") or {}).get("href", ""),
                obj.get("title", ""),
                body or "",
                who,
                "comment:/harness plan",
            )
        )
    return None


def azure(event: str, p: dict, label: str) -> Candidate | None:
    res = p.get("resource") or {}
    if event == "workitem.created" or (event == "workitem.updated" and _tag_added(res, label)):
        f = res.get("fields") or (res.get("revision") or {}).get("fields") or {}
        project = f.get("System.TeamProject")
        if not project or str(f.get("System.ChangedBy", "")).lower().endswith("[bot]"):
            return None
        wid = res.get("workItemId") or res.get("id")
        return _norm(
            Candidate(
                "azure",
                "issue",
                f"{project}/-",
                str(wid),
                res.get("url", ""),
                f.get("System.Title", ""),
                f.get("System.Description") or "",
                str(f.get("System.ChangedBy", "")),
                f"event:{event}",
            )
        )
    if event == "git.pullrequest.created":
        repo = res.get("repository") or {}
        project = (repo.get("project") or {}).get("name")
        if not project:
            return None
        return _norm(
            Candidate(
                "azure",
                "review-request",
                f"{project}/{repo.get('name', '-')}",
                str(res.get("pullRequestId")),
                res.get("url", ""),
                res.get("title", ""),
                res.get("description") or "",
                str((res.get("createdBy") or {}).get("uniqueName", "")),
                "pull-request:created",
            )
        )
    return None


def _tag_added(res: dict, label: str) -> bool:
    new = str(((res.get("fields") or {}).get("System.Tags") or {}).get("newValue", ""))
    old = str(((res.get("fields") or {}).get("System.Tags") or {}).get("oldValue", ""))
    has = lambda s: label in {t.strip() for t in s.split(";")}  # noqa: E731
    return has(new) and not has(old)
