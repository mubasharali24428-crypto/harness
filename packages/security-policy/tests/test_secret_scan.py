from harness_security_policy import Outcome, SecretScanPolicy, scan_outbound, scan_text

CLEAN = "def add(a, b):\n    return a + b\n"

OPENAI_KEY = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEA1234567890abcdef\n"
    "-----END RSA PRIVATE KEY-----"
)


def test_clean_text_allows_with_no_findings():
    decision = scan_outbound(CLEAN)
    assert decision.outcome is Outcome.ALLOW
    assert decision.reason == "secret.clean"
    assert decision.findings == ()


def test_provider_key_is_redacted_not_echoed():
    text = f"export OPENAI_API_KEY={OPENAI_KEY}"
    decision = scan_outbound(text)
    assert decision.outcome is Outcome.REDACT
    assert OPENAI_KEY not in decision.text
    assert "[REDACTED:secret.provider_api_key]" in decision.text
    for finding in decision.findings:
        assert OPENAI_KEY not in finding.as_dict().get("message", "")


def test_private_key_denies_by_default():
    decision = scan_outbound(PEM)
    assert decision.outcome is Outcome.DENY
    assert decision.reason == "secret.denied"
    assert decision.text is None


def test_bearer_token_redacts_only_the_credential_value():
    token = "A1b2C3d4E5f6G7h8I9j0"
    text = f"Authorization: Bearer {token}"
    decision = scan_outbound(text)
    assert decision.outcome is Outcome.REDACT
    assert token not in decision.text
    assert "Authorization: Bearer" in decision.text


def test_generic_credential_assignment_redacted():
    text = 'client_secret = "zzzzzzzzzzzzzzzzzzzzzz"'
    decision = scan_outbound(text)
    assert decision.outcome is Outcome.REDACT
    assert "zzzzzzzzzzzzzzzzzzzzzz" not in decision.text


def test_redaction_disabled_fails_closed():
    policy = SecretScanPolicy(redact=False)
    decision = scan_outbound("key sk-abcdefghijklmnopqrstuvwxyz0123456789", policy=policy)
    assert decision.outcome is Outcome.DENY
    assert decision.reason == "secret.denied_no_redaction"


def test_custom_policy_can_escalate_high_severity_to_deny():
    policy = SecretScanPolicy(deny_severities=frozenset({"critical", "high"}))
    decision = scan_outbound(f"token={OPENAI_KEY}", policy=policy)
    assert decision.outcome is Outcome.DENY


def test_overlapping_findings_merge_into_single_marker():
    jwt_in_bearer = "eyJabcdefgh.eyJijklmnop.signature123456"
    decision = scan_outbound(f"Authorization: Bearer {jwt_in_bearer}")
    assert decision.outcome is Outcome.REDACT
    assert decision.text.count("[REDACTED:") == 1
    assert jwt_in_bearer not in decision.text


def test_scan_text_returns_spans_in_document_order():
    text = f"{OPENAI_KEY} then sk-zyxwvutsrqponmlkjihgfedcba9876543210"
    findings = scan_text(text)
    starts = [f.start for f in findings]
    assert starts == sorted(starts)
    assert len(findings) >= 2


def test_oversized_text_rejected():
    import pytest

    from harness_security_policy.secret_scan import MAX_SCAN_CHARS

    with pytest.raises(ValueError):
        scan_text("a" * (MAX_SCAN_CHARS + 1))
