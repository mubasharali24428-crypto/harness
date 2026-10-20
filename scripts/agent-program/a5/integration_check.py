"""A5 integration check — evaluates whatever lane artifacts exist.

Inspection-only: this script never deploys, mutates, or requires any other
session's output. Missing lanes are reported as "absent" (not failures);
present-but-inconsistent artifacts fail the check.

Run from the repo root with the extensions venv:
    ./packages/extensions/.venv/bin/python scripts/agent-program/a5/integration_check.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
A5 = ROOT / "docs" / "agent-program" / "a5"

SESSIONS = {
    "S1": {"features": ["F061-F066"], "golden_glob": "**/golden/**/*"},
    "S2": {"features": ["F067-F070", "F125", "F150"], "golden_glob": "**/golden/**/*"},
    "S3": {"features": ["F121-F124", "F126", "F127"], "golden_glob": "**/golden/**/*"},
    "S4": {"features": ["F112-F117"], "golden_glob": "**/golden/**/*"},
    "S5": {"features": ["F118-F120", "F128-F130"], "golden_glob": "**/golden/**/*"},
}

PACKAGES = {
    "contracts": ROOT / "contracts",
    "extensions": ROOT / "packages" / "extensions",
    "wiki": ROOT / "integrations" / "connectors" / "wiki",
    "workflows": ROOT / "integrations" / "connectors" / "workflows",
    "telemetry": ROOT / "packages" / "telemetry",
    "ops": ROOT / "packages" / "ops",
}


def session_lane(name: str, spec: dict) -> dict:
    base = A5 / f"session-{name[1]}"
    lane: dict = {"features": spec["features"], "artifacts": {}}
    for artifact in ("ledger.json", "report.md"):
        lane["artifacts"][artifact] = (base / artifact).is_file()
    logs = sorted(p.name for p in base.glob("*.log")) if base.is_dir() else []
    lane["artifacts"]["logs"] = logs
    goldens = list(base.glob(spec["golden_glob"])) if base.is_dir() else []
    lane["goldens"] = sum(1 for p in goldens if p.is_file())
    lane["present"] = all(
        lane["artifacts"][a] for a in ("ledger.json", "report.md"))
    return lane


def package_lane(name: str, path: pathlib.Path) -> dict:
    tests = list((path / "tests").glob("test_*.py")) if path.is_dir() else []
    return {
        "path": str(path.relative_to(ROOT)),
        "present": path.is_dir(),
        "src": (path / "src").is_dir() or (path / name).is_dir() or (path / "harness_contracts").is_dir(),
        "tests": len(tests),
        "readme": (path / "README.md").is_file(),
    }


def interface_probes() -> dict:
    """Cheap, side-effect-free cross-lane interface checks (imports only)."""
    probes: dict = {}
    try:
        from harness_contracts.roles import Principal, Role  # noqa: F401
        probes["contracts.roles"] = True
    except Exception as exc:  # noqa: BLE001 - report, never raise
        probes["contracts.roles"] = f"{type(exc).__name__}: {exc}"

    try:
        from harness_telemetry import fixtures
        from harness_telemetry.dashboard import make_dashboard_app  # noqa: F401
        probes["telemetry.fixtures"] = fixtures.AS_OF.isoformat() == "2026-10-17T12:00:00+00:00"
    except Exception as exc:  # noqa: BLE001 - report, never raise
        probes["telemetry.fixtures"] = f"{type(exc).__name__}: {exc}"

    try:
        from harness_workflow_connectors.plan_gate import ProposePlanGate  # noqa: F401
        probes["connectors.plan_gate"] = True
    except Exception as exc:  # noqa: BLE001 - report, never raise
        probes["connectors.plan_gate"] = f"{type(exc).__name__}: {exc}"

    try:
        from harness_ops.app import make_ops_app
        from harness_ops.store import OpsStore

        store = OpsStore(str(pathlib.Path(tempfile.mkdtemp()) / "ic.sqlite"))
        app = make_ops_app(store=store, records_provider=list,
                           archive=None, now_fn=None)
        probes["ops.routes"] = len(app.routes) >= 11
    except Exception as exc:  # noqa: BLE001 - report, never raise
        probes["ops.routes"] = f"{type(exc).__name__}: {exc}"

    approvals = ROOT / "section4" / "src" / "harness_orchestrator"
    probes["orchestrator.approve_route"] = any(
        p.is_file() and "/approve" in p.read_text()
        for p in approvals.rglob("*.py")
    ) if approvals.is_dir() else False

    dist_log = A5 / "session-5" / "interop" / "distribution-verify.log"
    probes["distribution.verified"] = (
        dist_log.is_file() and "DISTRIBUTION_VERIFIED" in dist_log.read_text()
    )
    return probes


def main() -> int:
    report: dict = {"root": str(ROOT), "sessions": {}, "packages": {},
                    "probes": {}, "ok": True}

    for name, spec in SESSIONS.items():
        report["sessions"][name] = session_lane(name, spec)
    for name, path in PACKAGES.items():
        report["packages"][name] = package_lane(name, path)
    report["probes"] = interface_probes()

    expected_scripts = [A5 / ".." / ".." / ".." / "scripts" / "agent-program" / "a5"]
    scripts_dir = ROOT / "scripts" / "agent-program" / "a5"
    report["scripts"] = sorted(
        p.name for p in scripts_dir.glob("*.py")
    ) if scripts_dir.is_dir() else []
    report["expected_scripts_dir"] = str(expected_scripts[0].resolve().relative_to(ROOT))

    for lane in report["sessions"].values():
        if lane["present"] and lane["goldens"] == 0:
            report["ok"] = False
            lane["warning"] = "ledger present but no golden artifacts"
    for value in report["probes"].values():
        if value is not True:
            report["ok"] = False

    absent = [n for n, s in report["sessions"].items() if not s["present"]]
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nINTEGRATION_CHECK={'OK' if report['ok'] else 'INCONSISTENT'} "
          f"(absent lanes, informational only: {absent or 'none'})")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
