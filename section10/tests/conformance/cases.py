"""Golden-fixture request cases for the Section 10 conformance suite.

Each case: fixture filename, HTTP method, path, optional JSON body, and whether
a fresh app (clean state store) is used. The generator writes goldens from the
live app; the conformance test replays the same requests and demands equality.
"""

CASES = [
    {"fixture": "health.json", "method": "GET", "path": "/health"},
    {
        "fixture": "steps.json",
        "method": "GET",
        "path": "/api/v1/onboarding/steps",
    },
    {
        # M10-B3: interactive first-run checklist (deterministic — derives
        # `done` from a fresh store where nothing is acknowledged).
        "fixture": "checklist.json",
        "method": "GET",
        "path": "/api/v1/onboarding/checklist",
        "fresh": True,
    },
    {
        "fixture": "security_checklist.json",
        "method": "GET",
        "path": "/api/v1/onboarding/security-checklist",
    },
    {
        "fixture": "security_checklist_acknowledged.json",
        "method": "POST",
        "path": "/api/v1/onboarding/security-checklist/acknowledge",
        "fresh": True,
    },
    {
        "fixture": "guided_demo.json",
        "method": "GET",
        "path": "/api/v1/onboarding/guided-demo",
    },
    {
        "fixture": "demo_repo_manifest.json",
        "method": "GET",
        "path": "/api/v1/onboarding/demo-repo/manifest",
    },
    {
        "fixture": "telemetry_settings_default.json",
        "method": "GET",
        "path": "/api/v1/onboarding/telemetry-settings",
        "fresh": True,
    },
    {
        "fixture": "telemetry_error_opt_in_required.json",
        "method": "PUT",
        "path": "/api/v1/onboarding/telemetry-settings",
        "body": {"fullPromptText": True},
        "fresh": True,
    },
    {
        "fixture": "telemetry_settings_opted_in.json",
        "method": "PUT",
        "path": "/api/v1/onboarding/telemetry-settings",
        "body": {"fullPromptText": True, "optInAcknowledged": True},
        "fresh": True,
    },
    {
        "fixture": "telemetry_event_scrubbed.json",
        "method": "POST",
        "path": "/api/v1/onboarding/telemetry/events",
        "body": {
            "kind": "prompt_metadata",
            "payload": {"model": "deepseek/deepseek-coder", "promptText": "TOP SECRET"},
        },
        "fresh": True,
    },
    {
        "fixture": "telemetry_event_full_rejected.json",
        "method": "POST",
        "path": "/api/v1/onboarding/telemetry/events",
        "body": {"kind": "full_code", "payload": {"codeContent": "def f(): pass"}},
        "fresh": True,
    },
]
