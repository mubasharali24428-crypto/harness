"""Linux Landlock boundary — runs only where the runtime permits it."""

import subprocess
import sys

import pytest

from harness_sandbox import confinement

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="landlock is linux-only")


def test_landlock_enforces_write_boundary_when_supported(tmp_path):
    if not confinement.landlock_supported():
        pytest.skip("landlock not usable in this runtime")

    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside.txt"
    code = f"""
import pathlib
pathlib.Path({str(ws / 'inside.txt')!r}).write_text('ok')
try:
    pathlib.Path({str(outside)!r}).write_text('nope')
except PermissionError:
    raise SystemExit(3)
raise SystemExit(0)
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        preexec_fn=lambda: confinement.apply_landlock_in_child(ws, False),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 3, (proc.returncode, proc.stderr)
    assert (ws / "inside.txt").read_text() == "ok"
    assert not outside.exists()
