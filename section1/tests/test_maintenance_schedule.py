"""Mission 15 Wave 5 D2 — vacuum/maintenance schedule tests (DB#12).

Pins the maintenance lane's static contracts so drift fails CI:

* compose declares the opt-in 'maintenance' profile with a db-maintenance
  service that (a) runs scripts/maintenance.sh, (b) defaults to the three
  highest-churn tables (agent_events rate_buckets audit_log), (c) defaults to
  a weekly cadence (604800s), (d) reindex OFF by default, (e) depends on a
  healthy db, (f) persists logs + lock/stamp state on named volumes;
* the wrapper script is valid POSIX sh and carries an honest exit-code
  contract (0/1/2/3), a mkdir-based lock guard, identifier validation before
  any SQL interpolation, and VACUUM/REINDEX as separate psql statements
  (utility commands refuse to run inside psql's implicit transaction);
* docs/runbooks/maintenance.md exists and cross-references its neighbors
  (backup-and-pitr.md for the vacuum-before-base-backup ordering, ops.md
  index) — the W5-VT3 "runbook coherence" check.

Live execution evidence (2026-08-24, host + `--profile maintenance run --rm`
against harness-db-1): pasted in docs/runbooks/maintenance.md §Local proof.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPO_ROOT / "deploy"
COMPOSE = DEPLOY / "docker-compose.yml"
MAINTENANCE_SH = REPO_ROOT / "scripts" / "maintenance.sh"
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "maintenance.md"
BACKUP_RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "backup-and-pitr.md"

MAINTENANCE_TABLES = ["agent_events", "rate_buckets", "audit_log"]
WEEKLY_SECONDS = 604800


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def _svc_env_default(env: dict, key: str) -> str:
    """Compose env values stay RAW ('${VAR:-default}') until runtime; pull the
    documented default out of the interpolation string."""
    raw = env[key]
    m = re.match(r"\$\{\w+:-(.*)\}$", raw)
    return m.group(1) if m else raw


def _script_text() -> str:
    return MAINTENANCE_SH.read_text()


# ----------------------------------------------------------------- compose


def test_maintenance_service_exists_behind_its_own_profile():
    services = _compose()["services"]
    assert "db-maintenance" in services, "db-maintenance service missing from compose"
    assert "maintenance" in services["db-maintenance"]["profiles"], (
        "db-maintenance must be opt-in via the 'maintenance' profile — "
        "the default `up` must stay unchanged"
    )


def test_default_up_never_pulls_the_profile_in():
    """Every other profiled service keeps ITS profile; nothing gains
    'maintenance' implicitly (the events-retention/proxy/walg law)."""
    services = _compose()["services"]
    for name, svc in services.items():
        if name == "db-maintenance":
            continue
        profiles = svc.get("profiles") or []
        assert "maintenance" not in profiles, (
            f"{name} accidentally joined the maintenance profile"
        )


def test_wrapper_entrypoint_points_at_scripts_maintenance_sh():
    entrypoint = _compose()["services"]["db-maintenance"]["entrypoint"]
    assert entrypoint == ["sh", "/opt/harness/scripts/maintenance.sh"]


def test_scheduled_tables_are_the_three_highest_churn_ones():
    env = _compose()["services"]["db-maintenance"]["environment"]
    tables = _svc_env_default(env, "MAINTENANCE_TABLES").split()
    assert tables == MAINTENANCE_TABLES


def test_cadence_defaults_to_weekly_and_is_env_tunable():
    env = _compose()["services"]["db-maintenance"]["environment"]
    assert _svc_env_default(env, "MAINTENANCE_INTERVAL_SECONDS") == str(WEEKLY_SECONDS)
    raw = COMPOSE.read_text()
    assert "${MAINTENANCE_INTERVAL_SECONDS:-" in raw, "cadence knob must be env-tunable"


def test_reindex_is_opt_in_not_default():
    env = _compose()["services"]["db-maintenance"]["environment"]
    assert _svc_env_default(env, "MAINTENANCE_REINDEX") in ("0", "false", "no")


def test_job_targets_the_live_db_and_waits_for_health():
    svc = _compose()["services"]["db-maintenance"]
    assert svc["depends_on"]["db"]["condition"] == "service_healthy"
    assert svc["environment"]["PGHOST"].startswith("${MAINTENANCE_PGHOST:-db}")
    assert svc["environment"]["PGUSER"] == "harness"
    assert svc["environment"]["PGDATABASE"] == "harness"
    # Secrets enter via env interpolation only — never literals beyond dev.
    assert svc["environment"]["PGPASSWORD"] == "${POSTGRES_PASSWORD:-harness}"


def test_logs_and_lock_state_persist_on_named_volumes():
    svc = _compose()["services"]["db-maintenance"]
    volumes = {
        v.split(":")[0] for v in svc["volumes"] if not v.split(":")[0].startswith((".", "/"))
    }
    assert {"maintenance_logs", "maintenance_state"} <= volumes
    declared = set(_compose()["volumes"])
    assert {"maintenance_logs", "maintenance_state"} <= declared, (
        "volumes used but not declared at top level"
    )
    # The bind mount of scripts/ is READ-ONLY — a maintenance job must never
    # be able to rewrite its own code.
    binds = [v for v in svc["volumes"] if v.startswith(".")]
    assert binds == ["../scripts:/opt/harness/scripts:ro"], binds


# ------------------------------------------------------------------ script


def test_script_parses_as_posix_sh():
    proc = subprocess.run(
        ["sh", "-n", str(MAINTENANCE_SH)], capture_output=True, text=True
    )
    assert proc.returncode == 0, f"sh -n failed:\n{proc.stderr}"


def test_script_documents_the_honest_exit_code_contract():
    text = _script_text()
    for code, marker in [
        ("0", "success"),
        ("1", "bad storage of truth"),
        ("2", "usage error"),
        ("3", "lock held"),
    ]:
        # Contract lines live in the sh header comments ("#   0  success ...").
        assert re.search(rf"^#\s*{code}\s+{marker}", text, re.M), (
            f"exit-code contract lost its '{code} → {marker}' line"
        )


def test_lock_guard_is_present_and_stale_aware():
    text = _script_text()
    assert "mkdir \"$lock_dir\"" in text, "lock guard must use atomic mkdir"
    assert "LOCK_STALE_SECONDS" in text, "stale-lock threshold missing"
    assert "trap lock_release EXIT INT TERM" in text, "lock must release on exit/signals"


def test_identifiers_validated_before_sql_interpolation():
    text = _script_text()
    # The validator exists…
    assert re.search(r"valid_ident\(\)\s*\{", text)
    # …and is enforced BEFORE the table name reaches a SQL statement.
    loop_idx = text.index("for t in $TABLES; do")
    check_idx = text.index("valid_ident \"$t\"", loop_idx)
    sql_idx = text.index("to_regclass", loop_idx)
    assert check_idx < sql_idx, "identifier validation must precede SQL construction"


def test_vacuum_and_reindex_run_as_separate_statements():
    """VACUUM/REINDEX are utility statements — they fail inside the implicit
    transaction a multi-statement psql -c would open."""
    text = _script_text()
    vac = re.search(r'psql [^\n]*-c "VACUUM \(ANALYZE\) \\"\$1\\";"', text)
    rex = re.search(r'psql [^\n]*-c "REINDEX TABLE CONCURRENTLY \\"\$1\\";"', text)
    assert vac, "VACUUM (ANALYZE) must be its own single-statement psql call"
    assert rex, "REINDEX TABLE CONCURRENTLY must be its own single-statement psql call"
    assert text.index(vac.group(0)) < text.index(rex.group(0))


def test_reindex_gated_behind_the_flag_and_concurrent_mode_only():
    text = _script_text()
    gate = text.index('[ "$REINDEX" -eq 1 ]')
    # Search AFTER the gate: the header comment mentions REINDEX too.
    rex = text.index("REINDEX TABLE CONCURRENTLY", gate)
    assert rex < text.index("return 0", gate), (
        "REINDEX must sit inside the MAINTENANCE_REINDEX gate"
    )
    assert "CONCURRENTLY" in text and 'REINDEX TABLE "$1";' not in text, (
        "plain REINDEX takes an exclusive lock — CONCURRENTLY only"
    )


def test_bloat_indicators_query_pins_n_dead_tup_family():
    """The runbook's 'watch these numbers' queries must stay real columns."""
    text = _script_text()
    for col in ("n_dead_tup", "n_live_tup", "n_mod_since_analyze"):
        assert col in text, f"pg_stat_user_tables column {col} missing from snapshots"
    assert "dead_ratio" in text, "the dead-ratio signal must stay surfaced"


def test_once_mode_fails_loudly_loop_mode_survives():
    text = _script_text()
    # once: the pass result IS the process result.
    assert re.search(r"cmd_once\(\)\s*\{[^}]*run_pass_logged\s*$", text, re.M | re.S), (
        "cmd_once must propagate the pass exit code directly"
    )
    # loop: a failed pass warns and retries next cycle instead of dying.
    assert "pass failed — will retry next cycle" in text
    # loop dedups restarts against the last-success stamp.
    assert "sleeping until due" in text


# ----------------------------------------------------------------- runbook


def test_runbook_exists_and_coheres_with_neighbors():
    assert RUNBOOK.exists(), "docs/runbooks/maintenance.md missing"
    text = RUNBOOK.read_text()
    # WAL-G interplay: vacuum-before-base-backup must be stated AND the
    # backup runbook must point back here.
    assert "backup-and-pitr.md" in text, "must cross-reference the backup/PITR runbook"
    assert BACKUP_RUNBOOK.exists()
    backup_text = BACKUP_RUNBOOK.read_text()
    assert "maintenance.md" in backup_text, (
        "backup-and-pitr.md should point operators at maintenance.md "
        "(vacuum-before-base-backup ordering)"
    )
    # Ops index should list it (docs coherence).
    ops_index = (REPO_ROOT / "docs" / "runbooks" / "ops.md").read_text()
    assert "maintenance.md" in ops_index, "ops.md index should list the new runbook"


def test_runbook_documents_knobs_matching_compose_defaults():
    text = RUNBOOK.read_text()
    for needle in (
        str(WEEKLY_SECONDS),
        "agent_events",
        "rate_buckets",
        "audit_log",
        "MAINTENANCE_REINDEX",
        "--profile maintenance",
    ):
        assert needle in text, f"runbook lost documented detail: {needle}"
