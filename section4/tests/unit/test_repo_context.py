"""W2 (N02) — the agent sees the repo before it starts guessing.

The capability gap's own words: *"goal + plan text only, and runtime context is
switched off in the prompt (`includeRuntimeContext:false`)"*, called "the single
biggest quality lever after A1". The runtime key is the SDK's, not ours, so what
this product can do is put the baseline in front of the model itself: the shape of
the tree, what the README says it is, what git thinks changed, and which dependency
manifests exist.

Bounded on purpose. A context block that grows with the repo is a way to turn a
500-file project into an uncapped prompt and pay for it on every run, so the
limits are part of the contract and are tested.
"""

import subprocess

from harness_orchestrator.repo_context import build_repo_context


def _repo(tmp_path):
    root = tmp_path / "ws"
    (root / "src" / "app").mkdir(parents=True)
    (root / "src" / "app" / "main.py").write_text("print('hi')\n")
    (root / "src" / "util.py").write_text("X = 1\n")
    (root / "README.md").write_text("# Widget\n\nDoes widget things.\n")
    (root / "package.json").write_text('{"name":"widget","dependencies":{"react":"^19"}}\n')
    (root / "pyproject.toml").write_text('[project]\nname = "widget"\n')
    # Noise that must never reach a prompt: it is large, and it is not the repo.
    (root / "node_modules" / "react").mkdir(parents=True)
    (root / "node_modules" / "react" / "index.js").write_text("module.exports={}\n")
    (root / ".venv" / "lib").mkdir(parents=True)
    (root / ".venv" / "lib" / "x.py").write_text("noise\n")
    return root


def test_the_tree_is_visible_without_node_modules_noise(tmp_path):
    block = build_repo_context(_repo(tmp_path))
    assert "src/util.py" in block or "util.py" in block
    assert "main.py" in block
    assert "node_modules" not in block
    assert ".venv" not in block


def test_the_readme_says_what_the_project_is(tmp_path):
    block = build_repo_context(_repo(tmp_path))
    assert "Does widget things." in block


def test_dependency_manifests_are_named(tmp_path):
    block = build_repo_context(_repo(tmp_path))
    assert "package.json" in block
    assert "pyproject.toml" in block


def test_git_status_is_included_when_the_workspace_is_a_repo(tmp_path):
    root = _repo(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=root, check=False)
    (root / "src" / "util.py").write_text("X = 2\n")
    block = build_repo_context(root)
    assert "util.py" in block
    assert "git" in block.lower()


def test_a_non_repo_workspace_does_not_raise(tmp_path):
    """The seeded workspace need not be a repo at all; context must degrade, not
    fail the run."""
    block = build_repo_context(_repo(tmp_path))
    assert isinstance(block, str) and block


def test_the_block_is_hard_bounded(tmp_path):
    """Bounded even when the repo is not: the cap is the contract."""
    root = _repo(tmp_path)
    for i in range(400):
        (root / "src" / f"f{i:03d}.py").write_text("pass\n")
    (root / "README.md").write_text("# Widget\n\n" + ("word " * 5000))
    block = build_repo_context(root, max_chars=2000)
    assert len(block) <= 2000


def test_an_empty_workspace_yields_nothing_rather_than_a_shell_of_headers(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert build_repo_context(empty) == ""


def test_a_missing_workspace_is_not_an_exception(tmp_path):
    assert build_repo_context(tmp_path / "does-not-exist") == ""
