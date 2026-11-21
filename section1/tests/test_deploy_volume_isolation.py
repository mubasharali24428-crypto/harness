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


# R3-S2: the list above only covers scripts someone remembered. Scan instead.
_TEARDOWN = re.compile(r"down\s+(-v\b|--volumes\b)|\"down\",\s*\"(-v|--volumes)\"")


# `export HARNESS_RUNS_VOLUME=...` in shell, or `env["HARNESS_RUNS_VOLUME"] = ...` in Python.
_SCOPED = re.compile(r"^\s*export HARNESS_RUNS_VOLUME=|\[[\"']HARNESS_RUNS_VOLUME[\"']\]\s*=", re.M)


def _unscoped_teardowns(root: Path) -> list[str]:
    """Scripts under scripts/ and deploy/ that run `down -v` without scoping the runs volume."""
    bad = []
    for top in ("scripts", "deploy"):
        for path in sorted((root / top).rglob("*")):
            if path.suffix not in (".sh", ".py", ".mk") or not path.is_file():
                continue
            text = path.read_text(errors="ignore")
            if _TEARDOWN.search(text) and not _SCOPED.search(text):
                bad.append(str(path.relative_to(root)))
    return bad


def test_the_scan_flags_a_teardown_that_forgets_to_scope_the_runs_volume(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "new_drill.sh").write_text("docker compose -p x down -v\n")
    (tmp_path / "scripts" / "ok.sh").write_text(
        "export HARNESS_RUNS_VOLUME=x_runs\ndocker compose -p x down -v\n"
    )
    (tmp_path / "scripts" / "py_drill.py").write_text('run(["docker", "compose", "down", "-v"])\n')
    (tmp_path / "scripts" / "py_ok.py").write_text(
        'env["HARNESS_RUNS_VOLUME"] = "x_runs"\nrun(["docker", "compose", "down", "-v"])\n'
    )
    (tmp_path / "deploy").mkdir()
    assert _unscoped_teardowns(tmp_path) == ["scripts/new_drill.sh", "scripts/py_drill.py"]


def test_every_script_that_tears_down_with_volumes_scopes_the_runs_volume():
    assert _unscoped_teardowns(REPO_ROOT) == []


def _rc_override():
    class OverrideLoader(yaml.SafeLoader):
        pass

    for tag in ("!override", "!reset"):
        OverrideLoader.add_constructor(tag, OverrideLoader.construct_sequence)
    path = REPO_ROOT / "deploy" / "docker-compose.rc.yml"
    return yaml.load(path.read_text(), Loader=OverrideLoader)


def test_rc_uses_separate_run_storage_and_networks():
    rc = _rc_override()
    assert rc["name"] == "harness-rc"
    volume = rc["volumes"]["harness_runs"]["name"]
    assert volume == "harness_rc_runs"
    assert rc["networks"]["default"]["name"] == "harness_rc_default"
    assert rc["networks"]["sandbox_egress"]["name"] == "harness_rc_sandbox_egress"
    for service in ("orchestrator", "worker-daemon"):
        env = rc["services"][service]["environment"]
        assert env["HARNESS_RUNS_VOLUME"] == volume
        assert env["HARNESS_EGRESS_PROXY_CONTAINER"] == "harness-rc-sandbox-egress-1"
        assert env["SANDBOX_EGRESS_NETWORK"] == "harness_rc_sandbox_egress"


def test_rc_ports_are_loopback_only_and_do_not_reuse_demo_ports():
    services = _rc_override()["services"]
    expected = {
        "identity": "127.0.0.1:18001:8001",
        "github": "127.0.0.1:18002:8002",
        "models": "127.0.0.1:28003:8003",
        "orchestrator": "127.0.0.1:18004:8004",
        "sandbox": "127.0.0.1:18005:8005",
        "meeting": "127.0.0.1:18006:8006",
        "review": "127.0.0.1:18008:8008",
        "frontend": "127.0.0.1:3011:3000",
    }
    assert services["db"]["ports"] == []
    for service, port in expected.items():
        assert services[service]["ports"] == [port]


def test_rc_database_cannot_write_into_existing_backup_targets():
    db = _rc_override()["services"]["db"]
    assert db["volumes"] == ["pgdata:/var/lib/postgresql"]
    assert db["command"] == ["postgres", "-c", "max_connections=120"]
    for key in ("WALG_S3_PREFIX", "WALG_FILE_PREFIX", "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY", "AWS_ENDPOINT"):
        assert db["environment"][key] == ""


def test_rc_browser_addresses_are_build_inputs_not_only_runtime_environment():
    frontend = _rc_override()["services"]["frontend"]
    args = frontend["build"]["args"]
    assert args == frontend["environment"]
    assert args["NEXT_PUBLIC_IDENTITY_BASE_URL"] == "http://localhost:18001"
    assert args["NEXT_PUBLIC_ORCHESTRATOR_BASE_URL"] == "http://localhost:18004"
    assert args["NEXT_PUBLIC_IDENTITY_WS_URL"] == "ws://localhost:18001"
    dockerfile = (REPO_ROOT / "deploy" / "frontend.Dockerfile").read_text()
    before_build = dockerfile.split("RUN npm run build", 1)[0]
    for name in args:
        assert re.search(rf"^ARG {re.escape(name)}=", before_build, re.M), name
