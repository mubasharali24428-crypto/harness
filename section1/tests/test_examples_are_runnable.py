"""W18 slice 3 — a sample repository that does not reproduce is worse than none.

Two examples are referenced from the README as the first thing a new user tries.
These tests keep them honest: the "fix a failing test" kata must actually fail
(and only on the bug it describes), the review example must pass, both must carry
the conventions file the guide tells people to import, and that file must import
cleanly through the migration tool — including dropping no secret-shaped line,
because an example that trips the importer is teaching the wrong thing.
"""

import importlib.util
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
FIX = EXAMPLES / "fix-a-failing-test"
REVIEW = EXAMPLES / "review-a-pr"

_spec = importlib.util.spec_from_file_location(
    "migrate_agent_config_examples", ROOT / "scripts" / "migrate_agent_config.py"
)
migrate = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = migrate
_spec.loader.exec_module(migrate)


def _pytest(example: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(example)],
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("example", [FIX, REVIEW], ids=["fix-a-failing-test", "review-a-pr"])
def test_every_example_has_the_files_the_readme_promises(example):
    assert (example / "README.md").is_file()
    assert (example / "AGENTS.md").is_file()


@pytest.mark.skipif(
    "pytest" not in sysconfig.get_paths()["purelib"] and not (ROOT / "section1" / ".venv").exists(),
    reason="needs a pytest on this interpreter",
)
def test_the_fix_example_fails_on_its_bug_and_says_which_test():
    result = _pytest(FIX)
    assert result.returncode != 0, "the kata's test must fail before the run"
    assert "test_calc.py" in result.stdout
    assert "increment" in (FIX / "AGENTS.md").read_text(encoding="utf-8")


def test_the_review_example_is_green_so_the_review_is_about_the_diff():
    if not (ROOT / "section1" / ".venv").exists():
        pytest.skip("no pytest available")
    result = _pytest(REVIEW)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("example", [FIX, REVIEW], ids=["fix-a-failing-test", "review-a-pr"])
def test_the_examples_conventions_import_cleanly(example):
    sources = migrate.collect_sources(example)
    assert [source.label for source in sources] == ["AGENTS.md"]
    assert sources[0].dropped_lines == 0, (
        "an example's AGENTS.md tripped the secret filter — fix the example,"
        " not the filter"
    )
    document = migrate.build_document(sources, existing="")
    assert migrate.MARK_BEGIN in document.text
    assert "## From AGENTS.md" in document.text
