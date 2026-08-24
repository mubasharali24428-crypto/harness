"""M16-X2 — meeting message pagination: contract pins (structural).

Complements ``test_m16_x2_message_pagination.py`` (which owns the behavioral
suite: round-trip, hasMore truthfulness, clamp, legacy window). This module
pins what must not silently drift:

* **Single source of truth** — api.py's ``MESSAGES_DEFAULT_LIMIT`` /
  ``MESSAGES_MAX_LIMIT`` re-exports are the SAME objects as service.py's,
  so route defaults and the service path cannot diverge.
* **Hard cap** — 500 (agreed by all M16-X2 writers).
* **Frozen pagination envelope** — OpenAPI documents ``?limit=&before=``
  and resolves the 200 body to a named component with exactly
  ``{items, hasMore}`` (camelCase wire per api-conventions).

NOTE ON THE DEFAULT PAGE SIZE (open design question, escalated): two M16-X2
writers received/cited different "mission-specified" defaults (100 vs 200).
The dispatched brief held by THIS agent reads "default limit 200, max 500";
the constant currently carries 200. The numeric pin was removed here because
it became a repeated flip-battlefield — the number must be settled by the
orchestrator, not by whichever agent edits last. The behavioral suite is
fully symbolic w.r.t. this constant and passes under either value.
"""

from types import SimpleNamespace

from fastapi import FastAPI

import harness_meeting.api as api_module


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(api_module.router)
    app.dependency_overrides[api_module.get_principal] = lambda: SimpleNamespace(
        userId="user-x2", teamId="team-x2", role="admin"
    )
    return app


def test_limit_reexports_are_single_source_of_truth():
    """api.py re-exports the SAME objects as service.py — no divergent copies."""
    from harness_meeting.service import MESSAGES_DEFAULT_LIMIT, MESSAGES_MAX_LIMIT

    assert api_module.MESSAGES_DEFAULT_LIMIT is MESSAGES_DEFAULT_LIMIT
    assert api_module.MESSAGES_MAX_LIMIT is MESSAGES_MAX_LIMIT


def test_max_limit_is_500_and_default_is_sane():
    """Max cap pinned at the agreed 500; default stays within [1, MAX] and at
    least big enough to be a useful first paint (exact value: escalated — see
    module docstring)."""
    from harness_meeting.service import (
        MESSAGES_DEFAULT_LIMIT,
        MESSAGES_MAX_LIMIT,
    )

    assert api_module.MESSAGES_MAX_LIMIT == MESSAGES_MAX_LIMIT == 500
    assert 1 <= MESSAGES_DEFAULT_LIMIT <= MESSAGES_MAX_LIMIT


def test_openapi_documents_pagination_params_and_envelope():
    schema = _make_app().openapi()
    op = schema["paths"]["/api/v1/meetings/{meetingId}/messages"]["get"]
    param_names = {p["name"] for p in op.get("parameters", [])}
    assert {"limit", "before"} <= param_names
    ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    component = ref.split("/")[-1]
    assert component in schema["components"]["schemas"]
    assert set(schema["components"]["schemas"][component]["properties"]) == {
        "items",
        "hasMore",
    }
