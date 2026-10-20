"""The program documents stay honest (master prompt parts 18, 34, 35, 36).

* markdown links in `docs/briefs` resolve;
* the durable brief (`PROJECT-STATE.md`) stays short;
* the capability matrix has exactly 61 rows with a known state;
* every `verified` row cites an existing path, and each `evidence/...` path it cites exists;
* parts gated on outside parties are never plain `verified` (cap 8);
* the process-part rows cite the files that satisfy them; those files' own path references exist.

Pure file reads: no server, no database.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BRIEFS = ROOT / "docs" / "briefs"
MATRIX = BRIEFS / "capability-matrix.md"
STATE = BRIEFS / "PROJECT-STATE.md"
STATE_LINE_LIMIT = 130
STATES = {"verified", "implemented", "partial", "missing", "blocked"}
SKIP_DIRS = {"node_modules", ".venv", ".git", "__pycache__", ".next", "build"}
# Files the docs say are absent from the repository (see clean-checkout-gaps.md, process-parts.md).
DOCUMENTED_ABSENT = {
    "container_runtime.py",
    "model_relay.py",
    "pilot_metrics.py",
    "docs/master-prompts/harness-multiplayer-master-build-prompt.md",
}
# Parts that need an outside party for the last point (master prompt, owner direction).
EXTERNALLY_GATED = ("03", "37", "52", "53", "58", "61")

LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
PATHLIKE = re.compile(r"[\w.\[\]-]+(?:/[\w.\[\]-]+)*\.(?:md|py|tsx?|yml|yaml|json|sql|sh|mjs)\b")
BACKTICKED = re.compile(r"`([^`\n]+)`")
BASES = (ROOT, ROOT / "docs", BRIEFS)


def _files_by_name() -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    for p in ROOT.rglob("*"):
        if p.is_file() and not SKIP_DIRS.intersection(p.relative_to(ROOT).parts):
            out.setdefault(p.name, []).append(p)
    return out


@pytest.fixture(scope="module")
def names():
    return _files_by_name()


def _exists(token: str, names: dict[str, list[Path]]) -> bool:
    if "/" in token:
        return any((b / token).exists() for b in BASES)
    return token in names


def _matrix_rows() -> list[dict[str, str]]:
    rows = []
    for line in MATRIX.read_text(encoding="utf-8").splitlines():
        if re.match(r"\| \d\d \|", line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            rows.append(
                {
                    "n": cells[0],
                    "state": cells[3],
                    "evidence": "|".join(cells[4:]),
                    "word": cells[3].split()[0],
                }
            )
    return rows


def test_markdown_links_in_briefs_resolve():
    broken = []
    for md in sorted(BRIEFS.rglob("*.md")):
        for target in LINK.findall(md.read_text(encoding="utf-8", errors="replace")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if not (md.parent / target.split("#")[0].split("?")[0]).exists():
                broken.append(f"{md.relative_to(ROOT)} -> {target}")
    assert not broken, broken


def test_project_state_stays_a_short_brief():
    lines = STATE.read_text(encoding="utf-8").count("\n") + 1
    assert lines <= STATE_LINE_LIMIT, (
        f"PROJECT-STATE.md has {lines} lines; move history to evidence/s11/state-history.md"
    )


def test_matrix_has_61_rows_with_known_states():
    rows = _matrix_rows()
    assert [r["n"] for r in rows] == [f"{i:02d}" for i in range(1, 62)]
    assert {r["word"] for r in rows} <= STATES, {
        r["n"]: r["state"] for r in rows if r["word"] not in STATES
    }


def test_every_verified_row_cites_existing_evidence(names):
    problems = []
    for r in _matrix_rows():
        if r["word"] != "verified":
            continue
        cited = PATHLIKE.findall(r["evidence"])
        found = [t for t in cited if _exists(t, names)]
        dangling = [
            t
            for t in cited
            if t.startswith(("evidence/", "docs/briefs/evidence/")) and not _exists(t, names)
        ]
        if not found:
            problems.append(f"row {r['n']}: no cited path exists ({cited or 'none cited'})")
        if dangling:
            problems.append(f"row {r['n']}: evidence path missing {dangling}")
    assert not problems, problems


def test_externally_gated_parts_are_never_plain_verified():
    rows = {r["n"]: r for r in _matrix_rows()}
    for n in EXTERNALLY_GATED:
        r = rows[n]
        assert r["word"] != "verified" or "cap 8" in r["state"], (
            f"row {n} is {r['state']!r} without its cap"
        )


PROCESS_ROWS = {
    **dict.fromkeys(("01", "02", "15", "16", "17", "18", "19"), "process-parts.md"),
    **dict.fromkeys(("34", "35"), "final-deliverables.md"),
}


@pytest.mark.parametrize("row,artifact", sorted(PROCESS_ROWS.items()))
def test_process_rows_are_verified_and_cite_their_artifact(row, artifact):
    r = next(r for r in _matrix_rows() if r["n"] == row)
    assert r["word"] == "verified", f"row {row} is {r['state']!r}"
    assert artifact in r["evidence"], f"row {row} must cite {artifact}"


@pytest.mark.parametrize(
    "doc",
    [
        BRIEFS / "evidence" / "s11" / "process-parts.md",
        BRIEFS / "final-deliverables.md",
        BRIEFS / "stable-prompt-prefix.md",
    ],
    ids=lambda p: p.name,
)
def test_artifacts_named_in_process_docs_exist(doc, names):
    assert doc.exists(), f"{doc.relative_to(ROOT)} is missing"
    missing = []
    for tok in BACKTICKED.findall(doc.read_text(encoding="utf-8")):
        if any(c in tok for c in "<*") or not PATHLIKE.fullmatch(tok):
            continue
        if tok not in DOCUMENTED_ABSENT and not _exists(tok, names):
            missing.append(tok)
    assert not missing, sorted(set(missing))


def test_state_history_keeps_the_moved_sections():
    # moved verbatim from PROJECT-STATE.md, so its older path shorthand is not re-checked
    text = (BRIEFS / "evidence" / "s11" / "state-history.md").read_text(encoding="utf-8")
    for heading in (
        "## 3. Session 3 result",
        "## 3b.",
        "## 3c.",
        "## 3d.",
        "## 3e.",
        "## 3f.",
        "## 4. Verification record",
    ):
        assert heading in text, heading


COUNTS = re.compile(
    r"verified (\d+) · implemented (\d+) · partial (\d+) · missing (\d+) · blocked (\d+)"
)


@pytest.mark.parametrize(
    "doc", [MATRIX, STATE, BRIEFS / "final-deliverables.md"], ids=lambda p: p.name
)
def test_stated_state_counts_match_the_matrix(doc):
    rows = _matrix_rows()
    actual = tuple(
        sum(r["word"] == w for r in rows)
        for w in ("verified", "implemented", "partial", "missing", "blocked")
    )
    stated = COUNTS.search(doc.read_text(encoding="utf-8"))
    assert stated, f"{doc.name} has no 'verified N · implemented N · ...' counts line"
    assert tuple(int(x) for x in stated.groups()) == actual


def test_stable_prompt_prefix_records_cache_control_stance():
    text = (BRIEFS / "stable-prompt-prefix.md").read_text(encoding="utf-8")
    assert "(part 19.010)" in text and "No cache controls are used" in text
