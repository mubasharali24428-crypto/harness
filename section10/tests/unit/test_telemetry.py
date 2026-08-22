"""Telemetry policy: baseline on by default, full content only with explicit opt-in."""

import pytest

from harness_onboarding.telemetry import (
    ExplicitOptInRequiredError,
    FullCollectionNotEnabledError,
    TelemetryEvent,
    TelemetrySettings,
    TelemetrySettingsUpdate,
    apply_update,
    scrub,
    validate_event,
)


def make_event(kind: str, payload: dict) -> TelemetryEvent:
    return TelemetryEvent(kind=kind, payload=payload)


class TestDefaults:
    def test_baseline_on_by_default(self):
        s = TelemetrySettings()
        assert s.baselineEnabled and s.errorLogs and s.usageAnalytics
        assert s.promptMetadata and s.codeMetadata

    def test_full_content_off_by_default(self):
        s = TelemetrySettings()
        assert not s.fullPromptText and not s.fullCodeContent


class TestApplyUpdate:
    def test_enabling_full_content_requires_acknowledgment(self):
        with pytest.raises(ExplicitOptInRequiredError):
            apply_update(TelemetrySettings(), TelemetrySettingsUpdate(fullPromptText=True))

    def test_full_code_content_requires_acknowledgment(self):
        with pytest.raises(ExplicitOptInRequiredError):
            apply_update(TelemetrySettings(), TelemetrySettingsUpdate(fullCodeContent=True))

    def test_acknowledged_opt_in_latches(self):
        s = apply_update(
            TelemetrySettings(),
            TelemetrySettingsUpdate(fullPromptText=True, optInAcknowledged=True),
        )
        assert s.fullPromptText is True

    def test_disabling_baseline_disables_full_collection(self):
        s = TelemetrySettings(fullPromptText=True)
        s = apply_update(s, TelemetrySettingsUpdate(baselineEnabled=False))
        assert not s.fullPromptText and not s.fullCodeContent


class TestScrub:
    def test_forbidden_keys_stripped_recursively(self):
        ev = make_event(
            "prompt_metadata",
            {
                "model": "deepseek/deepseek-coder",
                "promptText": "SECRET INSTRUCTIONS",
                "nested": {"messages": ["should", "vanish"], "inputTokens": 12},
                "list": [{"codeContent": "def f(): pass"}],
            },
        )
        clean = scrub(ev)
        assert clean.payload == {
            "model": "deepseek/deepseek-coder",
            "nested": {"inputTokens": 12},
            "list": [{}],
        }

    def test_baseline_event_survives_scrub_untouched_when_clean(self):
        ev = make_event("usage_analytics", {"runsCreated": 3})
        assert scrub(ev).payload == {"runsCreated": 3}


class TestValidateEvent:
    def test_full_prompt_rejected_when_not_opted_in(self):
        with pytest.raises(FullCollectionNotEnabledError):
            validate_event(TelemetrySettings(), make_event("full_prompt", {"promptText": "x"}))

    def test_full_prompt_accepted_when_opted_in(self):
        s = TelemetrySettings(fullPromptText=True)
        ev = validate_event(s, make_event("full_prompt", {"promptText": "x"}))
        assert ev.payload == {"promptText": "x"}

    def test_baseline_rejected_when_baseline_disabled(self):
        s = TelemetrySettings(baselineEnabled=False)
        with pytest.raises(FullCollectionNotEnabledError):
            validate_event(s, make_event("error_log", {"msg": "boom"}))

    def test_unknown_kind_rejected(self):
        with pytest.raises(ValueError, match="unknown telemetry kind"):
            validate_event(TelemetrySettings(), make_event("telemetry_of_doom", {}))

    def test_clean_baseline_event_passes_with_metadata_intact(self):
        s = TelemetrySettings()
        ev = validate_event(
            s,
            make_event("code_metadata", {"filesChanged": 2, "languages": ["python"]}),
        )
        assert ev.payload == {"filesChanged": 2, "languages": ["python"]}

    def test_dirty_baseline_event_is_scrubbed_not_rejected(self):
        s = TelemetrySettings()
        ev = validate_event(s, make_event("prompt_metadata", {"promptText": "p", "model": "m"}))
        assert ev.payload == {"model": "m"}
