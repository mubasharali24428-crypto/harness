"""Canned manifest + monkeypatched docker — no daemon needed for any test.

The live tool was verified against the real harness-rc stack on 2026-11-21
(docs/briefs/evidence/security/p1-report-2026-11-21.md); these tests pin the
decision logic so a regression cannot pass silently.
"""
from __future__ import annotations

import json

import readiness_drift
import stack_tags


def _manifest(**over):
    base = {
        "tag": "vv90r6 (compose v-prefix of HARNESS_IMAGE_TAG=v90r6)",
        "git_head": "0d9da4a0deadbeefdeadbeefdeadbeefdeadbeef",
        "images": {"harness-backend:vv90r6": "sha256:b7f41c77"},
        "schema": {"applied": 74, "max_version": "0076"},
    }
    base.update(over)
    return base


def _rc_ps(tag="vv90r6", omit=(), extra_off_tag=None):
    rows = [f"harness-rc|{s}|harness-rc-{s}-1|harness-backend:{tag}"
            for s in stack_tags.EXPECTED_BACKEND_SERVICES if s not in omit]
    rows.append("harness-rc|db|harness-rc-db-1|harness-db-walg:v3.0.9")
    rows.append(f"harness-rc|{stack_tags.WORKER_DAEMON_SERVICE}|"
                f"harness-rc-{stack_tags.WORKER_DAEMON_SERVICE}-1|"
                f"harness-worker-daemon:{tag}")
    if extra_off_tag:
        rows.append(f"harness-rc|orchestrator|harness-rc-orchestrator-1|harness-backend:{extra_off_tag}")
    return "\n".join(rows) + "\n"


def _patch_live(monkeypatch, ps_text, digests=None, schema=None, gate=None, repo=None):
    """Monkeypatch every docker-backed helper check_live touches.

    gate = digest the container reports; repo = digest HEAD hashes (defaults
    to gate, i.e. byte-identical). Pass different values to simulate drift.
    """
    def run(command, **kwargs):
        if command[:2] == ["docker", "ps"]:
            return 0, ps_text, ""
        return 0, "", ""

    monkeypatch.setattr(readiness_drift, "_run", run)
    monkeypatch.setattr(readiness_drift, "image_digest",
                        lambda ref: ((digests or {}).get(ref, "sha256:b7f41c77"), None))
    monkeypatch.setattr(readiness_drift, "schema_state",
                        lambda container: (schema or (74, "0076"), None))
    monkeypatch.setattr(readiness_drift, "gate_digest", lambda container: (gate or "a" * 64, None))
    monkeypatch.setattr(readiness_drift, "repo_gate_digest", lambda: repo or gate or "a" * 64)


# --------------------------------------------------------------- CI mode

def test_ci_mode_manifest_on_lineage_and_schema_current_is_clean(monkeypatch):
    monkeypatch.setattr(readiness_drift, "manifest_git_head_ancestor", lambda sha: (True, None))
    monkeypatch.setattr(readiness_drift, "_run",
                        lambda command, **k: (0, "3\n", "") if command[1] == "rev-list" else (0, "", ""))
    monkeypatch.setattr(readiness_drift, "disk_migrations", lambda: (74, "0076"))
    lines, drift = readiness_drift.check_ci(_manifest())
    assert drift == 0
    assert any(l.startswith("ok   migrations") for l in lines)


def test_ci_mode_new_merged_migration_is_drift(monkeypatch):
    monkeypatch.setattr(readiness_drift, "manifest_git_head_ancestor", lambda sha: (True, None))
    monkeypatch.setattr(readiness_drift, "_run", lambda command, **k: (0, "3\n", ""))
    monkeypatch.setattr(readiness_drift, "disk_migrations", lambda: (75, "0077"))
    lines, drift = readiness_drift.check_ci(_manifest())
    assert drift >= 1
    assert any("0077" in l and "redeploy" in l for l in lines)


def test_ci_mode_manifest_off_lineage_is_drift(monkeypatch):
    monkeypatch.setattr(readiness_drift, "manifest_git_head_ancestor", lambda sha: (False, None))
    lines, drift = readiness_drift.check_ci(_manifest())
    assert drift == 1
    assert any("NOT an ancestor" in l for l in lines)


# -------------------------------------------------------------- live mode

def test_live_mode_all_green_on_one_tag(monkeypatch):
    _patch_live(monkeypatch, _rc_ps())
    lines, drift = readiness_drift.check_live(_manifest(), "harness-rc")
    assert drift == 0
    assert any("ok   schema: 74|0076" in l for l in lines)
    assert any("ok   gate: byte-identical" in l for l in lines)


def test_live_mode_image_digest_drift_fails(monkeypatch):
    _patch_live(monkeypatch, _rc_ps(), digests={"harness-backend:vv90r6": "sha256:deadbeef"})
    lines, drift = readiness_drift.check_live(_manifest(), "harness-rc")
    assert drift >= 1
    assert any("DRIFT image harness-backend:vv90r6" in l for l in lines)


def test_live_mode_service_off_the_manifest_tag_fails(monkeypatch):
    _patch_live(monkeypatch, _rc_ps(extra_off_tag="vv90r5"))
    lines, drift = readiness_drift.check_live(_manifest(), "harness-rc")
    assert drift >= 1
    assert any("off the manifest tag" in l and "orchestrator" in l for l in lines)


def test_live_mode_missing_backend_service_fails(monkeypatch):
    _patch_live(monkeypatch, _rc_ps(omit=("models",)))
    lines, drift = readiness_drift.check_live(_manifest(), "harness-rc")
    assert drift >= 1
    assert any("models" in l and "not running" in l for l in lines)


def test_live_mode_schema_drift_fails(monkeypatch):
    _patch_live(monkeypatch, _rc_ps(), schema=(75, "0077"))
    lines, drift = readiness_drift.check_live(_manifest(), "harness-rc")
    assert drift >= 1
    assert any("DRIFT schema" in l and "0077" in l for l in lines)


def test_live_mode_gate_drift_fails(monkeypatch):
    _patch_live(monkeypatch, _rc_ps(), gate="b" * 64, repo="a" * 64)
    lines, drift = readiness_drift.check_live(_manifest(), "harness-rc")
    assert drift >= 1
    assert any("DRIFT gate" in l for l in lines)


def test_live_mode_unseen_docker_is_undecidable_not_pass(monkeypatch):
    monkeypatch.setattr(readiness_drift, "_run",
                        lambda command, **k: (1, "", "Cannot connect to the Docker daemon"))
    _lines, drift = readiness_drift.check_live(_manifest(), "harness-rc")
    assert drift == 2  # main() turns this into exit 2 + "UNDECIDABLE"


# --------------------------------------------------------- write-manifest

def test_write_manifest_refuses_a_mixed_project(monkeypatch, tmp_path):
    mixed = _rc_ps() + "harness-rc|orchestrator|harness-rc-orchestrator-1|harness-backend:vv90r5\n"
    monkeypatch.setattr(readiness_drift, "_run",
                        lambda command, **k: (0, mixed, "") if command[:2] == ["docker", "ps"]
                        else (0, "", ""))
    assert readiness_drift.write_manifest(tmp_path / "m.json", "harness-rc") == 1
    assert not (tmp_path / "m.json").exists()


def test_write_manifest_writes_tag_schema_gate(monkeypatch, tmp_path):
    _patch_live(monkeypatch, _rc_ps(), schema=(74, "0076"), gate="c" * 64)
    monkeypatch.setattr(readiness_drift, "image_digest",
                        lambda ref: ("sha256:" + "d" * 64, None))
    monkeypatch.setattr(readiness_drift, "_run",
                        lambda command, **k: (0, "abc1234", "") if command[1] == "rev-parse"
                        else (0, _rc_ps(), ""))
    out = tmp_path / "m.json"
    assert readiness_drift.write_manifest(out, "harness-rc") == 0
    data = json.loads(out.read_text())
    assert data["tag"] == "vv90r6"
    assert data["schema"] == {"applied": 74, "max_version": "0076"}
    assert data["shell_gate_sha256"] == "c" * 64
    assert data["images"]["harness-backend:vv90r6"] == "sha256:" + "d" * 64
