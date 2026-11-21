"""G-H down -v guard (P7): the pitr drill's teardown must never resolve to a
live volume — runs storage, store, or database. The drill already scopes the
runs volume + network via env (test_deploy_volume_isolation pins the runs
part); this file pins THE WHOLE teardown surface of scripts/pitr_drill.sh:

1. every docker volume the drill touches is project-prefixed (refuses live names);
2. cleanup's `down -v` runs against COMPOSE with -p PROJECT (throwaway project);
3. a guard refuses live volume names in PITR_* overrides;
4. WALG_S3_PREFIX is drill-namespaced, never the live archive prefix.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DRILL = (REPO / "scripts" / "pitr_drill.sh").read_text()


def test_cleanup_down_v_is_project_scoped():
    start = DRILL.index("cleanup() {")
    end = DRILL.index("\n}", start)
    cleanup = DRILL[start:end]
    assert 'COMPOSE[@]}" down -v' in cleanup, "teardown must use the -p PROJECT compose array"
    assert "PITR_KEEP" in DRILL, "operator escape hatch documented in-script"


def test_drill_volumes_are_project_prefixed():
    # the restore volume is explicitly refused if it exists (a live-name collision guard)
    assert re.search(r'PITR_VOLUME="\$\{PROJECT\}_pgdata_restore"', DRILL)
    assert "docker volume inspect \"$PITR_VOLUME\"" in DRILL
    assert "already exists" in DRILL
    # runs volume scoped like the isolation test demands
    assert re.search(r'export HARNESS_RUNS_VOLUME="\$\{PROJECT\}_runs"', DRILL)
    # network kept off the live default network
    assert re.search(r'export HARNESS_NETWORK_DEFAULT="\$\{PROJECT\}_default"', DRILL)


def test_drill_refuses_a_project_that_has_live_containers():
    assert "already has containers" in DRILL
    assert re.search(r'docker ps .*project.*equal', DRILL) or "docker ps" in DRILL


def test_wal_archive_prefix_is_drill_namespaced():
    assert re.search(r'WALG_S3_PREFIX="s3://\$\{BUCKET\}/pitr-', DRILL), \
        "each drill streams under its own prefix; the live archive is never touched"
    assert 'WALG_FILE_PREFIX=""' in DRILL, \
        "the drill must unset the live file-archive prefix"


def test_restore_container_name_is_project_scoped():
    assert re.search(r'PITR_CONTAINER="\$\{PROJECT\}-restore"', DRILL)
