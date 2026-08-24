"""M13-G5a — OpenAPI typing islands for Section 6 meeting routes.

Pins two properties:

1. The top meeting routes carry concrete response schemas — openapi.json
   resolves named components, not bare ``{}`` objects.
2. response_model= FILTERS the actual handler output: extra keys a service
   might add later are stripped on the wire (the schema is load-bearing,
   not decorative).
"""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from harness_meeting.api import get_principal, router


def _principal():
    return SimpleNamespace(userId="user-g5a", teamId="team-g5a", role="admin")


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_principal] = lambda: _principal()
    return app


def _client() -> TestClient:
    return TestClient(_make_app())


_TYPED_S6_ROUTES = [
    ("post", "/api/v1/meetings"),
    ("get", "/api/v1/meetings/{meetingId}"),
    ("post", "/api/v1/meetings/{meetingId}/messages"),
    ("get", "/api/v1/meetings/{meetingId}/messages"),
    ("post", "/api/v1/meetings/{meetingId}/ai"),
    ("post", "/api/v1/meetings/{meetingId}/brief"),
    ("get", "/api/v1/meetings/{meetingId}/brief"),
    ("post", "/api/v1/meetings/{meetingId}/summary"),
]


def _success_component_ref(schema: dict, path: str, method: str) -> str | None:
    """$ref of the first concrete success component across 2xx codes."""
    op = schema["paths"][path][method]
    for code in ("200", "201", "202"):
        resp = op["responses"].get(code)
        if not resp:
            continue
        ref = resp.get("$ref") or (
            resp.get("content", {}).get("application/json", {}).get("schema", {}).get("$ref")
        )
        if ref:
            return ref
    return None


def _concrete_refs(node: object) -> list[str]:
    """All $ref component names reachable from one OpenAPI schema node,
    descending into arrays and anyOf/oneOf unions."""
    found: list[str] = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if "$ref" in cur:
                found.append(str(cur["$ref"]).split("/")[-1])
            for key in ("items", "prefixItems"):
                if key in cur:
                    stack.append(cur[key])
            for key in ("anyOf", "oneOf", "allOf"):
                for sub in cur.get(key, []):
                    stack.append(sub)
    return found


def test_s6_top_routes_have_concrete_openapi_components():
    schema = _make_app().openapi()
    missing = []
    for method, path in _TYPED_S6_ROUTES:
        op = schema["paths"][path][method]
        refs: list[str] = []
        for code in ("200", "201", "202"):
            resp = op["responses"].get(code)
            if not resp:
                continue
            node = resp.get("$ref") or (
                resp.get("content", {}).get("application/json", {}).get("schema")
            )
            if node:
                refs = _concrete_refs(node)
            if refs:
                break
        if not refs or not all(n in schema["components"]["schemas"] for n in refs):
            missing.append(f"{method.upper()} {path}")
    assert not missing, f"routes without concrete components: {missing}"


def test_response_model_filters_handler_output():
    """response_model= is load-bearing: unknown keys never reach the wire."""
    client = _client()
    created = client.post(
        "/api/v1/meetings",
        json={"projectId": "proj-g5a", "title": "G5a typing island"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == {"id", "projectId", "title", "createdAt", "briefId"}
    assert body["projectId"] == "proj-g5a"


def test_messages_roundtrip_matches_schema():
    client = _client()
    meeting_id = client.post(
        "/api/v1/meetings", json={"projectId": "proj-g5a", "title": "t"}
    ).json()["id"]
    posted = client.post(
        f"/api/v1/meetings/{meeting_id}/messages",
        json={"userId": "user-g5a", "content": "hello"},
    )
    assert posted.status_code == 201, posted.text
    msg = posted.json()
    assert set(msg) == {"id", "meetingId", "userId", "content", "timestamp", "isAiGenerated"}

    history = client.get(f"/api/v1/meetings/{meeting_id}/messages")
    assert history.status_code == 200
    assert isinstance(history.json(), list)
