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
