"""P-15 / I-02 negative tests for the verdict-bot workflow (no service needed).

The bot's danger is accidental secret exposure: `secrets.*` expanded into a
run command line lands verbatim in Actions logs. These tests pin the SHAPE of
.github/workflows/verdict-bot.yml:

* every `secrets.*` reference sits in an `env:` mapping value — never in a
  `run:` block, never inside shell text;
* the job posts nothing unless confirm_post is explicitly true (dry-run
  default), and says so in the log;
* the job runs with least privilege (contents: read — it must never hold a
  token that could merge).

The secret-scrubbing of the comment BODY itself is pinned separately by
section4/tests/unit/test_verdict_comment.py (test_secrets_never_in_output et
al.); this file is the workflow-shape half of the same guarantee.

Run:  PYTHONPATH=scripts/ops ./section1/.venv/bin/python -m pytest \\
        scripts/ops/test_verdict_bot_workflow.py -q
"""
from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github" / "workflows" / "verdict-bot.yml"
)


def _lines() -> list[str]:
    return WORKFLOW.read_text().splitlines()


def test_workflow_file_exists():
    assert WORKFLOW.is_file(), f"missing {WORKFLOW}"


def test_every_secrets_reference_is_an_env_mapping_value():
    """A `secrets.X` anywhere except `KEY: ${{ secrets.X }}` is a leak shape.
    Comment lines (`#`) are prose, not expansion points, and are skipped."""
    env_value = re.compile(r"^\s*[A-Z_]+:\s*\$\{\{\s*secrets\.[A-Z_]+\s*\}\}\s*$")
    offenders = [
        ln for ln in _lines()
        if "secrets." in ln and not ln.lstrip().startswith("#")
        and not env_value.match(ln)
    ]
    assert offenders == [], f"secrets.* outside env mapping values: {offenders}"


def test_run_block_never_expands_secrets():
    """Split the YAML on `run:` blocks (indented block scalars) and assert none
    of the shell text contains a secrets expansion."""
    lines = _lines()
    in_run = False
    offenders = []
    for ln in lines:
        if re.match(r"^\s*run:\s*(\||>)?\s*$", ln):
            in_run = True
            continue
        if in_run:
            if ln.strip() and not ln.startswith((" " * 10, " " * 11, " " * 12)):
                in_run = False
            elif "secrets." in ln:
                offenders.append(ln)
    assert offenders == [], f"secrets.* expanded inside a run block: {offenders}"


def test_dry_run_is_the_default_and_stated():
    src = WORKFLOW.read_text()
    assert re.search(
        r"confirm_post:\s*\n(\s*description:.*\n)?\s*type: boolean\s*\n\s*default: false",
        src,
    ), "confirm_post must default to false"
    assert "printing the comment, posting nothing" in src
    assert "posting the advisory comment" in src


def test_least_privilege_permissions():
    src = WORKFLOW.read_text()
    assert "contents: read" in src, "the bot must never hold write scopes"
    assert "pull-requests: write" not in src, (
        "commenting goes through the GitHub service (section2), not the Actions token")


def test_post_module_is_the_recorded_facts_builder():
    src = WORKFLOW.read_text()
    assert "harness_orchestrator.verdict_post" in src
    assert "--dry-run" in src
