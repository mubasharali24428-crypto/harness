"""W9: a screenshot is only evidence if it is a real raster of a named revision."""

from __future__ import annotations

import struct
import zlib

import pytest

from harness_orchestrator import visuals


def png(width: int, height: int, extra: bytes = b"") -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"\x00" + b"\x00" * (width * 3)
    return (visuals._PNG_MAGIC + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw * height)) + extra
            + chunk(b"IEND", b""))


def jpeg(width: int, height: int) -> bytes:
    sof = (b"\xff\xc0" + struct.pack(">H", 11) + b"\x08"
           + struct.pack(">HH", height, width) + b"\x03" + b"\x00" * 6)
    return b"\xff\xd8\xff\xe0" + struct.pack(">H", 4) + b"\x00\x00" + sof + b"\xff\xd9"


def webp_vp8x(width: int, height: int) -> bytes:
    body = (b"VP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00"
            + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little"))
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def test_png_dimensions_come_from_the_header():
    assert visuals.image_info(png(120, 40)) == ("png", 120, 40)


def test_jpeg_and_webp_are_understood():
    assert visuals.image_info(jpeg(64, 32)) == ("jpeg", 64, 32)
    assert visuals.image_info(webp_vp8x(300, 200)) == ("webp", 300, 200)


@pytest.mark.parametrize("data", [
    b"",
    b"<svg xmlns='http://www.w3.org/2000/svg'><script>x()</script></svg>",
    b"GIF89a" + b"\x00" * 40,
    b"%PDF-1.7\n" + b"x" * 40,
    b"\xff\xd8\xff\xe0" + b"\x00" * 20,  # JPEG magic, no frame header
])
def test_non_raster_or_unreadable_bytes_are_refused(data):
    with pytest.raises(visuals.VisualError) as exc:
        visuals.validate_visual("shot.png", data)
    assert exc.value.code in {"EMPTY_ARTIFACT", "VISUAL_TYPE_NOT_ALLOWED"}


def test_a_suffix_that_contradicts_the_bytes_is_refused():
    with pytest.raises(visuals.VisualError) as exc:
        visuals.validate_visual("shot.jpg", png(32, 32))
    assert exc.value.code == "VISUAL_TYPE_MISMATCH"
    with pytest.raises(visuals.VisualError) as exc:
        visuals.validate_visual("shot", png(32, 32))
    assert exc.value.code == "VISUAL_TYPE_MISMATCH"


def test_dimension_bounds_are_enforced_from_the_header():
    with pytest.raises(visuals.VisualError) as exc:
        visuals.validate_visual("wide.png", png(9000, 20))
    assert exc.value.code == "VISUAL_DIMENSIONS"
    assert exc.value.detail == {"width": 9000, "height": 20}
    with pytest.raises(visuals.VisualError) as exc:
        visuals.validate_visual("tiny.png", png(4, 4))
    assert exc.value.code == "VISUAL_DIMENSIONS"


def test_the_byte_cap_is_checked_before_the_header_is_trusted():
    with pytest.raises(visuals.VisualError) as exc:
        visuals.validate_visual("big.png", png(100, 100), max_bytes=64)
    assert exc.value.code == "VISUAL_TOO_LARGE"
    assert exc.value.status == 413


def test_the_accepted_shape_is_returned():
    assert visuals.validate_visual("shot.png", png(80, 60)) == {
        "media": "png", "width": 80, "height": 60, "sizeBytes": len(png(80, 60))}


def test_a_pair_must_share_every_revision_field():
    base = {"runHeadSha": "a" * 40, "baseSha": "b" * 40, "planVersion": 2,
            "changesDigest": "c" * 64}
    assert visuals._same_revision(base, dict(base))
    for field in ("runHeadSha", "baseSha", "planVersion", "changesDigest"):
        other = dict(base)
        other[field] = "moved"
        assert not visuals._same_revision(base, other), field
