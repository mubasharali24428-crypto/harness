#!/usr/bin/env python3
"""Every HARNESS_* environment variable the deployed code reads must be documented.

Why this gate exists (W15 gap fill, 2026-10-29): `.env.example` is the only
operator-facing catalogue of runtime knobs, and it carried 17 of the 56 the code
actually reads. Security and money switches were among the missing
(`HARNESS_SANDBOX_ALLOW_UNCONFINED`, `HARNESS_ALLOW_SELF_APPROVAL`,
`HARNESS_VISION_MODELS`, `HARNESS_TEAM_BUDGET_WARN_PCT`), so an operator could
only find them by reading Python — and could not tell a safe default from a
dangerous one.

Scope is the RUNTIME surface only: `section*/src`, `contracts/src` and
`deploy/runners`. Knobs read exclusively by tests, drills or image builds stay
undocumented on purpose (they are not deployment knobs).

Exit 0: catalogue complete. Exit 1: undocumented knobs (file:line each).

The reverse direction (documented but unread) is deliberately NOT checked:
compose and alertmanager.yml consume many entries through `${HARNESS_*}`
interpolation, and pydantic settings read others, so "no `os.environ` read" is
not evidence of a stale entry.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNTIME_GLOBS = ("section*/src/**/*.py", "contracts/src/**/*.py", "deploy/runners/**/*.py")
READ_RE = re.compile(
    r"""(?:os\.environ\.get|os\.getenv)\(\s*["'](HARNESS_[A-Z0-9_]+)["']"""
    r"""|os\.environ\[\s*["'](HARNESS_[A-Z0-9_]+)["']\s*\]"""
)
# A documented line is `KEY=` or `# KEY=` / `#KEY=` (the file documents optional
# knobs by commenting them with their default).
DOC_RE = re.compile(r"^#?\s*(HARNESS_[A-Z0-9_]+)\s*=", re.MULTILINE)


def runtime_reads() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for pattern in RUNTIME_GLOBS:
        for path in sorted(REPO.glob(pattern)):
            if ".venv" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                match = READ_RE.search(line)
                if match:
                    name = next(g for g in match.groups() if g)
                    found.setdefault(name, []).append(f"{path.relative_to(REPO)}:{lineno}")
    return found


def documented() -> set[str]:
    return set(DOC_RE.findall((REPO / ".env.example").read_text()))


def main() -> int:
    reads = runtime_reads()
    docs = documented()
    missing = {name: sites for name, sites in reads.items() if name not in docs}

    for name in sorted(missing):
        print(f"UNDOCUMENTED {name} — read at {', '.join(missing[name][:3])}")

    if missing:
        print(
            f"\n{len(missing)} undocumented of {len(reads)} runtime knobs "
            f"({len(docs)} entries in .env.example). Document each in "
            f".env.example with its code default and what it changes.",
            file=sys.stderr,
        )
        return 1
    print(f"env knob catalogue complete: {len(reads)} runtime reads, all in .env.example")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
