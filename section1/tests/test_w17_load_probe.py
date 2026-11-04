"""A failed isolated stack must stop the probe before any HTTP traffic."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "scripts/loadtest/run_probe.sh"


@pytest.mark.parametrize("args", [["smoke"], ["run", "8", "1", "w17-test"]])
def test_failed_compose_stops_probe_before_health_checks(tmp_path, args):
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\nexit 42\n")
    docker.chmod(0o755)
    curl = tmp_path / "curl"
    marker = tmp_path / "curl-was-called"
    curl.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 0\n")
    curl.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}

    result = subprocess.run([str(PROBE), *args], env=env, capture_output=True, timeout=5)

    assert result.returncode == 42
    assert not marker.exists()


def test_pitr_drill_without_env_reaches_compose_on_bash_32(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    drill = scripts / "pitr_drill.sh"
    # Avoid process substitution, which restricted test sandboxes may block;
    # logging is unrelated to whether the no-.env path reaches Compose.
    script = (ROOT / "scripts/pitr_drill.sh").read_text()
    drill.write_text(script.replace('exec > >(tee -a "$OUT_LOG") 2>&1',
                                    'exec >>"$OUT_LOG" 2>&1'))
    docker = tmp_path / "docker"
    docker.write_text("#!/bin/sh\nexit 42\n")
    docker.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
           "PITR_DRILL_OUT": str(tmp_path / "out.json"),
           "PITR_DRILL_LOG": str(tmp_path / "drill.log")}

    result = subprocess.run(["/bin/bash", str(drill)], env=env, capture_output=True, timeout=5)

    assert result.returncode == 42
    assert b"unbound variable" not in result.stderr


def _fake_docker(tmp_path, *, existing_project_containers=False, fail_on="up"):
    """Docker stub that logs argv and fails at `up`; optionally claims a live project."""
    log = tmp_path / "docker.log"
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        f"echo \"$*\" >> '{log}'\n"
        + ('case "$*" in *label=com.docker.compose.project=*) echo deadbeef; exit 0;; esac\n'
           if existing_project_containers else "")
        + 'case "$*" in "volume inspect"*) exit 1;; esac\n'
        + f'case "$*" in *" {fail_on} "*) exit 42;; esac\nexit 0\n'
    )
    docker.chmod(0o755)
    return docker, log


def _run_drill(tmp_path, extra_env=None):
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    drill = scripts / "pitr_drill.sh"
    script = (ROOT / "scripts/pitr_drill.sh").read_text()
    drill.write_text(script.replace('exec > >(tee -a "$OUT_LOG") 2>&1',
                                    'exec >>"$OUT_LOG" 2>&1'))
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
           "PITR_DRILL_OUT": str(tmp_path / "out.json"),
           "PITR_DRILL_LOG": str(tmp_path / "drill.log"), **(extra_env or {})}
    return subprocess.run(["/bin/bash", str(drill)], env=env, capture_output=True, timeout=10)


def test_pitr_drill_defaults_to_unique_isolated_names(tmp_path):
    _, log = _fake_docker(tmp_path)
    result = _run_drill(tmp_path)
    assert result.returncode == 42
    calls = log.read_text()
    # Never the live compose project, network, or the old fixed scratch names.
    assert "-p harness-pitr-" in calls
    assert " -p harness " not in calls
    old_fixed_names = ("harness-pitr ", "harness_pgdata_pitr", "harness-walg-db-1",
                       "/tmp/pitr-backup.out")
    for fixed in old_fixed_names:
        assert fixed not in calls
    assert "--env-file" not in calls


def test_pitr_drill_refuses_an_existing_compose_project(tmp_path):
    _, log = _fake_docker(tmp_path, existing_project_containers=True)
    result = _run_drill(tmp_path, {"PITR_PROJECT": "someone-elses-stack"})
    assert result.returncode != 0
    assert b"already has containers" in result.stderr
    calls = log.read_text()
    # No destructive or mutating call may precede the refusal.
    for verb in (" up ", " down ", " rm ", " volume rm", " stop "):
        assert verb not in calls


def test_pitr_drill_refuses_the_live_project_name(tmp_path):
    _, log = _fake_docker(tmp_path)
    result = _run_drill(tmp_path, {"PITR_PROJECT": "harness"})
    assert result.returncode != 0
    assert not log.exists() or " up " not in log.read_text()
