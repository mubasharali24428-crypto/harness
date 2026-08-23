# M10-B3 — Section 10 onboarding step + checklist tests.
#
# HOW TO RUN (honest constraints): section10 has NO virtualenv of its own, so
# these tests are plain pytest files meant to run with ANY pytest against
# PYTHONPATH=src (plus ../contracts/src for harness_contracts), e.g.:
#   cd section10 && PYTHONPATH=src:../contracts/src <any-python-with-fastapi> \
#       -m pytest tests/test_steps.py -q
# No section1 source is imported here — borrowing another section's interpreter
# as a runner is fine; importing its code is not.

import json

from fastapi.testclient import TestClient

from harness_onboarding.api import (
    ChecklistItem,
    build_checklist_items,
    derive_done,
    make_app,
    router,
)
from harness_onboarding.steps import STEPS, OnboardingStep, steps_payload
from harness_onboarding.store import StateStore

EXPECTED_ORDER = [
    "connect_github",
    "other_integrations",
    "create_team",
    "invite_members",
    "start_meeting",
    "guided_demo",
]


# ---------------------------------------------------------------------------
# Pure functions over steps.py — the frozen onboarding order (brief §11)
# ---------------------------------------------------------------------------


def test_steps_frozen_order():
    assert [s.id for s in STEPS] == EXPECTED_ORDER
    assert [s.order for s in STEPS] == list(range(1, len(STEPS) + 1))


def test_steps_payload_is_fresh_camelcase_dump():
    first = steps_payload()
    second = steps_payload()
    assert first is not second  # no shared mutable state between calls
    assert first == second
    for row, step in zip(first, STEPS, strict=True):
        assert set(row) == {
            "id",
            "order",
            "title",
            "description",
            "section",
            "serviceKey",
            "required",
            "availableInDeployment",
        }
        assert row["id"] == step.id
        assert row["serviceKey"] == step.serviceKey  # camelCase key survives


def test_steps_degraded_sections_are_flagged():
    # Steps owned by sections not yet deployed must degrade gracefully,
    # never dead-end the flow (module docstring contract).
    by_id = {s.id: s for s in STEPS}
    assert by_id["start_meeting"].availableInDeployment is False  # section 6
    assert by_id["start_meeting"].serviceKey == "meeting"
    assert by_id["other_integrations"].required is False


# ---------------------------------------------------------------------------
# derive_done / build_checklist_items — checklist derivation semantics
# ---------------------------------------------------------------------------


def test_derive_done_informational_step_always_done():
    assert derive_done("other_integrations", security_checklist_acknowledged=False) is True


def test_derive_done_guided_demo_tracks_security_ack():
    assert derive_done("guided_demo", security_checklist_acknowledged=False) is False
    assert derive_done("guided_demo", security_checklist_acknowledged=True) is True


def test_derive_done_unknown_or_unsignalled_steps_are_not_done():
    # Honest default: nothing pretends completion without a real signal.
    assert derive_done("connect_github", security_checklist_acknowledged=True) is False
    assert derive_done("create_team", security_checklist_acknowledged=True) is False
    assert derive_done("invite_members", security_checklist_acknowledged=True) is False
    assert derive_done("start_meeting", security_checklist_acknowledged=True) is False
    assert derive_done("no_such_step", security_checklist_acknowledged=False) is False


def test_build_checklist_items_wire_shape_and_order():
    items = build_checklist_items(security_checklist_acknowledged=False)
    assert [i["id"] for i in items] == EXPECTED_ORDER
    for item in items:
        assert set(item) == {"id", "title", "description", "done"}
        assert isinstance(item["done"], bool)
        assert item["title"]  # titles carry over from steps.py
        assert item["description"]  # every current step has prose help


def test_build_checklist_items_done_flags_default_vs_acknowledged():
    default = {
        i["id"]: i["done"]
        for i in build_checklist_items(security_checklist_acknowledged=False)
    }
    acknowledged = {
        i["id"]: i["done"] for i in build_checklist_items(security_checklist_acknowledged=True)
    }
    assert default == {
        "connect_github": False,
        "other_integrations": True,
        "create_team": False,
        "invite_members": False,
        "start_meeting": False,
        "guided_demo": False,
    }
    assert acknowledged["guided_demo"] is True
    assert acknowledged["other_integrations"] is True
    assert sum(acknowledged.values()) == 2  # nothing else flips without a signal


def test_build_checklist_items_sorts_custom_steps_by_order():
    custom = [
        OnboardingStep(
            id="b_step",
            order=2,
            title="B",
            description="second",
            section=10,
            serviceKey=None,
        ),
        OnboardingStep(
            id="a_step",
            order=1,
            title="A",
            description="first",
            section=10,
            serviceKey=None,
            required=False,
        ),
    ]
    items = build_checklist_items(security_checklist_acknowledged=False, steps=custom)
    assert [i["id"] for i in items] == ["a_step", "b_step"]
    # Sorting must not change semantics: these ids have no completion signal,
    # and required=False alone does NOT mark a step done (only the dedicated
    # informational step "other_integrations" is done by definition).
    assert [i["done"] for i in items] == [False, False]


def test_checklist_item_model_rejects_missing_done():
    try:
        ChecklistItem(id="x", title="X")  # type: ignore[call-arg]
    except Exception:
        pass
    else:  # pragma: no cover - reached only when validation silently regresses
        raise AssertionError("done must be required on the wire shape")


# ---------------------------------------------------------------------------
# Router shape — exercised through a scratch FastAPI app (no deploy wiring)
# ---------------------------------------------------------------------------


def _client(tmp_path, **store_state):
    state_path = tmp_path / "state.json"
    if store_state:
        state_path.write_text(json.dumps(store_state), encoding="utf-8")
    return TestClient(make_app(store=StateStore(state_path)))


def test_get_checklist_route_shape(tmp_path):
    resp = _client(tmp_path).get("/api/v1/onboarding/checklist")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items"}
    ids = [item["id"] for item in body["items"]]
    assert ids == EXPECTED_ORDER
    done = {item["id"]: item["done"] for item in body["items"]}
    assert done["other_integrations"] is True
    assert done["guided_demo"] is False  # fresh state: nothing acknowledged yet
    assert done["connect_github"] is False


def test_get_checklist_reflects_acknowledged_store(tmp_path):
    client = _client(tmp_path, securityChecklistAcknowledged=True)
    body = client.get("/api/v1/onboarding/checklist").json()
    done = {item["id"]: item["done"] for item in body["items"]}
    assert done["guided_demo"] is True


def test_checklist_after_acknowledge_action(tmp_path):
    client = _client(tmp_path)
    before = client.post("/api/v1/onboarding/security-checklist/acknowledge")
    assert before.status_code == 200
    body = client.get("/api/v1/onboarding/checklist").json()
    done = {item["id"]: item["done"] for item in body["items"]}
    assert done["guided_demo"] is True  # the one completion action S10 owns today


def test_get_steps_still_intact_alongside_new_route(tmp_path):
    resp = _client(tmp_path).get("/api/v1/onboarding/steps")
    assert resp.status_code == 200
    body = resp.json()
    assert [row["id"] for row in body["items"]] == EXPECTED_ORDER
    assert "done" not in body["items"][0]  # /steps keeps its frozen shape


def test_make_app_health_and_route_registration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # default Settings() writes state next to cwd only
    app = make_app()
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok", "service": "harness-onboarding"}
    resp = client.get("/api/v1/onboarding/checklist")
    assert resp.status_code == 200
    assert set(resp.json()) == {"items"}
    # FastAPI >=0.13x nests included routers (no flat paths on app.routes), so
    # assert registration on the router itself (which owns the /api/v1/onboarding
    # prefix) + functionally via the client above.
    assert "/api/v1/onboarding/checklist" in {getattr(r, "path", "") for r in router.routes}
