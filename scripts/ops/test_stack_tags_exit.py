"""Pin what main() really does when `docker ps` fails (no Docker involved).

The success path is covered in test_stack_tags.py; these three cases only
characterise failure. `stack_tags.subprocess.run` is monkeypatched exactly as
the existing test does, so no daemon or binary is needed.
"""
from __future__ import annotations

import pytest

import stack_tags


class _FailedPs:
    """Canned `docker ps` failure: rc=1, daemon unreachable, no ps output."""

    returncode = 1
    stdout = ""
    stderr = "Cannot connect to the Docker daemon"


def _patch_failed_ps(monkeypatch) -> None:
    monkeypatch.setattr(stack_tags.subprocess, "run", lambda *a, **k: _FailedPs())


def test_main_should_exit_nonzero_when_docker_ps_fails(monkeypatch):
    _patch_failed_ps(monkeypatch)
    assert stack_tags.main() != 0


def test_failed_docker_ps_still_prints_the_report(monkeypatch, capsys):
    _patch_failed_ps(monkeypatch)
    stack_tags.main()
    out = capsys.readouterr().out
    assert "docker ps exited 1" in out
    assert any(line.startswith("Stack tag report") for line in out.splitlines())


def test_missing_docker_binary_raises_filenotfounderror(monkeypatch):
    # Current behaviour, not a decision about what should happen.
    def _no_docker(*args, **kwargs):
        raise FileNotFoundError("No such file or directory: 'docker'")

    monkeypatch.setattr(stack_tags.subprocess, "run", _no_docker)
    with pytest.raises(FileNotFoundError):
        stack_tags.main()
