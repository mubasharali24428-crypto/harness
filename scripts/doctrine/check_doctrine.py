#!/usr/bin/env python3
"""check_doctrine.py — wave-close doctrine linter for ALEX_TEAM.

Checks the process conventions defined in docs/doctrine/mission-command-doctrine.md:
  §1/§2  every graph brief has an INTENT one-liner (first non-title line)
  §3     every lane file carries a ## BACKBRIEF section
  §4     AAR ledger rows are well-formed and classes are valid
Exit code 1 = doctrine violation found (blocks wave-close ritual).

Usage:
    python scripts/doctrine/check_doctrine.py docs/briefs [--ledger docs/doctrine/aar-ledger.csv]
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

INTENT_RE = re.compile(r"^INTENT\s*[—:-]\s*\S", re.IGNORECASE | re.MULTILINE)
BACKBRIEF_RE = re.compile(r"^##\s+BACKBRIEF\b", re.MULTILINE | re.IGNORECASE)
VALID_CLASSES = {"SUSTAIN", "IMPROVE"}
MAX_INTENT_WORDS = 40


def check_brief(path: Path) -> list[str]:
    """One violation string per problem in a brief/lane file."""
    problems: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: unreadable ({exc})"]
    lines = text.splitlines()
    # First non-empty, non-title line must be the INTENT line (§2).
    body = [ln.strip() for ln in lines if ln.strip()]
    first = next((ln for ln in body if not ln.startswith("#")), "")
    if not INTENT_RE.match(first):
        problems.append(f"{path}: §2 no INTENT one-liner as first non-title line")
    else:
        words = len(first.split())
        if words > MAX_INTENT_WORDS + 4:  # +4 tolerance for 'INTENT —' prefix
            problems.append(f"{path}: §2 intent line too long ({words} words > {MAX_INTENT_WORDS})")
    if not BACKBRIEF_RE.search(text):
        problems.append(f"{path}: §3 missing '## BACKBRIEF' section")
    return problems


def check_ledger(path: Path) -> list[str]:
    """Ledger CSV: header present, classes valid, evidence + owner non-empty."""
    problems: list[str] = []
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    except FileNotFoundError:
        return [f"{path}: ledger missing (§4 requires one at first wave close)"]
    except OSError as exc:
        return [f"{path}: unreadable ({exc})"]
    required = {"wave", "item", "class", "evidence", "owner"}
    for i, row in enumerate(rows, start=2):  # header is line 1
        missing = required - set(row)
        if missing:
            problems.append(f"{path}:{i}: missing columns {sorted(missing)}")
            continue
        if row["class"].strip().upper() not in VALID_CLASSES:
            problems.append(f"{path}:{i}: class {row['class']!r} not in {sorted(VALID_CLASSES)}")
        for col in ("item", "evidence", "owner"):
            if not row[col].strip():
                problems.append(f"{path}:{i}: empty {col}")
    return problems


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    briefs = Path(args[0]) if args else Path("docs/briefs")
    ledger = Path("docs/doctrine/aar-ledger.csv")
    for i, a in enumerate(argv):
        if a == "--ledger" and i + 1 < len(argv):  # space-separated form
            ledger = Path(argv[i + 1])
        elif a.startswith("--ledger="):  # equals form
            ledger = Path(a.split("=", 1)[1])
    problems: list[str] = []
    files = sorted(briefs.rglob("*.md")) if briefs.is_dir() else ([briefs] if briefs.exists() else [])
    for f in files:
        problems.extend(check_brief(f))
    # The template itself legitimately contains placeholder blocks — skip it.
    problems = [p for p in problems if "command-brief-template" not in p]
    problems.extend(check_ledger(ledger))
    if problems:
        print(f"DOCTRINE CHECK: {len(problems)} violation(s)")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"DOCTRINE CHECK: OK ({len(files)} brief(s) checked; ledger {ledger})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
