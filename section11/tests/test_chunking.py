"""Unit checks for the pure layer: walker filtering + deterministic windows."""

from harness_context.chunking import (
    chunk_file,
    chunk_text,
    iter_repo_files,
    lang_for,
    should_index,
)


def test_should_index_filters_noise():
    assert should_index("pkg/widget_store.py")
    assert should_index("docs/briefs/graph.md")
    assert not should_index("skipme.txt")
    assert not should_index("node_modules/junk.js")
    assert not should_index(".git/config.py")  # extension matches but dir is law
    assert not should_index("package-lock.json")


def test_lang_for_supported_set():
    assert lang_for("a.py") == "python"
    assert lang_for("b.TS") == "typescript"
    assert lang_for("c.md") == "markdown"
    assert lang_for("d.sql") == "sql"
    assert lang_for("e.json") == "json"
    assert lang_for("f.rs") is None


def test_walker_is_sorted_and_skips_dirs(tmp_path):
    (tmp_path / "zeta.py").write_text("x = 1\n")
    (tmp_path / "alpha.py").write_text("y = 2\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "noise.js").write_text("var n;\n")
    assert iter_repo_files(tmp_path) == ["alpha.py", "zeta.py"]


def test_windows_cover_every_line_exactly_once():
    text = "\n".join(f"line {i}" for i in range(1, 251))  # 250 lines
    chunks = chunk_text("big.py", text, window=120, overlap=20)
    # step=100: windows [1..120], [101..220], [201..250]
    assert [(c.start_line, c.end_line) for c in chunks] == [
        (1, 120),
        (101, 220),
        (201, 250),
    ]
    covered = [ln for c in chunks for ln in range(c.start_line, c.end_line + 1)]
    assert len(set(covered)) == 250  # every line retrievable from some chunk


def test_small_file_single_chunk_and_empty_file_no_chunks(tmp_path):
    f = tmp_path / "one.py"
    f.write_text("a = 1\nb = 2\n")
    chunks = chunk_file(tmp_path, "one.py")
    assert len(chunks) == 1 and chunks[0].lang == "python"
    (tmp_path / "empty.md").write_text("")
    assert chunk_file(tmp_path, "empty.md") == []


def test_identical_bytes_identical_hashes_changed_bytes_different_hash():
    a = chunk_text("x.py", "value = 42\n", window=10, overlap=2)
    b = chunk_text("x.py", "value = 42\n", window=10, overlap=2)
    c = chunk_text("x.py", "value = 43\n", window=10, overlap=2)
    assert a[0].chunk_hash == b[0].chunk_hash
    assert a[0].chunk_hash != c[0].chunk_hash
