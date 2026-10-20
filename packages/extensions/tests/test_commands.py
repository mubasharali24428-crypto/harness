"""Namespaced commands: template rendering and strict argument validation."""

from __future__ import annotations

from conftest import TEAM_A, as_user


async def test_create_and_invoke_command(client):
    req = as_user(client, "tok-eng-a")
    created = await req(
        "POST",
        "/api/v1/extensions/commands",
        json={
            "name": "release-notes",
            "template": "Draft release notes for {{version}} targeting {{channel}}.",
            "arguments": ["version", "channel"],
            "description": "Generate a release-notes draft",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "team/release-notes"
    assert body["namespace"] == "team"
    assert body["arguments"] == ["channel", "version"]

    invoked = await req(
        "POST",
        f"/api/v1/extensions/commands/{body['commandId']}/invoke",
        json={"arguments": {"version": "2.1.0", "channel": "beta"}},
    )
    assert invoked.status_code == 200, invoked.text
    out = invoked.json()
    assert out["renderedText"] == "Draft release notes for 2.1.0 targeting beta."
    assert len(out["renderedDigest"]) == 64

    listed = await req("GET", "/api/v1/extensions/commands")
    assert [c["name"] for c in listed.json()["items"]] == ["team/release-notes"]


async def test_invoke_strict_arguments(client):
    req = as_user(client, "tok-eng-a")
    created = (
        await req(
            "POST",
            "/api/v1/extensions/commands",
            json={"name": "greet", "template": "Hello {{who}}", "arguments": ["who"]},
        )
    ).json()
    cid = created["commandId"]

    missing = await req(
        "POST", f"/api/v1/extensions/commands/{cid}/invoke", json={"arguments": {}}
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "COMMAND_ARGS_MISSING"

    unknown = await req(
        "POST",
        f"/api/v1/extensions/commands/{cid}/invoke",
        json={"arguments": {"who": "x", "extra": 1}},
    )
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "COMMAND_ARGS_UNKNOWN"


async def test_undeclared_placeholder_rejected(client):
    req = as_user(client, "tok-eng-a")
    res = await req(
        "POST",
        "/api/v1/extensions/commands",
        json={"name": "bad", "template": "uses {{undeclared}}", "arguments": []},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "COMMAND_TEMPLATE_INVALID"


async def test_name_rules_and_conflict(client):
    req = as_user(client, "tok-eng-a")
    bad = await req(
        "POST",
        "/api/v1/extensions/commands",
        json={"name": "BAD NAME", "template": "x"},
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "COMMAND_NAME_INVALID"

    first = await req(
        "POST", "/api/v1/extensions/commands", json={"name": "dup", "template": "x"}
    )
    assert first.status_code == 201
    second = await req(
        "POST", "/api/v1/extensions/commands", json={"name": "dup", "template": "y"}
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "COMMAND_NAME_CONFLICT"


async def test_cross_tenant_no_oracle_and_viewer_denied(client):
    req_a = as_user(client, "tok-eng-a")
    req_b = as_user(client, "tok-eng-b")
    view = as_user(client, "tok-view-a")
    created = (
        await req_a(
            "POST", "/api/v1/extensions/commands", json={"name": "a-only", "template": "x"}
        )
    ).json()
    invoke = await req_b(
        "POST",
        f"/api/v1/extensions/commands/{created['commandId']}/invoke",
        json={"arguments": {}},
    )
    assert invoke.status_code == 404
    denied = await view(
        "POST",
        f"/api/v1/extensions/commands/{created['commandId']}/invoke",
        json={"arguments": {}},
    )
    assert denied.status_code == 403


async def test_removed_command_is_gone(app, client):
    req = as_user(client, "tok-eng-a")
    created = (
        await req(
            "POST", "/api/v1/extensions/commands", json={"name": "temp", "template": "x"}
        )
    ).json()
    cid = created["commandId"]
    assert (
        await req(
            "POST", f"/api/v1/extensions/commands/{cid}/invoke", json={"arguments": {}}
        )
    ).status_code == 200
    # Removal flips status in the store (console surface arrives in S4).
    app.state.extension_store.set_command_status(TEAM_A, cid, "removed")
    gone = await req(
        "POST", f"/api/v1/extensions/commands/{cid}/invoke", json={"arguments": {}}
    )
    assert gone.status_code == 404
    assert gone.json()["error"]["code"] == "COMMAND_NOT_FOUND"
    listed = await req("GET", "/api/v1/extensions/commands")
    assert cid not in [c["commandId"] for c in listed.json()["items"]]
