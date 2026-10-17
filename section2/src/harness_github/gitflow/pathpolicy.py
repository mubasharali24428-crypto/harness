"""Path rules for staged files: relative, no traversal, never .git, forbidden_paths hard."""

from __future__ import annotations


class PathPolicyError(ValueError):
    pass


def normalize(path: str) -> str:
    text = (path or "").strip()
    if not text or len(text) > 512:
        raise PathPolicyError("path is empty or too long")
    if (
        any(ord(c) < 0x20 or ord(c) == 0x7F for c in text)
        or "\\" in text
        or text.startswith(("/", "-"))
    ):
        raise PathPolicyError(f"unsafe path {text!r}")
    segs = [s for s in text.split("/") if s]
    if any(s in (".", "..") or s.casefold() == ".git" for s in segs):
        raise PathPolicyError(f"unsafe path {text!r}")
    return "/".join(segs)


def check_paths(paths: list[str], forbidden: list[str]) -> list[str]:
    banned = [normalize(f).casefold() for f in forbidden]
    out: list[str] = []
    for raw in paths:
        p = normalize(raw)
        low = p.casefold()
        if any(low == b or low.startswith(b + "/") for b in banned):
            raise PathPolicyError(f"modifying forbidden path {p!r} is blocked")
        out.append(p)
    return out
