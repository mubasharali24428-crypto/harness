"""Stage 2: the nightly backup wrapper keeps the properties that made past runs unsafe or silent."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKUP = REPO / "deploy" / "backup"
SCRIPTS = ["nightly.sh", "store-backup.sh", "store-restore.sh", "install-launchd.sh"]


def test_scripts_parse():
    for name in SCRIPTS:
        subprocess.run(["sh", "-n", str(BACKUP / name)], check=True)


def test_nightly_runs_every_step_and_never_starts_a_second_cluster():
    text = (BACKUP / "nightly.sh").read_text()
    for step in ("logical-dump", "walg-base", "store-snapshot", "archive-mirror"):
        assert f"step {step} " in text, step
    # Without --no-deps compose also starts walg-db, which would archive into the LIVE prefix.
    assert "run --rm --no-deps walg" in text
    assert "RESULT=FAILED" in text and "exit 1" in text


def test_store_restore_refuses_an_existing_volume():
    assert "refusing: volume" in (BACKUP / "store-restore.sh").read_text()


def test_catch_up_retention_and_visible_offsite_gap():
    nightly = (BACKUP / "nightly.sh").read_text()
    assert "--if-due" in nightly and "DUE_AFTER_HOURS" in nightly
    assert "WALG_RETENTION_KEEP=${WALG_RETENTION_KEEP:-7}" in nightly
    # --delete on the mirror is only safe behind the "source still has a base backup" guard.
    assert nightly.index("refusing to mirror") < nightly.index("rsync -a --delete")
    # An unconfigured off-site copy must be visible on every run, not silent.
    assert "WARN offsite" in nightly and "offsite=$OFFSITE" in nightly


def test_launchd_agent_catches_up_and_stays_awake():
    text = (BACKUP / "install-launchd.sh").read_text()
    assert "StartInterval" in text and "StartCalendarInterval" in text
    assert "/usr/bin/caffeinate" in text and "--if-due" in text
