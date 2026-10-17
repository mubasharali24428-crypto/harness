from harness_security_policy import Outcome, inspect_outbound
from harness_security_policy.dlp import iban_valid, luhn_valid, ssn_valid

VALID_CARD = "4111 1111 1111 1111"  # Luhn-valid test number
VALID_IBAN = "DE89370400440532013000"


def test_luhn_and_iban_validators():
    assert luhn_valid("4111111111111111")
    assert not luhn_valid("4111111111111112")
    assert iban_valid(VALID_IBAN)
    assert not iban_valid("DE00370400440532013000")
    assert ssn_valid("123-45-6789")
    assert not ssn_valid("000-45-6789")
    assert not ssn_valid("666-45-6789")
    assert not ssn_valid("900-45-6789")


def test_valid_card_is_redacted():
    decision = inspect_outbound(f"customer card: {VALID_CARD} on file")
    assert decision.outcome is Outcome.REDACT
    assert "4111" not in decision.text
    assert "[REDACTED:dlp.payment_card]" in decision.text


def test_invalid_card_number_untouched():
    decision = inspect_outbound("order 4111 1111 1111 1112 shipped")
    assert decision.outcome is Outcome.ALLOW
    assert decision.text == "order 4111 1111 1111 1112 shipped"


def test_credentialed_connection_string_denies():
    decision = inspect_outbound("postgres://admin:supersecret@db.internal:5432/prod")
    assert decision.outcome is Outcome.DENY
    assert decision.reason == "dlp.denied"
    assert decision.text is None


def test_connection_string_without_credentials_allows():
    decision = inspect_outbound("postgres://db.internal:5432/prod")
    assert decision.outcome is Outcome.ALLOW


def test_valid_iban_redacted_invalid_not():
    redacted = inspect_outbound(f"wire to {VALID_IBAN} today")
    assert redacted.outcome is Outcome.REDACT
    assert VALID_IBAN not in redacted.text

    untouched = inspect_outbound("wire to DE00370400440532013000 today")
    assert untouched.outcome is Outcome.ALLOW


def test_ssn_shaped_identifier_redacted():
    decision = inspect_outbound("SSN 123-45-6789 on file")
    assert decision.outcome is Outcome.REDACT
    assert "123-45-6789" not in decision.text


def test_email_and_internal_hostname_are_informational_only():
    text = "mail bob@example.com about deploy.corp rollout"
    decision = inspect_outbound(text)
    assert decision.outcome is Outcome.ALLOW
    assert decision.reason == "dlp.informational_only"
    assert decision.text == text
    rules = {f.rule_id for f in decision.findings}
    assert "dlp.email_address" in rules
    assert "dlp.internal_hostname" in rules


def test_deny_beats_redact_in_aggregate():
    text = f"card {VALID_CARD} and postgres://u:p@h:5432/db"
    decision = inspect_outbound(text)
    assert decision.outcome is Outcome.DENY
