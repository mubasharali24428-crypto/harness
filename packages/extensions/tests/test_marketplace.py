"""Curated marketplace: install/update/remove/revoke + tamper resistance."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
from conftest import as_user, make_settings

from harness_extensions.marketplace import canonical_digest


def _bundled_catalog_dir() -> Path:
    import harness_extensions

    return Path(harness_extensions.__file__).parent / "catalog"


async def test_catalog_lists_curated_entries(client):
    req = as_user(client, "tok-eng-a")
    res = await req("GET", "/api/v1/extensions/catalog")
    assert res.status_code == 200
    ids = {e["pluginId"] for e in res.json()["items"]}
    assert ids == {"incident-response", "repo-hygiene", "web-research"}


async def test_install_creates_skills_and_is_idempotent(client):
    req = as_user(client, "tok-eng-a")
    first = await req("POST", "/api/v1/extensions/catalog/incident-response/install")
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "installed"
    second = await req("POST", "/api/v1/extensions/catalog/incident-response/install")
    assert second.status_code == 201
    assert second.json()["installedAt"] == first.json()["installedAt"]

    skills = (await req("GET", "/api/v1/extensions/skills")).json()["items"]
    names = {s["name"] for s in skills}
    assert {"incident-response/triage", "incident-response/postmortem"} <= names
    for s in skills:
        if s["name"].startswith("incident-response/"):
            assert s["source"] == "plugin:incident-response"


async def test_uninstall_removes_plugin_skills(client):
    req = as_user(client, "tok-eng-a")
    await req("POST", "/api/v1/extensions/catalog/repo-hygiene/install")
    res = await req("POST", "/api/v1/extensions/plugins/repo-hygiene/uninstall")
    assert res.status_code == 200
    assert res.json()["status"] == "removed"
    skills = (await req("GET", "/api/v1/extensions/skills")).json()["items"]
    assert all(not s["name"].startswith("repo-hygiene/") for s in skills)


async def test_revoke_is_terminal_and_admin_only(client):
    req_admin = as_user(client, "tok-admin-a")
    req_eng = as_user(client, "tok-eng-a")
    await req_eng("POST", "/api/v1/extensions/catalog/web-research/install")
    denied = await req_eng("POST", "/api/v1/extensions/plugins/web-research/revoke")
    assert denied.status_code == 403
    revoke = await req_admin("POST", "/api/v1/extensions/plugins/web-research/revoke")
    assert revoke.status_code == 200
    assert revoke.json()["status"] == "revoked"
    reinstall = await req_eng("POST", "/api/v1/extensions/catalog/web-research/install")
    assert reinstall.status_code == 409
    assert reinstall.json()["error"]["code"] == "PLUGIN_REVOKED"
    skills = (await req_eng("GET", "/api/v1/extensions/skills")).json()["items"]
    assert all(not s["name"].startswith("web-research/") for s in skills)


async def test_tampered_payload_fails_closed(tmp_path):
    """Flip one byte in a payload: the entry must vanish from the catalog."""
    tampered = tmp_path / "catalog"
    shutil.copytree(_bundled_catalog_dir(), tampered)
    payload_path = tampered / "repo-hygiene-1.0.0.json"
    payload = json.loads(payload_path.read_text())
    payload["skills"][0]["content"] += "MALICIOUS"
    payload_path.write_text(json.dumps(payload))

    settings = make_settings(tmp_path, catalog_dir=str(tampered))
    from conftest import principal_dep

    from harness_extensions.api import make_app

    app = make_app(settings, get_principal_dep=principal_dep)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ext.test") as c:
        req = as_user(c, "tok-eng-a")
        catalog = await req("GET", "/api/v1/extensions/catalog")
        ids = {e["pluginId"] for e in catalog.json()["items"]}
        assert "repo-hygiene" not in ids  # refused at load
        assert "incident-response" in ids
        install = await req("POST", "/api/v1/extensions/catalog/repo-hygiene/install")
        assert install.status_code == 404
        assert install.json()["error"]["code"] == "PLUGIN_NOT_FOUND"


async def test_digest_mismatch_at_install_boundary(tmp_path):
    """Even a hand-built Catalog with a mismatched digest must not install."""
    from harness_extensions.marketplace import MarketplaceError, install_plugin, load_catalog
    from harness_extensions.store import Store

    settings = make_settings(tmp_path)
    catalog = load_catalog(settings)
    tainted_payloads = dict(catalog.payloads)
    tainted = dict(tainted_payloads["incident-response"])
    tainted["skills"] = [{"name": "triage", "content": "evil"}]
    tainted_payloads["incident-response"] = tainted
    tainted_catalog = type(catalog)(
        entries=catalog.entries, payloads=tainted_payloads, templates=catalog.templates
    )
    import pytest

    with pytest.raises(MarketplaceError) as err:
        install_plugin(
            Store(settings.db_path),
            settings,
            tainted_catalog,
            team_id="team-a",
            plugin_id="incident-response",
            installed_by="u-eng-a",
        )
    assert err.value.code == "PLUGIN_DIGEST_MISMATCH"


async def test_update_cycle_new_catalog_version(tmp_path):
    """Install v1; a v2 catalog then updates skills to a new immutable version."""
    upgraded = tmp_path / "catalog-v2"
    shutil.copytree(_bundled_catalog_dir(), upgraded)
    payload_path = upgraded / "incident-response-1.0.0.json"
    payload = json.loads(payload_path.read_text())
    payload["version"] = "2.0.0"
    payload["skills"][0]["content"] += "\n6. Page the on-call lead if impact > 30 min."
    new_payload_name = "incident-response-2.0.0.json"
    (upgraded / new_payload_name).write_text(json.dumps(payload))
    index_path = upgraded / "index.json"
    index = json.loads(index_path.read_text())
    for entry in index["entries"]:
        if entry["pluginId"] == "incident-response":
            entry["version"] = "2.0.0"
            entry["payload"] = new_payload_name
            entry["digest"] = canonical_digest(payload)
    index_path.write_text(json.dumps(index))

    from conftest import principal_dep

    from harness_extensions.api import make_app

    # v1 install against the bundled catalog.
    settings_v1 = make_settings(tmp_path)
    app_v1 = make_app(settings_v1, get_principal_dep=principal_dep)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_v1), base_url="http://ext.test"
    ) as c1:
        req1 = as_user(c1, "tok-eng-a")
        first = await req1("POST", "/api/v1/extensions/catalog/incident-response/install")
        assert first.json()["version"] == "1.0.0"
        skills = (await req1("GET", "/api/v1/extensions/skills")).json()["items"]
        triage = next(s for s in skills if s["name"] == "incident-response/triage")
        assert triage["latestVersion"] == 1

    # v2 catalog, same DB file: install performs the update.
    settings_v2 = make_settings(tmp_path, catalog_dir=str(upgraded))
    app_v2 = make_app(settings_v2, get_principal_dep=principal_dep)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_v2), base_url="http://ext.test"
    ) as c2:
        req2 = as_user(c2, "tok-eng-a")
        updated = await req2("POST", "/api/v1/extensions/catalog/incident-response/install")
        assert updated.json()["version"] == "2.0.0"
        skills = (await req2("GET", "/api/v1/extensions/skills")).json()["items"]
        triage = next(s for s in skills if s["name"] == "incident-response/triage")
        assert triage["latestVersion"] == 2
        latest = (
            await req2("GET", f"/api/v1/extensions/skills/{triage['skillId']}")
        ).json()
        assert "on-call lead" in latest["version"]["content"]
        # v1 content stays retrievable: versions are immutable.
        old = (
            await req2("GET", f"/api/v1/extensions/skills/{triage['skillId']}?version=1")
        ).json()
        assert "on-call lead" not in old["version"]["content"]

        un = await req2("POST", "/api/v1/extensions/plugins/incident-response/uninstall")
        assert un.status_code == 200
