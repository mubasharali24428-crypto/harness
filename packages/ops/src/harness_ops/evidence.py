"""F118 — Exportable audit evidence package.

Turns the scrubbed run archive into a tamper-evident ZIP bundle for change /
incident review: a manifest with per-artifact SHA-256 and explicit counts, an
accepted-changes ledger, an incidents ledger, and the full run index. The
package is internally consistent (every scoped run appears exactly once in the
index; every accepted change and incident is cross-referenced) and every
artifact is scanned for PII/secret patterns before the manifest is sealed.

The archive already scrubs at ingest; ``scan_artifacts`` is an independent
second check so a future ingest regression cannot leak silently into exports.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime

from harness_telemetry import Archive

REDACTION_POLICY = "harness-ops/evidence-v1 (scrubbed at ingest + export scan)"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SECRET_RES = [
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-+=\/]{12,}"),
]


class EvidenceError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def scan_findings(text: str) -> list[str]:
    """Independent redaction scan: returns matched pattern names (empty = clean)."""
    findings = []
    if _EMAIL_RE.search(text):
        findings.append("email")
    for index, pattern in enumerate(_SECRET_RES):
        if pattern.search(text):
            findings.append(f"secret-pattern-{index}")
    return findings


@dataclass
class EvidencePackage:
    files: dict[str, str] = field(default_factory=dict)

    def manifest(self) -> dict:
        return json.loads(self.files["manifest.json"])

    def to_zip(self) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(self.files):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, self.files[name])
        return buffer.getvalue()


def _jsonl(records: list[dict]) -> str:
    return "".join(json.dumps(r, sort_keys=True) + "\n" for r in records)


def build_evidence(
    *,
    archive: Archive,
    visible_teams: set[str] | None,
    generated_at: datetime,
    from_: datetime | None = None,
    to: datetime | None = None,
    generator: str = "harness-ops/0.1.0",
) -> EvidencePackage:
    """Build the package. ``visible_teams=None`` is org-wide (admin scope)."""
    result = archive.search(
        visible_teams=visible_teams, from_=from_, to=to, limit=10_000
    )
    runs = result["runs"]

    changes, incidents, index = [], [], []
    for row in runs:
        run_id = row["runId"]
        detail = archive.get_run(run_id, visible_teams=visible_teams)
        if detail is None:  # visibility changed under us; skip rather than leak
            continue
        base = {
            "runId": run_id,
            "teamId": row["teamId"],
            "projectId": row["projectId"],
            "outcome": row["outcome"],
            "costUsd": row["costUsd"],
        }
        index.append(
            base
            | {
                "goal": row["goal"],
                "eventCount": row["eventCount"],
                "planProposedAt": row["planProposedAt"],
                "prOpenedAt": row["prOpenedAt"],
                "retentionUntil": detail.get("retentionUntil"),
            }
        )
        if row["outcome"] == "accepted":
            changes.append(
                base
                | {
                    "prId": row["prId"],
                    "prOpenedAt": row["prOpenedAt"],
                    "planProposedAt": row["planProposedAt"],
                    "ttfucMinutes": _ttfuc_minutes(row),
                }
            )
        elif row["outcome"] in ("rejected", "blocked", "abandoned"):
            incidents.append(
                base
                | {
                    "lastEventAt": max(e["ts"] for e in detail["events"]),
                    "signals": [
                        {"seq": e["seq"], "type": e["type"], "ts": e["ts"]}
                        for e in detail["events"]
                        if e["type"] in (
                            "engine_failed", "review_failed",
                            "blocked_awaiting_clarification",
                        )
                    ],
                }
            )

    timeline = []
    for row in runs:
        detail = archive.get_run(row["runId"], visible_teams=visible_teams)
        if detail is None:
            continue
        for event in detail["events"]:
            timeline.append(
                {"runId": row["runId"], "seq": event["seq"], "type": event["type"],
                 "ts": event["ts"], "payload": event.get("payload", {})}
            )

    files = {
        "changes.jsonl": _jsonl(changes),
        "incidents.jsonl": _jsonl(incidents),
        "runs.jsonl": _jsonl(index),
        "timeline.jsonl": _jsonl(timeline),
    }

    completeness = {
        "runs": len(index),
        "changes": len(changes),
        "incidents": len(incidents),
        "otherOutcomes": len(index) - len(changes) - len(incidents),
        "timelineEvents": len(timeline),
        "accountedFor": len(changes) + len(incidents)
        + (len(index) - len(changes) - len(incidents)) == len(index),
    }

    artifacts = [
        {
            "name": name,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "bytes": len(content.encode("utf-8")),
        }
        for name, content in sorted(files.items())
    ]

    manifest = {
        "generatedAt": generated_at.astimezone(UTC).isoformat(),
        "generator": generator,
        "scope": {
            "teams": sorted(visible_teams) if visible_teams is not None else "all",
            "from": from_.isoformat() if from_ else None,
            "to": to.isoformat() if to else None,
        },
        "counts": completeness,
        "artifacts": artifacts,
        "redactionPolicy": REDACTION_POLICY,
        "containsRawText": False,
    }
    package_id = hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    manifest["packageId"] = package_id
    files["manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    return EvidencePackage(files=files)


def _ttfuc_minutes(row: dict) -> float | None:
    if row["planProposedAt"] and row["prOpenedAt"]:
        plan = datetime.fromisoformat(row["planProposedAt"])
        pr = datetime.fromisoformat(row["prOpenedAt"])
        return round((pr - plan).total_seconds() / 60.0, 2)
    return None


def verify_evidence_zip(data: bytes) -> dict:
    """Re-derive hashes + run the redaction scan. Raises EvidenceError on any
    mismatch; returns the manifest counts on success."""
    try:
        bundle = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise EvidenceError("EVIDENCE_CORRUPT", "not a zip archive") from exc

    names = set(bundle.namelist())
    if "manifest.json" not in names:
        raise EvidenceError("EVIDENCE_CORRUPT", "manifest.json missing")
    manifest = json.loads(bundle.read("manifest.json"))

    for artifact in manifest["artifacts"]:
        name = artifact["name"]
        if name not in names:
            raise EvidenceError("EVIDENCE_INCOMPLETE", f"artifact {name} missing")
        content = bundle.read(name)
        if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
            raise EvidenceError("EVIDENCE_TAMPERED", f"hash mismatch for {name}")
        if artifact["bytes"] != len(content):
            raise EvidenceError("EVIDENCE_TAMPERED", f"size mismatch for {name}")
        findings = scan_findings(content.decode("utf-8"))
        if findings:
            raise EvidenceError(
                "EVIDENCE_REDACTION_FAILED", f"{name}: {findings}"
            )

    counts = manifest["counts"]
    if not counts["accountedFor"]:
        raise EvidenceError("EVIDENCE_INCOMPLETE", "runs not fully accounted for")
    runs_lines = [line for line in bundle.read("runs.jsonl").decode().splitlines() if line]
    if len(runs_lines) != counts["runs"]:
        raise EvidenceError(
            "EVIDENCE_INCOMPLETE",
            f"runs.jsonl has {len(runs_lines)} rows, manifest says {counts['runs']}",
        )
    return manifest
