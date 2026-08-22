"""Unit tests for chat room and message model."""

from harness_meeting.chat import ChatMessage, MeetingRoom


def test_post_message_returns_chat_message():
    room = MeetingRoom("mtg-001")
    msg = room.post_message("user-1", "Hello world")
    assert isinstance(msg, ChatMessage)
    assert msg.meetingId == "mtg-001"
    assert msg.userId == "user-1"
    assert msg.content == "Hello world"
    assert msg.isAiGenerated is False


def test_post_ai_message():
    room = MeetingRoom("mtg-001")
    msg = room.post_message("ai-participant", "AI response", is_ai=True)
    assert msg.isAiGenerated is True


def test_get_history_returns_ordered_messages():
    room = MeetingRoom("mtg-002")
    room.post_message("u1", "First")
    room.post_message("u2", "Second")
    room.post_message("u1", "Third")
    history = room.get_history()
    assert len(history) == 3
    assert history[0].content == "First"
    assert history[2].content == "Third"


def test_get_transcript_text():
    room = MeetingRoom("mtg-003")
    room.post_message("user-1", "Hello")
    room.post_message("ai-participant", "Hi there", is_ai=True)
    transcript = room.get_transcript_text()
    assert "[user-1]: Hello" in transcript
    assert "[AI]: Hi there" in transcript


def test_message_ids_are_unique():
    room = MeetingRoom("mtg-004")
    ids = set()
    for i in range(20):
        msg = room.post_message("u1", f"msg-{i}")
        ids.add(msg.id)
    assert len(ids) == 20
