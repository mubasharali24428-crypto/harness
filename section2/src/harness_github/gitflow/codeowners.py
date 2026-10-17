"""F036: CODEOWNERS matching (GitHub semantics: last matching rule wins) + reviewer routing."""

from __future__ import annotations

import re
from dataclasses import dataclass

_USER = re.compile(r"^@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_TEAM = re.compile(r"^@[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_REVIEWERS = 15
MAX_RULES = 5000


@dataclass
class Rule:
    pattern: str
    owners: list[str]
    regex: re.Pattern[str]


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.startswith("/")
    dir_only = pattern.endswith("/")
    body = pattern.strip("/")
    if "/" in body:
        anchored = True
    out: list[str] = []
    i = 0
    while i < len(body):
        c = body[i]
        if body.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif body.startswith("/**", i) and i + 3 == len(body):
            out.append("/.*")
            i += 3
        elif body.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    core = "".join(out)
    prefix = "" if anchored else "(?:.*/)?"
    # A literal pattern owns everything beneath it; a trailing wildcard does NOT recurse
    # (GitHub: `docs/*` matches docs/a.md but not docs/build/b.md).
    last = body.rsplit("/", 1)[-1]
    if dir_only:
        suffix = "/.*"
    elif any(ch in last for ch in "*?"):
        suffix = ""
    else:
        suffix = "(?:/.*)?"
    return re.compile(f"^{prefix}{core}{suffix}$")


def parse(text: str) -> list[Rule]:
    rules: list[Rule] = []
    for raw in text.splitlines()[:MAX_RULES]:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"(?<!\\)\s+", line)
        owners: list[str] = []
        pattern = parts[0].replace("\\#", "#")
        for tok in parts[1:]:
            if tok.startswith("#"):
                break
            owners.append(tok)
        try:
            rules.append(Rule(pattern, owners, _compile(pattern)))
        except re.error:
            continue
    return rules


def owners_for(path: str, rules: list[Rule]) -> list[str]:
    owners: list[str] = []
    for rule in rules:  # last match wins, including a later ownerless rule (unowned)
        if rule.regex.match(path):
            owners = rule.owners
    return owners


def route_reviewers(
    changed_paths: list[str], codeowners_text: str, author_login: str | None = None
) -> dict:
    rules = parse(codeowners_text)
    users: dict[str, list[str]] = {}
    teams: dict[str, list[str]] = {}
    unowned: list[str] = []
    unroutable: dict[str, list[str]] = {}
    author = (author_login or "").lower().lstrip("@")
    for path in changed_paths:
        owners = owners_for(path, rules)
        if not owners:
            unowned.append(path)
            continue
        for owner in owners:
            if _USER.match(owner):
                if owner[1:].lower() == author:
                    continue  # GitHub rejects the PR author as a reviewer
                users.setdefault(owner[1:], []).append(path)
            elif _TEAM.match(owner):
                teams.setdefault(owner[1:], []).append(path)
            elif _EMAIL.match(owner):
                unroutable.setdefault(owner, []).append(path)  # cannot request by email
            else:
                unroutable.setdefault(owner, []).append(path)

    def ranked(d: dict[str, list[str]]) -> list[tuple[str, list[str]]]:
        return sorted(d.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))

    ru, rt = ranked(users), ranked(teams)
    return {
        "reviewers": [u for u, _ in ru[:MAX_REVIEWERS]],
        "teamReviewers": [t for t, _ in rt[:MAX_REVIEWERS]],
        "truncated": len(ru) > MAX_REVIEWERS or len(rt) > MAX_REVIEWERS,
        "ownership": {
            **{f"@{u}": p for u, p in ru},
            **{f"@{t}": p for t, p in rt},
        },
        "unowned": unowned,
        "unroutable": {k: v for k, v in unroutable.items()},
    }
