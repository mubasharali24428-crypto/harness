"""Capture deterministic S4 evidence: dashboard API goldens + span goldens.

Run with the extensions venv (has harness-telemetry + starlette + httpx):
    packages/extensions/.venv/bin/python scripts/agent-program/a5/capture_golden_s4.py

Everything is pinned to fixtures.AS_OF, so ingestion stamps (ingestedAt,
retentionUntil) are deterministic and no normalization is needed.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from harness_telemetry import (
    Archive,
    build_records,
    fixtures,
    parse_cost,
    parse_stream,
    spans_for_run,
    traceparent_for_run,
)
from harness_telemetry.dashboard import make_dashboard_app
from starlette.requests import Request
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs/agent-program/a5/session-4/interop/golden"


@dataclass
class Principal:
    userId: str
    teamId: str
    role: str


def principal_dep(mapping: dict[str, Principal]):
    def resolve(request: Request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        return mapping.get(token)

    return resolve

TOKENS = {
    "admin-token": Principal(userId="u-admin", teamId="team-a", role="admin"),
    "alice-token": Principal(userId="u-alice", teamId="team-a", role="member"),
}


def write(name: str, payload) -> None:
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"  {name}")


def main() -> None:
    events = parse_stream(fixtures.events())
    costs = [parse_cost(c) for c in fixtures.costs()]
    records = build_records(events, costs, fixtures.PROJECT_TEAM, as_of=fixtures.AS_OF)

    with tempfile.TemporaryDirectory() as tmp:
        archive = Archive(str(Path(tmp) / "golden.sqlite"))
        archive.ingest(events, costs, fixtures.PROJECT_TEAM, as_of=fixtures.AS_OF)
        app = make_dashboard_app(
            archive=archive,
            records=records,
            as_of=fixtures.AS_OF,
            get_principal_dep=principal_dep(TOKENS),
        )
        client = TestClient(app)
        admin = {"Authorization": "Bearer admin-token"}
        alice = {"Authorization": "Bearer alice-token"}

        print("dashboard goldens:")
        write("quality_org.json", client.get(
            "/api/v1/telemetry/quality", headers=admin).json())
        write("quality_team_b.json", client.get(
            "/api/v1/telemetry/quality", params={"team": "team-b"}, headers=admin).json())
        write("productivity_org.json", client.get(
            "/api/v1/telemetry/productivity", headers=admin).json())
        write("cost_org.json", client.get(
            "/api/v1/telemetry/cost", headers=admin).json())
        write("archive_search_org.json", client.get(
            "/api/v1/telemetry/archive/search", headers=admin).json())
        write("archive_search_login_team_b.json", client.get(
            "/api/v1/telemetry/archive/search",
            params={"q": "login", "team": "team-b"}, headers=admin).json())
        write("run_detail_run-a1.json", client.get(
            "/api/v1/telemetry/archive/runs/run-a1", headers=admin).json())
        write("run_detail_run-b2_scrubbed.json", client.get(
            "/api/v1/telemetry/archive/runs/run-b2", headers=admin).json())
        write("member_scoped_quality.json", client.get(
            "/api/v1/telemetry/quality", headers=alice).json())
        write("member_cross_team_forbidden.json", client.get(
            "/api/v1/telemetry/quality", params={"team": "team-b"}, headers=alice).json())
        write("unauthenticated.json", client.get("/api/v1/telemetry/quality").json())

        routes = sorted(
            (
                {"path": route.path, "methods": sorted(route.methods or [])}
                for route in app.routes
                if hasattr(route, "methods")
            ),
            key=lambda r: r["path"],
        )
        write("routes.json", routes)

    print("span goldens:")
    records_by_id = {r.runId: r for r in records}
    for run_id in sorted(records_by_id):
        record = records_by_id[run_id]
        spans = [asdict(s) for s in spans_for_run(record, outcome=record.outcome(fixtures.AS_OF))]
        write(f"spans/{run_id}.json", spans)

    write("traceparents.json", {
        run_id: traceparent_for_run(run_id) for run_id in sorted(records_by_id)
    })
    print(f"done -> {OUT}")


if __name__ == "__main__":
    main()
