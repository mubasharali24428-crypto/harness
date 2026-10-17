"""Provider secret scrubbing for engine-derived text.

Mirrors section3 providers' _scrub semantics: literal replacement of any
configured secret that could surface in logs, events, or the UI. Engine
exceptions may embed provider bodies or the Authorization header, and the
never-log-or-model-context rule applies to every surface a string reaches.
"""


def scrub_secrets(text: str, *secrets: str | None) -> str:
    """Replace each non-empty secret with “[redacted]”, leaving other text
    untouched (honest error wording stays honest)."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text
