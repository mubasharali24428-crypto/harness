"""Content-addressed blob store and content classifier for run artifacts (part 48.004, 48.009).

Store: bytes are written once under ``<root>/<team digest>/<sha[:2]>/<sha>``. The key is derived
ONLY from the sha256 of the bytes and a digest of the team id, never from a client-supplied name,
so a name cannot traverse anywhere; every key is re-validated against a strict pattern before a
path is built, and the resolved path must stay inside the root. Bytes are namespaced per team so
two teams uploading the same bytes never share (or delete) each other's blob. Reads re-hash and
refuse a corrupted blob.

Honest ceiling (same as ``diffstore``): the store is process-local disk (``HARNESS_ARTIFACT_ROOT``,
default ``$TMPDIR/harness_artifacts``). It survives a restart of one process but is not shared
between replicas and is not backed up; the artifacts table is durable, the bytes are not.

Classifier: a verdict from magic bytes AND extension AND markup sniffing. Executables, archives,
scripts and active content (HTML/SVG) are ``quarantined``; so is a file whose extension claims an
image/PDF that the bytes do not match. This is a static filter, not an antivirus: nothing is
executed or unpacked, and a payload inside a type this filter allows (an office macro in a PDF, an
image exploit) is NOT detected.
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

SCANNER_VERSION = 1
KEY_RE = re.compile(r"^[0-9a-f]{16}/[0-9a-f]{2}/[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def store_root() -> Path:
    raw = os.environ.get("HARNESS_ARTIFACT_ROOT", "")
    if raw:
        return Path(raw)
    return Path(os.environ.get("TMPDIR") or "/tmp") / "harness_artifacts"


def storage_ref(team_id: str, sha: str) -> str:
    """Store key for ``sha`` in ``team_id``'s namespace."""
    if not SHA_RE.match(sha):
        raise ValueError("bad sha256")
    return f"{hashlib.sha256(team_id.encode()).hexdigest()[:16]}/{sha[:2]}/{sha}"


def _path(ref: str) -> Path:
    if not isinstance(ref, str) or not KEY_RE.match(ref):
        raise ValueError("bad storage ref")
    root = store_root().resolve()
    path = (root / ref).resolve()
    path.relative_to(root)  # ValueError when it escapes the root
    return path


def put(ref: str, data: bytes) -> None:
    path = _path(ref)
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{id(data)}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)  # atomic publish; racing writers write identical bytes


def get(ref: str) -> bytes | None:
    """Bytes for ``ref``; None when absent or when the content no longer hashes to its key."""
    path = _path(ref)
    if not path.is_file():
        return None
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != ref.rsplit("/", 1)[1]:
        return None
    return data


def remove(ref: str) -> None:
    path = _path(ref)
    if path.is_file():
        path.unlink()


# ------------------------------------------------------------------------------ names


def clean_name(raw: str) -> str | None:
    """A display name that is safe to echo and to put in a header; None when it is not.

    Rejected: empty or over 120 chars, path separators, control and format characters (bidi
    overrides included), a leading dot, a trailing dot or space. The name is never used as a
    path: the store key comes from the hash."""
    name = unicodedata.normalize("NFC", raw or "").strip()
    if not 1 <= len(name) <= 120 or name.startswith(".") or name.endswith("."):
        return None
    for ch in name:
        if ch in "/\\" or unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn"):
            return None
    return name


# ------------------------------------------------------------------------- classifier

# (offset, magic bytes, category, label)
_MAGIC: tuple[tuple[int, bytes, str, str], ...] = (
    (0, b"MZ", "executable", "pe"),
    (0, b"\x7fELF", "executable", "elf"),
    (0, b"\xfe\xed\xfa\xce", "executable", "macho"),
    (0, b"\xfe\xed\xfa\xcf", "executable", "macho"),
    (0, b"\xce\xfa\xed\xfe", "executable", "macho"),
    (0, b"\xcf\xfa\xed\xfe", "executable", "macho"),
    (0, b"\xca\xfe\xba\xbe", "executable", "macho-or-java-class"),
    (0, b"\x00asm", "executable", "wasm"),
    (0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "executable", "ole-compound"),
    (0, b"#!", "script", "shebang"),
    (0, b"PK\x03\x04", "archive", "zip"),
    (0, b"PK\x05\x06", "archive", "zip"),
    (0, b"\x1f\x8b", "archive", "gzip"),
    (0, b"BZh", "archive", "bzip2"),
    (0, b"\xfd7zXZ\x00", "archive", "xz"),
    (0, b"7z\xbc\xaf\x27\x1c", "archive", "7z"),
    (0, b"Rar!\x1a\x07", "archive", "rar"),
    (257, b"ustar", "archive", "tar"),
    (0, b"\x89PNG\r\n\x1a\n", "image", "png"),
    (0, b"\xff\xd8\xff", "image", "jpeg"),
    (0, b"GIF87a", "image", "gif"),
    (0, b"GIF89a", "image", "gif"),
    (0, b"%PDF-", "pdf", "pdf"),
)

EXEC_EXT = {
    "exe",
    "dll",
    "so",
    "dylib",
    "com",
    "scr",
    "msi",
    "jar",
    "class",
    "apk",
    "app",
    "elf",
    "wasm",
    "pyc",
    "pif",
    "cpl",
    "sys",
    "ko",
}
SCRIPT_EXT = {
    "sh",
    "bash",
    "zsh",
    "csh",
    "ksh",
    "fish",
    "bat",
    "cmd",
    "ps1",
    "psm1",
    "vbs",
    "vbe",
    "wsf",
    "js",
    "mjs",
    "cjs",
    "py",
    "pl",
    "rb",
    "php",
    "lua",
    "applescript",
    "command",
    "scpt",
}
ARCHIVE_EXT = {
    "zip",
    "tar",
    "gz",
    "tgz",
    "bz2",
    "xz",
    "7z",
    "rar",
    "war",
    "ear",
    "iso",
    "dmg",
    "cab",
    "z",
    "zst",
    "lz",
}
ACTIVE_EXT = {"html", "htm", "xhtml", "svg", "svgz", "hta", "swf", "xht", "shtml"}
IMAGE_EXT = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "gif": "gif", "webp": "webp"}
TEXT_EXT = {
    "txt",
    "md",
    "markdown",
    "log",
    "csv",
    "tsv",
    "json",
    "yaml",
    "yml",
    "diff",
    "patch",
    "toml",
    "ini",
    "cfg",
    "rst",
    "ndjson",
}
_ACTIVE_MARKUP = (
    b"<!doctype html",
    b"<html",
    b"<script",
    b"<svg",
    b"<iframe",
    b"<?php",
    b"<body",
    b"<object",
    b"<embed",
)


def _weak_magic(label: str, data: bytes) -> bool:
    """MZ and BZh are short: text that merely starts with them must not be called binary."""
    if label == "pe":
        pe = int.from_bytes(data[60:64], "little") if len(data) >= 64 else 0
        return b"\x00" in data[:64] or data[pe : pe + 4] == b"PE\x00\x00"
    if label == "bzip2":
        return data[3:4].isdigit() and data[4:10] == b"1AY&SY"
    return True


def _sniff(data: bytes) -> tuple[str, str] | None:
    for off, magic, cat, label in _MAGIC:
        if data[off : off + len(magic)] == magic and _weak_magic(label, data):
            return cat, label
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image", "webp"
    return None


def _is_text(data: bytes) -> bool:
    if b"\x00" in data[:8192]:
        return False
    try:
        data[:65536].decode("utf-8")
    except UnicodeDecodeError as e:
        # a multi-byte char cut at the sample boundary is not binary
        return e.start >= min(len(data), 65536) - 3
    return True


def classify(name: str, data: bytes, declared: str = "") -> dict[str, Any]:
    """Verdict for one upload: ``{"verdict": "clean"|"quarantined", "reasons": [...], ...}``."""
    reasons: list[str] = []
    segments = [s.lower() for s in name.split(".")[1:]]  # every extension segment counts
    ext = segments[-1] if segments else ""
    magic = _sniff(data)
    textual = magic is None and _is_text(data)
    for seg in segments:
        for cat, table in (
            ("executable", EXEC_EXT),
            ("script", SCRIPT_EXT),
            ("archive", ARCHIVE_EXT),
            ("active", ACTIVE_EXT),
        ):
            if seg in table:
                reasons.append(f"{cat}_extension:.{seg}")
    if magic is not None and magic[0] in ("executable", "script", "archive"):
        reasons.append(f"{magic[0]}_magic:{magic[1]}")
    if textual:
        low = data[:4096].lower()
        if any(m in low for m in _ACTIVE_MARKUP):
            reasons.append("active_content_markup")
    if ext in IMAGE_EXT and (magic is None or magic[1] != IMAGE_EXT[ext]):
        reasons.append(f"extension_magic_mismatch:.{ext}")
    if ext == "pdf" and (magic is None or magic[1] != "pdf"):
        reasons.append("extension_magic_mismatch:.pdf")
    return {
        "verdict": "quarantined" if reasons else "clean",
        "reasons": sorted(set(reasons)),
        "sniffed": magic[1] if magic else ("text" if textual else "binary"),
        "textual": textual,
        "declaredType": re.sub(r"[^\x20-\x7e]", "", declared)[:120],
        "scannerVersion": SCANNER_VERSION,
    }


def serve_type(name: str, scan: dict[str, Any]) -> str:
    """The Content-Type the SERVER serves for a clean artifact. Never the client's claim."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    sniffed = scan.get("sniffed")
    if sniffed in ("png", "jpeg", "gif", "webp"):
        return f"image/{sniffed}"
    if sniffed == "pdf":
        return "application/pdf"
    if scan.get("textual"):
        return "application/json" if ext == "json" else "text/plain; charset=utf-8"
    return "application/octet-stream"
