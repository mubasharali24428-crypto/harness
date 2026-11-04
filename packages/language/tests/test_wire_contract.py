"""The wire half: Hx must speak Section 5's protocol without extending it.

Section 5 owns ``{path,line,col,kind,message}``. The code that would be a sixth
field rides inside ``message`` instead, and these tests pin both halves: the
bytes, the key order, the omission of absent keys, and the human line the
located tier parses.
"""

from __future__ import annotations

import json

from harness_hx import Diagnostic, render_json, render_line


def test_an_empty_run_is_an_empty_array_not_a_blank_line():
    assert render_json([]) == "[]"


def test_field_order_and_omitted_keys_are_exact():
    records = [
        Diagnostic("HX4001", "error", "capability 'tool.test' is not declared", "app.hx", 3, 7),
        Diagnostic("HX3005", "warning", "unused variable", "app.hx", 9),
        Diagnostic("HX6002", "note", "profile chosen by host"),
    ]
    assert render_json(records) == (
        '[{"path":"app.hx","line":3,"col":7,"kind":"error",'
        '"message":"[HX4001] capability \'tool.test\' is not declared"},'
        '{"path":"app.hx","line":9,"kind":"warning",'
        '"message":"[HX3005] unused variable"},'
        '{"kind":"note","message":"[HX6002] profile chosen by host"}]'
    )


def test_only_the_five_frozen_keys_can_appear():
    stream = render_json([Diagnostic("HX1001", "error", "boom", "a.hx", 1, 1)])
    record = json.loads(stream)[0]
    assert list(record) == ["path", "line", "col", "kind", "message"]


def test_no_position_is_invented():
    stream = render_json([Diagnostic("HX3005", "error", "unknown identifier 'x'")])
    record = json.loads(stream)[0]
    assert "path" not in record and "line" not in record and "col" not in record


def test_human_line_is_path_line_col_kind_message():
    diagnostic = Diagnostic("HX4001", "error", "nope", "src/app.hx", 12, 4)
    assert render_line(diagnostic) == "src/app.hx:12:4: error: [HX4001] nope"


def test_human_line_without_position_says_so_by_omission():
    assert render_line(Diagnostic("HX3005", "warning", "careful")) == "warning: [HX3005] careful"


def test_messages_are_utf8_safe_and_never_null_noise():
    stream = render_json([Diagnostic("HX2001", "error", "attendu ')'", "a.hx", 1, 2)])
    assert "null" not in stream
    assert json.loads(stream)[0]["message"] == "[HX2001] attendu ')'"
