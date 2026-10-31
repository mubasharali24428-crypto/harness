"""Stage 2: a throwaway compose project's `down -v` must never reach the live run volume.

`harness_runs` carries an explicit `name:` (worker-daemon mounts it by literal name), which
compose does NOT prefix with the project. So `docker compose -p <anything> down -v` resolves
to the live volume, protected only by Docker's "in use" refusal. The name is therefore an
override variable, and every script that tears down an isolated project must set it.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = REPO_ROOT / "deploy" / "docker-compose.yml"
_VAR = "${HARNESS_RUNS_VOLUME:-harness_runs}"
# Scripts that create a throwaway compose project and run `down -v` on it.
_ISOLATED_TEARDOWN = [
    "scripts/pitr_drill.sh",
    "scripts/loadtest/run_probe.sh",
    "scripts/loadtest/run_ladder.sh",
    "deploy/runners/rollback_rehearsal.sh",
]


def test_runs_volume_name_is_overridable_and_default_unchanged():
    vols = yaml.safe_load(_COMPOSE.read_text())["volumes"]
    assert vols["harness_runs"]["name"] == _VAR


def test_worker_daemon_names_the_same_volume_compose_creates():
    daemon = yaml.safe_load(_COMPOSE.read_text())["services"]["worker-daemon"]
    assert daemon["environment"]["HARNESS_RUNS_VOLUME"] == _VAR


def test_isolated_teardown_scripts_scope_the_runs_volume():
    for rel in _ISOLATED_TEARDOWN:
        text = (REPO_ROOT / rel).read_text()
        assert "down -v" in text, rel
        scoped = re.search(r"^export HARNESS_RUNS_VOLUME=", text, re.M)
        assert scoped, f"{rel} does not scope harness_runs"
