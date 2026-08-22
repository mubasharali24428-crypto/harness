from pathlib import Path

import pytest
from fixtures.golden_fixtures import GOLDEN_DIFF_HELLO, GOLDEN_DIFF_NEW_FILE

from harness_sandbox.diff_engine import apply_unified_diff
from harness_sandbox.policy import SecurityPolicyViolationError


def test_apply_diff_new_file(tmp_path: Path):
    apply_unified_diff(
        workspace_dir=tmp_path,
        target_path="new_module.py",
        diff_text=GOLDEN_DIFF_NEW_FILE,
    )
    new_file = tmp_path / "new_module.py"
    assert new_file.exists()
    assert "def add(a, b):" in new_file.read_text()


def test_apply_diff_existing_file(tmp_path: Path):
    target = tmp_path / "hello.py"
    target.write_text('print("hello")\n')

    apply_unified_diff(
        workspace_dir=tmp_path,
        target_path="hello.py",
        diff_text=GOLDEN_DIFF_HELLO,
    )
    content = target.read_text()
    assert 'print("world")' in content


def test_apply_diff_forbidden_path(tmp_path: Path):
    with pytest.raises(SecurityPolicyViolationError):
        apply_unified_diff(
            workspace_dir=tmp_path,
            target_path="auth/secrets.py",
            diff_text=GOLDEN_DIFF_NEW_FILE,
            forbidden_paths=["auth/secrets.py"],
        )
