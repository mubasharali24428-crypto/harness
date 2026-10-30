#!/usr/bin/env python3
"""W18 — bring a team's existing agent instructions into Harness.

A team that already uses Claude Code (`CLAUDE.md`), Codex / the agent-standards
`AGENTS.md`, or Cursor (`.cursor/rules/*.mdc`) has its conventions written down
already. This tool reads those files and merges them into ONE project rules
document through the public API — no schema, no new route, nothing that can
disagree with the product.

The laws, and why each one is not decoration:

* **Nothing is sent without `--apply`.** The default is a printed plan; a
  migration tool that writes on a typo is a data-loss tool.
* **Nothing secret-shaped is forwarded.** A config file is exactly where a team
  keeps `OPENAI_API_KEY=…`, and a rules document is read by every agent on the
  project. Lines that look like credentials (and whole private-key blocks) are
  dropped and counted — a migration must not become an exfiltration channel.
* **A re-run replaces its own block.** The merged document is fenced by
  `<!-- harness:migrated-agent-rules -->` markers; everything outside them is the
  team's own text and is never touched, and a second run does not duplicate.
* **Too big is said out loud.** The API caps a rules document at 64KB UTF-8. A
  section that does not fit is dropped WHOLE (never cut mid-thought) and named in
  the output; a single source bigger than the cap is refused outright.

Usage:

    python3 scripts/migrate_agent_config.py --project-id <id>            # plan only
    HARNESS_TOKEN=… python3 scripts/migrate_agent_config.py --project-id <id> --apply

`HARNESS_TOKEN` may be any session token or SDK-issued bearer for a member with
write access to the project; `--token-env` names a different variable.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MAX_RULES_BYTES = 64 * 1024  # mirrors rules_api.MAX_RULES_BYTES
MARK_BEGIN = "<!-- harness:migrated-agent-rules -->"
MARK_END = "<!-- /harness:migrated-agent-rules -->"
DEFAULT_BASE_URL = "http://127.0.0.1:8001"

#: Files a team is likely to already have, in the order they are read. `.mdc`
#: carries Cursor front-matter (description/globs) which the importer strips:
#: those are editor metadata, not instructions.
SOURCE_FILES = ("AGENTS.md", "CLAUDE.md")
SOURCE_GLOBS = (".cursor/rules/*.mdc", ".codex/rules/*.md")

#: Credential shapes. Deliberately broad: a false positive costs one line of a
#: rules file, a false negative puts a live key in front of every agent.
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bASIA[0-9A-Z]{16}\b"),
    re.compile(r"\bhrn_[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bhsc_[A-Za-z0-9_-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|apikey|secret|token|passwd|password|client[_-]?secret)"
        r"\s*[:=]\s*\S{6,}"
    ),
    re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+\S{8,}"),
)
_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)


class ImportTooLarge(RuntimeError):
    """One source alone exceeds the 64KB rules cap; refusing beats amputation."""


@dataclass(frozen=True)
class Source:
    label: str
    path: Path
    text: str
    dropped_lines: int


@dataclass(frozen=True)
class Document:
    text: str
    replaced: bool
    truncated_at: str | None


def _strip_front_matter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            return text[end + 4 :].lstrip("\n")
    return text


def scrub(text: str) -> tuple[str, int]:
    """Drop credential-shaped lines and private-key blocks; return the count."""
    dropped = 0

    def _drop_block(match: re.Match[str]) -> str:
        nonlocal dropped
        dropped += match.group(0).count("\n") + 1
        return ""

    text = _KEY_BLOCK.sub(_drop_block, text)
    kept: list[str] = []
    for line in text.splitlines():
        if any(pattern.search(line) for pattern in _SECRET_PATTERNS):
            dropped += 1
            continue
        kept.append(line)
    return "\n".join(kept), dropped


def collect_sources(root: Path) -> list[Source]:
    """Every supported instruction file under ``root``, scrubbed, by label."""
    found: list[Source] = []
    candidates = [root / name for name in SOURCE_FILES]
    for pattern in SOURCE_GLOBS:
        candidates.extend(sorted(root.glob(pattern)))
    for path in candidates:
        if not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix == ".mdc":
            raw = _strip_front_matter(raw)
        body, dropped = scrub(raw)
        if body.strip():
            found.append(
                Source(
                    label=path.relative_to(root).as_posix(),
                    path=path,
                    text=body.strip("\n"),
                    dropped_lines=dropped,
                )
            )
    # Root-level files first (the ones a team reads as "the" conventions), then
    # editor rule files — a stable order so a re-run produces the same document.
    found.sort(key=lambda source: ("/" in source.label, source.label))
    return found


def _section(source: Source) -> str:
    return f"## From {source.label}\n\n{source.text}\n"


def _render(sections: list[str]) -> str:
    return MARK_BEGIN + "\n" + "\n".join(sections) + MARK_END


def build_document(sources: list[Source], *, existing: str = "") -> Document:
    """Merge the sources into the project's rules text without losing the team's
    own writing and without exceeding the API's 64KB cap."""
    if not sources:
        raise ValueError("build_document needs at least one source")

    sections: list[str] = []
    for source in sources:
        section = _section(source)
        if len(_render([section]).encode("utf-8")) > MAX_RULES_BYTES:
            raise ImportTooLarge(
                f"{source.label} alone exceeds the {MAX_RULES_BYTES // 1024}KB rules cap"
            )
        sections.append(section)

    truncated_at: str | None = None
    while len((existing + "\n\n" + _render(sections)).encode("utf-8")) > MAX_RULES_BYTES:
        if len(sections) == 1:
            raise ImportTooLarge(
                f"{sources[0].label} leaves no room under the"
                f" {MAX_RULES_BYTES // 1024}KB rules cap"
            )
        truncated_at = sources[len(sections) - 1].label
        sections.pop()

    block = _render(sections)
    if MARK_BEGIN in existing and MARK_END in existing:
        start = existing.index(MARK_BEGIN)
        end = existing.index(MARK_END, start) + len(MARK_END)
        merged = existing[:start] + block + existing[end:]
        return Document(text=merged, replaced=True, truncated_at=truncated_at)
    if existing.strip():
        return Document(
            text=existing.rstrip("\n") + "\n\n" + block + "\n",
            replaced=False,
            truncated_at=truncated_at,
        )
    return Document(text=block + "\n", replaced=False, truncated_at=truncated_at)


def _http(url: str, method: str, token: str | None, body: dict | None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"{}")
        except ValueError:
            payload = {}
        return exc.code, payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import CLAUDE.md / AGENTS.md / Cursor rules into a project's rules."
    )
    parser.add_argument("--root", default=".", help="repository to read (default: .)")
    parser.add_argument("--project-id", required=True, help="target Harness project id")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="identity API base")
    parser.add_argument(
        "--apply", action="store_true", help="send the merged document (default: plan only)"
    )
    parser.add_argument(
        "--token-env",
        default="HARNESS_TOKEN",
        help="environment variable holding the bearer token (default HARNESS_TOKEN)",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    http: Callable[..., tuple[int, dict]] | None = None,
    environ: dict[str, str] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    env = os.environ if environ is None else environ
    send = http or _http

    sources = collect_sources(Path(args.root))
    if not sources:
        print(
            "nothing to import: no AGENTS.md, CLAUDE.md, .cursor/rules/*.mdc or"
            " .codex/rules/*.md under this directory"
        )
        return 0

    url = f"{args.base_url.rstrip('/')}/api/v1/projects/{args.project_id}/rules"
    dropped = sum(source.dropped_lines for source in sources)

    if not args.apply:
        document = build_document(sources, existing="")
        print(
            json.dumps(
                {
                    "action": "create",
                    "note": "the current document is read only with --apply;"
                    " this plan assumes the block is not there yet",
                    "sources": [source.label for source in sources],
                    "method": "PUT",
                    "path": f"/api/v1/projects/{args.project_id}/rules",
                    "url": url,
                    "bytes": len(document.text.encode("utf-8")),
                    "droppedSecretLines": dropped,
                    "truncatedAt": document.truncated_at,
                    "rulesText": document.text,
                },
                indent=2,
            )
        )
        return 0

    token = env.get(args.token_env, "")
    if not token:
        print(
            f"{args.token_env} is not set; refusing to send anything",
            file=sys.stderr,
        )
        return 2

    status, current = send(url, "GET", token, None)
    if status != 200:
        print(f"could not read the current rules ({status}): {current}", file=sys.stderr)
        return 1
    document = build_document(sources, existing=current.get("rulesText", ""))
    status, result = send(url, "PUT", token, {"rulesText": document.text})
    print(
        json.dumps(
            {
                "action": "replaced" if document.replaced else "appended",
                "status": status,
                "sources": [source.label for source in sources],
                "bytes": len(document.text.encode("utf-8")),
                "droppedSecretLines": dropped,
                "truncatedAt": document.truncated_at,
                "updatedAt": result.get("updatedAt"),
            },
            indent=2,
        )
    )
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    sys.exit(main())
