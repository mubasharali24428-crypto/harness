"""F150 distribution verification: build wheels, install into a FRESH venv,
run a smoke journey there, and print machine-readable evidence.

This proves the published artifacts work outside the dev tree — the fresh venv
has NO editable installs and NO repo on sys.path.

Run from the repo root (takes ~1-2 min; installs deps from PyPI):
    ./packages/extensions/.venv/bin/python packages/extensions/scripts/verify_distribution.py
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
PY = str(pathlib.Path(sys.executable))

SMOKE = r"""
import asyncio, json, pathlib, tempfile

import httpx
from fastapi import HTTPException
from harness_contracts.roles import Principal, Role

import harness_extensions
from harness_extensions.api import make_app
from harness_extensions.client import ExtensionsClient
from harness_extensions.config import Settings
from harness_wiki_connector import slugify

import harness_telemetry as ht
from harness_telemetry import fixtures as ht_fx
from harness_telemetry.dashboard import make_dashboard_app

import harness_workflow_connectors as hwc
from harness_workflow_connectors import slack as slack_mod
from harness_workflow_connectors.plan_gate import ProposePlanGate
from harness_workflow_connectors.service import make_service_app

import harness_ops
from harness_ops.evidence import build_evidence, verify_evidence_zip
from harness_ops.store import OpsStore, WebhookRow
from harness_ops.usage import usage_snapshot


async def connector_check():
    tenants = hwc.TenantMap(tenants={"slack": {"T1": hwc.TenantConfig(team_id="team-smoke")}})
    pipeline = hwc.IntakePipeline(
        provider="slack",
        scheme=hwc.SignatureScheme(name=slack_mod.SLACK_SCHEME_NAME, **slack_mod.SLACK_SIGNATURE),
        secret="s",
        tenants=tenants,
        idempotency=hwc.IdempotencyStore(str(pathlib.Path(tempfile.mkdtemp()) / "d.sqlite")),
        limiter=hwc.RateLimiter(),
        plan_gate=ProposePlanGate("http://127.0.0.1:1", bearer_token="x"),
        parse=slack_mod.parse_slack_events,
    )
    capp = make_service_app(pipelines={"slack": pipeline})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=capp), base_url="http://c") as c:
        r = await c.get("/healthz")
        assert r.status_code == 200 and r.json()["providers"] == ["slack"], r.text
    return hwc.__file__


def telemetry_check():
    class Admin:
        userId = "u-admin"
        teamId = "team-a"
        role = "admin"

    def dep(request):
        return Admin()

    evs = ht.parse_stream(ht_fx.events())
    costs = [ht.parse_cost(c) for c in ht_fx.costs()]
    records = ht.build_records(evs, costs, ht_fx.PROJECT_TEAM, as_of=ht_fx.AS_OF)
    q = ht.quality_metrics(records, as_of=ht_fx.AS_OF)
    assert q["accepted"] == 3 and q["claimedButNotAccepted"] == 1, q
    c = ht.cost_metrics(records)
    assert c["costPerAcceptedChange"] == "4.3667", c
    assert c["costPerClaimedCompletion"] == "4.2750", c

    arc = ht.Archive(str(pathlib.Path(tempfile.mkdtemp()) / "t.sqlite"))
    arc.ingest(evs, costs, ht_fx.PROJECT_TEAM, as_of=ht_fx.AS_OF)
    hits = arc.search(visible_teams=None, q="login")
    assert hits["total"] == 1 and "[EMAIL]" in hits["runs"][0]["goal"], hits

    accepted = next(r for r in records if r.runId == "run-a1")
    spans = ht.spans_for_run(accepted, outcome=accepted.outcome(ht_fx.AS_OF))
    assert spans[0].name == "harness.run"
    assert ht.parse_traceparent(ht.traceparent_for_run("run-a1"))[0] == spans[0].trace_id

    app = make_dashboard_app(archive=arc, records=records, as_of=ht_fx.AS_OF,
                             get_principal_dep=dep)
    return app


def ops_check():
    from harness_ops.schedule import next_occurrence

    evs = ht.parse_stream(ht_fx.events())
    costs = [ht.parse_cost(c) for c in ht_fx.costs()]
    records = ht.build_records(evs, costs, ht_fx.PROJECT_TEAM, as_of=ht_fx.AS_OF)
    snap = usage_snapshot(records, as_of=ht_fx.AS_OF)
    assert snap["tasks"] == 8 and snap["totalCostUsd"] == "24.7000", snap
    assert snap["costPerClaimedCompletion"] == "4.2750", snap

    arc = ht.Archive(str(pathlib.Path(tempfile.mkdtemp()) / "ops-arc.sqlite"))
    arc.ingest(evs, costs, ht_fx.PROJECT_TEAM, as_of=ht_fx.AS_OF)
    package = build_evidence(archive=arc, visible_teams=None,
                             generated_at=ht_fx.AS_OF)
    manifest = verify_evidence_zip(package.to_zip())
    assert manifest["counts"]["runs"] == 8, manifest
    assert manifest["counts"]["timelineEvents"] == 42, manifest

    dst = next_occurrence({"kind": "daily", "time": "02:30"},
                          "America/New_York",
                          after=ht_fx.AS_OF.replace(year=2026, month=3, day=7, hour=7, minute=31))
    assert dst.isoformat() == "2026-03-08T07:30:00+00:00", dst

    store = OpsStore(str(pathlib.Path(tempfile.mkdtemp()) / "ops.sqlite"))
    store.insert_webhook(WebhookRow(
        webhookId="wh-smoke", url="https://h.example/x", events=["alert.opened"],
        secret="s", teamId=None, active=True, createdBy="u", createdAt="x"))
    assert store.claim_schedule_run(
        schedule_id="sch", occurrence="2026-10-17T06:30:00+00:00",
        triggered_at=ht_fx.AS_OF, catchup=False) is True
    return harness_ops.__file__


async def who(request):
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if token != "smoke":
        raise HTTPException(status_code=401, detail="no")
    return Principal(userId="u1", teamId="team-smoke", role=Role.ENGINEER)


async def main():
    tmp = tempfile.TemporaryDirectory()
    settings = Settings(db_path=str(pathlib.Path(tmp.name) / "db.sqlite"), allow_private_hosts=True)
    app = make_app(settings, get_principal_dep=who)

    paths = set(app.openapi()["paths"])
    assert "/api/v1/extensions/commands" in paths, "commands route missing from wheel openapi"
    assert "/api/v1/extensions/hooks" in paths, "hooks route missing from wheel openapi"
    assert "/api/v1/extensions/policy" in paths, "policy route missing from wheel openapi"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://x") as http:
        client = ExtensionsClient("http://x", token="smoke", client=http)
        cmd = await client.create_command(name="hi", template="Hello {{who}}.", arguments=["who"])
        out = await client.invoke_command(cmd["commandId"], {"who": "wheel"})
        assert out["renderedText"] == "Hello wheel.", out
        catalog = await client.list_catalog()
        assert any(e["pluginId"] == "incident-response" for e in catalog), "catalog data missing"

    assert slugify("Wheel Smoke!") == "wheel-smoke"
    tapp = telemetry_check()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=tapp), base_url="http://t") as t:
        tr = await t.get("/api/v1/telemetry/quality", headers={"authorization": "Bearer smoke"})
        assert tr.status_code == 200 and tr.json()["tasks"] == 8, tr.text
    connector_file = await connector_check()
    ops_file = ops_check()
    print(json.dumps({
        "harness_extensions": harness_extensions.__file__,
        "commands_paths": sorted(p for p in paths if "commands" in p),
        "rendered": out["renderedText"],
        "catalog_entries": len(catalog),
        "harness_workflow_connectors": connector_file,
        "harness_telemetry": ht.__file__,
        "harness_ops": ops_file,
    }))


asyncio.run(main())
print("SMOKE_OK")
"""


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main() -> int:
    tmp = tempfile.TemporaryDirectory(prefix="a5-dist-")
    tmp_path = pathlib.Path(tmp.name)
    dist = tmp_path / "dist"
    dist.mkdir()

    # In-place build artifacts created under our own namespaces (ignored by
    # git, but do not leave litter behind).
    own_projects = (
        ROOT / "packages" / "extensions",
        ROOT / "integrations" / "connectors" / "wiki",
        ROOT / "integrations" / "connectors" / "workflows",
        ROOT / "packages" / "telemetry",
        ROOT / "packages" / "ops",
    )

    wheels: list[pathlib.Path] = []
    for project in (
        ROOT / "contracts",
        ROOT / "packages" / "extensions",
        ROOT / "integrations" / "connectors" / "wiki",
        ROOT / "integrations" / "connectors" / "workflows",
        ROOT / "packages" / "telemetry",
        ROOT / "packages" / "ops",
    ):
        res = run([PY, "-m", "pip", "wheel", str(project), "-w", str(dist), "--no-deps"])
        if res.returncode != 0:
            print(res.stdout[-2000:], res.stderr[-2000:])
            return 1
    for project in own_projects:
        shutil.rmtree(project / "build", ignore_errors=True)
    wheels = sorted(dist.glob("*.whl"))
    names = [w.name for w in wheels]
    print("built wheels:", names)
    assert any(n.startswith("harness_contracts") for n in names)
    assert any(n.startswith("harness_extensions") for n in names)
    assert any(n.startswith("harness_wiki_connector") for n in names)
    assert any(n.startswith("harness_workflow_connectors") for n in names)
    assert any(n.startswith("harness_telemetry") for n in names)
    assert any(n.startswith("harness_ops") for n in names)

    fresh = tmp_path / "fresh-venv"
    res = run([PY, "-m", "venv", str(fresh)])
    assert res.returncode == 0, res.stderr
    fresh_py = fresh / "bin" / "python"

    res = run([str(fresh_py), "-m", "pip", "install", "-q", *[str(w) for w in wheels]])
    if res.returncode != 0:
        print(res.stdout[-3000:], res.stderr[-3000:])
        return 1

    res = run([str(fresh_py), "-c", SMOKE])
    print(res.stdout.strip())
    if res.returncode != 0 or "SMOKE_OK" not in res.stdout:
        print("STDERR:", res.stderr[-3000:])
        return 1

    probe = (
        "import json\n"
        "from importlib.metadata import PackageNotFoundError, version\n"
        "names = ['harness-contracts','harness-extensions','harness-wiki-connector',"
        "'harness-workflow-connectors','harness-telemetry','harness-ops',"
        "'fastapi','httpx','mcp']\n"
        "out = {}\n"
        "for n in names:\n"
        "    try:\n"
        "        out[n] = version(n)\n"
        "    except PackageNotFoundError:\n"
        "        out[n] = None\n"
        "print(json.dumps(out))\n"
    )
    res = run([str(fresh_py), "-c", probe])
    versions: dict = json.loads(res.stdout.strip())
    assert versions["harness-extensions"], "fresh venv cannot see harness-extensions"
    assert versions["harness-wiki-connector"], "fresh venv cannot see harness-wiki-connector"
    assert versions["harness-telemetry"], "fresh venv cannot see harness-telemetry"
    assert versions["harness-ops"], "fresh venv cannot see harness-ops"
    evidence = {"wheels": names, "fresh_venv_installs": versions}
    print("EVIDENCE:", json.dumps(evidence, indent=2))
    print("DISTRIBUTION_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
