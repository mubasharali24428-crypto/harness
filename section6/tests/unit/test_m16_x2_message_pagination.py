"""M16-X2 — meeting message pagination (raw-03 #15 server half).

Pins the four named mission properties:

1. Round-trip: walking pages backwards via ``before=<messageSeq>`` chains
   with NO gap and NO overlap — concatenation equals the full transcript.
2. ``hasMore`` is exact at every boundary (never lies in either direction).
3. Default ``limit`` is enforced: a no-param GET returns the NEWEST window
   (last MESSAGES_DEFAULT_LIMIT), never the whole transcript.
4. Oversized ``limit`` is CLAMPED (not rejected) to MESSAGES_MAX_LIMIT.

Plus the X-lane fetch-pattern contract: items carry ``messageSeq`` usable
directly as the next ``before``, POST bodies stay unpaged, and unresolvable
cursors degrade to an honest empty page.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import harness_meeting.api as api_module
from harness_meeting.api import MESSAGES_DEFAULT_LIMIT, MESSAGES_MAX_LIMIT, get_principal
from harness_meeting.service import MeetingService


@pytest.fixture()
def svc(monkeypatch) -> MeetingService:
    """Fresh MeetingService wired into the router for full test isolation."""
    service = MeetingService()
    monkeypatch.setattr(api_module, "_service", service)
    return service


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(api_module.router)
    app.dependency_overrides[get_principal] = lambda: SimpleNamespace(
        userId="u-x2", teamId="team-x2"
    )
    app.state.project_team_lookup = lambda _pid: "team-x2"  # S1-R01d wiring
    return TestClient(app)


def _new_meeting(client: TestClient) -> str:
    res = client.post("/api/v1/meetings", json={"projectId": "proj-x2", "title": "X2"})
    assert res.status_code == 201
    return res.json()["id"]


def _seed(svc: MeetingService, n: int) -> str:
    """Meeting with n plain messages, seeded directly (no AI side effects)."""
    meeting = svc.create_meeting("proj-x2", "seed")
    room = svc.rooms[meeting.id]
    for i in range(1, n + 1):
        room.post_message(user_id="u-seed", content=f"m{i}")
    return meeting.id


# ---------------------------------------------------------------------------
# 1: round-trip — backward walk reproduces the transcript exactly
# ---------------------------------------------------------------------------


def test_backward_walk_has_no_gap_and_no_overlap(svc):
    client = _client()
    meeting_id = _new_meeting(client)
    total, page_size = 250, 40
    for i in range(1, total + 1):
        posted = client.post(
            f"/api/v1/meetings/{meeting_id}/messages",
            json={"userId": "u-x2", "content": f"m{i}"},
        )
        assert posted.status_code == 201

    # X-lane pattern verbatim: first paint -> walk back while hasMore.
    first = client.get(f"/api/v1/meetings/{meeting_id}/messages?limit={page_size}")
    body = first.json()
    collected, seen_ids = list(body["items"]), {m["id"] for m in body["items"]}
    walks = 0
    while body["hasMore"]:
        cursor = body["items"][0]["messageSeq"]
        nxt = client.get(
            f"/api/v1/meetings/{meeting_id}/messages?limit={page_size}&before={cursor}"
        )
        assert nxt.status_code == 200
        body = nxt.json()
        collected = body["items"] + collected  # PREPEND older page
        for m in body["items"]:
            assert m["id"] not in seen_ids, "overlap: message served twice"
            seen_ids.add(m["id"])
        walks += 1
        assert walks < total  # infinite-loop guard

    expected = [f"m{i}" for i in range(1, total + 1)]
    assert [m["content"] for m in collected] == expected, "gap or reorder in chain"
    assert len(collected) == total
    seqs = [m["messageSeq"] for m in collected]
    assert seqs == list(range(1, total + 1)), "messageSeq must be 1..N in order"
    # ceil(250/40) pages => 6 backward walks after the first paint
    assert walks == -(-total // page_size) - 1


# ---------------------------------------------------------------------------
# 2: hasMore exactness at boundaries
# ---------------------------------------------------------------------------


def test_hasmore_boundary_exact_count(svc):
    client = _client()
    meeting_id = _new_meeting(client)
    for i in range(1, MESSAGES_DEFAULT_LIMIT + 1):
        client.post(
            f"/api/v1/meetings/{meeting_id}/messages",
            json={"userId": "u-x2", "content": f"b{i}"},
        )
    exact = client.get(f"/api/v1/meetings/{meeting_id}/messages").json()
    assert len(exact["items"]) == MESSAGES_DEFAULT_LIMIT
    assert exact["hasMore"] is False, "N == limit: nothing older remains"

    client.post(
        f"/api/v1/meetings/{meeting_id}/messages",
        json={"userId": "u-x2", "content": "the straw"},
    )
    over = client.get(f"/api/v1/meetings/{meeting_id}/messages").json()
    assert len(over["items"]) == MESSAGES_DEFAULT_LIMIT
    assert over["hasMore"] is True, "N == limit+1: exactly one older message exists"
    assert over["items"][0]["messageSeq"] == 2
    assert over["items"][-1]["content"] == "the straw"


def test_hasmore_false_at_transcript_head(svc):
    client = _client()
    meeting_id = _seed(svc, 10)
    head = client.get(f"/api/v1/meetings/{meeting_id}/messages?limit=5&before=6").json()
    assert [m["content"] for m in head["items"]] == ["m1", "m2", "m3", "m4", "m5"]
    tail_page = client.get(
        f"/api/v1/meetings/{meeting_id}/messages?limit=5&before=1"
    ).json()
    assert tail_page["items"] == [] and tail_page["hasMore"] is False


def test_unresolvable_cursors_yield_honest_empty_page(svc):
    client = _client()
    meeting_id = _seed(svc, 5)
    # Unresolvable LEGACY id cursors => empty page, never fabricated data.
    page = client.get(f"/api/v1/meetings/{meeting_id}/messages?before=not-a-real-id")
    assert page.status_code == 200
    assert page.json() == {"items": [], "hasMore": False}


def test_cursor_past_transcript_tail_behaves_like_sql_keyset(svc):
    client = _client()
    meeting_id = _seed(svc, 5)
    # before=<huge seq> == WHERE seq < 999999: everything, newest-window.
    over = client.get(f"/api/v1/meetings/{meeting_id}/messages?before=999999").json()
    assert len(over["items"]) == 5 and over["hasMore"] is False
    # before=0 anchors at the head => strictly older than seq<=0 => nothing.
    under = client.get(f"/api/v1/meetings/{meeting_id}/messages?limit=3&before=0").json()
    assert under["items"] == [] and under["hasMore"] is False


# ---------------------------------------------------------------------------
# 3: default limit enforced (unbounded default removed)
# ---------------------------------------------------------------------------


def test_default_limit_returns_newest_window_only(svc):
    client = _client()
    meeting_id = _seed(svc, MESSAGES_DEFAULT_LIMIT + 30)
    page = client.get(f"/api/v1/meetings/{meeting_id}/messages").json()
    assert len(page["items"]) == MESSAGES_DEFAULT_LIMIT, "no-param GET must cap"
    assert page["hasMore"] is True
    assert page["items"][0]["messageSeq"] == 31, "window must be the NEWEST one"
    assert page["items"][-1]["content"] == f"m{MESSAGES_DEFAULT_LIMIT + 30}"


def test_service_default_matches_route_default(svc):
    meeting_id = _seed(svc, MESSAGES_DEFAULT_LIMIT + 1)
    items, has_more = svc.get_messages(meeting_id)
    assert len(items) == MESSAGES_DEFAULT_LIMIT
    assert has_more is True


# ---------------------------------------------------------------------------
# 4: oversized limit clamped, never rejected
# ---------------------------------------------------------------------------


def test_oversized_limit_clamped_to_max(svc):
    client = _client()
    meeting_id = _seed(svc, 600)
    res = client.get(f"/api/v1/meetings/{meeting_id}/messages?limit=100000")
    assert res.status_code == 200, "clamp, never reject"
    page = res.json()
    assert len(page["items"]) == MESSAGES_MAX_LIMIT <= 600
    assert page["hasMore"] is True
    assert page["items"][0]["messageSeq"] == 600 - MESSAGES_MAX_LIMIT + 1


def test_limit_below_one_rejected_cleanly(svc):
    client = _client()
    meeting_id = _seed(svc, 3)
    res = client.get(f"/api/v1/meetings/{meeting_id}/messages?limit=0")
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# X-lane contract: messageSeq stamping, shapes, legacy cursor, 404
# ---------------------------------------------------------------------------


def test_items_carry_message_seq_but_post_body_is_unchanged(svc):
    client = _client()
    meeting_id = _new_meeting(client)
    posted = client.post(
        f"/api/v1/meetings/{meeting_id}/messages",
        json={"userId": "u-x2", "content": "hello"},
    )
    assert set(posted.json()) == {
        "id",
        "meetingId",
        "userId",
        "content",
        "timestamp",
        "isAiGenerated",
    }, "POST wire shape must stay unpaged"

    page = client.get(f"/api/v1/meetings/{meeting_id}/messages").json()
    assert set(page) == {"items", "hasMore"}
    assert set(page["items"][0]) == {
        "id",
        "meetingId",
        "userId",
        "content",
        "timestamp",
        "isAiGenerated",
        "messageSeq",
    }
    assert page["items"][0]["messageSeq"] == 1


def test_legacy_message_id_cursor_accepted_one_release(svc):
    client = _client()
    meeting_id = _seed(svc, 12)
    page = client.get(f"/api/v1/meetings/{meeting_id}/messages?limit=4").json()
    first_id = page["items"][0]["id"]
    first_seq = page["items"][0]["messageSeq"]
    via_id = client.get(
        f"/api/v1/meetings/{meeting_id}/messages?limit=4&before={first_id}"
    ).json()
    via_seq = client.get(
        f"/api/v1/meetings/{meeting_id}/messages?limit=4&before={first_seq}"
    ).json()
    assert [m["id"] for m in via_id["items"]] == [m["id"] for m in via_seq["items"]]
    assert via_id["hasMore"] == via_seq["hasMore"]


def test_unknown_meeting_still_404(svc):
    client = _client()
    res = client.get("/api/v1/meetings/mtg-nope/messages")
    assert res.status_code == 404
    res = client.get("/api/v1/meetings/mtg-nope/messages?limit=5&before=3")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Scale: 100k-message meeting answers with a bounded page
# ---------------------------------------------------------------------------


def test_100k_messages_answer_bounded(svc):
    client = _client()
    meeting_id = _seed(svc, 100_000)
    page = client.get(f"/api/v1/meetings/{meeting_id}/messages").json()
    assert len(page["items"]) == MESSAGES_DEFAULT_LIMIT
    assert page["hasMore"] is True
    assert page["items"][0]["messageSeq"] == 100_000 - MESSAGES_DEFAULT_LIMIT + 1

    older = client.get(
        f"/api/v1/meetings/{meeting_id}/messages?limit=10&before=99001"
    ).json()
    assert [m["messageSeq"] for m in older["items"]] == list(range(98991, 99001))


# ---------------------------------------------------------------------------
# Service core: first_seq authoritative mid-transcript
# ---------------------------------------------------------------------------


def test_get_message_page_first_seq_mid_transcript(svc):
    meeting_id = _seed(svc, 10)
    items, has_more, first_seq = svc.get_message_page(meeting_id, limit=3, before=5)
    assert [m.content for m in items] == ["m2", "m3", "m4"]
    assert has_more is True
    assert first_seq == 2
