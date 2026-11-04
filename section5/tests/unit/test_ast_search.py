"""W4 spike: AST search is useful and cannot leave the workspace."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from harness_sandbox import ast_search


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "retry.py").write_text(
        '"""Retry policy."""\n'
        "\n"
        "RETRY_BACKOFF_SECONDS = 5\n"
        "\n"
        "\n"
        "class RetryPolicy:\n"
        "    def compute_backoff(self, attempt):\n"
        "        return RETRY_BACKOFF_SECONDS * attempt\n"
        "\n"
        "    class Inner:\n"
        "        pass\n"
        "\n"
        "\n"
        "async def flush_backoff_queue():\n"
        "    pass\n",
        encoding="utf-8",
    )
    (root / "pkg" / "notes.txt").write_text("compute_backoff is mentioned here\n",
                                            encoding="utf-8")
    (tmp_path / "outside.py").write_text("def compute_backoff_secret():\n    pass\n",
                                         encoding="utf-8")
    os.symlink(tmp_path / "outside.py", root / "pkg" / "escape.py")
    return root


def test_finds_definitions_by_substring(tmp_path):
    root = _workspace(tmp_path)
    out = ast_search.search(root, "backoff")
    names = {r["qualname"] for r in out["results"]}
    assert names == {
        "RETRY_BACKOFF_SECONDS",
        "RetryPolicy.compute_backoff",
        "flush_backoff_queue",
    }
    assert out["truncated"] is False
    assert out["skipped"] == ["pkg/escape.py"]


def test_kind_filter_and_line_numbers(tmp_path):
    root = _workspace(tmp_path)
    out = ast_search.search(root, "backoff", kind="function")
    assert [r["qualname"] for r in out["results"]] == ["flush_backoff_queue"]
    by_name = {r["qualname"]: r for r in ast_search.search(root, "backoff")["results"]}
    assert by_name["RETRY_BACKOFF_SECONDS"]["line"] == 3
    assert by_name["RetryPolicy.compute_backoff"]["kind"] == "method"
    assert by_name["RetryPolicy.compute_backoff"]["line"] == 7


def test_symlink_out_of_the_workspace_is_never_read(tmp_path):
    root = _workspace(tmp_path)
    out = ast_search.search(root, "compute_backoff_secret")
    assert out["results"] == []
    assert "pkg/escape.py" in out["skipped"]


def test_non_python_files_are_ignored(tmp_path):
    root = _workspace(tmp_path)
    out = ast_search.search(root, "compute_backoff is mentioned")
    assert out["results"] == []
    assert out["scannedFiles"] == 1  # retry.py; notes.txt is not Python, the link is skipped


def test_syntax_error_is_recorded_and_other_files_still_search(tmp_path):
    root = _workspace(tmp_path)
    (root / "pkg" / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    out = ast_search.search(root, "backoff")
    assert out["results"], "a broken file must not hide the other matches"
    assert any(e["path"] == "pkg/broken.py" for e in out["errors"])


def test_result_cap_truncates_and_says_so(tmp_path):
    root = tmp_path / "many"
    root.mkdir()
    for index in range(20):
        (root / f"m{index:02d}.py").write_text(f"def target_{index}():\n    pass\n",
                                               encoding="utf-8")
    out = ast_search.search(root, "target", max_results=5)
    assert len(out["results"]) == 5
    assert out["truncated"] is True


def test_search_writes_nothing(tmp_path):
    root = _workspace(tmp_path)

    def digest() -> dict[str, str]:
        rows = {}
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                rows[path.relative_to(root).as_posix()] = hashlib.sha256(
                    path.read_bytes()).hexdigest()
        return rows

    before = digest()
    ast_search.search(root, "backoff")
    assert digest() == before


def test_refuses_an_unusable_root_or_query(tmp_path):
    with pytest.raises(ast_search.AstSearchError):
        ast_search.search(tmp_path / "missing", "x")
    with pytest.raises(ast_search.AstSearchError):
        ast_search.search(tmp_path, "   ")


def test_cli_prints_one_json_object(tmp_path, capsys):
    root = _workspace(tmp_path)
    code = ast_search.main(["--root", str(root), "--query", "backoff"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["query"] == "backoff" and out["results"]
    bad = ast_search.main(["--root", str(tmp_path / "missing"), "--query", "x"])
    assert bad == 2 and "error" in json.loads(capsys.readouterr().out)
