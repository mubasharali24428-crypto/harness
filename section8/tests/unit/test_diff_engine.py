"""Unit tests for the diff engine parser."""


from harness_review.diff_engine import (
    extract_unchanged_context,
    parse_diff,
    summarize_diff,
)
from harness_review.models import ChangeStatus
from tests.fixtures.golden_fixtures import GOLDEN_DIFF, GOLDEN_DIFF_MODIFY, GOLDEN_DIFF_RISK


def test_parse_simple_new_file():
    files = parse_diff(GOLDEN_DIFF)
    assert len(files) == 1
    f = files[0]
    assert f.path == "src/auth.py"
    assert f.status == ChangeStatus.ADDED
    assert f.additions == 4
    assert f.deletions == 0


def test_parse_modified_file():
    files = parse_diff(GOLDEN_DIFF_MODIFY)
    assert len(files) == 1
    f = files[0]
    assert f.path == "src/calc.py"
    assert f.status == ChangeStatus.MODIFIED
    assert f.additions >= 1
    assert f.deletions >= 1


def test_parse_risk_diff_has_additions_and_deletions():
    files = parse_diff(GOLDEN_DIFF_RISK)
    assert len(files) == 1
    f = files[0]
    assert f.status == ChangeStatus.MODIFIED
    assert f.additions > 0
    assert f.deletions > 0


def test_parse_empty_diff():
    assert parse_diff("") == []
    assert parse_diff("no diff content") == []


def test_summarize_diff():
    summary = summarize_diff(GOLDEN_DIFF)
    assert summary["total_files"] == 1
    assert summary["total_additions"] == 4
    assert summary["total_deletions"] == 0
    assert len(summary["files"]) == 1


def test_extract_unchanged_context_with_no_context():
    result = extract_unchanged_context("")
    assert result is None


def test_extract_unchanged_context_with_context():
    diff = (
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1,3 +1,4 @@\n"
        " import os\n"
        " from fastapi import FastAPI\n"
        "+app = FastAPI()\n"
        " \n"
        " if __name__ == '__main__':\n"
        "     app.run()\n"
    )
    result = extract_unchanged_context(diff)
    assert result is not None
    assert "import os" in result


def test_parse_multiple_files():
    diff = (
        "diff --git a/src/a.py b/src/a.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/src/a.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+def a(): pass\n"
        "\n"
        "diff --git b/src/b.py b/src/b.py\n"
        "deleted file mode 100644\n"
        "--- a/src/b.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-def b(): pass\n"
    )
    files = parse_diff(diff)
    assert len(files) == 2
    assert files[0].status == ChangeStatus.ADDED
    assert files[0].path == "src/a.py"
    assert files[1].status == ChangeStatus.REMOVED
    assert files[1].path == "src/b.py"
