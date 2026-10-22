"""confine_argv: wrapped argv runs the target, writes only under allowed roots,
and refuses without confinement.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from harness_sandbox import confinement

requires_confinement = pytest.mark.skipif(
    not (confinement.seatbelt_available() or confinement.landlock_supported()),
    reason="no OS confinement on this host",
)


def _write_code(path: Path) -> str:
    return f"open({str(path)!r}, 'w').write('x')"


@requires_confinement
def test_confined_process_writes_inside_its_workspace(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    argv = confinement.confine_argv(
        [sys.executable, "-c", _write_code(workspace / "ok.txt")],
        workspace=workspace,
        tmpdir=tmp_path / "tmp",
    )
    done = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert (workspace / "ok.txt").read_text() == "x"


@requires_confinement
def test_confined_process_cannot_write_outside_its_roots(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = Path(tempfile.mkdtemp(prefix="confine-probe-"))
    try:
        argv = confinement.confine_argv(
            [sys.executable, "-c", _write_code(outside / "escape.txt")],
            workspace=workspace,
            tmpdir=tmp_path / "tmp",
        )
        done = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        assert done.returncode != 0
        assert not (outside / "escape.txt").exists()
    finally:
        shutil.rmtree(outside, ignore_errors=True)


def test_refuses_to_wrap_when_no_confinement_is_available(tmp_path, monkeypatch):
    monkeypatch.setattr(confinement, "seatbelt_available", lambda: False)
    monkeypatch.setattr(confinement, "landlock_supported", lambda: False)
    with pytest.raises(confinement.ConfinementUnavailableError):
        confinement.confine_argv(
            ["/bin/echo", "hi"], workspace=tmp_path, tmpdir=tmp_path / "tmp"
        )
