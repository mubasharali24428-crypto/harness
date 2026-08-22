"""Telemetry consent policy — brief §25, §16 Q25, §351-357.

Baseline telemetry (error logs, usage analytics, prompt metadata such as model +
token counts, code metadata such as files-changed counts and languages) is on by
default. Full prompt text and full code content are collected ONLY after
explicit opt-in, and the enforcement is server-side, not a UI checkbox.
"""

from pydantic import BaseModel, Field

BASELINE_KINDS = frozenset({"error_log", "usage_analytics", "prompt_metadata", "code_metadata"})
FULL_KINDS = frozenset({"full_prompt", "full_code"})

# Keys that carry full content and must never survive a baseline-only pipeline.
FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {"promptText", "messages", "codeContent", "fileContents", "diff"}
)


class TelemetrySettings(BaseModel):
    baselineEnabled: bool = True
    errorLogs: bool = True
    usageAnalytics: bool = True
    promptMetadata: bool = True
    codeMetadata: bool = True
    fullPromptText: bool = False
    fullCodeContent: bool = False


class TelemetrySettingsUpdate(BaseModel):
    """Full-content flags require optInAcknowledged=true in the same request."""

    baselineEnabled: bool | None = None
    errorLogs: bool | None = None
    usageAnalytics: bool | None = None
    promptMetadata: bool | None = None
    codeMetadata: bool | None = None
    fullPromptText: bool | None = None
    fullCodeContent: bool | None = None
    optInAcknowledged: bool = False


class TelemetryEvent(BaseModel):
    kind: str
    payload: dict = Field(default_factory=dict)
    ts: str | None = None


class ExplicitOptInRequiredError(Exception):
    """Raised when a request tries to enable full-content collection without acknowledgment."""


class FullCollectionNotEnabledError(Exception):
    """Raised when a full-content event arrives while consent is off."""


def wants_full_content(update: TelemetrySettingsUpdate) -> bool:
    return bool(update.fullPromptText) or bool(update.fullCodeContent)


def apply_update(current: TelemetrySettings, update: TelemetrySettingsUpdate) -> TelemetrySettings:
    if wants_full_content(update) and not update.optInAcknowledged:
        raise ExplicitOptInRequiredError(
            "full prompt/code collection requires explicit opt-in acknowledgment"
        )
    # Once acknowledged, flags latch only to True via this path.
    if update.fullPromptText is not None:
        current.fullPromptText = bool(update.fullPromptText)
    if update.fullCodeContent is not None:
        current.fullCodeContent = bool(update.fullCodeContent)
    baseline_fields = (
        "baselineEnabled",
        "errorLogs",
        "usageAnalytics",
        "promptMetadata",
        "codeMetadata",
    )
    for field in baseline_fields:
        value = getattr(update, field)
        if value is not None:
            setattr(current, field, bool(value))
    if not current.baselineEnabled:
        # Baseline off implies no full collection either.
        current.fullPromptText = False
        current.fullCodeContent = False
    return current


def validate_event(settings: TelemetrySettings, event: TelemetryEvent) -> TelemetryEvent:
    kind = event.kind
    if kind in FULL_KINDS:
        allowed = settings.fullPromptText if kind == "full_prompt" else settings.fullCodeContent
        if not (settings.baselineEnabled and allowed):
            raise FullCollectionNotEnabledError(
                f"telemetry kind '{kind}' rejected: full-content collection is not enabled"
            )
        return event
    if kind not in BASELINE_KINDS:
        raise ValueError(f"unknown telemetry kind '{kind}'")
    if not settings.baselineEnabled:
        raise FullCollectionNotEnabledError("baseline telemetry is disabled for this instance")
    return scrub(event)


def scrub(event: TelemetryEvent) -> TelemetryEvent:
    """Strip any full-content keys from a baseline event payload, recursively."""

    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in FORBIDDEN_PAYLOAD_KEYS}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    return TelemetryEvent(kind=event.kind, payload=clean(event.payload), ts=event.ts)


def scrubbed(settings: TelemetrySettings, event: TelemetryEvent) -> TelemetryEvent:
    """Convenience wrapper used by the ingest endpoint after validation."""

    return validate_event(settings, event)
