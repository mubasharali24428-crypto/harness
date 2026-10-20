"""F118 acceptance: export completeness, cross-referenced ledgers, redaction
enforced twice (ingest + independent export scan), tamper evidence."""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from io import BytesIO

import pytest
from support import fixture_archive

from harness_ops.evidence import (
    EvidenceError,
    build_evidence,
    scan_findings,
    verify_evidence_zip,
)

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


@pytest.fixture()
def org_package(tmp_path):
    archive = fixture_archive(str(tmp_path / "archive.sqlite"))
    return build_evidence(archive=archive, visible_teams=None, generated_at=NOW)


def _rewrite(bundle_bytes: bytes, name: str, new_content: bytes) -> bytes:
    source = zipfile.ZipFile(BytesIO(bundle_bytes))
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.namelist():
            payload = new_content if item == name else source.read(item)
            target.writestr(item, payload)
    return buffer.getvalue()


def test_export_is_complete_and_accounts_for_every_run(org_package):
    manifest = org_package.manifest()
    counts = manifest["counts"]
    assert counts == {
        "runs": 8, "changes": 3, "incidents": 4, "otherOutcomes": 1,
        "timelineEvents": 42, "accountedFor": True,
    }
    assert manifest["redactionPolicy"].startswith("harness-ops/evidence-v1")
    assert manifest["containsRawText"] is False
    verified = verify_evidence_zip(org_package.to_zip())
    assert verified["packageId"] == manifest["packageId"]


def test_zip_is_deterministic(org_package):
    assert org_package.to_zip() == org_package.to_zip()


def test_accepted_changes_ledger(org_package):
    rows = [
        json.loads(line)
        for line in org_package.files["changes.jsonl"].strip().splitlines()
    ]
    assert sorted(r["runId"] for r in rows) == ["run-a1", "run-a2", "run-b1"]
    assert sorted(r["ttfucMinutes"] for r in rows) == [35.0, 75.0, 85.0]
    assert all(r["prId"] and r["prOpenedAt"] for r in rows)


def test_incident_ledger_cross_references_signals(org_package):
    rows = [json.loads(line) for line in org_package.files["incidents.jsonl"].strip().splitlines()]
    by_run = {r["runId"]: r for r in rows}
    assert set(by_run) == {"run-a3", "run-a4", "run-a5", "run-b2"}
    a3_signals = {s["type"] for s in by_run["run-a3"]["signals"]}
    assert "review_failed" in a3_signals
    a5_signals = {s["type"] for s in by_run["run-a5"]["signals"]}
    assert "blocked_awaiting_clarification" in a5_signals
    assert by_run["run-a4"]["lastEventAt"]


def test_team_scoped_export_contains_only_that_team(tmp_path):
    archive = fixture_archive(str(tmp_path / "archive.sqlite"))
    package = build_evidence(archive=archive, visible_teams={"team-b"}, generated_at=NOW)
    counts = package.manifest()["counts"]
    assert counts["runs"] == 2
    assert counts["changes"] == 1
    assert counts["incidents"] == 1
    assert package.manifest()["scope"]["teams"] == ["team-b"]
    runs = [json.loads(line) for line in package.files["runs.jsonl"].strip().splitlines()]
    assert {r["runId"] for r in runs} == {"run-b1", "run-b2"}


def test_goals_in_export_are_scrubbed_everywhere(org_package):
    blob = "".join(org_package.files.values())
    assert "[EMAIL]" in blob
    assert "alice@example.com" not in blob
    assert "hunter2" not in blob
    assert "[REDACTED]" in blob


def test_redaction_scan_catches_patterns():
    assert scan_findings("mail me alice@example.com") == ["email"]
    assert "secret-pattern-0" in scan_findings("xoxb-123456789012-abcd")
    assert "secret-pattern-3" in scan_findings("sk-" + "a" * 30)
    assert scan_findings("Fix login for [EMAIL]; [REDACTED]") == []


def test_verify_detects_tampering(org_package):
    bundle = org_package.to_zip()
    tampered = _rewrite(bundle, "runs.jsonl", org_package.files["runs.jsonl"].encode() + b"x\n")
    with pytest.raises(EvidenceError) as exc:
        verify_evidence_zip(tampered)
    assert exc.value.code == "EVIDENCE_TAMPERED"


def test_verify_detects_redaction_regression(org_package):
    edited = org_package.files["runs.jsonl"].replace("[EMAIL]", "eve@example.com").encode()
    import hashlib

    manifest = json.loads(org_package.files["manifest.json"])
    for artifact in manifest["artifacts"]:
        if artifact["name"] == "runs.jsonl":
            artifact["sha256"] = hashlib.sha256(edited).hexdigest()
            artifact["bytes"] = len(edited)
    bundle = org_package.to_zip()
    bundle = _rewrite(bundle, "runs.jsonl", edited)
    bundle = _rewrite(bundle, "manifest.json",
                      json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n")
    with pytest.raises(EvidenceError) as exc:
        verify_evidence_zip(bundle)
    assert exc.value.code == "EVIDENCE_REDACTION_FAILED"


def test_verify_detects_missing_artifact(org_package):
    bundle = org_package.to_zip()
    source = zipfile.ZipFile(BytesIO(bundle))
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as target:
        for item in source.namelist():
            if item != "incidents.jsonl":
                target.writestr(item, source.read(item))
    with pytest.raises(EvidenceError) as exc:
        verify_evidence_zip(buffer.getvalue())
    assert exc.value.code == "EVIDENCE_INCOMPLETE"


def test_verify_rejects_garbage():
    with pytest.raises(EvidenceError) as exc:
        verify_evidence_zip(b"definitely not a zip")
    assert exc.value.code == "EVIDENCE_CORRUPT"
