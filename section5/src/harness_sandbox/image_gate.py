"""Section 5 image gate for the engine's ``image`` tool (W11).

Visual regression needs two primitives the runtime does not ship: compare two
renders, and read exact pixels. Both are pure functions over files, run exactly
like ``shell_gate.py`` / ``web_gate.py`` / ``browser_gate.py``:

    python -I image_gate.py < {"action": "diff", "before": ..., "after": ...}

Guarantees, each testable without a browser:

* **Both inputs and the output stay in the workspace.** Every path is resolved
  with realpath and must land under ``workspace``; symlinks included.
* **Bounded work.** Each PNG is capped by bytes (default 10 MiB, the artifact
  cap) and pixels (default 4 megapixels — the comparison is O(pixels) in pure
  Python); the diff PNG is capped by the same byte limit.
* **No guessing about formats.** 8-bit RGB/RGBA, non-interlaced PNGs — what
  Chromium writes — decode. Palette, 16-bit, interlaced or truncated files are
  refused by name. There is no Pillow in the worker image, deliberately.
* **The diff is arithmetic, not an opinion.** Changed pixels are those whose
  largest RGB channel delta exceeds ``tolerance``; the result carries the exact
  ratio, bounding box, max delta, mean colours of the changed pixels, and a PNG
  that paints those pixels magenta over a grey copy of the target.

Actions:

* ``diff``    — ``{before, after, out?, tolerance?}`` → counts, box, diff PNG.
* ``sample``  — ``{path, points}`` → ``[{x, y, rgb, hex}]`` (max 64 points).
* ``palette`` — ``{path, maxColors?}`` → the dominant colours of one PNG with
  their exact hex, pixel share and bounding box, in one call. ``sample``
  answers single points, so learning a layout costs a call per region; this
  answers "what colours, where" directly and is bounded by the same guards.

One JSON line out; a policy refusal is ``{"denied": reason}``, bad data is
``{"error": reason}``.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import zlib
from pathlib import Path

MAX_BYTES_DEFAULT = 10 * 1024 * 1024  # the per-artifact cap; a diff image is an artifact
MAX_PIXELS_DEFAULT = 4_000_000  # 800x600 is 480k; 4M stays well under a few seconds
MAX_POINTS = 64
MAX_COLORS_DEFAULT = 12
MAX_COLORS_CEILING = 24
#: colours are bucketed on 3 bits per channel (a 5x5x5 cube): antialiasing
#: noise collapses, distinct flat colours do not.
_COLOR_SHIFT = 3
DEFAULT_TOLERANCE = 16
_ACTIONS = ("diff", "sample", "palette")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class Refused(Exception):
    """A request the gate will not execute (path, format, size, dimensions)."""

    def __init__(self, reason: str):
        super().__init__(reason)


# ------------------------------------------------------------------ PNG codec


def decode_png(data: bytes) -> tuple[int, int, int, list[bytearray]]:
    """(width, height, bytes-per-pixel, rows) for 8-bit RGB/RGBA non-interlaced PNGs."""
    if data[:8] != _PNG_MAGIC:
        raise ValueError("not a PNG (bad signature)")
    if len(data) < 33 or data[12:16] != b"IHDR":
        raise ValueError("PNG has no IHDR")
    width, height = struct.unpack(">II", data[16:24])
    bit_depth, colour, _, _, interlace = data[24], data[25], data[26], data[27], data[28]
    if bit_depth != 8 or interlace != 0 or colour not in (2, 6):
        raise ValueError(
            f"unsupported PNG (bit depth {bit_depth}, colour type {colour}, "
            f"interlace {interlace}); only 8-bit RGB/RGBA, non-interlaced"
        )
    if width == 0 or height == 0:
        raise ValueError("PNG has zero width or height")
    bpp = 3 if colour == 2 else 4
    idat = bytearray()
    offset = 8
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("truncated PNG chunk header")
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        if offset + 12 + length > len(data):
            raise ValueError("truncated PNG chunk")
        if kind == b"IDAT":
            idat += data[offset + 8:offset + 8 + length]
        if kind == b"IEND":
            break
        offset += 12 + length
    if not idat:
        raise ValueError("PNG has no IDAT data")
    raw = zlib.decompress(bytes(idat))
    stride = width * bpp
    if len(raw) < height * (stride + 1):
        raise ValueError("PNG pixel data is truncated")
    rows: list[bytearray] = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        filt = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if filt == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif filt == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filt == 3:  # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filt == 4:  # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        elif filt != 0:
            raise ValueError(f"unknown PNG filter {filt}")
        rows.append(line)
        prev = line
    return width, height, bpp, rows


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def encode_png(width: int, height: int, rows: list[bytearray]) -> bytes:
    """8-bit RGB PNG from rows of ``width * 3`` bytes (used for the diff image)."""
    raw = bytearray()
    for row in rows:
        raw.append(0)  # filter: None — the delta PNG is mostly flat colour
        raw += row
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        _PNG_MAGIC
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _chunk(b"IEND", b"")
    )


# ------------------------------------------------------------------ paths


def _workspace_root(workspace: str) -> str:
    ws = os.path.realpath(workspace)
    if not os.path.isdir(ws):
        raise Refused("workspace does not exist")
    return ws


def resolve_path(workspace: str, path: str, *, field: str, must_exist: bool) -> Path:
    """The absolute PNG path under the workspace, or Refused."""
    if not isinstance(path, str) or not path.strip():
        raise Refused(f"{field} must be a non-empty relative path ending in .png")
    ws = _workspace_root(workspace)
    target = os.path.realpath(os.path.join(ws, path))
    if os.path.commonpath([ws, target]) != ws:
        raise Refused(f"{field} {path!r} escapes the workspace")
    if target == ws:
        raise Refused(f"{field} must name a file, not the workspace root")
    if not target.lower().endswith(".png"):
        raise Refused(f"{field} must end in .png")
    if must_exist and not os.path.isfile(target):
        raise Refused(f"{field} {path!r} does not exist")
    return Path(target)


def load_png(path: Path, rel: str, max_bytes: int, max_pixels: int):
    size = path.stat().st_size
    if size > max_bytes:
        raise Refused(f"{rel} is {size} bytes (cap {max_bytes}); render smaller")
    try:
        width, height, bpp, rows = decode_png(path.read_bytes())
    except (ValueError, zlib.error) as exc:
        raise Refused(f"{rel}: {exc}") from exc
    if width * height > max_pixels:
        raise Refused(
            f"{rel} is {width}x{height} ({width * height} pixels, cap {max_pixels})"
        )
    return width, height, bpp, rows


# ------------------------------------------------------------------ actions


def _diff(request: dict) -> dict:
    workspace = str(request.get("workspace") or "")
    max_bytes = int(request.get("maxBytes") or MAX_BYTES_DEFAULT)
    max_pixels = min(int(request.get("maxPixels") or MAX_PIXELS_DEFAULT), MAX_PIXELS_DEFAULT)
    tolerance = request.get("tolerance", DEFAULT_TOLERANCE)
    if not isinstance(tolerance, int) or isinstance(tolerance, bool) or not 0 <= tolerance <= 255:
        raise Refused("tolerance must be an integer between 0 and 255")

    before_path = resolve_path(workspace, str(request.get("before") or ""),
                               field="before", must_exist=True)
    after_path = resolve_path(workspace, str(request.get("after") or ""),
                              field="after", must_exist=True)
    before_rel = os.path.relpath(before_path, os.path.realpath(workspace))
    after_rel = os.path.relpath(after_path, os.path.realpath(workspace))
    bw, bh, bbpp, brows = load_png(before_path, before_rel, max_bytes, max_pixels)
    aw, ah, abpp, arows = load_png(after_path, after_rel, max_bytes, max_pixels)
    if (bw, bh) != (aw, ah):
        raise Refused(
            f"images differ in size ({bw}x{bh} vs {aw}x{ah}); "
            "render both at the same viewport before diffing"
        )

    out_rel = str(request.get("out") or "")
    if not out_rel:
        out_rel = os.path.splitext(before_rel)[0] + ".diff.png"
    out_path = resolve_path(workspace, out_rel, field="out", must_exist=False)
    if out_path in (before_path, after_path):
        raise Refused("out must not overwrite an input image")

    changed = 0
    x0, y0, x1, y1 = bw, bh, -1, -1
    max_delta = 0
    sum_before = [0, 0, 0]
    sum_after = [0, 0, 0]
    out_rows: list[bytearray] = []
    for y in range(bh):
        br, ar = brows[y], arows[y]
        grey = bytearray(bw * 3)
        if br == ar:
            # Flat background rows are the common case; copy greyscaled target.
            for x in range(bw):
                i, o = x * bbpp, x * 3
                g = (ar[i] * 299 + ar[i + 1] * 587 + ar[i + 2] * 114) // 1000
                grey[o] = grey[o + 1] = grey[o + 2] = g
            out_rows.append(grey)
            continue
        for x in range(bw):
            bi, ai, o = x * bbpp, x * abpp, x * 3
            br_r, br_g, br_b = br[bi], br[bi + 1], br[bi + 2]
            ar_r, ar_g, ar_b = ar[ai], ar[ai + 1], ar[ai + 2]
            delta = max(abs(br_r - ar_r), abs(br_g - ar_g), abs(br_b - ar_b))
            if delta > tolerance:
                changed += 1
                if delta > max_delta:
                    max_delta = delta
                if x < x0:
                    x0 = x
                if y < y0:
                    y0 = y
                if x > x1:
                    x1 = x
                if y > y1:
                    y1 = y
                sum_before[0] += br_r
                sum_before[1] += br_g
                sum_before[2] += br_b
                sum_after[0] += ar_r
                sum_after[1] += ar_g
                sum_after[2] += ar_b
                grey[o] = 255
                grey[o + 1] = 0
                grey[o + 2] = 255
            else:
                g = (ar_r * 299 + ar_g * 587 + ar_b * 114) // 1000
                grey[o] = grey[o + 1] = grey[o + 2] = g
        out_rows.append(grey)

    total = bw * bh
    ratio = changed / total if total else 0.0
    data = encode_png(bw, bh, out_rows)
    if len(data) > max_bytes:
        raise Refused(f"diff image is {len(data)} bytes (cap {max_bytes})")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)

    def mean(values: list[int]) -> list[int]:
        return [v // changed for v in values] if changed else []

    return {
        "ok": True,
        "before": before_rel,
        "after": after_rel,
        "width": bw,
        "height": bh,
        "changedPixels": changed,
        "totalPixels": total,
        "ratio": round(ratio, 6),
        "tolerance": tolerance,
        "bbox": [x0, y0, x1 - x0 + 1, y1 - y0 + 1] if changed else None,
        "maxDelta": max_delta,
        "changedMeanBefore": mean(sum_before),
        "changedMeanAfter": mean(sum_after),
        "out": os.path.relpath(out_path, os.path.realpath(workspace)),
        "outBytes": len(data),
        "hint": "open the out image with the read tool to see where it differs",
    }


def _sample(request: dict) -> dict:
    workspace = str(request.get("workspace") or "")
    max_bytes = int(request.get("maxBytes") or MAX_BYTES_DEFAULT)
    max_pixels = min(int(request.get("maxPixels") or MAX_PIXELS_DEFAULT), MAX_PIXELS_DEFAULT)
    path = resolve_path(workspace, str(request.get("path") or ""),
                        field="path", must_exist=True)
    rel = os.path.relpath(path, os.path.realpath(workspace))
    width, height, bpp, rows = load_png(path, rel, max_bytes, max_pixels)

    points = request.get("points")
    if not isinstance(points, list) or not points:
        raise Refused("points must be a non-empty list of [x, y] pairs")
    if len(points) > MAX_POINTS:
        raise Refused(f"at most {MAX_POINTS} points per call (got {len(points)})")
    out = []
    for point in points:
        if (not isinstance(point, (list, tuple)) or len(point) != 2
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in point)):
            raise Refused(f"point {point!r} is not an [x, y] integer pair")
        x, y = point
        if not (0 <= x < width and 0 <= y < height):
            raise Refused(f"point ({x}, {y}) is outside {width}x{height}")
        i = x * bpp
        rgb = [rows[y][i], rows[y][i + 1], rows[y][i + 2]]
        out.append({"x": x, "y": y, "rgb": rgb,
                    "hex": "#{:02x}{:02x}{:02x}".format(*rgb)})
    return {"ok": True, "path": rel, "width": width, "height": height, "points": out}


def _palette(request: dict) -> dict:
    """What colours a page is built from, and where — in one bounded call.

    ``sample`` answers exact points, so learning a layout costs one call per
    region; that is the shape that sent a W11 design run into 60 pixel probes.
    This answers the same question directly: the image is bucketed on 3 bits
    per channel (flat colours stay distinct, antialiasing noise collapses), and
    the largest buckets come back with their exact dominant colour, pixel
    count, share and bounding box.

    Deterministic (no random tie-breaking: buckets sort by count then by
    colour), capped by the same byte/pixel guards, and it never leaves the
    workspace.
    """
    workspace = str(request.get("workspace") or "")
    max_bytes = int(request.get("maxBytes") or MAX_BYTES_DEFAULT)
    max_pixels = min(int(request.get("maxPixels") or MAX_PIXELS_DEFAULT), MAX_PIXELS_DEFAULT)
    path = resolve_path(workspace, str(request.get("path") or ""),
                        field="path", must_exist=True)
    rel = os.path.relpath(path, os.path.realpath(workspace))
    width, height, bpp, rows = load_png(path, rel, max_bytes, max_pixels)
    try:
        limit = int(request.get("maxColors") or MAX_COLORS_DEFAULT)
    except (TypeError, ValueError):
        raise Refused("maxColors must be an integer") from None
    if not 1 <= limit <= MAX_COLORS_CEILING:
        raise Refused(f"maxColors must be 1..{MAX_COLORS_CEILING}")

    buckets: dict[tuple[int, int, int], dict] = {}
    total = width * height
    for y, row in enumerate(rows):
        for x in range(width):
            i = x * bpp
            rgb = (row[i], row[i + 1], row[i + 2])
            key = (rgb[0] >> _COLOR_SHIFT, rgb[1] >> _COLOR_SHIFT, rgb[2] >> _COLOR_SHIFT)
            bucket = buckets.get(key)
            if bucket is None:
                buckets[key] = {"count": 1, "rgb": rgb, "sum": [int(v) for v in rgb],
                                "x0": x, "x1": x, "y0": y, "y1": y}
                continue
            bucket["count"] += 1
            bucket["sum"] = [s + v for s, v in zip(bucket["sum"], rgb, strict=True)]
            if x < bucket["x0"]:
                bucket["x0"] = x
            if x > bucket["x1"]:
                bucket["x1"] = x
            if y < bucket["y0"]:
                bucket["y0"] = y
            if y > bucket["y1"]:
                bucket["y1"] = y

    ordered = sorted(buckets.values(), key=lambda b: (-b["count"], b["rgb"]))[:limit]
    colors = []
    for bucket in ordered:
        rgb = bucket["rgb"]
        colors.append({
            "rgb": list(rgb),
            "hex": "#{:02x}{:02x}{:02x}".format(*rgb),
            "meanRgb": [round(v / bucket["count"]) for v in bucket["sum"]],
            "pixels": bucket["count"],
            "share": round(bucket["count"] / total, 6),
            "bbox": [bucket["x0"], bucket["y0"],
                     bucket["x1"] - bucket["x0"] + 1, bucket["y1"] - bucket["y0"] + 1],
        })
    return {
        "ok": True, "path": rel, "width": width, "height": height,
        "pixels": total, "distinctBuckets": len(buckets), "colors": colors,
        "hint": "one call answers the layout's colours; sample exact points "
                "only to disambiguate",
    }


def render(request: dict) -> dict:
    """A policy refusal is a result, not an exception: callers always get one dict."""
    try:
        action = str(request.get("action") or "diff")
        if action not in _ACTIONS:
            raise Refused(f"action must be one of {_ACTIONS}")
        if not str(request.get("workspace") or "").strip():
            raise Refused("workspace is required")
        if action == "diff":
            return _diff(request)
        if action == "palette":
            return _palette(request)
        return _sample(request)
    except Refused as exc:
        return {"denied": str(exc)}
    except (KeyError, TypeError, struct.error) as exc:
        return {"error": f"malformed gate request: {exc}"}


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        result = render(request)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        result = {"error": f"malformed gate request: {exc}"}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
