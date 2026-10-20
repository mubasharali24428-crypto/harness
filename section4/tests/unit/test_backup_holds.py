"""Part 31/58: backup expiry waits for run retention holds (both backup scripts, fake binaries)."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
WALG = ROOT / "deploy" / "runners" / "backup.sh"
DUMP = ROOT / "deploy" / "backup" / "backup.sh"

# Answers the two hold queries from $FAKE_TABLE ('t'/'f'), $FAKE_HOLDS and $FAKE_PSQL_FAIL.
_PSQL = """\
[ -z "$FAKE_PSQL_FAIL" ] || exit 2
for a in "$@"; do last="$a"; done
case "$last" in
  *to_regclass*) echo "${FAKE_TABLE:-t}" ;;
  *count*) echo "${FAKE_HOLDS:-0}" ;;
esac
"""
_WALG = 'echo "$@" >> "$FAKE_LOG"\n'
_DOCKER = """\
case "$1" in
  inspect) exit 0 ;;
  exec)
    shift
    while [ "${1#-}" != "$1" ]; do shift; done  # flags such as -i
    shift                                      # container name
    case "$1" in
      pg_dump) echo dump-bytes ;;
      pg_restore) cat > /dev/null ;;
      psql) exec psql "$@" ;;
    esac ;;
esac
"""


def _bin(path: Path, name: str, body: str) -> None:
    f = path / name
    f.write_text("#!/bin/sh\n" + body)
    f.chmod(f.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def run(tmp_path):
    fakes = tmp_path / "bin"
    fakes.mkdir()
    for name, body in (("psql", _PSQL), ("wal-g", _WALG), ("docker", _DOCKER)):
        _bin(fakes, name, body)
    log = tmp_path / "walg.log"
    log.write_text("")

    def go(script, *args, **env):
        e = {**os.environ, "PATH": f"{fakes}:{os.environ['PATH']}", "FAKE_LOG": str(log),
             "FAKE_PSQL_FAIL": "", "FAKE_HOLDS": "0", "FAKE_TABLE": "t", **env}
        r = subprocess.run(["sh", str(script), *args], env=e, capture_output=True, text=True)
        return r, log.read_text()

    return go


def walg_retention(run, **env):
    return run(WALG, "retention", WALG_FILE_PREFIX="/unused", WALG_RETENTION_KEEP="2", **env)


def test_walg_prune_runs_when_there_are_no_holds(run):
    r, log = walg_retention(run)
    assert r.returncode == 0 and "delete retain 2 --confirm" in log


def test_walg_prune_runs_before_the_hold_table_exists(run):
    r, log = walg_retention(run, FAKE_TABLE="f")
    assert r.returncode == 0 and "delete retain 2 --confirm" in log


@pytest.mark.parametrize("env", [{"FAKE_HOLDS": "2"}, {"FAKE_PSQL_FAIL": "1"}, {"FAKE_HOLDS": "x"}])
def test_walg_prune_is_refused_while_holds_are_active_or_unreadable(run, env):
    r, log = walg_retention(run, **env)
    assert r.returncode != 0 and "delete" not in log
    assert "refusing to prune" in r.stderr


def test_walg_base_backup_still_succeeds_when_its_prune_is_skipped(run):
    r, log = run(WALG, "base", WALG_FILE_PREFIX="/unused", WALG_RETENTION_KEEP="2",
                 FAKE_HOLDS="1", BACKUP_MIN_WAL_SECONDS="999999999")
    assert "backup-push" in log and "delete" not in log
    assert "SKIPPED" in r.stderr and "ERROR: refusing" not in r.stderr


def _snapshots(tmp_path):
    d = tmp_path / "dumps"
    d.mkdir()
    for stamp in ("20200101T000000Z", "20200102T000000Z", "20200103T000000Z"):
        (d / f"{stamp}.dump").write_text("old")
    return d


@pytest.mark.parametrize("env, kept", [({"FAKE_HOLDS": "0"}, 1), ({"FAKE_HOLDS": "3"}, 4),
                                       ({"FAKE_PSQL_FAIL": "1"}, 4)])
def test_snapshot_expiry_waits_for_holds(run, tmp_path, env, kept):
    d = _snapshots(tmp_path)
    r, _ = run(DUMP, BACKUP_DIR=str(d), BACKUP_KEEP="1", **env)
    assert r.returncode == 0, r.stderr
    assert len(list(d.glob("*.dump"))) == kept
