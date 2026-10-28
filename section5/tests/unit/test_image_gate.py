"""image_gate.py: the engine's image tool, run standalone as the worker runs it.

The arithmetic is the claim, so the tests pin exact counts and boxes rather than
"something changed": a 10x10 patch in a 100x50 image is 100 pixels, ratio 0.02,
box [20,10,10,10]. The refusals come first — escapes, non-PNG, size mismatch,
caps — because a diff tool that reads outside the workspace is a file oracle.
"""

from __future__ import annotations

import importlib.util
import io
import json
import struct
import sys
import zlib
from pathlib import Path

GATE = Path(__file__).resolve().parents[2] / "src" / "harness_sandbox" / "image_gate.py"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mockup-w11.png"


def _gate():
    spec = importlib.util.spec_from_file_location("image_gate", GATE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["image_gate"] = module
    spec.loader.exec_module(module)
    return module


def _png(module, width: int, height: int, colour=(10, 20, 30), rect=None) -> bytes:
    """A solid image, optionally with one filled rectangle (x, y, w, h, colour)."""
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            c = colour
            if rect:
                rx, ry, rw, rh, rc = rect
                if rx <= x < rx + rw and ry <= y < ry + rh:
                    c = rc
            row += bytes(c)
        rows.append(row)
    return module.encode_png(width, height, rows)


def _request(tmp_path: Path, **over) -> dict:
    req = {
        "action": "diff",
        "before": "before.png",
        "after": "after.png",
        "workspace": str(tmp_path),
        "maxBytes": 10 * 1024 * 1024,
    }
    req.update(over)
    return req


def _ready(tmp_path: Path, **kw):
    """Two 100x50 PNGs on disk; ``before_kw``/``after_kw`` feed :func:`_png`."""
    gate = _gate()
    (tmp_path / "before.png").write_bytes(_png(gate, 100, 50, **kw.get("before_kw", {})))
    (tmp_path / "after.png").write_bytes(_png(gate, 100, 50, **kw.get("after_kw", {})))
    return gate


def test_identical_images_have_zero_changed_pixels(tmp_path):
    gate = _ready(tmp_path)
    out = gate.render(_request(tmp_path))
    assert out["ok"] is True, out
    assert out["changedPixels"] == 0
    assert out["ratio"] == 0.0
    assert out["bbox"] is None
    assert out["changedMeanBefore"] == []
    assert out["out"] == "before.diff.png"


def test_a_changed_square_reports_its_exact_ratio_and_box(tmp_path):
    gate = _ready(
        tmp_path,
        after_kw={"rect": (20, 10, 10, 10, (200, 40, 40))},
    )
    out = gate.render(_request(tmp_path))
    assert out["ok"] is True, out
    assert out["changedPixels"] == 100
    assert out["totalPixels"] == 5000
    assert out["ratio"] == 0.02
    assert out["bbox"] == [20, 10, 10, 10]
    assert out["changedMeanBefore"] == [10, 20, 30]
    assert out["changedMeanAfter"] == [200, 40, 40]
    assert out["maxDelta"] == 190


def test_tolerance_decides_what_counts_as_a_change(tmp_path):
    gate = _gate()
    (tmp_path / "before.png").write_bytes(_png(gate, 100, 50))
    (tmp_path / "after.png").write_bytes(_png(gate, 100, 50, colour=(18, 28, 38)))
    assert gate.render(_request(tmp_path, tolerance=16))["changedPixels"] == 0
    assert gate.render(_request(tmp_path, tolerance=4))["changedPixels"] == 5000


def test_the_diff_png_marks_exactly_the_changed_pixels(tmp_path):
    gate = _ready(tmp_path, after_kw={"rect": (20, 10, 10, 10, (200, 40, 40))})
    out = gate.render(_request(tmp_path))
    data = (tmp_path / out["out"]).read_bytes()
    width, height, bpp, rows = gate.decode_png(data)
    assert (width, height) == (100, 50)
    assert tuple(rows[12][21 * bpp:21 * bpp + 3]) == (255, 0, 255)  # inside the patch
    assert rows[5][5 * bpp] == rows[5][5 * bpp + 1] == rows[5][5 * bpp + 2]  # grey
    assert "outBytes" in out and out["outBytes"] == len(data)


def test_a_path_outside_the_workspace_is_refused(tmp_path):
    gate = _ready(tmp_path)
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(_png(gate, 1, 1))
    for field in ("before", "after", "out"):
        out = gate.render(_request(tmp_path, **{field: "../outside.png"}))
        assert "denied" in out and "escapes the workspace" in out["denied"], (field, out)


def test_the_workspace_root_itself_is_refused(tmp_path):
    gate = _ready(tmp_path)
    out = gate.render(_request(tmp_path, before="."))
    assert "denied" in out


def test_a_missing_file_is_refused(tmp_path):
    gate = _gate()
    (tmp_path / "before.png").write_bytes(_png(gate, 10, 10))
    out = gate.render(_request(tmp_path))
    assert out.get("denied", "").endswith("does not exist")


def test_a_non_png_is_refused_even_with_a_png_name(tmp_path):
    gate = _ready(tmp_path)
    (tmp_path / "after.png").write_bytes(b"not a png at all")
    out = gate.render(_request(tmp_path))
    assert "denied" in out and "PNG" in out["denied"]


def test_a_size_mismatch_is_refused_with_both_sizes(tmp_path):
    gate = _gate()
    (tmp_path / "before.png").write_bytes(_png(gate, 100, 50))
    (tmp_path / "after.png").write_bytes(_png(gate, 100, 60))
    out = gate.render(_request(tmp_path))
    assert "denied" in out and "100x50 vs 100x60" in out["denied"]


def test_the_byte_cap_refuses_before_decoding(tmp_path):
    gate = _ready(tmp_path)
    out = gate.render(_request(tmp_path, maxBytes=10))
    assert "denied" in out and "bytes (cap 10)" in out["denied"]


def test_the_pixel_cap_refuses_before_scanning(tmp_path):
    gate = _ready(tmp_path)
    out = gate.render(_request(tmp_path, maxPixels=100))
    assert "denied" in out and "pixels, cap 100" in out["denied"]


def test_out_must_not_overwrite_an_input(tmp_path):
    gate = _ready(tmp_path)
    out = gate.render(_request(tmp_path, out="before.png"))
    assert "denied" in out and "must not overwrite" in out["denied"]


def test_a_bad_tolerance_is_refused(tmp_path):
    gate = _ready(tmp_path)
    for bad in (-1, 256, "16", True, 1.5):
        out = gate.render(_request(tmp_path, tolerance=bad))
        assert "denied" in out and "tolerance" in out["denied"], bad


def test_sample_returns_the_exact_colour(tmp_path):
    gate = _gate()
    (tmp_path / "shot.png").write_bytes(
        _png(gate, 40, 30, rect=(5, 5, 10, 10, (79, 124, 255)))
    )
    out = gate.render({"action": "sample", "path": "shot.png",
                       "points": [[0, 0], [7, 9], [39, 29]],
                       "workspace": str(tmp_path)})
    assert out["ok"] is True, out
    assert out["width"] == 40 and out["height"] == 30
    assert [p["hex"] for p in out["points"]] == ["#0a141e", "#4f7cff", "#0a141e"]
    assert out["points"][1]["rgb"] == [79, 124, 255]


def test_sample_refuses_points_outside_the_image(tmp_path):
    gate = _gate()
    (tmp_path / "shot.png").write_bytes(_png(gate, 4, 4))
    out = gate.render({"action": "sample", "path": "shot.png",
                       "points": [[4, 0]], "workspace": str(tmp_path)})
    assert "denied" in out and "outside 4x4" in out["denied"]


def test_sample_refuses_more_than_sixty_four_points(tmp_path):
    gate = _gate()
    (tmp_path / "shot.png").write_bytes(_png(gate, 4, 4))
    out = gate.render({"action": "sample", "path": "shot.png",
                       "points": [[0, 0]] * 65, "workspace": str(tmp_path)})
    assert "denied" in out and "at most 64" in out["denied"]


def test_an_unknown_action_is_refused(tmp_path):
    out = _gate().render({"action": "ocr", "workspace": str(tmp_path)})
    assert "denied" in out and "action" in out["denied"]


def test_an_unsupported_png_is_refused_by_name(tmp_path):
    gate = _gate()
    header = struct.pack(">IIBBBBB", 2, 2, 16, 2, 0, 0, 0)  # 16-bit RGB
    data = (b"\x89PNG\r\n\x1a\n" + gate._chunk(b"IHDR", header)
            + gate._chunk(b"IDAT", zlib.compress(b"\x00" * 20))
            + gate._chunk(b"IEND", b""))
    (tmp_path / "before.png").write_bytes(data)
    (tmp_path / "after.png").write_bytes(_png(gate, 2, 2))
    out = gate.render(_request(tmp_path))
    assert "denied" in out and "unsupported PNG" in out["denied"]


def test_a_real_chromium_png_decodes_with_its_flat_colours():
    """The fixture is an 800x600 mockup rendered by the worker image's Chromium."""
    gate = _gate()
    width, height, bpp, rows = gate.decode_png(FIXTURE.read_bytes())
    assert (width, height) == (800, 600)

    def at(x, y):
        i = x * bpp
        return tuple(rows[y][i:i + 3])

    assert at(10, 10) == (15, 17, 21)  # canvas
    assert at(150, 145) == (79, 124, 255)  # accent bar
    assert at(150, 300) == (23, 26, 33)  # card
    assert at(150, 415) == (62, 207, 142)  # green swatch
    assert at(320, 415) == (242, 184, 75)  # amber swatch


def test_main_reads_one_json_line_from_stdin(tmp_path, capsys, monkeypatch):
    gate = _gate()
    (tmp_path / "before.png").write_bytes(_png(gate, 4, 4))
    (tmp_path / "after.png").write_bytes(_png(gate, 4, 4))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_request(tmp_path))))
    assert gate.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["ratio"] == 0.0
